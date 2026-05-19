from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any, Mapping, Sequence

from apps.knowledge.tables.column_roles import (
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
    _ARABIC_CHAR_RE,
    _ARABIC_DIACRITICS_RE,
    _column_numeric_signal,
)
from apps.knowledge.models import KnowledgeUploadTable
from apps.knowledge.tables.scope_engine import SCOPE_REASON_ABSTAIN, canonical_scope_reason

logger = logging.getLogger(__name__)


class IngestionTableSemanticsMixin:


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

    @staticmethod
    def _match_case(replacement: str, original: str) -> str:
        if not original:
            return replacement
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement.lower()

    def _compile_ocr_replacements(self, raw: Any) -> list[tuple[re.Pattern[str], str]]:
        defaults = (
            (r"\bfoos\b", "fees"),
            (r"\bfous\b", "fees"),
            (r"\bfroo\b", "free"),
            (r"\bfrog\b", "free"),
            (r"\bfino\b", "free"),
            (r"\bronowal\b", "renewal"),
            (r"\brenowal\b", "renewal"),
            (r"\bbhield\b", "shield"),
        )
        replacements: list[tuple[str, str]] = list(defaults)
        if isinstance(raw, str) and raw.strip():
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    replacements.extend((str(k), str(v)) for k, v in loaded.items())
                elif isinstance(loaded, list):
                    for item in loaded:
                        if isinstance(item, dict):
                            pattern = item.get("pattern")
                            replacement = item.get("replacement")
                            if pattern and replacement is not None:
                                replacements.append((str(pattern), str(replacement)))
            except json.JSONDecodeError:
                pass
        compiled: list[tuple[re.Pattern[str], str]] = []
        for pattern, replacement in replacements:
            try:
                compiled.append((re.compile(pattern, flags=re.IGNORECASE), replacement))
            except re.error:
                continue
        return compiled

    def _normalize_arabic_text(self, text: str) -> str:
        text = _ARABIC_DIACRITICS_RE.sub("", text)
        text = text.replace("ـ", "")
        text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ٱ", "ا")
        text = text.replace("ى", "ي")
        return text

    def _normalize_currency_tokens(self, text: str) -> str:
        text = re.sub(r"\bE\s*G\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*G\s*F\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*6\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE\s*B\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bB\s*G\s*P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bEGF\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bE6P\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bEBP\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bBGP\b", "EGP", text, flags=re.IGNORECASE)
        text = re.sub(r"\bL\.?\s*E\.?\b", "EGP", text, flags=re.IGNORECASE)
        return text

    def _normalize_currency_spacing(self, text: str) -> str:
        if not self.ocr_currency_spacing_enabled:
            return text
        return re.sub(r"\bEGP(?=\d)", "EGP ", text)

    def _normalize_percent_spacing(self, text: str) -> str:
        if not self.ocr_percent_space_fix_enabled:
            return text
        def _fix(match: re.Match[str]) -> str:
            whole = match.group(1)
            frac = match.group(2)
            return f"{whole}.{frac}%"
        text = re.sub(r"\b(\d)\s+(\d{1,2})\s*%", _fix, text)
        text = re.sub(r"\b(\d{1,3})\s*%", r"\1%", text)
        text = re.sub(r"%\s*%+", "%", text)
        return text

    def _normalize_percent_sanity(self, text: str) -> str:
        if not self.ocr_percent_fix_enabled:
            return text
        max_val = self.ocr_percent_sanity_max
        if max_val <= 0:
            return text
        def _fix(match: re.Match[str]) -> str:
            raw = match.group(1)
            try:
                value = float(raw)
            except ValueError:
                return match.group(0)
            if value <= max_val or value >= 1000:
                return match.group(0)
            fixed = value / 100.0
            rendered = f"{fixed:.2f}".rstrip("0").rstrip(".")
            return f"{rendered}%"
        return re.sub(r"\b(\d{2,3})\s*%", _fix, text)

    def _normalize_ocr_text(self, text: str) -> str:
        if not self.ocr_normalization_enabled:
            return text
        text = unicodedata.normalize("NFKC", text)
        text = text.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ")
        text = self._normalize_currency_tokens(text)
        text = self._normalize_currency_spacing(text)
        text = self._normalize_percent_spacing(text)
        for pattern, replacement in self.ocr_word_replacements:
            text = pattern.sub(lambda m: self._match_case(replacement, m.group(0)), text)
        if _ARABIC_CHAR_RE.search(text):
            text = self._normalize_arabic_text(text)
        text = self._normalize_percent_sanity(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _table_cell_text(self, value: Any) -> str:
        text = self._sanitize_text(value)
        text = text.replace("\t", " ").replace("|", " ")
        text = re.sub(r"\s+", " ", text).strip()
        return self._normalize_ocr_text(text)

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

    def _table_parent_markdown_from_model(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        raw_schema: Sequence[str],
        privacy_rules: Mapping[str, Any],
        max_rows: int,
        max_chars: int,
    ) -> tuple[str, bool]:
        preface: list[str] = []
        if table.section_heading:
            preface.append(f"[Section] {table.section_heading}")
        title = table.title or f"Table {table.order_index}"
        preface.append(f"[Table] {title}")

        headers = [entry[0] for entry in column_map]
        if not headers:
            return "", False

        lines = list(preface)
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        char_count = sum(len(line) + 1 for line in lines)

        data_rows = 0
        truncated = False
        for row in table.rows.all():
            if (row.metadata or {}).get("row_type") == "header":
                continue
            row_attributes = self._row_model_attributes(row, raw_schema)
            if self._row_is_internal(row_attributes, privacy_rules):
                continue
            cell_lookup = {cell.column_index: cell.raw_text for cell in row.cells.all()}
            values = [self._table_cell_text(cell_lookup.get(idx, "")) for _, _, idx in column_map]
            line = "| " + " | ".join(values) + " |"
            if max_rows and data_rows >= max_rows:
                truncated = True
                break
            if max_chars and (char_count + len(line) + 1) > max_chars:
                truncated = True
                break
            lines.append(line)
            char_count += len(line) + 1
            data_rows += 1

        if truncated:
            lines.append("[Table truncated]")
        return "\n".join(lines).strip(), truncated

    def _table_row_chunk_payloads(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        raw_schema: Sequence[str],
        privacy_rules: Mapping[str, Any],
        base_metadata: Mapping[str, Any],
        max_rows: int,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        title = table.title or f"Table {table.order_index}"
        data_rows = 0
        suppressed_low_signal_rows = 0
        shard_size = max(1, int(max_rows or 1))
        table_rows = list(table.rows.all())
        table_data_dictionary = (
            getattr(table, "data_dictionary", {})
            if isinstance(getattr(table, "data_dictionary", {}), Mapping)
            else {}
        )
        cached_column_roles = table_data_dictionary.get("column_roles") if isinstance(table_data_dictionary, Mapping) else None
        inferred_column_roles = self._infer_table_column_roles(
            table_rows=table_rows,
            column_map=column_map,
            cached_roles=cached_column_roles if isinstance(cached_column_roles, (list, tuple)) else None,
        )
        role_lookup = role_lookup_by_index(inferred_column_roles)
        descriptor_labels: list[str] = []
        qualifier_labels: list[str] = []
        inferred_segment_labels: list[str] = []
        for label, _canonical, raw_idx in column_map:
            role = str((role_lookup.get(raw_idx) or {}).get("role") or "").strip()
            if role == COLUMN_ROLE_SCOPE_DIMENSION:
                inferred_segment_labels.append(label)
                continue
            if role == COLUMN_ROLE_QUALIFIER:
                qualifier_labels.append(label)
                continue
            if role == COLUMN_ROLE_DESCRIPTOR:
                descriptor_labels.append(label)
                continue
            if role == COLUMN_ROLE_NOTE:
                continue

        contextual_labels = set(dict.fromkeys(descriptor_labels + qualifier_labels))
        if not inferred_segment_labels:
            contextual_index_set = {
                idx
                for idx, (label, _canonical, _raw_idx) in enumerate(column_map)
                if label in contextual_labels
            }
            inferred_segment_labels = [
                label
                for idx, (label, _canonical, _raw_idx) in enumerate(column_map)
                if idx not in contextual_index_set
            ]
        active_subsection: str = ""
        for row in table_rows:
            if (row.metadata or {}).get("row_type") == "header":
                continue

            # ── Section header rows: track label, skip as data ──
            row_model_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            if row_model_meta.get("row_type") == "section_header":
                row_cells = list(row.cells.all())
                cell_lookup = {cell.column_index: cell.raw_text for cell in row_cells}
                label = ""
                for _, _, idx in column_map:
                    label = self._table_cell_text(cell_lookup.get(idx, ""))
                    if label:
                        break
                active_subsection = self._table_subsection_label(label)
                continue

            row_attributes = self._row_model_attributes(row, raw_schema)
            if self._row_is_internal(row_attributes, privacy_rules):
                continue
            row_cells = list(row.cells.all())
            cell_lookup = {cell.column_index: cell.raw_text for cell in row_cells}
            pairs: list[str] = []
            value_by_label: dict[str, str] = {}
            # Extract row_label from first column (typically the row identifier/name)
            row_label = ""
            for label, _, idx in column_map:
                value = self._table_cell_text(cell_lookup.get(idx, ""))
                if value:
                    pairs.append(f"{label}: {value}")
                    value_by_label[label] = value
                    # First column value becomes the row_label for search indexing
                    if not row_label:
                        row_label = value
            if not pairs:
                continue

            # Fallback: detect uniform-value rows not caught by annotation
            unique_values = set(value_by_label.values())
            if len(unique_values) == 1 and len(value_by_label) >= 4:
                active_subsection = self._table_subsection_label(next(iter(unique_values), ""))
                continue
            scope_contract = self._resolve_row_scope_contract(
                row_model_meta=row_model_meta,
                value_by_label=value_by_label,
                contextual_labels=contextual_labels,
                inferred_segment_labels=inferred_segment_labels,
            )
            inferred_scope_columns = list(scope_contract.get("inferred_scope_columns") or [])
            observed_value_columns = list(scope_contract.get("observed_value_columns") or [])
            qualifier_columns = list(scope_contract.get("qualifier_columns") or [])
            scope_dimension_columns = list(scope_contract.get("scope_dimension_columns") or [])
            scope_reason = canonical_scope_reason(scope_contract.get("scope_reason"))
            scope_confidence = self._coerce_scope_confidence(scope_contract.get("scope_confidence"))

            fee_value = self._table_cell_text(str(row_model_meta.get("scope_value") or ""))
            if not fee_value:
                scoped_values = [
                    value_by_label.get(label, "")
                    for label in inferred_scope_columns
                    if value_by_label.get(label, "")
                ]
                unique_values = list(dict.fromkeys(scoped_values))
                if len(unique_values) == 1:
                    fee_value = unique_values[0]

            row_signal_score, row_signal_diag = self._table_row_signal_score(
                value_by_label=value_by_label,
                inferred_scope_columns=inferred_scope_columns,
                observed_value_columns=observed_value_columns,
                fee_value=fee_value,
            )
            structural_row_diag = self._table_row_structural_context_profile(
                value_by_label=value_by_label,
                scope_dimension_columns=(
                    scope_dimension_columns
                    or row_model_meta.get("scope_dimension_columns")
                    or inferred_scope_columns
                ),
                fee_value=fee_value,
                row_signal_diag=row_signal_diag,
            )
            row_signal_override = self._collapsed_native_text_row_override_applies(
                table=table,
                value_by_label=value_by_label,
                row_signal_diag=row_signal_diag,
                structural_row_diag=structural_row_diag,
            )
            if (
                row_signal_diag["pair_count"] < self.table_row_signal_min_pairs
                and row_signal_score < self.table_row_signal_min_score
            ):
                if not row_signal_override:
                    suppressed_low_signal_rows += 1
                    continue

            evidence_cell_ids = [
                str(cell.id)
                for cell in row_cells
                if str(cell.raw_text or "").strip()
            ]
            preface = []
            # Keep derived headings out of row-level chunks to avoid broad-term noise.
            section_heading = str(getattr(table, "section_heading", "") or "").strip()
            if section_heading:
                table_meta = getattr(table, "metadata", None) if isinstance(getattr(table, "metadata", None), Mapping) else {}
                derived_heading = str((table_meta or {}).get("derived_section_heading") or "").strip()
                if not derived_heading or derived_heading != section_heading:
                    preface.append(f"[Section] {section_heading}")
            if active_subsection:
                preface.append(f"[SubSection] {active_subsection}")
            preface.append(f"[Table] {title}")
            preface.append(f"[Row] {row.row_index}")
            if inferred_scope_columns:
                preface.append(f"[Scope] {', '.join(inferred_scope_columns)}")
            text = "\n".join(preface + pairs)
            shard_index = data_rows // shard_size
            shard_offset = data_rows % shard_size
            row_meta = dict(base_metadata)
            row_meta.update(
                {
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "is_table_preview": False,
                    "table_row_index": row.row_index,
                    "row_label": row_label,  # Enable row-label search matching
                    "search_tier": "drill_down",
                    "table_row_contract_version": scope_contract.get("contract_version") or TABLE_SCOPE_CONTRACT_VERSION,
                    "table_row_observed_value_columns": observed_value_columns,
                    "table_row_qualifier_columns": qualifier_columns,
                    "table_row_scope_dimension_columns": scope_dimension_columns,
                    "table_row_inferred_scope_columns": inferred_scope_columns,
                    "table_row_scope_reason": scope_reason or SCOPE_REASON_ABSTAIN,
                    "table_row_scope_confidence": scope_confidence,
                    "table_row_fee_value": fee_value,
                    "table_row_evidence_cell_ids": evidence_cell_ids,
                    "table_row_shard_index": int(shard_index),
                    "table_row_shard_size": int(shard_size),
                    "table_row_shard_offset": int(shard_offset),
                    "table_row_signal_filter_enabled": True,
                    "table_row_signal_min_pairs": int(self.table_row_signal_min_pairs),
                    "table_row_signal_min_score": float(self.table_row_signal_min_score),
                    "table_row_signal_pair_count": int(row_signal_diag["pair_count"]),
                    "table_row_signal_numeric_value_count": int(row_signal_diag["numeric_value_count"]),
                    "table_row_signal_value_keyword_count": int(row_signal_diag["keyword_value_count"]),
                    "table_row_signal_scope_column_count": int(row_signal_diag["scope_column_count"]),
                    "table_row_signal_observed_value_column_count": int(
                        row_signal_diag["observed_value_column_count"]
                    ),
                    "table_row_signal_has_fee_value": bool(row_signal_diag["has_fee_value"]),
                    "table_row_signal_score": float(row_signal_diag["score"]),
                    "table_row_signal_override": bool(row_signal_override),
                    "table_row_is_structural_context": bool(
                        structural_row_diag["is_structural_context"]
                    ),
                    "table_row_structural_scope_label_count": int(
                        structural_row_diag["scope_label_count"]
                    ),
                    "table_row_structural_scope_echo_count": int(
                        structural_row_diag["scope_echo_count"]
                    ),
                    "table_row_structural_scope_numeric_count": int(
                        structural_row_diag["scope_numeric_count"]
                    ),
                    "table_row_structural_scope_echo_ratio": float(
                        structural_row_diag["scope_echo_ratio"]
                    ),
                }
            )
            payloads.append({"text": text, "metadata": row_meta})
            data_rows += 1
        if suppressed_low_signal_rows > 0:
            table_id = getattr(table, "id", None)
            page_number = None
            page = getattr(table, "page", None)
            if page is not None:
                page_number = getattr(page, "page_number", None)
            logger.info(
                "table.row_signal_filter table_id=%s page=%s kept=%s suppressed=%s min_pairs=%s min_score=%.2f",
                table_id,
                page_number,
                len(payloads),
                suppressed_low_signal_rows,
                self.table_row_signal_min_pairs,
                self.table_row_signal_min_score,
            )
        return payloads

    def _table_summary_chunk_payloads(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        base_metadata: Mapping[str, Any],
        total_data_rows: int,
        row_label_entries: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Build one primary-search summary chunk per row shard."""
        column_names = [entry[0] for entry in column_map]
        if not column_names:
            return []

        title = table.title or f"Table {table.order_index}"
        grouped_labels: dict[int, list[str]] = {}
        grouped_row_indices: dict[int, list[int]] = {}
        for entry in row_label_entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                shard_index = int(entry.get("shard_index") or 0)
            except (TypeError, ValueError):
                shard_index = 0
            label = self._sanitize_text(entry.get("label") or "").strip()
            if label:
                grouped_labels.setdefault(shard_index, []).append(label)
            raw_row_index = entry.get("row_index")
            try:
                row_index = int(raw_row_index)
            except (TypeError, ValueError):
                row_index = None  # type: ignore[assignment]
            if row_index is not None:
                grouped_row_indices.setdefault(shard_index, []).append(row_index)

        if not grouped_labels and not grouped_row_indices:
            grouped_labels[0] = []

        shard_ids = sorted(set(grouped_labels.keys()) | set(grouped_row_indices.keys()))
        total_shards = max(1, len(shard_ids))
        payloads: list[dict[str, Any]] = []
        for position, shard_index in enumerate(shard_ids, start=1):
            lines: list[str] = []
            if table.section_heading:
                lines.append(f"[Section] {table.section_heading}")
            lines.append(f"[Table] {title}")
            lines.append(f"[Columns] {' | '.join(column_names)}")
            lines.append(f"[Rows] {total_data_rows} data rows")
            lines.append(f"[Shard] {position}/{total_shards}")

            row_indices = grouped_row_indices.get(shard_index) or []
            if row_indices:
                lines.append(f"[Row Range] {min(row_indices)} - {max(row_indices)}")
                lines.append(f"[Rows In Shard] {len(row_indices)}")

            shard_labels = grouped_labels.get(shard_index) or []
            cap = self.table_summary_max_row_labels
            if shard_labels:
                sample = shard_labels[:cap]
                label_text = ", ".join(sample)
                if len(shard_labels) > cap:
                    label_text += f", ... and {len(shard_labels) - cap} more"
                lines.append(f"[Row Labels] {label_text}")

            summary_meta = dict(base_metadata)
            summary_meta.update(
                {
                    "content_source": "table_summary",
                    "table_chunk_role": "summary",
                    "is_table_preview": True,
                    "search_tier": "primary",
                    "table_total_rows": total_data_rows,
                    "table_row_shard_index": int(shard_index),
                    "table_shard_position": int(position),
                    "table_shard_count": int(total_shards),
                }
            )
            if row_indices:
                summary_meta["table_row_shard_start_row"] = int(min(row_indices))
                summary_meta["table_row_shard_end_row"] = int(max(row_indices))
                summary_meta["table_row_shard_row_count"] = int(len(row_indices))

            payloads.append({"text": "\n".join(lines), "metadata": summary_meta})
        return payloads

    def _table_summary_chunk_payload(
        self,
        *,
        table: KnowledgeUploadTable,
        column_map: Sequence[tuple[str, str, int]],
        base_metadata: Mapping[str, Any],
        total_data_rows: int,
        row_labels: Sequence[str],
    ) -> dict[str, Any] | None:
        """Backward-compatible wrapper for a single summary chunk.

        New ingestion paths should use `_table_summary_chunk_payloads` for
        row-sharded summaries.
        """
        entries = [{"label": label, "row_index": idx, "shard_index": 0} for idx, label in enumerate(row_labels)]
        payloads = self._table_summary_chunk_payloads(
            table=table,
            column_map=column_map,
            base_metadata=base_metadata,
            total_data_rows=total_data_rows,
            row_label_entries=entries,
        )
        return payloads[0] if payloads else None

    def _table_preview_text(
        self,
        tables: Sequence[TablePayload],
        *,
        row_limit: int = 5,
        rules: Mapping[str, Any] | None = None,
    ) -> str:
        if not tables:
            return ""
        lines: list[str] = []
        for table in tables:
            sheet_role = str((table.metadata or {}).get("sheet_role") or "")
            if sheet_role == "reference_hidden":
                continue
            visible_columns = table.column_schema
            if rules:
                visible_columns = [col for col in table.column_schema if not self._column_is_sensitive(col, rules)]
            if not visible_columns:
                continue
            if table.title:
                lines.append(f"[Table] {table.title}")
            header_line = "\t".join(visible_columns)
            if header_line.strip():
                lines.append(header_line)
            effective_row_limit = row_limit
            if sheet_role == "instructional":
                effective_row_limit = min(row_limit, 3)
            elif sheet_role == "summary":
                effective_row_limit = min(row_limit, 4)
            elif sheet_role == "form_like":
                effective_row_limit = min(row_limit, 4)
            for row in table.rows[:effective_row_limit]:
                attributes = self._row_attributes_from_table(row, table.column_schema)
                if rules and self._row_is_internal(attributes, rules):
                    continue
                values = [attributes.get(column, "") for column in visible_columns]
                if any(values):
                    lines.append("\t".join(values))
            lines.append("")
        return "\n".join(lines).strip()
