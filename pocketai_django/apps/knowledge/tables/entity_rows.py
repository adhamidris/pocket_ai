from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.contracts import TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.ingestion.signals import _column_numeric_signal
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadTableRow


class IngestionTableEntityRowsMixin:

    _GENERIC_TABLE_TITLE_RE = re.compile(r"^Table\s+\d+$", re.IGNORECASE)
    _GENERIC_COLUMN_LABEL_RE = re.compile(r"^(?:column[_\s-]*\d+|col[_\s-]*\d+|\d+)$", re.IGNORECASE)
    _COMMON_DESCRIPTOR_COLUMN_RE = re.compile(
        r"^(?:service|tariff|fee|fees|charge|charges|type|card type|account type|description|item|items|name|category|metric|parameter)$",
        re.IGNORECASE,
    )

    @staticmethod
    def _derive_table_title(table_payload: TablePayload, upload: KnowledgeUpload) -> str:
        raw_title: Any = table_payload.title
        if isinstance(raw_title, Mapping):
            raw_title = raw_title.get("content") or raw_title.get("text") or ""
        current = str(raw_title or "").strip()
        if current and not IngestionTableEntityRowsMixin._GENERIC_TABLE_TITLE_RE.match(current):
            return current

        doc_name = ""
        raw = (upload.display_name or upload.source_name or "").strip()
        if raw:
            doc_name = Path(raw).stem.strip()

        section = (table_payload.section_heading or "").strip()

        if doc_name and section:
            return f"{doc_name} – {section}"
        if doc_name:
            return f"{doc_name} – Table {table_payload.order_index}"
        if section:
            return section

        cols = [str(c).strip() for c in (table_payload.column_schema or []) if str(c).strip()]
        if cols:
            preview = ", ".join(cols[:4])
            if len(cols) > 4:
                preview += ", ..."
            return f"Table {table_payload.order_index} ({preview})"

        return f"Table {table_payload.order_index}"

    @classmethod
    def _table_column_label_is_generic(cls, value: Any) -> bool:
        label = str(value or "").strip()
        if not label:
            return True
        return bool(cls._GENERIC_COLUMN_LABEL_RE.fullmatch(label))

    @classmethod
    def _schema_label_can_be_section_heading(cls, value: Any) -> bool:
        label = re.sub(r"\s+", " ", str(value or "").strip())
        if not label:
            return False
        if cls._table_column_label_is_generic(label):
            return False
        if cls._COMMON_DESCRIPTOR_COLUMN_RE.fullmatch(label):
            return False
        if len(label) > 80:
            return False
        if len(re.findall(r"[A-Za-z0-9\u0600-\u06FF]+", label)) > 10:
            return False
        if _column_numeric_signal(label):
            return False
        return True

    @staticmethod
    def _table_cell_is_pure_value_like(value: str) -> bool:
        sample = re.sub(r"\s+", " ", str(value or "").strip())
        if not sample:
            return False
        lowered = sample.lower()
        if lowered in {"free", "n/a", "na", "none", "waived", "no fee", "no fees", "-"}:
            return True
        if re.fullmatch(r"[+\-]?(?:\d[\d,]*(?:[.:]\d+)?%?|[%$€£¥₹]+)", sample):
            return True
        tokens = [token.lower() for token in re.findall(r"[A-Za-z0-9]+", sample)]
        if not tokens:
            return False
        currency_tokens = {
            "usd", "eur", "gbp", "jpy", "chf", "aud", "cad", "cny", "inr",
            "sar", "aed", "egp", "qar", "kwd", "omr", "bhd", "try", "zar",
        }
        non_currency_alpha = [
            token for token in tokens
            if token not in currency_tokens and re.search(r"[a-z]", token)
        ]
        if _column_numeric_signal(sample) and len(non_currency_alpha) <= 1:
            return True
        return False

    def _table_cell_looks_like_header_label(self, value: str) -> bool:
        sample = self._table_cell_text(value)
        if not sample:
            return False
        if self._table_cell_is_pure_value_like(sample):
            return False
        if len(sample) > 90:
            return False
        word_count = len(re.findall(r"[A-Za-z0-9\u0600-\u06FF]+", sample))
        if word_count <= 0 or word_count > 10:
            return False
        return bool(re.search(r"[A-Za-z\u0600-\u06FF]", sample))

    def _row_cell_texts_by_index(self, row: TableRowPayload, *, width: int) -> list[str]:
        values = ["" for _ in range(max(0, width))]
        for cell in row.cells or []:
            try:
                idx = int(cell.column_index)
            except (TypeError, ValueError):
                continue
            if idx < 0:
                continue
            while idx >= len(values):
                values.append("")
            values[idx] = self._table_cell_text(cell.raw_text)
        return values

    def _embedded_header_candidate_score(
        self,
        *,
        schema: Sequence[str],
        row: TableRowPayload,
    ) -> tuple[bool, list[str], dict[str, Any]]:
        width = max(len(schema), max((int(cell.column_index) + 1 for cell in row.cells or []), default=0))
        values = self._row_cell_texts_by_index(row, width=width)
        non_empty = [value for value in values if value]
        if len(non_empty) < max(1, min(2, width - 1)):
            return False, values, {"reason": "too_few_header_values", "non_empty": len(non_empty)}

        generic_header_hits = 0
        header_like_count = 0
        pure_value_count = 0
        for idx, value in enumerate(values):
            if not value:
                continue
            if self._table_cell_is_pure_value_like(value):
                pure_value_count += 1
                continue
            if self._table_cell_looks_like_header_label(value):
                header_like_count += 1
                schema_label = schema[idx] if idx < len(schema) else ""
                if self._table_column_label_is_generic(schema_label):
                    generic_header_hits += 1

        if generic_header_hits <= 0:
            return False, values, {
                "reason": "no_generic_columns_with_header_labels",
                "generic_header_hits": generic_header_hits,
                "header_like_count": header_like_count,
            }
        if header_like_count < max(1, min(2, len(non_empty))):
            return False, values, {
                "reason": "insufficient_header_like_cells",
                "header_like_count": header_like_count,
            }
        if pure_value_count >= max(1, math.ceil(len(non_empty) * 0.5)):
            return False, values, {
                "reason": "value_like_row",
                "pure_value_count": pure_value_count,
                "non_empty": len(non_empty),
            }
        return True, values, {
            "generic_header_hits": generic_header_hits,
            "header_like_count": header_like_count,
            "pure_value_count": pure_value_count,
            "non_empty": len(non_empty),
        }

    def _apply_schema_to_rows(
        self,
        rows: Sequence[TableRowPayload],
        schema: Sequence[str],
    ) -> list[TableRowPayload]:
        normalized_schema = [str(col or "").strip() or f"column_{idx + 1}" for idx, col in enumerate(schema)]
        new_rows: list[TableRowPayload] = []
        for row in rows:
            new_cells: list[TableCellPayload] = []
            for cell in row.cells or []:
                try:
                    idx = int(cell.column_index)
                except (TypeError, ValueError):
                    idx = 0
                column_key = normalized_schema[idx] if 0 <= idx < len(normalized_schema) else f"column_{idx + 1}"
                new_cells.append(
                    TableCellPayload(
                        row_index=cell.row_index,
                        column_index=cell.column_index,
                        column_key=column_key,
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
                    metadata=row.metadata,
                    cells=new_cells,
                )
            )
        return new_rows

    def _promote_embedded_header_rows(self, table: TablePayload) -> tuple[TablePayload, int]:
        rows = list(table.rows or [])
        if len(rows) < 2:
            return table, 0
        schema = [str(col or "").strip() or f"column_{idx + 1}" for idx, col in enumerate(table.column_schema or [])]
        if not schema:
            return table, 0

        candidate_positions: list[tuple[int, list[str], dict[str, Any]]] = []
        for position, row in enumerate(sorted(rows, key=lambda item: int(getattr(item, "row_index", 0)))[:3]):
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            row_type = str(row_meta.get("row_type") or "").strip().lower()
            if row_type in {"header", "section_header"}:
                continue
            is_candidate, values, diag = self._embedded_header_candidate_score(schema=schema, row=row)
            if not is_candidate:
                continue
            candidate_positions.append((int(row.row_index), values, diag))
            break

        if not candidate_positions:
            return table, 0

        header_row_index, header_values, diag = candidate_positions[0]
        width = max(len(schema), len(header_values))
        normalized_schema: list[str] = []
        renamed_columns = 0
        for idx in range(width):
            current = schema[idx] if idx < len(schema) else f"column_{idx + 1}"
            header_value = header_values[idx] if idx < len(header_values) else ""
            if header_value and (self._table_column_label_is_generic(current) or not current):
                normalized_schema.append(header_value)
                if header_value != current:
                    renamed_columns += 1
            else:
                normalized_schema.append(current or header_value or f"column_{idx + 1}")

        rebuilt_rows: list[TableRowPayload] = []
        for row in rows:
            row_meta = dict(row.metadata or {})
            if int(row.row_index) == header_row_index:
                row_meta["original_row_type"] = row_meta.get("row_type") or "data"
                row_meta["row_type"] = "header"
                row_meta["embedded_header_promoted"] = True
            rebuilt_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row_meta,
                    cells=row.cells,
                )
            )
        rebuilt_rows = self._apply_schema_to_rows(rebuilt_rows, normalized_schema)

        inferred_section = ""
        if (
            not str(table.section_heading or "").strip()
            and header_values
            and not header_values[0]
            and self._schema_label_can_be_section_heading(schema[0] if schema else "")
        ):
            inferred_section = schema[0]

        table_meta = dict(table.metadata or {})
        table_meta["embedded_header_rows_promoted"] = int(table_meta.get("embedded_header_rows_promoted") or 0) + 1
        table_meta["embedded_header_renamed_columns"] = renamed_columns
        table_meta["embedded_header_source_row"] = header_row_index
        table_meta["embedded_header_diagnostics"] = diag
        if inferred_section:
            table_meta["embedded_header_section_label"] = inferred_section
            table_meta["embedded_header_section_label_source"] = "schema_column_1"

        return (
            TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading or inferred_section,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=normalized_schema,
                data_dictionary=table.data_dictionary,
                metadata=table_meta,
                rows=rebuilt_rows,
            ),
            1,
        )

    def _row_attributes_from_table(
        self,
        row: TableRowPayload,
        column_schema: Sequence[str],
    ) -> dict[str, str]:
        attributes: dict[str, str] = {}
        for cell in row.cells:
            key = cell.column_key or (column_schema[cell.column_index] if cell.column_index < len(column_schema) else "")
            key = key.strip() if isinstance(key, str) else ""
            if not key:
                key = f"column_{cell.column_index + 1}"
            value = (cell.raw_text or "").strip()
            if value:
                existing = attributes.get(key, "")
                if existing and existing != value:
                    if self._docx_cell_is_helper_token(existing):
                        attributes[key] = self._docx_combine_cell_texts([existing, value])
                    elif self._docx_cell_is_helper_token(value):
                        attributes[key] = self._docx_combine_cell_texts([existing, value])
                    elif existing in value:
                        attributes[key] = value
                    elif value in existing:
                        attributes[key] = existing
                    else:
                        attributes[key] = value
                else:
                    attributes[key] = value
        for idx, column in enumerate(column_schema):
            normalized = column.strip() if isinstance(column, str) else ""
            if normalized.startswith("helper_"):
                base_key = normalized[len("helper_") :].strip()
                helper_value = attributes.get(normalized, "")
                if base_key and helper_value and self._docx_cell_is_helper_token(helper_value):
                    base_value = attributes.get(base_key, "")
                    if base_value:
                        attributes[base_key] = self._docx_combine_cell_texts([helper_value, base_value])
                        attributes[normalized] = ""
        # include columns with no explicit cell entry to preserve schema ordering
        for idx, column in enumerate(column_schema):
            normalized = column.strip() if isinstance(column, str) else ""
            if not normalized:
                normalized = f"column_{idx + 1}"
            attributes.setdefault(normalized, "")
        return attributes

    @staticmethod
    def _column_values_for_quality_rows(rows: Sequence[Any], col_idx: int) -> list[str]:
        values: list[str] = []
        for row in rows:
            for cell in list(getattr(row, "cells", None) or []):
                if int(getattr(cell, "column_index", -1)) != col_idx:
                    continue
                cell_text = str(getattr(cell, "raw_text", "") or "").strip()
                if cell_text:
                    values.append(cell_text)
        return values

    @classmethod
    def _quality_misalignment_is_legitimate(
        cls,
        *,
        col_idx: int,
        header_column_coverage: Sequence[str],
        data_rows: Sequence[Any],
    ) -> bool:
        values = cls._column_values_for_quality_rows(data_rows, col_idx)
        if not values:
            return False
        labeled_other_columns = sum(
            1 for idx, value in enumerate(header_column_coverage) if idx != col_idx and str(value or "").strip()
        )
        if labeled_other_columns <= 0:
            return False

        text_like = sum(
            1
            for value in values
            if re.search(r"[A-Za-z\u0600-\u06FF]", value)
        )
        numeric_like = sum(1 for value in values if _column_numeric_signal(value) or cls._docx_cell_is_helper_token(value))

        if col_idx == 0 and text_like >= max(2, int(math.ceil(len(values) * 0.6))):
            return True
        if len(header_column_coverage) <= 2 and numeric_like >= max(2, int(math.ceil(len(values) * 0.6))):
            return True
        return False

    def _row_model_attributes(
        self,
        row: KnowledgeUploadTableRow,
        column_schema: Sequence[str],
    ) -> dict[str, str]:
        attributes: dict[str, str] = {}
        schema = list(column_schema)
        for cell in row.cells.all():
            column = cell.column_key or (schema[cell.column_index] if cell.column_index < len(schema) else "")
            key = column or f"column_{cell.column_index + 1}"
            attributes[key] = (cell.raw_text or "").strip()
        if not attributes:
            for idx, column in enumerate(schema):
                key = column or f"column_{idx + 1}"
                attributes.setdefault(key, "")
        return attributes
