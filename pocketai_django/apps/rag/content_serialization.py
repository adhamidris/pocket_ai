from __future__ import annotations

import logging
from typing import Any, Mapping

from django.db.models import Prefetch

from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
)


logger = logging.getLogger(__name__)


class ContentSerializationMixin:
    @staticmethod
    def _structured_exports(
        upload: KnowledgeUpload,
        *,
        max_tables: int = 3,
        max_pages: int | None = 50,
    ) -> dict[str, tuple[Mapping[str, object], ...]]:
        metadata = upload.ingestion_metadata or {}
        exports = metadata.get("structured_exports") if isinstance(metadata, dict) else None
        if not isinstance(exports, dict):
            return {"tables": tuple(), "issues": tuple(), "pages": tuple()}
        tables = exports.get("tables") or []
        issues = exports.get("issues") or []
        pages = exports.get("pages") or []

        def _normalize_list(source: Any, limit: int | None = None) -> tuple[Mapping[str, object], ...]:
            if not isinstance(source, list):
                return tuple()
            sliced = source if limit is None else source[:limit]
            normalized: list[Mapping[str, object]] = []
            for item in sliced:
                if isinstance(item, dict):
                    normalized.append(item)
            return tuple(normalized)

        return {
            "tables": _normalize_list(tables, max_tables),
            "issues": _normalize_list(issues, 10),
            "pages": _normalize_list(pages, max_pages),
        }

    @staticmethod
    def _ingestion_issue_summaries(upload: KnowledgeUpload) -> tuple[Mapping[str, object], ...]:
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        exports = metadata.get("structured_exports")
        if not isinstance(exports, dict):
            return tuple()
        issues = exports.get("issues")
        if not isinstance(issues, list):
            return tuple()
        normalized: list[Mapping[str, object]] = []
        for issue in issues:
            if isinstance(issue, dict):
                normalized.append(issue)
        return tuple(normalized)

    @staticmethod
    def _truncation_metrics(upload: KnowledgeUpload) -> dict[str, object]:
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        metrics: dict[str, object] = {}
        truncated_entities = metadata.get("truncated_entities")
        if isinstance(truncated_entities, int) and truncated_entities > 0:
            metrics["truncated_entities"] = truncated_entities
        table_trunc = metadata.get("table_truncation")
        if isinstance(table_trunc, dict):
            for key in ("truncated_tables", "truncated_rows", "truncated_columns"):
                value = table_trunc.get(key)
                if isinstance(value, int) and value > 0:
                    metrics[key] = value
        table_stats = metadata.get("table_stats")
        if isinstance(table_stats, dict):
            def _coerce_int(value: object) -> int:
                try:
                    return int(value) if value is not None else 0
                except (TypeError, ValueError):
                    return 0

            total_rows = _coerce_int(table_stats.get("total_rows"))
            indexed_rows = _coerce_int(table_stats.get("indexed_rows"))
            row_cap = _coerce_int(table_stats.get("row_cap"))
            source_rows = _coerce_int(table_stats.get("source_row_count"))
            partial_tables = _coerce_int(table_stats.get("partial_tables"))
            if total_rows:
                metrics["table_total_rows"] = total_rows
            if indexed_rows:
                metrics["table_indexed_rows"] = indexed_rows
            if row_cap:
                metrics["table_row_cap"] = row_cap
            if source_rows:
                metrics["table_source_rows"] = source_rows
            if partial_tables:
                metrics["table_partial_tables"] = partial_tables
            row_tier = table_stats.get("row_tier")
            if isinstance(row_tier, str) and row_tier:
                metrics["table_row_tier"] = row_tier
            if bool(table_stats.get("partial_index")) or (indexed_rows and total_rows and indexed_rows < total_rows):
                metrics["partial_index"] = True
        if metrics.get("truncated_rows") or metrics.get("table_partial_tables"):
            metrics["partial_index"] = True
        return metrics
    
    def _structured_counts(self, upload: KnowledgeUpload) -> tuple[int, int]:
        cache_key = (upload.business_profile_id, upload.id)
        cached = self._structured_count_cache.get(cache_key)
        if cached:
            self._structured_count_cache.move_to_end(cache_key)
            return cached
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        exports = metadata.get("structured_exports") if isinstance(metadata, dict) else None
        tables = issues = 0
        if isinstance(exports, dict):
            tables = len(exports.get("tables") or [])
            issues = len(exports.get("issues") or [])
        else:
            try:
                tables = upload.tables.count()
            except Exception:
                tables = 0
            try:
                issues = upload.issues.count()
            except Exception:
                issues = 0
        counts = (tables, issues)
        self._structured_count_cache[cache_key] = counts
        self._structured_count_cache.move_to_end(cache_key)
        if len(self._structured_count_cache) > self.structured_count_cache_limit:
            self._structured_count_cache.popitem(last=False)
        return counts

    def _serialize_structured_tables_with_rows(
        self,
        upload: KnowledgeUpload,
        *,
        max_tables: int = 3,
        max_rows: int = 5,
        max_columns: int = 8,
    ) -> list[dict[str, object]]:
        tables_manager = getattr(upload, "tables", None)
        if not hasattr(tables_manager, "order_by"):
            return []
        enriched: list[dict[str, object]] = []
        table_qs = tables_manager.order_by("order_index")
        for table in table_qs[:max_tables]:
            rows_manager = getattr(table, "rows", None)
            rows_sample: list[list[str]] = []
            rows_qs = rows_manager.order_by("row_index") if hasattr(rows_manager, "order_by") else None
            if rows_qs is not None:
                limited_rows = rows_qs[:max_rows] if max_rows else rows_qs
                for row in limited_rows:
                    cells_manager = getattr(row, "cells", None)
                    cells_qs = cells_manager.order_by("column_index") if hasattr(cells_manager, "order_by") else None
                    if cells_qs is None:
                        rows_sample.append([])
                        continue
                    limited_cells = cells_qs[:max_columns] if max_columns else cells_qs
                    rows_sample.append([cell.raw_text for cell in limited_cells])
            enriched.append(
                {
                    "order_index": table.order_index,
                    "title": table.title or f"Table {table.order_index}",
                    "section_heading": table.section_heading or "",
                    "page_number": table.page.page_number if table.page else None,
                    "column_schema": list(table.column_schema or []),
                    "row_count": rows_manager.count() if hasattr(rows_manager, "count") else 0,
                    "rowsSample": rows_sample,
                    "bbox": dict(table.bbox or {}),
                    "metadata": dict(table.metadata or {}),
                }
            )
        return enriched

    def _table_row_sample(
        self,
        chunk: KnowledgeUploadChunk,
        *,
        max_columns: int = 6,
        max_rows: int = 1,
        query: str | None = None,
    ) -> tuple[Mapping[str, object], ...]:
        chunk_metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        if not chunk_metadata.get("is_table_chunk"):
            return tuple()

        table_id = chunk_metadata.get("table_id")
        table = None
        if table_id:
            try:
                table = KnowledgeUploadTable.objects.filter(id=table_id).first()
            except Exception:
                table = None
        if table is None:
            upload = chunk.upload
            tables_manager = getattr(upload, "tables", None)
            if not hasattr(tables_manager, "order_by"):
                return tuple()
            try:
                table = tables_manager.order_by("order_index").first()
            except Exception:
                return tuple()
        if table is None:
            return tuple()

        target_row_index = chunk_metadata.get("table_row_index")
        if isinstance(target_row_index, str) and target_row_index.isdigit():
            target_row_index = int(target_row_index)
        if not isinstance(target_row_index, int):
            target_row_index = None

        max_rows = max(1, int(max_rows))
        max_columns = max(2, int(max_columns))
        row_limit = max(1, min(50, max_rows * 25))

        try:
            row_qs = (
                table.rows.filter(row_index__isnull=False)
                .exclude(metadata__row_type="header")
                .order_by("row_index")
                .prefetch_related(
                    Prefetch(
                        "cells",
                        queryset=KnowledgeUploadTableCell.objects.order_by("column_index"),
                    )
                )
            )
            if target_row_index is not None:
                row_qs = row_qs.filter(row_index=target_row_index)
                selected_rows = list(row_qs[:max_rows])
            else:
                rows = list(row_qs[:row_limit])
                if not rows:
                    return tuple()
                if not query or len(rows) <= 1:
                    selected_rows = rows[:max_rows]
                else:
                    query_tokens = {tok for tok in (query or "").lower().split() if tok and len(tok) >= 3}
                    scored: list[tuple[int, int]] = []
                    for idx, row in enumerate(rows):
                        score = 0
                        for cell in row.cells.all():
                            cell_text = str(cell.raw_text or "").lower()
                            for tok in query_tokens:
                                if tok in cell_text:
                                    score += 1
                        scored.append((score, idx))
                    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
                    picked = [rows[idx] for score, idx in scored[:max_rows] if score > 0]
                    selected_rows = picked if picked else rows[:max_rows]

            if not selected_rows:
                return tuple()

            all_cells = list(selected_rows[0].cells.all()) if selected_rows else []
            ordered_columns = [
                (cell.column_index, (cell.column_key or f"column_{(cell.column_index or 0) + 1}"))
                for cell in all_cells
            ]
            ordered_columns.sort(key=lambda item: item[0])
            column_labels = [label for _, label in ordered_columns]
            if len(column_labels) > max_columns:
                column_labels = column_labels[:max_columns]

            rows_payload: list[list[str]] = []
            for row in selected_rows:
                cell_lookup = {cell.column_index: (cell.raw_text or "") for cell in row.cells.all()}
                values: list[str] = []
                for col_idx, _label in ordered_columns[: len(column_labels)]:
                    values.append(str(cell_lookup.get(col_idx, "")))
                rows_payload.append(values)

            structured_table = {
                "title": table.title or table.section_heading or f"Table {table.order_index}",
                "columns": column_labels,
                "rows": rows_payload,
                "metadata": {
                    "table_id": str(table.id),
                    "table_order_index": table.order_index,
                    "page_number": table.page.page_number if table.page else None,
                    "row_indexes": [row.row_index for row in selected_rows],
                    "chunk_role": chunk_metadata.get("table_chunk_role"),
                },
            }
            table_meta = table.metadata if isinstance(getattr(table, "metadata", None), Mapping) else {}
            if table_meta:
                quality_score = table_meta.get("quality_score")
                if isinstance(quality_score, (int, float)):
                    structured_table["metadata"]["quality_score"] = float(quality_score)
                is_decorative = table_meta.get("is_decorative")
                if isinstance(is_decorative, bool):
                    structured_table["metadata"]["is_decorative"] = is_decorative
                signals = table_meta.get("quality_signals")
                if isinstance(signals, Mapping) and signals.get("column_misalignment") is True:
                    structured_table["metadata"]["column_misalignment"] = True
            return (structured_table,)
        except Exception:
            return tuple()

    @staticmethod
    def _render_structured_tables_text(upload: KnowledgeUpload, *, max_preview_rows: int = 5) -> str:
        tables_manager = getattr(upload, "tables", None)
        if not hasattr(tables_manager, "all"):
            return ""
        tables = list(tables_manager.all())
        if not tables:
            return ""
        lines = ["[Structured Tables]"]
        for table in tables:
            page_number = table.page.page_number if table.page else None
            title = table.title or f"Table {table.order_index}"
            header = ", ".join((table.column_schema or [])[:10])
            lines.append(f"- {title} (page {page_number or 'n/a'}) columns: {header or 'unspecified'}")
            rows = list(table.rows.all()) if hasattr(table, "rows") else []
            preview_rows = rows[:max_preview_rows]
            for row in preview_rows:
                cells = list(row.cells.all()) if hasattr(row, "cells") else []
                cell_values = [cell.raw_text for cell in sorted(cells, key=lambda cell: cell.column_index)]
                if cell_values:
                    lines.append(f"    • {' | '.join(cell_values)}")
            if len(rows) > max_preview_rows:
                lines.append(f"    • … ({len(rows) - max_preview_rows} more rows)")
        return "\n".join(lines)

    @staticmethod
    def _render_issue_text(upload: KnowledgeUpload, *, max_issues: int = 5) -> str:
        issues_manager = getattr(upload, "issues", None)
        if not hasattr(issues_manager, "all"):
            return ""
        issues = list(issues_manager.all())[:max_issues]
        if not issues:
            return ""
        lines = ["[Ingestion Issues]"]
        for issue in issues:
            location = []
            if issue.page:
                location.append(f"page {issue.page.page_number}")
            if issue.table:
                location.append(f"table {issue.table.order_index}")
            if issue.table_row:
                location.append(f"row {issue.table_row.row_index}")
            if issue.table_cell:
                location.append(f"cell {issue.table_cell.column_index}")
            location_str = " • ".join(location)
            lines.append(f"- {issue.severity.upper()} {issue.issue_code}: {issue.description} ({location_str or 'no location'})")
        return "\n".join(lines)

    @staticmethod
    def _public_label(upload: KnowledgeUpload) -> str:
        metadata = upload.metadata or {}
        if isinstance(metadata, dict):
            integration_resource = metadata.get("integration_resource") if isinstance(metadata.get("integration_resource"), Mapping) else None
            if isinstance(integration_resource, Mapping):
                sheet_label = integration_resource.get("sheet_label")
                if isinstance(sheet_label, str) and sheet_label.strip():
                    return sheet_label.strip()
            for key in ("public_label", "customer_label", "display_label"):
                label = metadata.get(key)
                if isinstance(label, str) and label.strip():
                    return label.strip()
        return (upload.display_name or upload.source_name or upload.external_reference or "Knowledge Resource").strip()

    @staticmethod
    def _summarize_upload(upload: KnowledgeUpload) -> str:
        summary = (upload.summary or upload.description or "No summary available.").strip()
        return summary[:280]

    @staticmethod
    def _summarize_chunk(chunk: KnowledgeUploadChunk) -> str:
        text = (chunk.content or "").strip()
        if not text:
            return "No summary available."
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return "No summary available."

        first_line = lines[0]
        if len(first_line) >= 40 or len(lines) == 1:
            return (first_line or text)[:280]

        parts: list[str] = []
        for line in lines[:10]:
            if not line:
                continue
            candidate = "\n".join([*parts, line]) if parts else line
            if len(candidate) > 280:
                break
            parts.append(line)
            if len(parts) >= 6 and any(char.isdigit() for char in candidate):
                break
        snippet = "\n".join(parts) if parts else (first_line or text)
        return snippet[:280]

    @staticmethod
    def _extract_content(upload: KnowledgeUpload) -> str:
        if upload.text_detail and upload.text_detail.content:
            return upload.text_detail.content
        if upload.description:
            return upload.description
        if upload.summary:
            return upload.summary
        return ""

    @classmethod
    def _trim_content(cls, content: str, *, max_chars: int) -> str:
        text, _ = cls._trim_with_flag(content, max_chars=max_chars)
        return text

    @staticmethod
    def _trim_with_flag(content: str, *, max_chars: int) -> tuple[str, bool]:
        text = (content or "").strip()
        if not text or max_chars <= 0:
            return text, False
        if len(text) <= max_chars:
            return text, False
        logger.info("Trimming knowledge content from %s to %s chars", len(text), max_chars)
        trimmed = text[:max_chars].rstrip()
        return f"{trimmed}\n\n[Content truncated]", True
