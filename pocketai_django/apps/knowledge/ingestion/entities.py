from __future__ import annotations

from collections import Counter
import logging
import math
import re
from typing import Any, Mapping, Sequence

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import KnowledgeVisibility
from apps.knowledge.ingestion.contracts import TablePayload, TableRowPayload
from apps.knowledge.ingestion.entity_aliases import IngestionEntityAliasesMixin
from apps.knowledge.ingestion.entity_evidence import IngestionEntityEvidenceMixin
from apps.knowledge.ingestion.entity_payloads import IngestionEntityPayloadsMixin
from apps.knowledge.ingestion.entity_persistence import IngestionEntityPersistenceMixin
from apps.knowledge.ingestion.signals import (
    _SPREADSHEET_CONTROL_CELL_RE,
    _SPREADSHEET_MASKED_PLACEHOLDER_RE,
    _SPREADSHEET_PLACEHOLDER_CELL_RE,
    _SPREADSHEET_PURE_NUMBER_RE,
    _SPREADSHEET_RECORD_ID_RE,
    _SPREADSHEET_SUMMARY_ROW_RE,
    _SPREADSHEET_ZERO_LIKE_RE,
)
from apps.knowledge.models import KnowledgeUpload
from apps.knowledge.tables.entity_rows import IngestionTableEntityRowsMixin

logger = logging.getLogger(__name__)


class IngestionEntitiesMixin(
    IngestionTableEntityRowsMixin,
    IngestionEntityAliasesMixin,
    IngestionEntityEvidenceMixin,
    IngestionEntityPayloadsMixin,
    IngestionEntityPersistenceMixin,
):

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
