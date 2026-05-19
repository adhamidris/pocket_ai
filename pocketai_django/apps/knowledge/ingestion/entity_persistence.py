from __future__ import annotations

from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.aliases import ALIAS_MAX_LENGTH
from apps.knowledge.models import (
    KnowledgeAlias,
    KnowledgeEntity,
    KnowledgeUpload,
    KnowledgeUploadChunk,
)


class IngestionEntityPersistenceMixin:

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
