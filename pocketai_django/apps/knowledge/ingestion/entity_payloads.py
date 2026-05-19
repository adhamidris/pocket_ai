from __future__ import annotations

from typing import Any, Mapping, Sequence


class IngestionEntityPayloadsMixin:

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
