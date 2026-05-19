from __future__ import annotations

from collections import Counter
import logging
import math
from pathlib import Path
import re
import unicodedata
import uuid
from typing import Any, Mapping, Sequence

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import KnowledgeVisibility
from apps.knowledge.ingestion.aliases import (
    ALIAS_KEYWORDS,
    ALIAS_MAX_LENGTH,
    ALIAS_MIN_LENGTH,
    ALIAS_SYMBOL_MIN_LENGTH,
    DATE_TOKEN_PATTERN,
    IDENTIFIER_TOKEN_PATTERN,
    ID_LINE_PATTERN,
)
from apps.knowledge.ingestion.contracts import TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.ingestion.signals import (
    _SPREADSHEET_CONTROL_CELL_RE,
    _SPREADSHEET_MASKED_PLACEHOLDER_RE,
    _SPREADSHEET_PLACEHOLDER_CELL_RE,
    _SPREADSHEET_PURE_NUMBER_RE,
    _SPREADSHEET_RECORD_ID_RE,
    _SPREADSHEET_SUMMARY_ROW_RE,
    _SPREADSHEET_ZERO_LIKE_RE,
    _column_numeric_signal,
)
from apps.knowledge.models import (
    KnowledgeAlias,
    KnowledgeEntity,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTableRow,
)

logger = logging.getLogger(__name__)


class IngestionEntitiesMixin:


    def _table_row_entities(
        self,
        tables: Sequence[TablePayload],
        *,
        business_profile=None,
        upload: KnowledgeUpload | None = None,
    ) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        business_name = getattr(business_profile, "name", None)
        alias_hygiene = False
        spreadsheet_stats = {
            "kept": 0,
            "skipped": 0,
            "hidden_sheet": 0,
            "instruction_sheet": 0,
            "low_value": 0,
            "header_row": 0,
        }
        if upload and getattr(upload, "business_profile", None):
            alias_hygiene = FeatureFlagService.snapshot(upload.business_profile).rag_alias_hygiene
        rules = self._table_privacy_rules(upload)
        for table_idx, table in enumerate(tables):
            entity_type = self._derive_table_entity_type(table, table_idx)
            column_schema = table.column_schema or []
            spreadsheet_context = self._build_spreadsheet_entity_context(table, column_schema)
            for row in table.rows:
                entity_index = len(entities)
                attributes = self._row_attributes_from_table(row, column_schema)
                if self._row_is_internal(attributes, rules):
                    continue
                should_create, skip_reason = self._should_create_table_row_entity(
                    table=table,
                    row=row,
                    attributes=attributes,
                    spreadsheet_context=spreadsheet_context,
                )
                if not should_create:
                    if skip_reason:
                        spreadsheet_stats["skipped"] += 1
                        spreadsheet_stats[skip_reason] = spreadsheet_stats.get(skip_reason, 0) + 1
                    continue
                visible_columns = [
                    column for column in column_schema if not self._column_is_sensitive(column, rules)
                ]
                if not visible_columns:
                    continue
                limited_attributes = {column: attributes.get(column, "") for column in visible_columns}
                entity_name = self._infer_table_row_entity_name(
                    entity_type,
                    table,
                    limited_attributes,
                    row.row_index,
                )
                flattened = dict(limited_attributes)
                flattened["table_title"] = table.title or ""
                flattened["sheet_name"] = (table.metadata or {}).get("sheet_name", "")
                flattened["table_order_index"] = str(table.order_index)
                columns = self._select_entity_columns(limited_attributes)
                if not columns:
                    columns = list(limited_attributes.keys())
                limited_attributes = {column: limited_attributes.get(column, "") for column in columns}
                aliases, alias_sources = self._collect_aliases_from_record(
                    record=flattened,
                    flattened=flattened,
                    attributes=limited_attributes,
                    entity_name=entity_name,
                    alias_hygiene=alias_hygiene,
                )
                table_meta = {
                    "table_order_index": table.order_index,
                    "table_title": table.title,
                    "section_heading": table.section_heading,
                    "sheet_name": (table.metadata or {}).get("sheet_name"),
                    "sheet_role": (table.metadata or {}).get("sheet_role"),
                    "row_index": row.row_index,
                }
                entities.append(
                    {
                        "entity_type": entity_type,
                        "entity_name": entity_name,
                        "entity_business": business_name,
                        "columns": columns,
                        "attributes": limited_attributes,
                        "aliases": aliases,
                        "alias_sources": sorted(alias_sources),
                        "alias_source_type": "table",
                        "table_metadata": table_meta,
                        "chunk_strategy": "table_entity",
                        "table_metadata": table_meta,
                        "visibility": getattr(upload, "visibility", KnowledgeVisibility.PRIVATE) if upload else KnowledgeVisibility.PRIVATE,
                        "entity_index": entity_index,
                    }
                )
                if self._table_uses_spreadsheet_entity_gate(table):
                    spreadsheet_stats["kept"] += 1
        if self._table_entity_stats_active(spreadsheet_stats):
            logger.info(
                "ingest.spreadsheet_entities kept=%s skipped=%s hidden_sheet=%s instruction_sheet=%s header_row=%s low_value=%s",
                spreadsheet_stats["kept"],
                spreadsheet_stats["skipped"],
                spreadsheet_stats["hidden_sheet"],
                spreadsheet_stats["instruction_sheet"],
                spreadsheet_stats["header_row"],
                spreadsheet_stats["low_value"],
            )
        return entities

    @staticmethod
    def _table_entity_stats_active(stats: Mapping[str, int]) -> bool:
        return any(int(stats.get(key, 0) or 0) for key in ("kept", "skipped"))

    @staticmethod
    def _table_uses_spreadsheet_entity_gate(table: TablePayload) -> bool:
        source = str((table.metadata or {}).get("source") or "").lower()
        return source == "xlsx"

    def _should_create_table_row_entity(
        self,
        *,
        table: TablePayload,
        row: TableRowPayload,
        attributes: Mapping[str, str],
        spreadsheet_context: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
        row_kind = str(row_meta.get("row_kind") or "").strip().lower()
        row_type = str(row_meta.get("row_type") or "").strip().lower()
        if row_kind == "header" or row_type in {"header", "section_header"}:
            return False, "header_row"
        if not self._table_uses_spreadsheet_entity_gate(table):
            return True, None
        return self._should_create_spreadsheet_row_entity(
            table=table,
            row=row,
            attributes=attributes,
            spreadsheet_context=spreadsheet_context,
        )

    @staticmethod
    def _normalize_spreadsheet_signal_value(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    @staticmethod
    def _spreadsheet_value_is_control(value: str) -> bool:
        sample = str(value or "").strip()
        return bool(
            _SPREADSHEET_PLACEHOLDER_CELL_RE.match(sample)
            or _SPREADSHEET_CONTROL_CELL_RE.fullmatch(sample)
            or _SPREADSHEET_MASKED_PLACEHOLDER_RE.fullmatch(sample)
        )

    @staticmethod
    def _spreadsheet_value_is_non_default_numeric(value: str) -> bool:
        sample = str(value or "").strip()
        return bool(_SPREADSHEET_PURE_NUMBER_RE.fullmatch(sample) and not _SPREADSHEET_ZERO_LIKE_RE.fullmatch(sample))

    def _build_spreadsheet_entity_context(
        self,
        table: TablePayload,
        column_schema: Sequence[str],
    ) -> Mapping[str, Any] | None:
        if not self._table_uses_spreadsheet_entity_gate(table):
            return None
        repeated_text_counts: Counter[str] = Counter()
        rows = table.rows or []
        for row in rows:
            attributes = self._row_attributes_from_table(row, column_schema)
            seen_text_values: set[str] = set()
            for raw_value in attributes.values():
                value = str(raw_value or "").strip()
                if not value:
                    continue
                if self._spreadsheet_value_is_non_default_numeric(value):
                    continue
                if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value):
                    continue
                if self._spreadsheet_value_is_control(value):
                    continue
                if _SPREADSHEET_RECORD_ID_RE.fullmatch(value):
                    continue
                seen_text_values.add(self._normalize_spreadsheet_signal_value(value))
            repeated_text_counts.update(seen_text_values)
        row_count = max(len(rows), 1)
        boilerplate_threshold = max(3, math.ceil(row_count * 0.2))
        boilerplate_texts = {
            value for value, count in repeated_text_counts.items() if count >= boilerplate_threshold
        }
        return {
            "boilerplate_texts": boilerplate_texts,
            "row_count": row_count,
            "boilerplate_threshold": boilerplate_threshold,
        }

    def _should_create_spreadsheet_row_entity(
        self,
        *,
        table: TablePayload,
        row: TableRowPayload,
        attributes: Mapping[str, str],
        spreadsheet_context: Mapping[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        table_meta = table.metadata or {}
        row_meta = row.metadata or {}
        sheet_role = str(table_meta.get("sheet_role") or "")
        if sheet_role == "reference_hidden" or bool(table_meta.get("sheet_hidden")):
            return False, "hidden_sheet"
        if sheet_role == "instructional":
            return False, "instruction_sheet"

        values = [str(value or "").strip() for value in attributes.values() if str(value or "").strip()]
        if not values:
            return False, "low_value"

        descriptor = values[0]
        zero_like_count = sum(1 for value in values if _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value))
        placeholder_count = sum(1 for value in values if _SPREADSHEET_PLACEHOLDER_CELL_RE.match(value))
        control_count = sum(1 for value in values if self._spreadsheet_value_is_control(value))
        identifier_like = bool(values and _SPREADSHEET_RECORD_ID_RE.fullmatch(values[0]))
        summary_like = any(_SPREADSHEET_SUMMARY_ROW_RE.search(value) for value in values)
        guidance_like = self._spreadsheet_row_looks_guidance(values)
        meaningful_values = [
            value
            for value in values
            if not _SPREADSHEET_ZERO_LIKE_RE.fullmatch(value)
            and not self._spreadsheet_value_is_control(value)
        ]
        boilerplate_texts = set((spreadsheet_context or {}).get("boilerplate_texts") or [])
        substantive_text_values: list[str] = []
        substantive_text_norms: set[str] = set()
        substantive_long_text_count = 0
        substantive_numeric_count = 0
        for value in meaningful_values:
            if self._spreadsheet_value_is_non_default_numeric(value):
                substantive_numeric_count += 1
                continue
            normalized = self._normalize_spreadsheet_signal_value(value)
            if normalized in boilerplate_texts:
                continue
            if normalized in substantive_text_norms:
                continue
            substantive_text_norms.add(normalized)
            substantive_text_values.append(value)
            if len(value) >= 24 or len(value.split()) >= 4:
                substantive_long_text_count += 1
        if guidance_like:
            return False, "low_value"
        if identifier_like or summary_like:
            return True, None
        if sheet_role == "summary":
            if substantive_numeric_count >= 1 or len(substantive_text_values) >= 2:
                return True, None
            return False, "low_value"
        if not identifier_like and placeholder_count >= 1 and zero_like_count >= 2 and len(substantive_text_values) <= 1 and substantive_numeric_count == 0:
            return False, "low_value"
        if _SPREADSHEET_PLACEHOLDER_CELL_RE.match(descriptor) and zero_like_count >= 2 and placeholder_count >= 1:
            return False, "low_value"
        if placeholder_count >= 1 and zero_like_count >= 3 and len(meaningful_values) <= 3:
            return False, "low_value"
        if len(values) >= 5 and zero_like_count >= max(3, len(values) // 2) and len(meaningful_values) <= 2:
            return False, "low_value"
        if row_meta.get("row_kind") in {"default_zero_row", "placeholder_row", "scaffold_row"}:
            return False, "low_value"
        if _SPREADSHEET_PLACEHOLDER_CELL_RE.match(descriptor) and len(meaningful_values) <= 3:
            return False, "low_value"
        if sheet_role == "transactional" and substantive_numeric_count == 0:
            if substantive_long_text_count >= 1:
                return True, None
            if len(substantive_text_values) < 3:
                return False, "low_value"
        if control_count >= 2 and zero_like_count >= 2 and substantive_numeric_count == 0 and len(substantive_text_values) <= 1:
            return False, "low_value"
        return True, None

    @staticmethod
    def _derive_table_entity_type(table: TablePayload, index: int) -> str:
        candidate = (
            (table.metadata or {}).get("entity_type")
            or (table.metadata or {}).get("sheet_name")
            or table.section_heading
            or table.title
            or f"table_{index + 1}"
        )
        normalized = re.sub(r"[^a-z0-9]+", "_", (candidate or "").lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized or "table_row"

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
        if current and not IngestionEntitiesMixin._GENERIC_TABLE_TITLE_RE.match(current):
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

    @staticmethod
    def _infer_table_row_entity_name(
        entity_type: str,
        table: TablePayload,
        attributes: Mapping[str, str],
        row_index: int,
    ) -> str:
        priority_keys = (
            "name",
            "title",
            "plan",
            "product",
            "sku",
            "code",
            "id",
            "identifier",
            "slug",
            "trip",
            "customer",
        )
        for key in priority_keys:
            value = attributes.get(key)
            if value:
                return value
        for column in table.column_schema:
            if not column:
                continue
            value = attributes.get(column)
            if value:
                return value
        for value in attributes.values():
            if value:
                return value
        label = entity_type.replace("_", " ").title() or "Row"
        return f"{label} {row_index}"

    @staticmethod
    def _infer_entity_name(record_label: str, record: Mapping[str, Any], flattened: Mapping[str, str], index: int) -> str:
        candidate_keys = ("name", "title", "destination", "city", "label", "slug")
        for key in candidate_keys:
            value = record.get(key) if isinstance(record, Mapping) else None
            if isinstance(value, str) and value.strip():
                return value.strip()
            if key in flattened and flattened[key]:
                return flattened[key]
        fallback = record.get("id") if isinstance(record, Mapping) else None
        if fallback:
            return str(fallback)
        label = record_label.rstrip("s") or record_label or "record"
        return f"{label.title()} {index + 1}"

    @staticmethod
    def _select_entity_columns(flattened: Mapping[str, str]) -> list[str]:
        if not flattened:
            return []
        preferred = [
            "name",
            "title",
            "destination",
            "slug",
            "city",
            "region",
            "trip_count",
            "trip_titles",
            "trip_prices",
            "adult_price_per_person",
            "child_price_per_person",
            "currency",
            "business",
            "duration_days",
        ]
        columns: list[str] = []
        seen: set[str] = set()
        for key in preferred:
            if key in flattened and key not in seen and flattened[key]:
                columns.append(key)
                seen.add(key)
        for key, value in flattened.items():
            if key in seen:
                continue
            if value:
                columns.append(key)
                seen.add(key)
        if not columns:
            columns = list(flattened.keys())
        return columns


    @staticmethod
    def _render_json_entity_summary(
        *,
        entity_title: str,
        entity_type: str,
        column_schema: Sequence[str],
        attributes: Mapping[str, str],
    ) -> str:
        lines = [f"{entity_type.title()}: {entity_title}"]
        for column in column_schema[:8]:
            value = attributes.get(column)
            if value:
                lines.append(f"- {column}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _looks_like_identifier(candidate: str) -> bool:
        if not candidate:
            return False
        token = candidate.strip()
        if not token:
            return False
        lowered = token.lower()
        if len(lowered) >= ALIAS_MIN_LENGTH:
            return True
        if len(lowered) >= ALIAS_SYMBOL_MIN_LENGTH and any(ch in "-_0123456789" for ch in lowered):
            return True
        return bool(IDENTIFIER_TOKEN_PATTERN.fullmatch(lowered))

    @staticmethod
    def _is_noisy_identifier(candidate: str) -> bool:
        if not candidate:
            return False
        token = candidate.strip()
        if not token:
            return False
        if DATE_TOKEN_PATTERN.search(token):
            return True
        if re.fullmatch(r"[\d\s\-]+", token):
            digits = re.sub(r"\D", "", token)
            if 13 <= len(digits) <= 19:
                return True
        return False

    @staticmethod
    def _normalize_alias_value(value: str) -> str:
        if not value:
            return ""
        normalized = re.sub(r"\s+", "-", value.strip().lower())
        normalized = re.sub(r"-{2,}", "-", normalized)
        normalized = normalized.strip("-")
        if len(normalized) > ALIAS_MAX_LENGTH:
            normalized = normalized[:ALIAS_MAX_LENGTH]
        return normalized

    @staticmethod
    def _collect_aliases_from_record(
        *,
        record: Mapping[str, Any],
        flattened: Mapping[str, str],
        attributes: Mapping[str, str],
        entity_name: str,
        alias_hygiene: bool = False,
    ) -> tuple[list[str], set[str]]:
        alias_candidates: list[str] = []
        alias_sources: set[str] = set()
        seen: set[str] = set()

        def maybe_add(value: Any, source: str) -> None:
            if not isinstance(value, str):
                return
            candidate = value.strip()
            if not candidate:
                return
            if len(candidate) > ALIAS_MAX_LENGTH:
                candidate = candidate[:ALIAS_MAX_LENGTH]
            if alias_hygiene and IngestionEntitiesMixin._is_noisy_identifier(candidate):
                return
            if not IngestionEntitiesMixin._looks_like_identifier(candidate):
                return
            lowered = candidate.lower()
            if lowered in seen:
                return
            seen.add(lowered)
            alias_candidates.append(candidate)
            alias_sources.add(source)

        maybe_add(entity_name, "entity_name")
        for key in ALIAS_KEYWORDS:
            maybe_add(record.get(key), f"record_{key}")
        for key, value in flattened.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"flattened_{key}")
        for key, value in attributes.items():
            key_lower = key.lower()
            if any(keyword in key_lower for keyword in ALIAS_KEYWORDS):
                maybe_add(value, f"attribute_{key}")
        # Include values that look like identifiers even if the key didn't match
        for value in flattened.values():
            if isinstance(value, str) and IngestionEntitiesMixin._looks_like_identifier(value):
                maybe_add(value, "inline_pattern")
        return alias_candidates[:8], alias_sources

    @staticmethod
    def _alias_metadata(aliases: Sequence[str]) -> dict[str, Any]:
        normalized: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            if not alias:
                continue
            trimmed = alias.strip()
            if not trimmed:
                continue
            if len(trimmed) > ALIAS_MAX_LENGTH:
                trimmed = trimmed[:ALIAS_MAX_LENGTH]
            lower = trimmed.lower()
            if lower in seen:
                continue
            seen.add(lower)
            normalized.append(trimmed)
        if not normalized:
            return {}
        alias_string = " ".join(sorted(seen))
        return {"aliases": normalized, "alias_string": alias_string}

    @staticmethod
    def _append_identifier_line(text: str, aliases: Sequence[str]) -> str:
        alias_list = [alias for alias in aliases if alias]
        if not alias_list:
            return text
        if "Identifiers:" in text:
            return text
        suffix = "Identifiers: " + ", ".join(alias_list[:6])
        return f"{text.rstrip()}\n{suffix}"

    def _extract_inline_identifiers(self, text: str, *, alias_hygiene: bool = False) -> list[str]:
        if not text:
            return []
        aliases: list[str] = []
        seen: set[str] = set()
        for match in IDENTIFIER_TOKEN_PATTERN.finditer(text.lower()):
            alias = match.group().strip()
            if not alias:
                continue
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            if alias_hygiene and self._is_noisy_identifier(alias):
                continue
            if not self._looks_like_identifier(alias):
                continue
            if alias not in seen:
                seen.add(alias)
                aliases.append(alias)
        for match in ID_LINE_PATTERN.finditer(text):
            alias = match.group(1).strip()
            if len(alias) > ALIAS_MAX_LENGTH:
                alias = alias[:ALIAS_MAX_LENGTH]
            alias_lower = alias.lower()
            if alias_hygiene and self._is_noisy_identifier(alias):
                continue
            if alias_lower and alias_lower not in seen and self._looks_like_identifier(alias):
                seen.add(alias_lower)
                aliases.append(alias)
        return aliases[:6]

    def _inject_identifiers_into_text(
        self,
        text: str,
        *,
        alias_hygiene: bool = False,
    ) -> tuple[str, list[str]]:
        aliases = self._extract_inline_identifiers(text, alias_hygiene=alias_hygiene)
        if aliases:
            text = self._append_identifier_line(text, aliases)
        return text, aliases

    @staticmethod
    def _finalize_alias_metadata(metadata: dict[str, Any]) -> None:
        aliases = metadata.get("aliases")
        if not aliases:
            metadata.pop("alias_string", None)
            return
        normalized: list[str] = []
        seen: set[str] = set()
        for alias in aliases if isinstance(aliases, (list, tuple)) else [aliases]:
            if not alias:
                continue
            trimmed = str(alias).strip()
            if not trimmed:
                continue
            if len(trimmed) > ALIAS_MAX_LENGTH:
                trimmed = trimmed[:ALIAS_MAX_LENGTH]
            lower = trimmed.lower()
            if lower in seen:
                continue
            seen.add(lower)
            normalized.append(trimmed)
        metadata["aliases"] = normalized
        metadata["alias_string"] = " ".join(sorted(seen))



    @staticmethod
    def _representation_from_metadata(metadata: Mapping[str, Any]) -> str:
        if not metadata:
            return "text"
        if metadata.get("is_table_chunk"):
            return "table"
        index_type = str(metadata.get("index_type") or "").strip().lower()
        if index_type == "entity":
            return "json"
        if index_type == "table":
            return "table"
        return "text"

    @staticmethod
    def _normalize_evidence_phrase(value: str, *, max_tokens: int = 24) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        normalized = normalized.lower()
        normalized = re.sub(r"[_/\-]+", " ", normalized)
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return ""
        tokens = normalized.split(" ")
        if len(tokens) > max_tokens:
            tokens = tokens[:max_tokens]
        return " ".join(tokens)

    @staticmethod
    def _evidence_group_id_for_key(upload_id: uuid.UUID, key: str) -> str:
        namespace = uuid.UUID(str(upload_id))
        stable_key = key or "empty"
        return str(uuid.uuid5(namespace, stable_key))

    def _payload_primary_evidence_key(
        self,
        *,
        payload_text: str,
        metadata: Mapping[str, Any],
    ) -> str:
        representation = self._representation_from_metadata(metadata)
        table_id = str(metadata.get("table_id") or "").strip()
        table_role = str(metadata.get("table_chunk_role") or "").strip().lower()

        if representation == "table":
            row_index = metadata.get("table_row_index")
            if table_id and row_index is not None:
                return f"table_row:{table_id}:{row_index}"
            row_label = self._normalize_evidence_phrase(str(metadata.get("row_label") or ""))
            if table_id and row_label:
                return f"table_row_label:{table_id}:{row_label}"
            if row_label:
                return f"table_row_label:{row_label}"
            if table_id and table_role:
                return f"table:{table_id}:{table_role}"
            if table_id:
                return f"table:{table_id}"

        if representation == "json":
            entity_name = self._normalize_evidence_phrase(str(metadata.get("entity_name") or ""))
            if entity_name:
                return f"entity:{entity_name}"
            entity_index = metadata.get("entity_index")
            if entity_index is not None:
                return f"entity_index:{entity_index}"

        page_anchor = str(metadata.get("page_anchor") or "").strip()
        anchors = metadata.get("block_anchors")
        if isinstance(anchors, list) and len(anchors) == 1 and anchors[0]:
            anchor_value = str(anchors[0]).strip()
            if anchor_value:
                return f"text_anchor:{anchor_value}"
        if page_anchor:
            normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=40)
            if normalized_text:
                return f"text_page:{page_anchor}:{normalized_text[:180]}"
            return f"text_page:{page_anchor}"

        normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=48)
        if normalized_text:
            return f"text:{normalized_text[:220]}"
        return "text:empty"

    def _assign_evidence_group_metadata(
        self,
        *,
        upload: KnowledgeUpload,
        segment_payloads: Sequence[dict[str, Any]],
    ) -> None:
        if not segment_payloads:
            return

        table_label_groups: dict[str, set[str]] = {}
        prepared: list[tuple[dict[str, Any], dict[str, Any], str, str, str]] = []

        for payload in segment_payloads:
            if not isinstance(payload, dict):
                continue
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                payload["metadata"] = metadata
            payload_text = str(payload.get("text") or "")
            representation = self._representation_from_metadata(metadata)
            evidence_type = str(
                metadata.get("content_source") or metadata.get("index_type") or representation
            ).strip().lower() or representation
            metadata["representation"] = representation
            metadata["evidence_type"] = evidence_type
            evidence_key = self._payload_primary_evidence_key(payload_text=payload_text, metadata=metadata)
            prepared.append((payload, metadata, representation, evidence_key, payload_text))

            if representation != "table":
                continue
            evidence_group_id = self._evidence_group_id_for_key(upload.id, evidence_key)
            metadata["evidence_group_id"] = evidence_group_id
            metadata["evidence_key"] = evidence_key
            row_label = self._normalize_evidence_phrase(str(metadata.get("row_label") or ""))
            if row_label:
                table_label_groups.setdefault(row_label, set()).add(evidence_group_id)

        if not prepared:
            return

        unambiguous_table_label_groups = {
            label: next(iter(group_ids))
            for label, group_ids in table_label_groups.items()
            if len(group_ids) == 1
        }
        sorted_table_labels = sorted(unambiguous_table_label_groups.keys(), key=len, reverse=True)

        for _, metadata, representation, evidence_key, payload_text in prepared:
            if representation == "table":
                continue

            linked_group_id = ""
            if representation == "text":
                line_count = len([line for line in payload_text.splitlines() if line.strip()])
                if not line_count:
                    line_count = 1 if payload_text.strip() else 0
                if (
                    payload_text
                    and len(payload_text) <= self.evidence_text_link_max_chars
                    and line_count <= self.evidence_text_link_max_lines
                    and sorted_table_labels
                ):
                    normalized_text = self._normalize_evidence_phrase(payload_text, max_tokens=120)
                    if normalized_text:
                        haystack = f" {normalized_text} "
                        for label in sorted_table_labels:
                            if len(label) < 6:
                                continue
                            if label not in unambiguous_table_label_groups:
                                continue
                            if f" {label} " in haystack:
                                linked_group_id = unambiguous_table_label_groups[label]
                                metadata["evidence_linked_label"] = label
                                evidence_key = f"linked_table_label:{label}"
                                break

            metadata["evidence_key"] = evidence_key
            metadata["evidence_group_id"] = linked_group_id or self._evidence_group_id_for_key(upload.id, evidence_key)

    def _build_entity_segment_payloads(
        self,
        entities: Sequence[Mapping[str, Any]],
        *,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        compact_groups: dict[tuple[str, str, str], list[tuple[int, Mapping[str, Any]]]] = {}
        compact_roles = {"summary", "form_like"}
        for index, entity in enumerate(entities):
            table_meta = entity.get("table_metadata")
            sheet_role = ""
            sheet_name = ""
            table_title = ""
            if isinstance(table_meta, dict):
                sheet_role = str(table_meta.get("sheet_role") or "")
                sheet_name = str(table_meta.get("sheet_name") or "")
                table_title = str(table_meta.get("table_title") or "")
            if sheet_role in compact_roles:
                compact_groups.setdefault((sheet_role, sheet_name, table_title), []).append((index, entity))
                continue
            payloads.append(
                self._build_single_entity_segment_payload(
                    entity,
                    entity_index=index,
                    alias_hygiene=alias_hygiene,
                )
            )
        for (sheet_role, sheet_name, table_title), grouped_entities in compact_groups.items():
            payloads.extend(
                self._build_compact_spreadsheet_entity_payloads(
                    grouped_entities,
                    sheet_role=sheet_role,
                    sheet_name=sheet_name,
                    table_title=table_title,
                    alias_hygiene=alias_hygiene,
                )
            )
        return payloads

    def _build_single_entity_segment_payload(
        self,
        entity: Mapping[str, Any],
        *,
        entity_index: int,
        alias_hygiene: bool = False,
    ) -> dict[str, Any]:
        attributes = entity.get("attributes") or {}
        columns = entity.get("columns") or []
        alias_list = list(entity.get("aliases") or [])
        entity_type = entity.get("entity_type") or "record"
        entity_name = entity.get("entity_name") or f"{entity_type.title()} {entity_index + 1}"
        lines = [f"{entity_type.title()}: {entity_name}"]
        for column in columns[:16]:
            value = attributes.get(column)
            if value:
                lines.append(f"- {column}: {value}")
        text = "\n".join(lines).strip()
        text, inline_aliases = self._inject_identifiers_into_text(text, alias_hygiene=alias_hygiene)
        combined_aliases = alias_list[:]
        for alias in inline_aliases:
            if alias and alias not in combined_aliases:
                combined_aliases.append(alias)
        metadata: dict[str, Any] = {
            "strategy": entity.get("chunk_strategy") or "json_entity",
            "index_type": "entity",
            "entity_type": entity_type,
            "entity_name": entity_name,
            "entity_business": entity.get("entity_business"),
            "entity_index": entity.get("entity_index", entity_index),
            "visibility": entity.get("visibility") or entity.get("entity_visibility"),
        }
        table_meta = entity.get("table_metadata")
        if isinstance(table_meta, dict):
            metadata["table_metadata"] = table_meta
            metadata["sheet_role"] = table_meta.get("sheet_role")
            metadata["sheet_name"] = table_meta.get("sheet_name")
            metadata["table_title"] = table_meta.get("table_title")
        metadata.update(self._alias_metadata(combined_aliases))
        return {"text": text, "metadata": metadata}

    def _build_compact_spreadsheet_entity_payloads(
        self,
        entities: Sequence[tuple[int, Mapping[str, Any]]],
        *,
        sheet_role: str,
        sheet_name: str,
        table_title: str,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        compact_payloads: list[dict[str, Any]] = []
        if not entities:
            return compact_payloads

        shard_size = 8 if sheet_role == "form_like" else 10
        label = table_title or sheet_name or "Spreadsheet Sheet"
        for shard_index in range(0, len(entities), shard_size):
            shard = entities[shard_index : shard_index + shard_size]
            first_payload = shard[0][1]
            lines = [f"{sheet_role.replace('_', ' ').title()}: {label}"]
            alias_values: list[str] = []
            entity_names: list[str] = []
            for _, entity in shard:
                attributes = entity.get("attributes") or {}
                columns = entity.get("columns") or []
                entity_name = str(entity.get("entity_name") or "").strip() or "Row"
                entity_names.append(entity_name)
                detail_parts = [entity_name]
                for column in columns[:6]:
                    value = str(attributes.get(column) or "").strip()
                    if value:
                        detail_parts.append(f"{column}: {value}")
                lines.append(f"- {' | '.join(detail_parts)}")
                for alias in entity.get("aliases") or []:
                    cleaned = str(alias or "").strip()
                    if cleaned and cleaned not in alias_values:
                        alias_values.append(cleaned)

            text = "\n".join(lines).strip()
            text, inline_aliases = self._inject_identifiers_into_text(text, alias_hygiene=alias_hygiene)
            for alias in inline_aliases:
                if alias and alias not in alias_values:
                    alias_values.append(alias)

            metadata: dict[str, Any] = {
                "strategy": "table_entity_compact",
                "index_type": "entity",
                "entity_type": first_payload.get("entity_type") or "record",
                "entity_name": label,
                "entity_business": first_payload.get("entity_business"),
                "visibility": first_payload.get("visibility") or first_payload.get("entity_visibility"),
                "sheet_role": sheet_role,
                "sheet_name": sheet_name,
                "table_title": table_title,
                "table_entity_compact": True,
                "entity_names": entity_names,
                "entity_count": len(shard),
                "compact_shard_index": (shard_index // shard_size) + 1,
            }
            table_meta = first_payload.get("table_metadata")
            if isinstance(table_meta, dict):
                metadata["table_metadata"] = table_meta
            metadata.update(self._alias_metadata(alias_values))
            compact_payloads.append({"text": text, "metadata": metadata})
        return compact_payloads

    def _persist_entities(
        self,
        upload: KnowledgeUpload,
        entities: Sequence[Mapping[str, Any]],
        chunks: Sequence[KnowledgeUploadChunk],
    ) -> dict[str, Any]:
        KnowledgeEntity.objects.filter(upload=upload).delete()
        chunk_by_index: dict[int, KnowledgeUploadChunk] = {}
        for chunk in chunks:
            metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            idx = metadata.get("entity_index")
            if isinstance(idx, int):
                chunk_by_index[idx] = chunk
        business = upload.business_profile
        entity_models: list[KnowledgeEntity] = []
        for entity in entities:
            idx = entity.get("entity_index")
            chunk = chunk_by_index.get(idx) if isinstance(idx, int) else None
            raw_entity_type = entity.get("entity_type") or ""
            raw_entity_name = entity.get("entity_name") or ""
            raw_primary_label = raw_entity_name or raw_entity_type or ""
            entity_type = self._clamp_model_field_text(KnowledgeEntity, "entity_type", raw_entity_type)
            entity_name = self._clamp_model_field_text(KnowledgeEntity, "entity_name", raw_entity_name)
            primary_label = self._clamp_model_field_text(
                KnowledgeEntity,
                "primary_label",
                raw_primary_label,
            )
            entity_metadata = {
                "attributes": entity.get("attributes"),
                "columns": entity.get("columns"),
                "table_metadata": entity.get("table_metadata"),
            }
            if raw_entity_type and raw_entity_type != entity_type:
                entity_metadata["entity_type_truncated"] = raw_entity_type
            if raw_entity_name and raw_entity_name != entity_name:
                entity_metadata["entity_name_truncated"] = raw_entity_name
            if raw_primary_label and raw_primary_label != primary_label:
                entity_metadata["primary_label_truncated"] = raw_primary_label
            entity_model = KnowledgeEntity(
                business_profile=business,
                upload=upload,
                chunk_id=chunk.id if chunk else None,
                entity_type=entity_type,
                entity_name=entity_name,
                primary_label=primary_label,
                metadata=entity_metadata,
            )
            entity_models.append(entity_model)
        KnowledgeEntity.objects.bulk_create(entity_models, batch_size=200)

        alias_models: list[KnowledgeAlias] = []
        alias_sources: set[str] = set()
        alias_values: list[str] = []
        for model, payload in zip(entity_models, entities):
            payload_sources = payload.get("alias_sources") or []
            alias_sources.update(payload_sources)
            seen_aliases: set[str] = set()
            for alias in payload.get("aliases") or []:
                if not alias:
                    continue
                cleaned = str(alias).strip()
                if not cleaned:
                    continue
                if len(cleaned) > ALIAS_MAX_LENGTH:
                    cleaned = cleaned[:ALIAS_MAX_LENGTH]
                normalized = self._normalize_alias_value(cleaned)
                if not normalized or normalized in seen_aliases:
                    continue
                seen_aliases.add(normalized)
                alias_models.append(
                    KnowledgeAlias(
                        business_profile=business,
                        entity=model,
                        alias_raw=cleaned,
                        alias_normalized=normalized,
                        alias_search_vector=normalized.replace("-", " "),
                        source=payload.get("alias_source_type") or "json",
                    )
                )
                if len(alias_values) < 2048:
                    alias_values.append(normalized)
        if alias_models:
            KnowledgeAlias.objects.bulk_create(alias_models, batch_size=500)
            self._invalidate_alias_cache(business.id)
        return {
            "entity_count": len(entity_models),
            "alias_count": len(alias_models),
            "alias_sources": sorted(alias_sources),
            "alias_values": alias_values,
        }
