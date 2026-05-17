from __future__ import annotations

from typing import Mapping

from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadChunk


class KnowledgeMetadataMixin:
    @staticmethod
    def _is_legacy_pdf_table_entity_chunk(chunk: KnowledgeUploadChunk) -> bool:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        if metadata.get("strategy") != "json_entity":
            return False
        upload = getattr(chunk, "upload", None)
        ingestion_meta = getattr(upload, "ingestion_metadata", None) if upload else None
        if not isinstance(ingestion_meta, Mapping):
            return False
        return str(ingestion_meta.get("format") or "").strip().lower() == "pdf"

    @staticmethod
    def _is_pinned(upload: KnowledgeUpload) -> bool:
        metadata = upload.metadata if isinstance(upload.metadata, dict) else {}
        ingestion = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        tags = upload.tags if isinstance(upload.tags, list) else []
        flag_sources: list[object] = []
        flag_sources.extend(metadata.get(key) for key in ("pin", "pinned", "always_on_prompt", "alwaysOnPrompt") if metadata)
        flag_sources.extend(ingestion.get(key) for key in ("pin", "pinned") if ingestion)
        normalized_tags = {str(tag).strip().lower() for tag in tags if isinstance(tag, str)}
        if any(KnowledgeMetadataMixin._coerce_bool(flag) for flag in flag_sources if flag is not None):
            return True
        if any(tag in {"pin", "pinned", "always-on", "always_on", "alwayson"} for tag in normalized_tags):
            return True
        return False

    @staticmethod
    def _coerce_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {"1", "true", "yes", "y", "t", "pin"}
        return False

    @staticmethod
    def _coerce_int(value: object) -> int:
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _topic_hints(cls, upload: KnowledgeUpload) -> tuple[str, ...]:
        metadata = upload.metadata if isinstance(upload.metadata, dict) else {}
        tags = upload.tags if isinstance(upload.tags, list) else []
        hints: list[str] = []
        for source in (
            metadata.get("coverage"),
            metadata.get("topics"),
            metadata.get("labels"),
            metadata.get("keywords"),
            tags,
            [upload.category] if upload.category else [],
        ):
            hints.extend(cls._normalize_topic_list(source))
        seen: set[str] = set()
        ordered: list[str] = []
        for hint in hints:
            if hint and hint not in seen:
                seen.add(hint)
                ordered.append(hint)
        return tuple(ordered)

    @classmethod
    def _normalize_topic_list(cls, source: object) -> list[str]:
        if source is None:
            return []
        if isinstance(source, str):
            normalized = cls._normalize_topic_value(source)
            return [normalized] if normalized else []
        if isinstance(source, (list, tuple, set)):
            result: list[str] = []
            for item in source:
                normalized = cls._normalize_topic_value(item)
                if normalized:
                    result.append(normalized)
            return result
        return []

    @staticmethod
    def _normalize_topic_value(value: object) -> str:
        if not isinstance(value, str):
            return ""
        normalized = " ".join(value.replace("_", " ").replace("/", " ").split()).strip().lower()
        return normalized
