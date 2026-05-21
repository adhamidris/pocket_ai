from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import apply_customer_visible_chunks
from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.contracts import (
    KNOWLEDGE_READ_STATE_PREVIEW,
    KnowledgeSnippet,
)


class ChunkContentLoadingMixin:

    def load_chunk_contents(
        self,
        *,
        business_profile,
        chunk_ids: Sequence[str],
        neighbor: int = 1,
        max_chars: int | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        """
        Fetch exact chunk(s) and stitch +/- neighbor chunks from the same upload
        for minimal, focused context delivery.
        """
        normalized: list[uuid.UUID] = []
        for value in (chunk_ids or ()):
            try:
                normalized.append(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
        if not normalized:
            return tuple()

        chunks: list[KnowledgeUploadChunk] = list(
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    id__in=normalized,
                    business_profile=business_profile,
                    upload__status=KnowledgeStatus.ACTIVE,
                )
            )
            .select_related("upload")
            .order_by("upload_id", "chunk_index")
        )
        if not chunks:
            return tuple()

        by_upload: dict[uuid.UUID, list[KnowledgeUploadChunk]] = {}
        for ch in chunks:
            by_upload.setdefault(ch.upload_id, []).append(ch)

        stitched_snippets: list[KnowledgeSnippet] = []
        limit = self.inline_char_limit_for_business(business_profile, max_chars)
        configured_neighbor = self._neighbor_window_for_business(business_profile, neighbor)

        entity_chunk_cache: dict[tuple[uuid.UUID, str], list[KnowledgeUploadChunk]] = {}
        structured_cache: dict[uuid.UUID, tuple[Mapping[str, object], ...]] = {}
        issue_cache: dict[uuid.UUID, tuple[Mapping[str, object], ...]] = {}

        for upload_id, requested in by_upload.items():
            upload = requested[0].upload
            trunc_metrics = self._truncation_metrics(upload)
            request_entries: list[tuple[KnowledgeUploadChunk, int]] = []
            for ch in requested:
                if ch.chunk_index is None:
                    continue
                effective_neighbor = self._effective_neighbor_window(ch, configured_neighbor)
                request_entries.append((ch, effective_neighbor))
            if not request_entries:
                continue

            min_idx = min(max(0, ch.chunk_index - span) for ch, span in request_entries if ch.chunk_index is not None)
            max_idx = max((ch.chunk_index or 0) + span for ch, span in request_entries)
            cached_window = self._window_cache_get(upload.business_profile_id, upload.id, min_idx, max_idx)
            if cached_window is not None:
                window_chunks = cached_window
            else:
                window_chunks = list(
                    KnowledgeUploadChunk.objects.filter(
                        upload=upload,
                        chunk_index__gte=min_idx,
                        chunk_index__lte=max_idx,
                    ).order_by("chunk_index")
                )
                self._window_cache_set(upload.business_profile_id, upload.id, min_idx, max_idx, window_chunks)
            by_index = {ch.chunk_index: ch for ch in window_chunks}
            if upload.id not in structured_cache:
                structured_cache[upload.id] = tuple(self._serialize_structured_tables_with_rows(upload, max_tables=3, max_rows=5))
                issue_cache[upload.id] = self._ingestion_issue_summaries(upload)

            for req, span in request_entries:
                idx = req.chunk_index
                start = max(0, idx - span)
                end = idx + span
                parts: list[str] = []
                for i in range(start, end + 1):
                    ch = by_index.get(i)
                    if ch and ch.content:
                        parts.append(ch.content)
                extra_parts: list[str] = []
                chunk_metadata = req.metadata if isinstance(req.metadata, dict) else {}
                entity_name = chunk_metadata.get("entity_name")
                if entity_name:
                    entity_chunks = self._fetch_entity_chunks(upload, entity_name, cache=entity_chunk_cache)
                    for extra in entity_chunks:
                        if extra.chunk_index == idx:
                            continue
                        if extra.content:
                            extra_parts.append(extra.content)
                        if len(extra_parts) >= 2:
                            break
                combined_sections = parts + extra_parts
                combined = "\n\n".join(section for section in combined_sections if section).strip()
                if combined:
                    trimmed, truncated = self._trim_with_flag(combined, max_chars=limit)
                else:
                    trimmed, truncated = ("", False)

                label = getattr(upload, "display_name", None) or "Document"
                summary = (trimmed.splitlines()[0] if trimmed else req.content or "No summary available.").strip()
                chunk_metadata = req.metadata if isinstance(req.metadata, dict) else {}
                read_state = KNOWLEDGE_READ_STATE_PREVIEW if truncated else self._read_state_for_content(trimmed, chunk_metadata)
                structured_tables = structured_cache.get(upload.id) or tuple()
                issues = issue_cache.get(upload.id) or tuple()
                partial_flag = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False
                source_diag: dict[str, object] = {"neighbor_window": span, "inline_char_limit": limit}
                if trunc_metrics:
                    source_diag.update(trunc_metrics)
                if truncated:
                    source_diag["partial_content"] = True
                snippet = KnowledgeSnippet(
                    id=req.id,
                    title=f"{label} – chunk {req.chunk_index}",
                    summary=summary[:280],
                    source=upload.get_source_type_display(),
                    content=trimmed,
                    content_mode="preview",
                    public_label=label,
                    structured_tables=structured_tables,
                    issues=issues,
                    page_summaries=tuple(),
                    read_state=read_state,
                    topic_hints=tuple(),
                    is_pinned=False,
                    upload_id=upload.id,
                    chunk_id=req.id,
                    chunk_index=req.chunk_index,
                    page_number=(req.chunk_index + 1) if req.chunk_index is not None else None,
                    entity_type=chunk_metadata.get("entity_type"),
                    entity_name=chunk_metadata.get("entity_name"),
                    entity_business=chunk_metadata.get("entity_business"),
                    is_table_chunk=bool(chunk_metadata.get("is_table_chunk")),
                    table_id=str(chunk_metadata.get("table_id") or "") or None,
                    aliases=tuple(chunk_metadata.get("aliases") or ()),
                    search_stage="load_chunk",
                    confidence_score=1.0,
                    truncated=truncated,
                    source_diagnostics=source_diag,
                    partial_index=partial_flag,
                    structured_table_count=len(structured_tables),
                    issue_count=len(issues),
                    structured_table_hint=None,
                )
                stitched_snippets.append(snippet)

        return tuple(stitched_snippets)
