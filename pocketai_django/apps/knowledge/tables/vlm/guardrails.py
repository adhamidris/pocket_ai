from __future__ import annotations

from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.azure_di import AzureDocumentIntelligenceExtractor
from apps.knowledge.ingestion.contracts import TablePayload, TableRowPayload
from apps.knowledge.tables.semantic.scope_engine import SCOPE_REASON_ABSTAIN, canonical_scope_reason


class IngestionTableVlmGuardrailsMixin:

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
