from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import IssuePayload, TablePayload
from apps.knowledge.models import KnowledgeUploadTable
from apps.knowledge.tables.postprocess_rows import IngestionTablePostprocessRowsMixin
from apps.knowledge.tables.postprocess_signals import IngestionTablePostprocessSignalsMixin
from apps.knowledge.tables.postprocess_stitching import IngestionTablePostprocessStitchingMixin


class IngestionTablePostprocessingMixin(
    IngestionTablePostprocessSignalsMixin,
    IngestionTablePostprocessRowsMixin,
    IngestionTablePostprocessStitchingMixin,
):

    def _build_table_profile(self, tables: Sequence[TablePayload]) -> dict[str, Any] | None:
        if not tables:
            return None
        column_limit = max(16, int(getattr(settings, "RAG_TABLE_PROFILE_COLUMN_LIMIT", 256)))
        token_limit = max(32, int(getattr(settings, "RAG_TABLE_PROFILE_ROW_LABEL_TOKEN_LIMIT", 800)))
        label_limit = max(8, int(getattr(settings, "RAG_TABLE_PROFILE_ROW_LABEL_SAMPLE_LIMIT", 60)))

        columns: set[str] = set()
        row_label_tokens: set[str] = set()
        row_label_samples: list[str] = []
        token_split = re.compile(r"[^\w]+", flags=re.UNICODE)

        for table in tables:
            schema = table.column_schema or []
            for col in schema:
                lowered = str(col or "").strip().lower()
                if lowered:
                    columns.add(lowered)
                    if len(columns) >= column_limit:
                        break
            if len(columns) >= column_limit:
                columns = set(sorted(columns)[:column_limit])

            labels = self._table_row_label_set(table)
            for label in labels:
                if label and len(row_label_samples) < label_limit:
                    row_label_samples.append(label)
                for token in token_split.split(label):
                    cleaned = token.strip().lower()
                    if not cleaned or cleaned.isdigit():
                        continue
                    row_label_tokens.add(cleaned)
                    if len(row_label_tokens) >= token_limit:
                        break
                if len(row_label_tokens) >= token_limit:
                    break

            if len(columns) >= column_limit and len(row_label_tokens) >= token_limit:
                break

        profile: dict[str, Any] = {
            "version": 1,
            "generated_at": timezone.now().isoformat(),
            "table_count": len(tables),
            "columns": sorted(columns)[:column_limit],
            "row_label_tokens": sorted(row_label_tokens)[:token_limit],
            "row_label_samples": row_label_samples[:label_limit],
        }
        return profile

    def _table_schema_is_generic(self, table: TablePayload) -> bool:
        if not table.column_schema:
            return True
        assessment = self._assess_table_quality(table)
        signals = assessment.get("signals") or {}
        if signals.get("nonsense_columns"):
            return True
        header_confidence = float(signals.get("header_confidence") or 0.0)
        if header_confidence < 0.3:
            return True
        return False

    def _table_dedupe_heading_key(self, table: TablePayload) -> str:
        meta = table.metadata if isinstance(table.metadata, Mapping) else {}
        candidates = [
            str(table.section_heading or ""),
            str(meta.get("derived_section_heading") or ""),
            str(table.title or ""),
        ]
        for candidate in candidates:
            normalized = self._normalize_evidence_phrase(candidate)
            if normalized:
                return normalized
        return ""

    def _table_region_overlap_ratio(self, left: TablePayload, right: TablePayload) -> float:
        left_bbox = self._normalize_bbox(left.bbox)
        right_bbox = self._normalize_bbox(right.bbox)
        if not left_bbox or not right_bbox:
            return 0.0

        ix0 = max(left_bbox["x0"], right_bbox["x0"])
        iy0 = max(left_bbox["y0"], right_bbox["y0"])
        ix1 = min(left_bbox["x1"], right_bbox["x1"])
        iy1 = min(left_bbox["y1"], right_bbox["y1"])
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0

        intersection = (ix1 - ix0) * (iy1 - iy0)
        union = self._bbox_area(left_bbox) + self._bbox_area(right_bbox) - intersection
        if union <= 0.0:
            return 0.0
        return intersection / union

    def _tables_are_duplicate_candidates(
        self,
        left: TablePayload,
        right: TablePayload,
        *,
        left_labels: set[str],
        right_labels: set[str],
    ) -> tuple[bool, dict[str, Any]]:
        overlap = 0.0
        if left_labels and right_labels:
            overlap = len(left_labels & right_labels) / max(1, len(left_labels | right_labels))
        if overlap < self.table_dedupe_min_overlap:
            return False, {"overlap": round(overlap, 3), "reason": "label_overlap_below_threshold"}

        left_heading = self._table_dedupe_heading_key(left)
        right_heading = self._table_dedupe_heading_key(right)
        headings_match = bool(left_heading and right_heading and left_heading == right_heading)
        region_overlap = self._table_region_overlap_ratio(left, right)
        same_region = region_overlap >= 0.3

        is_duplicate = headings_match or same_region
        reason = "heading_match" if headings_match else ("region_overlap" if same_region else "distinct_region_or_heading")
        return is_duplicate, {
            "overlap": round(overlap, 3),
            "region_overlap": round(region_overlap, 3),
            "headings_match": headings_match,
            "reason": reason,
        }

    def _apply_schema_override(
        self,
        table: TablePayload,
        schema: Sequence[str],
        *,
        inferred_from: int | None = None,
    ) -> TablePayload:
        normalized_schema = [str(col or "").strip() or f"column_{idx+1}" for idx, col in enumerate(schema)]
        new_rows: list[TableRowPayload] = []
        for row in table.rows:
            row_meta = dict(row.metadata or {})
            if row_meta.get("row_type") == "header":
                row_meta["row_type"] = "data"
                row_meta["header_inferred"] = True
            new_cells: list[TableCellPayload] = []
            for cell in row.cells:
                col_key = normalized_schema[cell.column_index] if cell.column_index < len(normalized_schema) else f"column_{cell.column_index+1}"
                new_cells.append(
                    TableCellPayload(
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                        column_key=col_key,
                        raw_text=cell.raw_text,
                        normalized_value=cell.normalized_value,
                        bbox=cell.bbox,
                        confidence=cell.confidence,
                        metadata=cell.metadata,
                    )
                )
            new_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row_meta,
                    cells=new_cells,
                )
            )
        table_meta = dict(table.metadata or {})
        table_meta["header_inferred"] = True
        if inferred_from is not None:
            table_meta["header_inferred_from"] = inferred_from
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=normalized_schema,
            data_dictionary=table.data_dictionary,
            metadata=table_meta,
            rows=new_rows,
        )

    def _postprocess_tables(
        self,
        tables: Sequence[TablePayload],
    ) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        if not tables:
            return [], [], {}
        grouped: dict[int | None, list[TablePayload]] = {}
        for table in tables:
            grouped.setdefault(table.page_number, []).append(table)
        issues: list[IssuePayload] = []
        meta = {
            "deduped_tables": 0,
            "header_inferred": 0,
            "embedded_header_rows_promoted": 0,
            "merged_parent_label_cells_carried_down": 0,
            "row_continuation_stitched_pairs": 0,
            "value_fragment_stitched_cells": 0,
            "scope_refreshed_tables": 0,
        }
        processed: list[TablePayload] = []

        def _jaccard(a: set[str], b: set[str]) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / max(1, len(a | b))

        for page_number, page_tables in grouped.items():
            page_tables = sorted(page_tables, key=lambda t: t.order_index)
            promoted_tables: list[TablePayload] = []
            for table in page_tables:
                promoted_table, promoted_headers = self._promote_embedded_header_rows(table)
                if promoted_headers > 0:
                    meta["embedded_header_rows_promoted"] += promoted_headers
                    issues.append(
                        IssuePayload(
                            code="table_embedded_header_promoted",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Header-like table row promoted to schema/header metadata before indexing.",
                            page_number=page_number,
                            table_order_index=table.order_index,
                            details={
                                "promoted_rows": promoted_headers,
                                "source_row": (promoted_table.metadata or {}).get("embedded_header_source_row"),
                                "renamed_columns": (promoted_table.metadata or {}).get("embedded_header_renamed_columns"),
                            },
                        )
                    )
                promoted_tables.append(promoted_table)
            page_tables = promoted_tables

            carried_tables: list[TablePayload] = []
            for table in page_tables:
                carried_table, carried_cells = self._carry_down_merged_parent_labels(table)
                if carried_cells > 0:
                    meta["merged_parent_label_cells_carried_down"] += carried_cells
                    issues.append(
                        IssuePayload(
                            code="table_merged_parent_labels_carried_down",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Merged parent row labels were carried down into child rows.",
                            page_number=page_number,
                            table_order_index=table.order_index,
                            details={"carried_cells": carried_cells},
                        )
                    )
                carried_tables.append(carried_table)
            page_tables = carried_tables

            stitched_tables: list[TablePayload] = []
            for table in page_tables:
                stitched_table, stitched_pairs = self._stitch_table_row_continuations(table)
                if stitched_pairs > 0:
                    meta["row_continuation_stitched_pairs"] += stitched_pairs
                    issues.append(
                        IssuePayload(
                            code="table_row_continuation_stitched",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Descriptor continuation text stitched across adjacent rows.",
                            page_number=page_number,
                            table_order_index=table.order_index,
                            details={"stitched_pairs": stitched_pairs},
                        )
                    )
                stitched_tables.append(stitched_table)
            page_tables = stitched_tables

            value_stitched_tables: list[TablePayload] = []
            for table in page_tables:
                value_table, stitched_cells = self._stitch_scope_value_fragments(table)
                if stitched_cells > 0:
                    meta["value_fragment_stitched_cells"] += stitched_cells
                value_stitched_tables.append(value_table)
            page_tables = value_stitched_tables

            if self.table_dedupe_enabled:
                deduped: list[TablePayload] = []
                dedupe_labels: list[set[str]] = []
                dedupe_quality: list[float] = []
                for table in page_tables:
                    labels = self._table_row_label_set(table)
                    quality = float(self._assess_table_quality(table).get("quality_score") or 0.0)
                    merged = False
                    if labels:
                        for idx, existing in enumerate(deduped):
                            if len(existing.column_schema) != len(table.column_schema):
                                continue
                            is_duplicate, dedupe_diag = self._tables_are_duplicate_candidates(
                                existing,
                                table,
                                left_labels=dedupe_labels[idx],
                                right_labels=labels,
                            )
                            if is_duplicate:
                                meta["deduped_tables"] += 1
                                if quality > dedupe_quality[idx]:
                                    deduped[idx] = table
                                    dedupe_labels[idx] = labels
                                    dedupe_quality[idx] = quality
                                issues.append(
                                    IssuePayload(
                                        code="table_duplicate_suppressed",
                                        severity=KnowledgeIssueSeverity.INFO.value,
                                        description="Duplicate table suppressed based on row-label overlap.",
                                        page_number=page_number,
                                        table_order_index=table.order_index,
                                        details=dedupe_diag,
                                    )
                                )
                                merged = True
                                break
                    if not merged:
                        deduped.append(table)
                        dedupe_labels.append(labels)
                        dedupe_quality.append(quality)
                page_tables = deduped

            prev_schema: list[str] | None = None
            prev_labels: set[str] | None = None
            prev_order_index: int | None = None
            for table in page_tables:
                labels = self._table_row_label_set(table)
                is_generic = self._table_schema_is_generic(table)
                if (
                    self.table_header_propagation_enabled
                    and is_generic
                    and prev_schema
                    and labels
                    and prev_labels
                    and len(prev_schema) == len(table.column_schema)
                ):
                    overlap = _jaccard(labels, prev_labels)
                    if overlap >= self.table_header_propagation_min_overlap:
                        table = self._apply_schema_override(table, prev_schema, inferred_from=prev_order_index)
                        meta["header_inferred"] += 1
                        issues.append(
                            IssuePayload(
                                code="table_header_inferred",
                                severity=KnowledgeIssueSeverity.INFO.value,
                                description="Table headers inferred from adjacent table on same page.",
                                page_number=page_number,
                                table_order_index=table.order_index,
                                details={"overlap": round(overlap, 3), "source_table": prev_order_index},
                            )
                        )
                if not is_generic and labels:
                    prev_schema = list(table.column_schema)
                    prev_labels = labels
                    prev_order_index = table.order_index
                table = self._refresh_table_scope_annotations(table)
                meta["scope_refreshed_tables"] += 1
                processed.append(table)

        return processed, issues, meta
