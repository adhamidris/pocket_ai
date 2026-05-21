from __future__ import annotations

import dataclasses
import uuid
from typing import Sequence

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import apply_customer_visible_uploads
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
)
from apps.rag.content.page_sources import PageWindowSourceMixin
from apps.rag.contracts import (
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
    KNOWLEDGE_READ_STATE_SUMMARY,
    KnowledgeSnippet,
)


class PageWindowLoadingMixin(PageWindowSourceMixin):

    def load_page_window(
        self,
        *,
        business_profile,
        upload_id: uuid.UUID | None = None,
        chunk_id: uuid.UUID | None = None,
        page_index: int = 1,
        neighbor: int = 1,
        mode: str = "excerpt",
        token_budget: int | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        """
        Fetch a single chunk page with a tighter char budget so the LLM can
        request additional windows via repeated tool calls.
        """

        inline_cap = self.inline_char_limit_for_business(business_profile)
        page_cap = self.page_char_limit_for_business(business_profile)
        normalized_mode = (mode or "excerpt").strip().lower()
        effective_limit = inline_cap if normalized_mode == "full_page" else min(page_cap, inline_cap)
        if token_budget is not None:
            try:
                approx_chars = max(200, int(token_budget) * 4)
                effective_limit = max(200, min(effective_limit, approx_chars))
            except (TypeError, ValueError):
                pass
        effective_neighbor = self._neighbor_window_for_business(business_profile, neighbor)

        page_from_blocks = False
        upload_obj = None
        page_text = ""
        page_truncated = False
        page_source = "none"
        resolved_upload_id = upload_id

        if chunk_id is not None and upload_id is None:
            try:
                chunk = (
                    KnowledgeUploadChunk.objects.filter(
                        id=chunk_id,
                        upload__business_profile=business_profile,
                        upload__status=KnowledgeStatus.ACTIVE,
                    )
                    .select_related("upload")
                    .only("id", "upload_id", "metadata")
                    .first()
                )

                if chunk:
                    resolved_upload_id = chunk.upload_id
                    chunk_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
                    if page_index == 1:
                        meta_page = chunk_meta.get("table_page_number") or chunk_meta.get("chunk_page") or chunk_meta.get("page_number")
                        if meta_page:
                            try:
                                parsed_page = int(meta_page)
                                if parsed_page >= 1:
                                    page_index = parsed_page
                            except (TypeError, ValueError):
                                pass
            except Exception:
                pass

        if resolved_upload_id is not None:
            try:
                upload_obj = (
                    apply_customer_visible_uploads(
                        KnowledgeUpload.objects.filter(
                            id=resolved_upload_id,
                            business_profile=business_profile,
                            status=KnowledgeStatus.ACTIVE,
                        )
                    )
                    .only("id", "ingestion_metadata", "display_name", "source_type", "summary")
                    .first()
                )

                if upload_obj:
                    table_content, table_truncated, has_tables = self._get_structured_table_content_for_page(
                        upload_obj,
                        page_index,
                        max_chars=effective_limit,
                    )

                    if has_tables and table_content:
                        page_text = table_content
                        page_truncated = table_truncated
                        page_from_blocks = True
                        page_source = "structured_tables"
                    else:
                        page_text, page_truncated = self._get_page_text_from_blocks(
                            upload_obj,
                            page_index,
                            max_chars=effective_limit,
                        )
                        if page_text:
                            page_from_blocks = True
                            page_source = "page_blocks"
                        else:
                            page_source = "none"
            except Exception:
                pass

        if page_from_blocks and upload_obj:
            synopsis = self._page_synopsis_text(upload_obj, page_index, "")
            label = getattr(upload_obj, "display_name", None) or "Document"

            if normalized_mode != "full_page":
                content_value = synopsis or page_text[:500]
                read_state = KNOWLEDGE_READ_STATE_SUMMARY
                truncated_flag = False
            else:
                content_value = page_text
                read_state = KNOWLEDGE_READ_STATE_PREVIEW if page_truncated else KNOWLEDGE_READ_STATE_FULL
                truncated_flag = page_truncated

            diagnostics = {
                "page_request": True,
                "page_number": page_index,
                "page_mode": normalized_mode,
                "page_char_limit": effective_limit,
                "page_source": page_source,
            }

            return (
                KnowledgeSnippet(
                    id=upload_obj.id,
                    title=f"{label} – page {page_index}",
                    summary=synopsis or (page_text[:280] if page_text else ""),
                    source=upload_obj.get_source_type_display(),
                    content=content_value,
                    content_mode="full_page" if normalized_mode == "full_page" else "excerpt",
                    public_label=label,
                    structured_tables=tuple(),
                    issues=tuple(),
                    page_summaries=tuple(),
                    read_state=read_state,
                    topic_hints=tuple(),
                    is_pinned=False,
                    upload_id=upload_obj.id,
                    chunk_id=None,
                    chunk_index=None,
                    page_number=page_index,
                    page_mode=normalized_mode,
                    entity_type=None,
                    entity_name=None,
                    entity_business=None,
                    is_table_chunk=page_source == "structured_tables",
                    aliases=tuple(),
                    search_stage="load_page",
                    confidence_score=1.0,
                    truncated=truncated_flag,
                    source_diagnostics=diagnostics,
                    partial_index=False,
                    structured_table_count=0,
                    issue_count=0,
                    structured_table_hint=None,
                ),
            )

        target_chunk_id = chunk_id
        fallback_upload_id: uuid.UUID | None = None
        if target_chunk_id is None and upload_id is not None:
            chunk = self._resolve_upload_page_chunk(
                business_profile=business_profile,
                upload_id=upload_id,
                page_index=page_index,
            )
            if chunk:
                target_chunk_id = chunk.id
            else:
                fallback_upload_id = upload_id

        if target_chunk_id:
            snippets = self.load_chunk_contents(
                business_profile=business_profile,
                chunk_ids=[str(target_chunk_id)],
                neighbor=effective_neighbor,
                max_chars=effective_limit,
            )
        elif fallback_upload_id:
            snippets = self.load_contents(
                business_profile=business_profile,
                knowledge_ids=[str(fallback_upload_id)],
                max_chars=effective_limit,
            )
        else:
            return tuple()

        annotated: list[KnowledgeSnippet] = []
        upload_lookup: dict[uuid.UUID, KnowledgeUpload] = {}
        if normalized_mode != "full_page":
            upload_ids = {snippet.upload_id for snippet in snippets if snippet.upload_id}
            if upload_ids:
                upload_lookup = {
                    obj.id: obj
                    for obj in apply_customer_visible_uploads(
                        KnowledgeUpload.objects.filter(id__in=upload_ids)
                    ).only("id", "ingestion_metadata")
                }
        for snippet in snippets:
            actual_page = page_index
            if snippet.chunk_index is not None:
                actual_page = max(1, int(snippet.chunk_index) + 1)
            diagnostics = dict(snippet.source_diagnostics or {})
            diagnostics.update(
                {
                    "page_request": True,
                    "page_number": actual_page,
                    "page_mode": normalized_mode,
                    "page_char_limit": effective_limit,
                    "neighbor_window": effective_neighbor,
                    "page_source": "chunk_fallback",
                }
            )
            content_value = snippet.content
            read_state = snippet.read_state
            truncated_flag = snippet.truncated
            content_mode_value = "full_page" if normalized_mode == "full_page" else "excerpt"
            if normalized_mode != "full_page":
                upload_obj = upload_lookup.get(snippet.upload_id) if snippet.upload_id else None
                synopsis = self._page_synopsis_text(upload_obj, actual_page, snippet.summary)
                content_value = synopsis or snippet.summary
                read_state = KNOWLEDGE_READ_STATE_SUMMARY
                truncated_flag = False
            else:
                read_state = KNOWLEDGE_READ_STATE_PREVIEW if snippet.truncated else KNOWLEDGE_READ_STATE_FULL
            annotated.append(
                dataclasses.replace(
                    snippet,
                    content=content_value,
                    content_mode=content_mode_value,
                    read_state=read_state,
                    truncated=truncated_flag,
                    source_diagnostics=diagnostics,
                    page_number=actual_page,
                    page_mode=normalized_mode,
                )
            )
        return tuple(annotated)
