from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from apps.knowledge.tables.semantic.chunk_payloads import IngestionTableChunkPayloadsMixin
from apps.knowledge.tables.semantic.text_normalization import IngestionTableTextNormalizationMixin
from apps.knowledge.tables.semantic.column_roles import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_NOTE,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    column_role_payloads,
    infer_column_roles,
    role_lookup_by_index,
)
from apps.knowledge.ingestion.contracts import ExtractionResult, TablePayload
from apps.knowledge.ingestion.signals import (
    COLUMN_ROLE_INFERENCE_VERSION,
    TABLE_SCOPE_CONTRACT_VERSION,
    _column_numeric_signal,
)
from apps.knowledge.models import KnowledgeUploadTable
from apps.knowledge.tables.semantic.scope_engine import canonical_scope_reason


class IngestionTableSemanticsMixin(
    IngestionTableTextNormalizationMixin,
    IngestionTableChunkPayloadsMixin,
):


    @staticmethod
    def _row_cells_for_role_inference(row: Any) -> list[Any]:
        cells_attr = getattr(row, "cells", None)
        if cells_attr is None:
            return []
        if hasattr(cells_attr, "all"):
            return list(cells_attr.all())
        if isinstance(cells_attr, (list, tuple)):
            return list(cells_attr)
        return []

    def _infer_table_column_roles(
        self,
        *,
        table_rows: Sequence[Any],
        column_map: Sequence[tuple[str, str, int]],
        cached_roles: Sequence[Mapping[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not column_map:
            return []
        if isinstance(cached_roles, (list, tuple)):
            lookup = role_lookup_by_index(cached_roles)
            normalized_cached: list[dict[str, Any]] = []
            for _idx, (label, _canonical, raw_idx) in enumerate(column_map):
                payload = lookup.get(raw_idx)
                if not payload:
                    continue
                normalized_cached.append(
                    {
                        "column_index": int(raw_idx),
                        "column_key": str(payload.get("column_key") or label),
                        "role": str(payload.get("role") or COLUMN_ROLE_SCOPE_DIMENSION),
                        "confidence": round(float(payload.get("confidence") or 0.0), 3),
                        "role_scores": dict(payload.get("role_scores") or {}),
                        "signals": dict(payload.get("signals") or {}),
                    }
                )
            if len(normalized_cached) >= max(1, len(column_map) - 1):
                return normalized_cached

        row_values: list[list[str]] = []
        header_row_positions: set[int] = set()
        schema = [str(label or "").strip() or f"column_{idx + 1}" for idx, (label, _canonical, _raw_idx) in enumerate(column_map)]
        for pos, row in enumerate(table_rows):
            row_meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
            if str(row_meta.get("row_type") or "").strip().lower() == "header":
                header_row_positions.add(pos)
            row_cells = self._row_cells_for_role_inference(row)
            cell_lookup = {}
            for cell in row_cells:
                try:
                    idx = int(getattr(cell, "column_index", 0))
                except (TypeError, ValueError):
                    continue
                cell_lookup[idx] = self._table_cell_text(getattr(cell, "raw_text", ""))
            row_values.append(
                [self._table_cell_text(cell_lookup.get(raw_idx, "")) for _label, _canonical, raw_idx in column_map]
            )

        profiles = infer_column_roles(
            row_values=row_values,
            column_schema=schema,
            header_row_indices=header_row_positions,
            min_scope_columns=3,
        )
        payloads: list[dict[str, Any]] = []
        for idx, payload in enumerate(column_role_payloads(profiles)):
            raw_idx = column_map[idx][2] if idx < len(column_map) else idx
            payload["column_index"] = int(raw_idx)
            payload["column_key"] = str(column_map[idx][0] if idx < len(column_map) else payload.get("column_key") or "")
            payloads.append(payload)
        return payloads

    def _column_is_sensitive(self, column: str, rules: Mapping[str, Any]) -> bool:
        canonical = self._canonical_column_name(column)
        if canonical and canonical in rules.get("sensitive_exact", set()):
            return True
        patterns = rules.get("sensitive_patterns") or []
        combined = column or ""
        for pattern in patterns:
            try:
                if pattern.search(combined) or (canonical and pattern.search(canonical)):
                    return True
            except re.error:
                continue
        return False

    def _row_is_internal(self, attributes: Mapping[str, str], rules: Mapping[str, Any]) -> bool:
        column = rules.get("row_flag_column")
        values = rules.get("row_flag_values") or set()
        if not column or not values:
            return False
        for key, value in attributes.items():
            canonical = self._canonical_column_name(key)
            if canonical == column and str(value or "").strip().lower() in values:
                return True
        return False


    def _table_subsection_label(self, value: Any) -> str:
        """
        Keep subsection labels only when they look like short, title-like context.

        This avoids leaking value-heavy or concatenated row content into every
        subsequent row chunk via `[SubSection] ...`.
        """
        text = self._table_cell_text(value)
        if not text:
            return ""
        word_count = len(re.findall(r"[A-Za-z0-9\u0600-\u06FF]+", text))
        numeric_like_count = len(
            [
                token
                for token in re.split(r"\s+", text)
                if token and _column_numeric_signal(token)
            ]
        )
        if len(text) > 80:
            return ""
        if word_count > 10:
            return ""
        if numeric_like_count >= 2:
            return ""
        return text

    def _clean_scope_labels(self, values: Any) -> list[str]:
        if not isinstance(values, (list, tuple)):
            return []
        cleaned: list[str] = []
        for entry in values:
            label = self._table_cell_text(entry)
            if label:
                cleaned.append(label)
        return list(dict.fromkeys(cleaned))

    @staticmethod
    def _coerce_scope_confidence(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return round(float(value), 3)
        if isinstance(value, str):
            try:
                return round(float(value), 3)
            except (TypeError, ValueError):
                return None
        return None

    def _resolve_row_scope_contract(
        self,
        *,
        row_model_meta: Mapping[str, Any],
        value_by_label: Mapping[str, str],
        contextual_labels: set[str],
        inferred_segment_labels: Sequence[str],
    ) -> dict[str, Any]:
        observed_value_columns = self._clean_scope_labels(row_model_meta.get("observed_value_columns"))
        if not observed_value_columns:
            observed_value_columns = list(dict.fromkeys(value_by_label.keys()))

        qualifier_columns = self._clean_scope_labels(row_model_meta.get("qualifier_columns"))
        if not qualifier_columns:
            qualifier_columns = [label for label in observed_value_columns if label in contextual_labels]
        qualifier_columns = list(dict.fromkeys(qualifier_columns))

        scope_dimension_columns = self._clean_scope_labels(row_model_meta.get("scope_dimension_columns"))
        if not scope_dimension_columns:
            scope_dimension_columns = list(dict.fromkeys(inferred_segment_labels))
        scope_dimension_columns = list(dict.fromkeys(scope_dimension_columns))

        inferred_scope_columns = self._clean_scope_labels(row_model_meta.get("inferred_scope_columns"))
        if scope_dimension_columns:
            scope_dimension_set = set(scope_dimension_columns)
            inferred_scope_columns = [
                label for label in inferred_scope_columns if label in scope_dimension_set
            ]
        inferred_scope_columns = list(dict.fromkeys(inferred_scope_columns))

        scope_confidence = self._coerce_scope_confidence(row_model_meta.get("scope_confidence"))

        raw_scope_reason = self._table_cell_text(row_model_meta.get("scope_reason"))
        scope_reason = canonical_scope_reason(raw_scope_reason)

        return {
            "contract_version": str(row_model_meta.get("table_scope_contract_version") or TABLE_SCOPE_CONTRACT_VERSION),
            "observed_value_columns": observed_value_columns,
            "qualifier_columns": qualifier_columns,
            "scope_dimension_columns": scope_dimension_columns,
            "inferred_scope_columns": inferred_scope_columns,
            "scope_confidence": scope_confidence,
            "scope_reason": scope_reason,
        }

    def _table_header_labels_for_model(
        self,
        table: KnowledgeUploadTable,
        raw_schema: Sequence[str],
    ) -> list[str]:
        header_row = None
        for row in table.rows.all():
            if (row.metadata or {}).get("row_type") == "header":
                header_row = row
                break
        labels: list[str] = []
        if header_row:
            header_cells = sorted(list(header_row.cells.all()), key=lambda c: c.column_index)
            max_len = max(len(raw_schema), len(header_cells))
            for idx in range(max_len):
                if idx < len(header_cells):
                    label = header_cells[idx].raw_text
                elif idx < len(raw_schema):
                    label = raw_schema[idx]
                else:
                    label = f"column_{idx + 1}"
                cleaned = self._table_cell_text(label)
                labels.append(cleaned or (raw_schema[idx] if idx < len(raw_schema) else f"column_{idx + 1}"))
        else:
            for idx, col in enumerate(raw_schema):
                label = self._table_cell_text(col or f"column_{idx + 1}")
                labels.append(label or f"column_{idx + 1}")
        return labels

    def _table_column_map_for_model(
        self,
        header_labels: Sequence[str],
        raw_schema: Sequence[str],
        rules: Mapping[str, Any],
    ) -> tuple[list[tuple[str, str, int]], list[str]]:
        column_map: list[tuple[str, str, int]] = []
        hidden_columns: list[str] = []
        max_len = max(len(header_labels), len(raw_schema))
        for idx in range(max_len):
            label = header_labels[idx] if idx < len(header_labels) else ""
            if not label and idx < len(raw_schema):
                label = raw_schema[idx]
            label = self._table_cell_text(label) or f"column_{idx + 1}"
            if self._column_is_sensitive(label, rules):
                hidden_columns.append(label)
                continue
            canonical = self._canonical_column_name(label, f"column_{idx + 1}")
            column_map.append((label, canonical, idx))
        return column_map, hidden_columns

    def _table_column_map_for_payload(
        self,
        table: TablePayload,
    ) -> list[tuple[str, str, int]]:
        schema = [
            self._table_cell_text(value) or f"column_{idx + 1}"
            for idx, value in enumerate(table.column_schema or [])
        ]
        width = len(schema)
        for row in table.rows or []:
            for cell in self._row_cells_for_role_inference(row):
                try:
                    col_idx = int(getattr(cell, "column_index", -1))
                except (TypeError, ValueError):
                    continue
                width = max(width, col_idx + 1)

        column_map: list[tuple[str, str, int]] = []
        for idx in range(max(0, width)):
            label = schema[idx] if idx < len(schema) else f"column_{idx + 1}"
            canonical = self._canonical_column_name(label, f"column_{idx + 1}")
            column_map.append((label, canonical, idx))
        return column_map

    def _enrich_table_payload_column_roles(self, table: TablePayload) -> TablePayload:
        column_map = self._table_column_map_for_payload(table)
        if not column_map:
            return table

        data_dictionary = dict(table.data_dictionary or {})
        cached_roles = data_dictionary.get("column_roles")
        inferred_roles = self._infer_table_column_roles(
            table_rows=list(table.rows or []),
            column_map=column_map,
            cached_roles=cached_roles if isinstance(cached_roles, (list, tuple)) else None,
        )
        if not inferred_roles:
            return table

        role_lookup = role_lookup_by_index(inferred_roles)
        columns_by_role: dict[str, list[str]] = {
            COLUMN_ROLE_DESCRIPTOR: [],
            COLUMN_ROLE_QUALIFIER: [],
            COLUMN_ROLE_SCOPE_DIMENSION: [],
            COLUMN_ROLE_NOTE: [],
        }
        confidence_values: list[float] = []
        for label, _canonical, raw_idx in column_map:
            payload = role_lookup.get(raw_idx) or {}
            role = str(payload.get("role") or COLUMN_ROLE_SCOPE_DIMENSION).strip() or COLUMN_ROLE_SCOPE_DIMENSION
            if role in columns_by_role:
                columns_by_role[role].append(label)
            confidence = self._coerce_scope_confidence(payload.get("confidence"))
            if confidence is not None:
                confidence_values.append(confidence)
        columns_by_role = {
            role: list(dict.fromkeys(labels))
            for role, labels in columns_by_role.items()
        }

        role_summary = {
            "descriptor_count": len(columns_by_role.get(COLUMN_ROLE_DESCRIPTOR, [])),
            "qualifier_count": len(columns_by_role.get(COLUMN_ROLE_QUALIFIER, [])),
            "scope_dimension_count": len(columns_by_role.get(COLUMN_ROLE_SCOPE_DIMENSION, [])),
            "note_count": len(columns_by_role.get(COLUMN_ROLE_NOTE, [])),
            "columns_profiled": len(inferred_roles),
        }
        if confidence_values:
            role_summary["average_confidence"] = round(
                sum(confidence_values) / float(len(confidence_values)),
                3,
            )

        data_dictionary.update(
            {
                "column_role_inference_version": COLUMN_ROLE_INFERENCE_VERSION,
                "column_roles": inferred_roles,
                "column_roles_by_type": columns_by_role,
                "column_role_summary": role_summary,
            }
        )
        return TablePayload(
            order_index=table.order_index,
            title=table.title,
            section_heading=table.section_heading,
            page_number=table.page_number,
            bbox=table.bbox,
            column_schema=table.column_schema,
            data_dictionary=data_dictionary,
            metadata=table.metadata,
            rows=table.rows,
        )

    def _enrich_extraction_with_column_roles(self, extraction: ExtractionResult) -> ExtractionResult:
        if not extraction.tables:
            return extraction

        enriched_tables: list[TablePayload] = []
        tables_with_roles = 0
        profiled_columns = 0
        for table in extraction.tables:
            enriched = self._enrich_table_payload_column_roles(table)
            enriched_tables.append(enriched)
            data_dictionary = enriched.data_dictionary if isinstance(enriched.data_dictionary, Mapping) else {}
            roles = data_dictionary.get("column_roles")
            if isinstance(roles, (list, tuple)) and roles:
                tables_with_roles += 1
                profiled_columns += len(roles)

        metadata = dict(extraction.metadata or {})
        metadata["column_role_inference"] = {
            "version": COLUMN_ROLE_INFERENCE_VERSION,
            "table_count": len(enriched_tables),
            "tables_with_roles": tables_with_roles,
            "columns_profiled": profiled_columns,
        }
        return ExtractionResult(
            text=extraction.text,
            format_hint=extraction.format_hint,
            metadata=metadata,
            pages=extraction.pages,
            tables=enriched_tables,
            issues=extraction.issues,
            entities=extraction.entities,
            text_html=extraction.text_html,
        )
