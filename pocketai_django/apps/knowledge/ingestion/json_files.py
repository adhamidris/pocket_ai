from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import (
    ExtractionResult,
    IssuePayload,
    KnowledgeIngestionError,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
from apps.knowledge.models import KnowledgeUpload


class IngestionJsonFilesMixin:

    def _json_entity_limit(self, business_profile) -> int:
        if not business_profile:
            return self.default_json_entity_limit
        metadata = business_profile.metadata if isinstance(getattr(business_profile, "metadata", None), dict) else {}
        override = metadata.get("ingest_max_json_entities")
        try:
            value = int(override)
            return max(1, value)
        except (TypeError, ValueError):
            return self.default_json_entity_limit


    def _extract_json(
        self,
        path: Path,
        *,
        entity_limit: int | None = None,
        upload: KnowledgeUpload | None = None,
    ) -> ExtractionResult:
        raw_text = self._extract_text_file(path)
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise KnowledgeIngestionError(f"Invalid JSON document: {exc}") from exc

        alias_hygiene = False
        if upload and getattr(upload, "business_profile", None):
            alias_hygiene = FeatureFlagService.snapshot(upload.business_profile).rag_alias_hygiene
        entities, alias_sources = self._json_entities_from_data(data, alias_hygiene=alias_hygiene)
        if not entities:
            return ExtractionResult(
                text=raw_text,
                format_hint="json",
                metadata={"json_entity_count": 0},
                pages=[],
                tables=[],
                issues=[
                    IssuePayload(
                        code="json_entities_not_detected",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description="JSON document did not contain a recognizable list of entities.",
                    )
                ],
            )

        limit = max(1, int(entity_limit or self.default_json_entity_limit))
        limited_entities = entities[: limit]
        issues: list[IssuePayload] = []
        truncated_count = max(0, len(entities) - len(limited_entities))
        if truncated_count:
            issues.append(
                IssuePayload(
                    code="json_entities_truncated",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description=f"Captured first {limit} entities out of {len(entities)}.",
                )
            )

        tables: list[TablePayload] = []
        summaries: list[str] = []
        for order_index, entity in enumerate(limited_entities, start=1):
            column_schema = entity["columns"]
            attributes = entity["attributes"]
            entity_name = entity["entity_name"]
            entity_type = entity["entity_type"]
            aliases = entity.get("aliases") or []

            cells = [
                TableCellPayload(
                    row_index=0,
                    column_index=col_idx,
                    column_key=column,
                    raw_text=attributes.get(column, ""),
                    metadata={},
                )
                for col_idx, column in enumerate(column_schema)
            ]
            row = TableRowPayload(
                row_index=0,
                page_number=None,
                raw_text=" | ".join(
                    f"{column}: {attributes.get(column, '')}" for column in column_schema if attributes.get(column)
                ),
                metadata={"entity_name": entity_name, **self._alias_metadata(aliases)},
                cells=cells,
            )
            tables.append(
                TablePayload(
                    order_index=order_index,
                    title=entity_name or f"{entity_type.title()} {order_index}",
                    section_heading=entity_type.title(),
                    page_number=None,
                    column_schema=column_schema,
                    metadata={
                        "entity_type": entity_type,
                        "entity_name": entity_name,
                        "entity_business": entity["entity_business"],
                        "json_entity": True,
                        **self._alias_metadata(aliases),
                    },
                    rows=[row],
                )
            )
            summary_text = self._render_json_entity_summary(
                entity_title=entity_name or f"{entity_type.title()} {order_index}",
                entity_type=entity_type,
                column_schema=column_schema,
                attributes=attributes,
            )
            if aliases:
                summary_text = self._append_identifier_line(summary_text, aliases)
            summaries.append(summary_text)

        text = "\n\n".join(summaries) if summaries else raw_text
        metadata = {
            "json_entity_count": len(entities),
            "json_entities_indexed": len(limited_entities),
            "json_entity_type": limited_entities[0]["entity_type"] if limited_entities else "record",
            "json_entity_limit": limit,
            "json_entities_truncated": truncated_count,
            "json_alias_sources": sorted(alias_sources),
        }
        return ExtractionResult(
            text=text,
            format_hint="json",
            metadata=metadata,
            pages=[],
            tables=tables,
            issues=issues,
            entities=limited_entities,
        )

    def _json_entities_from_data(
        self,
        data: Any,
        *,
        alias_hygiene: bool = False,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        entities: list[dict[str, Any]] = []
        alias_sources_union: set[str] = set()
        for record_label, record in self._iter_json_entity_records(data):
            flattened = self._flatten_json_record(record)
            if not flattened or not self._is_structured_record(record, flattened):
                continue
            entity_index = len(entities)
            entity_name = self._infer_entity_name(record_label, record, flattened, entity_index)
            entity_business = (
                record.get("business")
                or record.get("company")
                or record.get("brand")
                or flattened.get("business")
                or flattened.get("company")
            )
            columns = self._select_entity_columns(flattened)
            if not columns:
                columns = list(flattened.keys())
            attributes = {column: flattened.get(column, "") for column in columns}
            aliases, alias_sources = self._collect_aliases_from_record(
                record=record,
                flattened=flattened,
                attributes=attributes,
                entity_name=entity_name,
                alias_hygiene=alias_hygiene,
            )
            alias_sources_union.update(alias_sources)
            entities.append(
                {
                    "entity_type": (record_label.rstrip("s") or record_label or "record").lower(),
                    "entity_name": entity_name,
                    "entity_business": entity_business,
                    "columns": columns,
                    "attributes": attributes,
                    "aliases": aliases,
                    "alias_sources": sorted(alias_sources),
                    "entity_index": entity_index,
                }
            )
            if len(entities) >= self.max_json_entity_candidates:
                break
        return entities, alias_sources_union

    def _iter_json_entity_records(self, data: Any) -> Iterable[tuple[str, Mapping[str, Any]]]:
        queue: deque[tuple[str, Any]] = deque()
        if isinstance(data, dict):
            queue.append(("record", data))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    queue.append(("record", item))

        seen_ids: set[int] = set()
        while queue and len(seen_ids) < self.max_json_entity_candidates:
            label, record = queue.popleft()
            if not isinstance(record, dict):
                continue
            marker = id(record)
            if marker in seen_ids:
                continue
            seen_ids.add(marker)
            yield label, record
            if len(seen_ids) >= self.max_json_entity_candidates:
                break
            for key, value in record.items():
                next_label = key.rstrip("s") or key or label
                if isinstance(value, dict):
                    queue.append((next_label, value))
                elif isinstance(value, list):
                    for entry in value:
                        if isinstance(entry, dict):
                            queue.append((next_label, entry))

    @staticmethod
    def _flatten_json_record(record: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}

        def visit(prefix: str, value: Any) -> None:
            key_prefix = prefix.strip(".")
            if isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    if sub_key is None:
                        continue
                    next_prefix = f"{prefix}.{sub_key}" if prefix else str(sub_key)
                    visit(next_prefix, sub_val)
            elif isinstance(value, list):
                if not value:
                    result[key_prefix] = ""
                    return
                scalar_items = [item for item in value if isinstance(item, (str, int, float, bool))]
                if scalar_items and len(scalar_items) == len(value):
                    joined = ", ".join(IngestionJsonFilesMixin._stringify_json_scalar(item) for item in scalar_items[:5])
                    if len(value) > 5:
                        joined = f"{joined} …"
                    result[key_prefix] = joined
                    return
                dict_items = [item for item in value if isinstance(item, dict)]
                if dict_items:
                    result[f"{key_prefix}_count"] = str(len(dict_items))
                    first = dict_items[0]
                    for sub_key, sub_val in list(first.items())[:3]:
                        nested_key = f"{key_prefix}_0_{sub_key}".strip("_")
                        result[nested_key] = IngestionJsonFilesMixin._stringify_json_scalar(sub_val)
                    return
                result[key_prefix] = IngestionJsonFilesMixin._stringify_json_scalar(value)
            else:
                result[key_prefix] = IngestionJsonFilesMixin._stringify_json_scalar(value)

        visit("", record)

        trips = record.get("trips")
        if isinstance(trips, list):
            result["trip_count"] = str(len(trips))
            trip_titles = [
                item.get("title")
                for item in trips
                if isinstance(item, dict) and isinstance(item.get("title"), str)
            ]
            if trip_titles:
                result["trip_titles"] = "; ".join(trip_titles[:3])
            prices: list[str] = []
            for trip in trips:
                if not isinstance(trip, dict):
                    continue
                pricing = trip.get("pricing")
                if isinstance(pricing, dict):
                    price = (
                        pricing.get("adult_price_per_person")
                        or pricing.get("price")
                        or pricing.get("starts_at")
                    )
                    if price:
                        prices.append(IngestionJsonFilesMixin._stringify_json_scalar(price))
                if len(prices) >= 3:
                    break
            if prices:
                result["trip_prices"] = ", ".join(prices)

        return {k: v for k, v in result.items() if k and v is not None}

    @staticmethod
    def _is_structured_record(record: Mapping[str, Any], flattened: Mapping[str, str]) -> bool:
        if not flattened:
            return False
        key_hints = (
            "name",
            "title",
            "slug",
            "label",
            "code",
            "id",
            "identifier",
            "question",
            "destination",
            "city",
            "product",
            "sku",
            "reference",
        )
        for key in key_hints:
            direct = record.get(key) if isinstance(record, Mapping) else None
            indirect = flattened.get(key)
            value = direct or indirect
            if isinstance(value, str) and value.strip():
                return True
            if isinstance(value, (int, float)):
                return True
        populated = sum(1 for value in flattened.values() if value)
        return populated >= 2

    @staticmethod
    def _stringify_json_scalar(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value.strip()
        return json.dumps(value, ensure_ascii=False)[:500]
