from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion_azure_di import AzureDocumentIntelligenceExtractor
from apps.knowledge.ingestion_contracts import (
    IssuePayload,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
from apps.knowledge.ingestion_table_detection import TableDetector
from apps.knowledge.table_scope_engine import SCOPE_REASON_ABSTAIN, canonical_scope_reason


logger = logging.getLogger(__name__)

try:  # pragma: no cover - dependency failure should be surfaced at runtime
    import fitz  # type: ignore[attr-defined]  # PyMuPDF
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore


class IngestionTableVlmRepairMixin:


    @staticmethod
    def _table_data_rows(table: TablePayload) -> list[TableRowPayload]:
        return [
            row
            for row in (table.rows or [])
            if str((row.metadata or {}).get("row_type") or "").strip().lower()
            not in {"header", "section_header"}
        ]

    def _table_column_count(self, table: TablePayload) -> int:
        schema_count = len(table.column_schema or [])
        row_count = max((len(row.cells or []) for row in (table.rows or [])), default=0)
        return max(schema_count, row_count)

    @staticmethod
    def _table_non_empty_cell_count(table: TablePayload) -> int:
        count = 0
        for row in table.rows or []:
            if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header":
                continue
            for cell in row.cells or []:
                if str(cell.raw_text or "").strip():
                    count += 1
        return count

    def _table_row_labels(self, table: TablePayload, *, limit: int = 500) -> list[str]:
        labels: list[str] = []
        for row in self._table_data_rows(table):
            value = ""
            for cell in row.cells or []:
                if int(getattr(cell, "column_index", -1)) == 0:
                    value = self._normalize_evidence_phrase(str(cell.raw_text or ""))
                    break
            if not value:
                value = self._normalize_evidence_phrase(str(row.raw_text or ""))
            if value:
                labels.append(value)
            if limit and len(labels) >= limit:
                break
        return labels

    @staticmethod
    def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
        if not left or not right:
            return 0
        previous = [0] * (len(right) + 1)
        current = [0] * (len(right) + 1)
        for token_left in left:
            for idx, token_right in enumerate(right, start=1):
                if token_left == token_right:
                    current[idx] = previous[idx - 1] + 1
                else:
                    current[idx] = max(previous[idx], current[idx - 1])
            previous, current = current, [0] * (len(right) + 1)
        return previous[-1]

    @classmethod
    def _lcs_ratio(cls, left: Sequence[str], right: Sequence[str]) -> float:
        if not left and not right:
            return 1.0
        if not left or not right:
            return 0.0
        lcs = cls._lcs_length(left, right)
        return float(lcs) / float(max(len(left), len(right), 1))

    def _table_with_scope_annotations_for_guardrails(self, table: TablePayload) -> TablePayload:
        if not table.rows or len(table.column_schema or []) < 3:
            return table
        data_rows = self._table_data_rows(table)
        if not data_rows:
            return table

        has_scope_meta = True
        for row in data_rows:
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            inferred_scope = row_meta.get("inferred_scope_columns")
            scope_reason = row_meta.get("scope_reason")
            if inferred_scope is None or scope_reason is None:
                has_scope_meta = False
                break
        if has_scope_meta:
            return table

        header_rows: set[int] = {
            int(row.row_index)
            for row in (table.rows or [])
            if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"
        }
        annotator = AzureDocumentIntelligenceExtractor(endpoint=None, key=None)
        annotated_rows = annotator._annotate_row_applicability(
            table_rows=table.rows or [],
            column_schema=table.column_schema or [],
            header_rows=header_rows,
        )
        meta = dict(table.metadata or {})
        meta["scope_guardrail_annotated"] = True
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=table.column_schema,
            data_dictionary=dict(table.data_dictionary or {}),
            metadata=meta,
            rows=annotated_rows,
        )

    def _table_scope_snapshot(self, table: TablePayload) -> dict[str, int]:
        rows_with_scope = 0
        rows_with_non_abstain = 0
        rows_with_confidence = 0
        scope_axis_violations = 0
        for row in self._table_data_rows(table):
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            inferred_scope = self._clean_scope_labels(row_meta.get("inferred_scope_columns"))
            scope_dimensions = self._clean_scope_labels(row_meta.get("scope_dimension_columns"))
            if inferred_scope:
                rows_with_scope += 1
            reason = canonical_scope_reason(row_meta.get("scope_reason"))
            if inferred_scope and reason != SCOPE_REASON_ABSTAIN:
                rows_with_non_abstain += 1
            confidence = self._coerce_scope_confidence(row_meta.get("scope_confidence"))
            if inferred_scope and confidence is not None:
                rows_with_confidence += 1
            if inferred_scope and scope_dimensions:
                scope_set = set(scope_dimensions)
                if any(label not in scope_set for label in inferred_scope):
                    scope_axis_violations += 1
        return {
            "rows_with_scope": rows_with_scope,
            "rows_with_non_abstain": rows_with_non_abstain,
            "rows_with_confidence": rows_with_confidence,
            "scope_axis_violations": scope_axis_violations,
        }

    def _table_guardrail_snapshot(self, table: TablePayload) -> dict[str, Any]:
        data_rows = self._table_data_rows(table)
        labels = self._table_row_labels(table)
        scope = self._table_scope_snapshot(table)
        return {
            "data_row_count": len(data_rows),
            "column_count": self._table_column_count(table),
            "non_empty_cell_count": self._table_non_empty_cell_count(table),
            "row_labels": labels,
            **scope,
        }

    def _evaluate_vlm_guardrails(
        self,
        *,
        baseline: TablePayload,
        candidate: TablePayload,
    ) -> tuple[bool, dict[str, Any], TablePayload]:
        baseline_scoped = self._table_with_scope_annotations_for_guardrails(baseline)
        candidate_scoped = self._table_with_scope_annotations_for_guardrails(candidate)

        baseline_snapshot = self._table_guardrail_snapshot(baseline_scoped)
        candidate_snapshot = self._table_guardrail_snapshot(candidate_scoped)

        baseline_rows = int(baseline_snapshot.get("data_row_count") or 0)
        candidate_rows = int(candidate_snapshot.get("data_row_count") or 0)
        row_recall = float(candidate_rows) / float(max(1, baseline_rows))

        baseline_labels = list(baseline_snapshot.get("row_labels") or [])
        candidate_labels = list(candidate_snapshot.get("row_labels") or [])
        row_order_ratio = self._lcs_ratio(baseline_labels, candidate_labels)

        baseline_columns = int(baseline_snapshot.get("column_count") or 0)
        candidate_columns = int(candidate_snapshot.get("column_count") or 0)
        schema_recall = float(candidate_columns) / float(max(1, baseline_columns))

        baseline_cells = int(baseline_snapshot.get("non_empty_cell_count") or 0)
        candidate_cells = int(candidate_snapshot.get("non_empty_cell_count") or 0)
        cell_recall = float(candidate_cells) / float(max(1, baseline_cells))

        baseline_scope_rows = int(baseline_snapshot.get("rows_with_scope") or 0)
        candidate_scope_rows = int(candidate_snapshot.get("rows_with_scope") or 0)
        scope_row_recall = (
            float(candidate_scope_rows) / float(max(1, baseline_scope_rows))
            if baseline_scope_rows > 0
            else 1.0
        )

        baseline_scope_non_abstain = int(baseline_snapshot.get("rows_with_non_abstain") or 0)
        candidate_scope_non_abstain = int(candidate_snapshot.get("rows_with_non_abstain") or 0)
        scope_non_abstain_recall = (
            float(candidate_scope_non_abstain) / float(max(1, baseline_scope_non_abstain))
            if baseline_scope_non_abstain > 0
            else 1.0
        )

        baseline_axis_violations = int(baseline_snapshot.get("scope_axis_violations") or 0)
        candidate_axis_violations = int(candidate_snapshot.get("scope_axis_violations") or 0)

        reasons: list[str] = []
        soft_signals: list[str] = []
        if self.table_vlm_guardrails_enabled:
            hard_row_floor = max(
                0.0,
                min(1.0, float(self.table_vlm_guardrail_hard_row_recall_floor)),
            )
            row_merge_normalization = bool(
                row_recall < self.table_vlm_guardrail_min_row_recall
                and cell_recall >= max(self.table_vlm_guardrail_min_cell_recall, 1.08)
                and schema_recall >= self.table_vlm_guardrail_min_schema_recall
            )
            if row_recall < self.table_vlm_guardrail_min_row_recall:
                if row_recall < hard_row_floor:
                    reasons.append("row_coverage_regression")
                elif row_merge_normalization:
                    soft_signals.append("row_count_normalization")
                else:
                    reasons.append("row_coverage_regression")
            if baseline_rows >= 3 and baseline_labels and candidate_labels:
                if row_order_ratio < self.table_vlm_guardrail_min_order_ratio:
                    if row_recall >= self.table_vlm_guardrail_min_row_recall:
                        reasons.append("row_order_regression")
                    elif row_merge_normalization:
                        soft_signals.append("row_order_shift_with_row_merge")
                    else:
                        reasons.append("row_order_regression")
            if schema_recall < self.table_vlm_guardrail_min_schema_recall:
                reasons.append("schema_coverage_regression")
            if cell_recall < self.table_vlm_guardrail_min_cell_recall:
                reasons.append("value_coverage_regression")
            if baseline_scope_rows > 0 and scope_row_recall < 1.0:
                if row_merge_normalization:
                    soft_signals.append("scope_row_delta_with_row_merge")
                else:
                    reasons.append("scope_row_regression")
            if baseline_scope_non_abstain > 0 and scope_non_abstain_recall < 1.0:
                if row_merge_normalization:
                    soft_signals.append("scope_quality_delta_with_row_merge")
                else:
                    reasons.append("scope_quality_regression")
            if candidate_axis_violations > baseline_axis_violations:
                reasons.append("scope_axis_violation_increase")
        else:
            row_merge_normalization = False

        diagnostics = {
            "accepted": not reasons,
            "rejection_reasons": reasons,
            "soft_signals": soft_signals,
            "thresholds": {
                "row_recall": self.table_vlm_guardrail_min_row_recall,
                "hard_row_recall_floor": self.table_vlm_guardrail_hard_row_recall_floor,
                "row_order_ratio": self.table_vlm_guardrail_min_order_ratio,
                "schema_recall": self.table_vlm_guardrail_min_schema_recall,
                "cell_recall": self.table_vlm_guardrail_min_cell_recall,
            },
            "metrics": {
                "row_recall": round(row_recall, 4),
                "row_order_ratio": round(row_order_ratio, 4),
                "schema_recall": round(schema_recall, 4),
                "cell_recall": round(cell_recall, 4),
                "scope_row_recall": round(scope_row_recall, 4),
                "scope_non_abstain_recall": round(scope_non_abstain_recall, 4),
                "row_merge_normalization": row_merge_normalization,
                "baseline_scope_axis_violations": baseline_axis_violations,
                "candidate_scope_axis_violations": candidate_axis_violations,
            },
            "baseline_snapshot": baseline_snapshot,
            "candidate_snapshot": candidate_snapshot,
        }
        return (not reasons), diagnostics, candidate_scoped

    def _repair_tables_with_vlm(
        self,
        path: Path,
        tables: list[TablePayload],
    ) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        if not self.table_vlm_enabled or not tables:
            return tables, [], {}
        if fitz is None:
            return tables, [
                IssuePayload(
                    code="table_vlm_no_renderer",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="VLM repair skipped because PyMuPDF is unavailable.",
                )
            ], {}

        candidates: list[tuple[int, float, bool]] = []
        for idx, table in enumerate(tables):
            structure_conf = self._get_table_structure_confidence(table)
            
            # Check for column misalignment (empty header cells with non-empty data)
            has_column_misalignment = False
            header_cells: list[str] = []
            for row in (table.rows or []):
                if (row.metadata or {}).get("row_type") == "header":
                    header_cells = [str(cell.raw_text or "") for cell in (row.cells or [])]
                    break
            
            if header_cells:
                data_rows = [r for r in (table.rows or []) if (r.metadata or {}).get("row_type") != "header"][:5]
                for col_idx, header_val in enumerate(header_cells):
                    if not str(header_val or "").strip():  # Empty header
                        for row in data_rows:
                            for cell in (row.cells or []):
                                if cell.column_index == col_idx:
                                    cell_text = str(cell.raw_text or "").strip()
                                    if cell_text and len(cell_text) > 2:
                                        has_column_misalignment = True
                                        break
                            if has_column_misalignment:
                                break
                    if has_column_misalignment:
                        break
            
            # Trigger VLM repair if confidence is low OR column misalignment detected
            if isinstance(structure_conf, (int, float)) and structure_conf >= self.table_vlm_confidence_threshold:
                if not has_column_misalignment:
                    continue
                # Log that we're triggering repair due to column misalignment
                logger.info(
                    "table.vlm.triggered_by_misalignment table=%s conf=%s",
                    table.order_index,
                    structure_conf,
                )
            
            # Only require page_number - we'll fallback to full page if bbox is missing
            if not table.page_number:
                continue
            candidates.append(
                (
                    idx,
                    float(structure_conf) if isinstance(structure_conf, (int, float)) else 0.0,
                    has_column_misalignment,
                )
            )

        if not candidates:
            return tables, [], {
                "attempted": 0,
                "repaired": 0,
                "rejected": 0,
                "skipped": len(tables),
                "model": self.table_vlm_model,
                "guardrails_enabled": self.table_vlm_guardrails_enabled,
            }

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return tables, [
                IssuePayload(
                    code="table_vlm_missing_key",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="OPENAI_API_KEY not configured; skipping VLM repair.",
                )
            ], {}

        try:
            from openai import OpenAI
        except Exception as exc:
            return tables, [
                IssuePayload(
                    code="table_vlm_missing_client",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description=f"OpenAI client unavailable: {exc}",
                )
            ], {}

        client = OpenAI(api_key=api_key)
        repaired: list[TablePayload] = list(tables)
        issues: list[IssuePayload] = []
        meta: dict[str, Any] = {
            "attempted": 0,
            "repaired": 0,
            "rejected": 0,
            "model": self.table_vlm_model,
            "guardrails_enabled": self.table_vlm_guardrails_enabled,
            "guardrail_version": "v2",
        }
        remaining_budget = max(0, self.table_vlm_max_repairs)

        def _repair_sort_key(item: tuple[int, float, bool]) -> tuple[int, float, int, int]:
            index, conf, misaligned = item
            table = tables[index]
            data_rows = len(
                [row for row in (table.rows or []) if (row.metadata or {}).get("row_type") != "header"]
            )
            columns = max(len(table.column_schema or []), max((len(r.cells or []) for r in (table.rows or [])), default=0))
            # Prioritize misaligned tables first (they cause wrong value attribution),
            # then prioritize by low confidence, then larger tables.
            return (0 if misaligned else 1, conf, -data_rows, -columns)

        def _table_hint(table: TablePayload) -> str:
            parts: list[str] = []
            if table.title:
                parts.append(f"Title: {table.title}")
            if table.section_heading:
                parts.append(f"Section: {table.section_heading}")
            # Use non-empty column headers as the primary anchor for full-page extraction.
            schema = [str(col or '').strip() for col in (table.column_schema or []) if str(col or '').strip()]
            if schema:
                parts.append("Columns: " + " | ".join(schema[:10]))
            # Add a few row-label anchors (first column of early rows).
            labels: list[str] = []
            for row in (table.rows or []):
                if (row.metadata or {}).get("row_type") == "header":
                    continue
                first_cell = None
                for cell in (row.cells or []):
                    if cell.column_index == 0:
                        first_cell = cell
                        break
                raw = str(getattr(first_cell, "raw_text", "") or "").strip() if first_cell else str(row.raw_text or "").strip()
                if raw:
                    labels.append(raw)
                if len(labels) >= 5:
                    break
            if labels:
                parts.append("Row labels (examples): " + " | ".join(labels))
            parts.append(f"Order index: {table.order_index} (page {table.page_number})")
            return "\n".join(parts).strip()

        for idx, conf, _misaligned in sorted(candidates, key=_repair_sort_key):
            if remaining_budget <= 0:
                break
            table = tables[idx]
            repair_reason = "misalignment" if _misaligned else "low_confidence"

            # Try to crop table region, fallback to full page if bbox is missing
            crop_bytes = self._render_table_crop(path, int(table.page_number), table.bbox)
            render_mode = "crop"
            if not crop_bytes:
                # Fallback: render full page when bbox is missing/invalid
                crop_bytes = self._render_full_page(path, int(table.page_number))
                render_mode = "full_page"
                if crop_bytes:
                    logger.info(
                        "table.vlm.fallback_full_page table=%s page=%s reason=bbox_missing",
                        table.order_index,
                        table.page_number,
                    )
            if not crop_bytes:
                continue

            meta["attempted"] += 1
            remaining_budget -= 1
            # Use a strong hint for full-page extraction (title + columns + row labels).
            table_hint = _table_hint(table)
            attempted_modes: list[str] = [render_mode]
            payload = self._run_vlm_table_repair(
                client, crop_bytes, render_mode=render_mode, table_hint=table_hint
            )
            if not payload and render_mode == "crop":
                retry_bytes = self._render_full_page(path, int(table.page_number))
                if retry_bytes:
                    attempted_modes.append("full_page")
                    logger.info(
                        "table.vlm.retry_full_page_after_crop_failure table=%s page=%s",
                        table.order_index,
                        table.page_number,
                    )
                    payload = self._run_vlm_table_repair(
                        client,
                        retry_bytes,
                        render_mode="full_page",
                        table_hint=table_hint,
                    )
                    if payload:
                        render_mode = "full_page_retry"
            if not payload:
                issues.append(
                    IssuePayload(
                        code="table_vlm_failed",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description=f"VLM repair failed for table {table.order_index}.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={
                            "structure_confidence": conf,
                            "render_mode": render_mode,
                            "attempted_modes": attempted_modes,
                        },
                    )
                )
                continue

            vlm_table = self._table_payload_from_vlm(
                payload=payload,
                order_index=table.order_index,
                page_number=int(table.page_number),
                bbox=table.bbox,
                title=table.title,
                section_heading=table.section_heading,
                source_metadata=table.metadata,
            )
            if vlm_table:
                accepted, diagnostics, guarded_table = self._evaluate_vlm_guardrails(
                    baseline=table,
                    candidate=vlm_table,
                )
                diagnostics_record = {
                    "order_index": table.order_index,
                    "page_number": table.page_number,
                    "reason": repair_reason,
                    "render_mode": render_mode,
                    **diagnostics,
                }
                meta.setdefault("guardrail_diagnostics", []).append(diagnostics_record)

                # Crop extracts can be partial on dense PDFs. If the first candidate is rejected,
                # run a single full-page retry before final rejection.
                #
                # However, not every guardrail rejection is likely to be fixed by switching to
                # a full-page render. Only retry when the rejection indicates missing coverage
                # (rows/schema/values/scope), or when this repair was triggered by misalignment.
                if not accepted and render_mode == "crop":
                    retry_reasons = set(diagnostics.get("rejection_reasons") or [])
                    coverage_retry_reasons = {
                        "row_coverage_regression",
                        "schema_coverage_regression",
                        "value_coverage_regression",
                        "scope_row_regression",
                        "scope_quality_regression",
                    }
                    should_retry_full_page = bool(retry_reasons & coverage_retry_reasons) or (
                        repair_reason == "misalignment"
                    )
                    if not should_retry_full_page:
                        logger.info(
                            "table.vlm.skip_full_page_retry_after_guardrail_rejection table=%s page=%s reasons=%s",
                            table.order_index,
                            table.page_number,
                            ",".join(sorted(retry_reasons)),
                        )
                        # Fall through to rejection handling below.
                    else:
                        retry_bytes = self._render_full_page(path, int(table.page_number))
                        if retry_bytes:
                            attempted_modes.append("full_page")
                            logger.info(
                                "table.vlm.retry_full_page_after_guardrail_rejection table=%s page=%s reasons=%s",
                                table.order_index,
                                table.page_number,
                                ",".join(diagnostics.get("rejection_reasons") or []),
                            )
                            retry_payload = self._run_vlm_table_repair(
                                client,
                                retry_bytes,
                                render_mode="full_page",
                                table_hint=table_hint,
                            )
                            if retry_payload:
                                retry_table = self._table_payload_from_vlm(
                                    payload=retry_payload,
                                    order_index=table.order_index,
                                    page_number=int(table.page_number),
                                    bbox=table.bbox,
                                    title=table.title,
                                    section_heading=table.section_heading,
                                    source_metadata=table.metadata,
                                )
                                if retry_table:
                                    retry_accepted, retry_diagnostics, retry_guarded_table = (
                                        self._evaluate_vlm_guardrails(
                                            baseline=table,
                                            candidate=retry_table,
                                        )
                                    )
                                    retry_record = {
                                        "order_index": table.order_index,
                                        "page_number": table.page_number,
                                        "reason": repair_reason,
                                        "render_mode": "full_page_retry",
                                        **retry_diagnostics,
                                    }
                                    meta.setdefault("guardrail_diagnostics", []).append(retry_record)
                                    if retry_accepted:
                                        accepted = True
                                        diagnostics = retry_diagnostics
                                        guarded_table = retry_guarded_table
                                        render_mode = "full_page_retry"
                                    else:
                                        diagnostics = retry_diagnostics
                                        render_mode = "full_page_retry"

                if not accepted:
                    meta["rejected"] += 1
                    meta.setdefault("rejected_tables", []).append(
                        {
                            "order_index": table.order_index,
                            "page_number": table.page_number,
                            "reason": repair_reason,
                            "render_mode": render_mode,
                            "rejection_reasons": diagnostics.get("rejection_reasons") or [],
                        }
                    )
                    logger.info(
                        "table.vlm.rejected table=%s page=%s reasons=%s",
                        table.order_index,
                        table.page_number,
                        ",".join(diagnostics.get("rejection_reasons") or []),
                    )
                    issues.append(
                        IssuePayload(
                            code="table_vlm_rejected_regression",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description=f"VLM candidate rejected by guardrails for table {table.order_index}.",
                            page_number=table.page_number,
                            table_order_index=table.order_index,
                            details={
                                "structure_confidence": conf,
                                "repair_reason": repair_reason,
                                "render_mode": render_mode,
                                "attempted_modes": attempted_modes,
                                "rejection_reasons": diagnostics.get("rejection_reasons") or [],
                                "metrics": diagnostics.get("metrics") or {},
                            },
                        )
                    )
                    continue

                meta["repaired"] += 1
                meta.setdefault("repaired_tables", []).append(
                    {
                        "order_index": table.order_index,
                        "page_number": table.page_number,
                        "reason": repair_reason,
                        "render_mode": render_mode,
                    }
                )
                logger.info(
                    "table.vlm.repaired table=%s page=%s reason=%s render=%s conf=%s model=%s",
                    table.order_index,
                    table.page_number,
                    repair_reason,
                    render_mode,
                    conf,
                    self.table_vlm_model,
                )
                repaired[idx] = guarded_table

        return repaired, issues, meta

    @staticmethod
    def _render_table_crop(path: Path, page_number: int, bbox: dict[str, float]) -> bytes | None:
        if fitz is None:
            return None
        doc = None
        try:
            x0 = float(bbox.get("x0", 0.0))
            y0 = float(bbox.get("y0", 0.0))
            x1 = float(bbox.get("x1", 0.0))
            y1 = float(bbox.get("y1", 0.0))
            if x1 <= x0 or y1 <= y0:
                return None

            doc = fitz.open(path)
            page = doc[page_number - 1]
            rect = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(clip=rect, dpi=200)
            return pix.tobytes("png")
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    @staticmethod
    def _render_full_page(path: Path, page_number: int) -> bytes | None:
        """Render entire PDF page as PNG for VLM repair when bbox is unavailable."""
        if fitz is None:
            return None
        doc = None
        try:
            doc = fitz.open(path)
            if page_number < 1 or page_number > len(doc):
                return None
            page = doc[page_number - 1]
            # Use lower DPI for full page to keep token cost reasonable
            pix = page.get_pixmap(dpi=150)
            return pix.tobytes("png")
        except Exception:
            return None
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass

    def _run_vlm_table_repair(
        self,
        client: Any,
        image_bytes: bytes,
        render_mode: str = "crop",
        table_hint: str | None = None,
    ) -> dict[str, Any] | None:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        
        _merged_cell_instruction = (
            "IMPORTANT: If a cell value is visually centered across multiple columns "
            "(i.e. it spans or merges several column positions), you MUST repeat that "
            "same value in EVERY column it applies to.  Do NOT leave the other columns "
            "empty — duplicate the value so each spanned column contains it."
        )

        if render_mode == "full_page" and table_hint:
            prompt = (
                "Extract the table from this page image.\n"
                "Select the table that best matches the hint below (it includes expected columns/row labels).\n"
                "HINT:\n"
                f"{table_hint}\n\n"
                "Return strict JSON with keys: columns (array of column header strings) and rows "
                "(array of arrays with cell values). Rows should contain only data rows (no header row). "
                "Make sure to capture ALL columns and ALL values correctly.\n\n"
                f"{_merged_cell_instruction}"
            )
            max_tokens = 4000  # Full page may have more data
        else:
            prompt = (
                "Extract the table from this image. "
                "Return strict JSON with keys: columns (array of strings) and rows "
                "(array of arrays). Rows should contain only data rows (no header row).\n\n"
                f"{_merged_cell_instruction}"
            )
            max_tokens = 1200
        
        try:
            response = client.chat.completions.create(
                model=self.table_vlm_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{encoded}"},
                            },
                        ],
                    }
                ],
                max_tokens=max_tokens,
            )
        except Exception as exc:
            logger.warning("table.vlm.repair_failed error=%s", exc)
            return None

        content = ""
        try:
            content = response.choices[0].message.content or ""
        except Exception:
            content = ""
        if not content:
            return None
        return self._parse_vlm_table_json(content)

    @staticmethod
    def _parse_vlm_table_json(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        if "columns" not in payload or "rows" not in payload:
            return None
        return payload

    def _table_payload_from_vlm(
        self,
        *,
        payload: Mapping[str, Any],
        order_index: int,
        page_number: int,
        bbox: dict[str, float],
        title: str,
        section_heading: str,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> TablePayload | None:
        columns = payload.get("columns") or []
        rows = payload.get("rows") or []
        if not isinstance(columns, list) or not isinstance(rows, list):
            return None

        column_schema: list[str] = []
        for idx, col in enumerate(columns):
            column_schema.append(TableDetector._normalize_header_cell(str(col), idx))
        if not column_schema:
            max_cols = max((len(r) for r in rows if isinstance(r, list)), default=0)
            column_schema = [f"column_{i+1}" for i in range(max_cols)]

        table_rows: list[TableRowPayload] = []
        header_cells: list[TableCellPayload] = []
        for col_idx, label in enumerate(columns):
            header_cells.append(
                TableCellPayload(
                    row_index=0,
                    column_index=col_idx,
                    column_key=column_schema[col_idx],
                    raw_text=str(label),
                    normalized_value=TableDetector._normalize_cell_value(str(label)),
                    bbox=bbox,
                    confidence=None,
                )
            )
        if header_cells:
            table_rows.append(
                TableRowPayload(
                    row_index=0,
                    page_number=page_number,
                    bbox=bbox,
                    raw_text=" | ".join(str(c.raw_text) for c in header_cells),
                    metadata={"row_type": "header"},
                    cells=header_cells,
                )
            )

        for row_idx, row in enumerate(rows, start=1):
            if not isinstance(row, list):
                continue
            # Detect runs of identical non-empty adjacent values — these
            # indicate the VLM correctly duplicated a spanning/merged value.
            # We tag each cell in such a run with the true column_span so
            # downstream applicability annotation can treat them as explicit
            # spans rather than independent values.
            str_values = [str(v) if v is not None else "" for v in row]
            span_for_col: dict[int, int] = {}
            col_cursor = 0
            while col_cursor < len(str_values):
                val = str_values[col_cursor].strip()
                if val:
                    run_end = col_cursor + 1
                    while run_end < len(str_values) and str_values[run_end].strip() == val:
                        run_end += 1
                    run_length = run_end - col_cursor
                    if run_length > 1:
                        for ci in range(col_cursor, run_end):
                            span_for_col[ci] = run_length
                    col_cursor = run_end
                else:
                    col_cursor += 1
            cells: list[TableCellPayload] = []
            for col_idx, value in enumerate(row):
                raw_text = str(value) if value is not None else ""
                column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                cell_meta: dict[str, Any] = {}
                if col_idx in span_for_col:
                    cell_meta["column_span"] = span_for_col[col_idx]
                cells.append(
                    TableCellPayload(
                        row_index=row_idx,
                        column_index=col_idx,
                        column_key=column_key,
                        raw_text=raw_text,
                        normalized_value=TableDetector._normalize_cell_value(raw_text),
                        bbox=bbox,
                        confidence=None,
                        metadata=cell_meta,
                    )
                )
            table_rows.append(
                TableRowPayload(
                    row_index=row_idx,
                    page_number=page_number,
                    bbox=bbox,
                    raw_text=" | ".join(str(c.raw_text) for c in cells),
                    metadata={"row_type": "data"},
                    cells=cells,
                )
            )

        merged_meta: dict[str, Any] = dict(source_metadata or {})
        base_detected_via = str(merged_meta.get("detected_via") or "").strip() or "vlm"
        if "vlm" not in base_detected_via.lower():
            merged_meta["detected_via"] = f"{base_detected_via}+vlm"
        merged_meta["vlm_model"] = self.table_vlm_model
        try:
            existing_conf = float(merged_meta.get("structure_confidence") or 0.0)
        except (TypeError, ValueError):
            existing_conf = 0.0
        merged_meta["structure_confidence"] = max(0.0, min(1.0, max(existing_conf, 0.9)))

        return TablePayload(
            order_index=order_index,
            title=title or f"Table {order_index}",
            section_heading=section_heading or "",
            page_number=page_number,
            bbox=bbox,
            column_schema=column_schema,
            data_dictionary={},
            metadata=merged_meta,
            rows=table_rows,
        )
