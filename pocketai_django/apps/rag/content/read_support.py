from __future__ import annotations

import uuid
from typing import Mapping

from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadChunk
from apps.rag.contracts import (
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_SUMMARY,
)


class ContentReadSupportMixin:

    def _read_state_for_content(self, content: str | None, metadata: Mapping[str, object] | None) -> str:
        if not content:
            return KNOWLEDGE_READ_STATE_SUMMARY
        target_threshold = self.read_ready_threshold
        meta = metadata or {}
        if meta.get("is_table_chunk"):
            target_threshold = self.table_ready_threshold
        if len(content) >= target_threshold:
            return KNOWLEDGE_READ_STATE_FULL
        return KNOWLEDGE_READ_STATE_SUMMARY

    def _effective_neighbor_window(self, chunk: KnowledgeUploadChunk, default_neighbor: int) -> int:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        window = default_neighbor
        if metadata.get("is_table_chunk"):
            window = max(window, self.entity_neighbor_min)
        if metadata.get("entity_name"):
            window = max(window, self.entity_neighbor_min + 1)
        return window

    @staticmethod
    def _fetch_entity_chunks(
        upload: KnowledgeUpload,
        entity_name: str,
        *,
        cache: dict[tuple[uuid.UUID, str], list[KnowledgeUploadChunk]],
    ) -> list[KnowledgeUploadChunk]:
        if not entity_name:
            return []
        key = (upload.id, entity_name.lower())
        if key in cache:
            return cache[key]
        qs = KnowledgeUploadChunk.objects.filter(
            upload=upload,
            metadata__entity_name__iexact=entity_name,
        ).order_by("chunk_index")
        cache[key] = list(qs)
        return cache[key]

    def inline_char_limit_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve the inline char budget for a business without changing legacy defaults.
        """

        base = requested if requested is not None else self.inline_char_limit_default
        override = self._business_override(business_profile, "inline_knowledge_char_limit", base)
        limit = max(200, int(override))
        return limit

    def _neighbor_window_for_business(self, business_profile, requested: int | None = None) -> int:
        """
        Resolve the neighbor span for chunk reads so we can tune per business later.
        """

        base = requested if requested is not None else self.chunk_neighbor_window_default
        override = self._business_override(business_profile, "chunk_neighbor_window", base)
        window = max(0, int(override))
        return window

    def page_char_limit_for_business(self, business_profile, requested: int | None = None) -> int:
        base = requested if requested is not None else self.page_char_limit_default
        override = self._business_override(business_profile, "page_char_limit", base)
        return max(200, int(override))

    def _window_cache_get(
        self,
        business_id: uuid.UUID,
        upload_id: uuid.UUID,
        start: int,
        end: int,
    ) -> list[KnowledgeUploadChunk] | None:
        key = (business_id, upload_id, start, end)
        cached = self._window_cache.get(key)
        if cached is not None:
            self._window_cache.move_to_end(key)
        return cached

    def _window_cache_set(
        self,
        business_id: uuid.UUID,
        upload_id: uuid.UUID,
        start: int,
        end: int,
        chunks: list[KnowledgeUploadChunk],
    ) -> None:
        key = (business_id, upload_id, start, end)
        self._window_cache[key] = chunks
        self._window_cache.move_to_end(key)
        if len(self._window_cache) > self.window_cache_limit:
            self._window_cache.popitem(last=False)

    def _page_summary_entries(self, upload: KnowledgeUpload) -> dict[int, Mapping[str, object]]:
        cache_key = (upload.business_profile_id, upload.id)
        cached = self._page_summary_cache.get(cache_key)
        if cached is not None:
            self._page_summary_cache.move_to_end(cache_key)
            return cached
        metadata = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
        exports = metadata.get("structured_exports") if isinstance(metadata, dict) else None
        pages = exports.get("pages") if isinstance(exports, dict) else []
        entries: dict[int, Mapping[str, object]] = {}
        if isinstance(pages, list):
            for item in pages:
                if not isinstance(item, Mapping):
                    continue
                page_number = item.get("page_number")
                try:
                    page_index = int(page_number)
                except (TypeError, ValueError):
                    continue
                entries[page_index] = item
        self._page_summary_cache[cache_key] = entries
        self._page_summary_cache.move_to_end(cache_key)
        if len(self._page_summary_cache) > self.page_summary_cache_limit:
            self._page_summary_cache.popitem(last=False)
        return entries

    def _page_synopsis_text(self, upload: KnowledgeUpload | None, page_number: int | None, fallback: str | None = None) -> str:
        if not upload or not page_number:
            return (fallback or "").strip()
        entries = self._page_summary_entries(upload)
        entry = entries.get(page_number)
        if not entry:
            return (fallback or "").strip()
        synopsis = entry.get("synopsis")
        if isinstance(synopsis, str) and synopsis.strip():
            return synopsis.strip()
        heading = entry.get("heading") or entry.get("section_heading")
        if isinstance(heading, str) and heading.strip():
            return heading.strip()
        headings = entry.get("headings")
        if isinstance(headings, list):
            for candidate in headings:
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
        return (fallback or "").strip()
