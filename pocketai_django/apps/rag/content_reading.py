from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Mapping, Sequence

from django.db.models import Prefetch

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import (
    apply_customer_visible_chunks,
    apply_customer_visible_uploads,
)
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadIssue,
    KnowledgeUploadPageBlock,
    KnowledgeUploadTable,
)
from apps.rag.contracts import (
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
    KNOWLEDGE_READ_STATE_SUMMARY,
)


logger = logging.getLogger(__name__)


class ContentReadingMixin:
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

    def load_contents(
        self,
        *,
        business_profile,
        knowledge_ids: Sequence[str],
        max_chars: int | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        normalized: list[uuid.UUID] = []
        for value in knowledge_ids:
            try:
                normalized.append(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
        if not normalized:
            return tuple()
        table_prefetch = Prefetch(
            "tables",
            queryset=KnowledgeUploadTable.objects.order_by("order_index").select_related("page"),
        )
        issue_prefetch = Prefetch(
            "issues",
            queryset=KnowledgeUploadIssue.objects.order_by("-created_at").select_related("page", "table", "table_row", "table_cell"),
        )
        qs = (
            apply_customer_visible_uploads(
                KnowledgeUpload.objects.filter(
                    business_profile=business_profile,
                    status=KnowledgeStatus.ACTIVE,
                    id__in=normalized,
                )
            )
            .select_related("text_detail")
            .prefetch_related(table_prefetch, issue_prefetch)
            .order_by("-updated_at")
        )
        snippets: list[KnowledgeSnippet] = []
        limit = self.inline_char_limit_for_business(business_profile, max_chars)
        for upload in qs:
            trunc_metrics = self._truncation_metrics(upload)
            label = self._public_label(upload)
            raw_content = self._extract_content(upload)
            if raw_content:
                content, truncated = self._trim_with_flag(raw_content, max_chars=limit)
            else:
                content, truncated = ("", False)
            structured = self._structured_exports(upload)
            enriched_tables = self._serialize_structured_tables_with_rows(upload, max_tables=3, max_rows=5)
            table_count = len(enriched_tables) or len(structured["tables"])
            issue_count = len(structured["issues"])
            supplemental_sections: list[dict[str, object]] = []
            if table_count:
                supplemental_sections.append(
                    {
                        "type": "table_preview",
                        "label": f"{table_count} structured table{'s' if table_count != 1 else ''}",
                        "reference": "structured_tables",
                        "item_count": table_count,
                    }
                )
            if issue_count:
                supplemental_sections.append(
                    {
                        "type": "ingestion_issues",
                        "label": f"{issue_count} ingestion issue{'s' if issue_count != 1 else ''}",
                        "reference": "issues",
                        "item_count": issue_count,
                    }
                )
            doc_read_state = KNOWLEDGE_READ_STATE_PREVIEW if truncated else self._read_state_for_content(content, {})
            source_diag: dict[str, object] = {"load": "document", "inline_char_limit": limit}
            if trunc_metrics:
                source_diag.update(trunc_metrics)
            if truncated:
                source_diag["partial_content"] = True
            partial_flag = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False
            snippets.append(
                KnowledgeSnippet(
                    id=upload.id,
                    title=label,
                    summary=self._summarize_upload(upload),
                    source=upload.source_name or upload.source_type,
                    content=content,
                    content_mode="full_document",
                    public_label=label,
                    structured_tables=enriched_tables or structured["tables"],
                    issues=structured["issues"],
                    page_summaries=structured["pages"],
                    read_state=doc_read_state,
                    topic_hints=self._topic_hints(upload),
                    is_pinned=self._is_pinned(upload),
                    supplemental_sections=tuple(supplemental_sections),
                    entity_type=None,
                    entity_name=None,
                    entity_business=None,
                    is_table_chunk=False,
                    aliases=tuple(),
                    search_stage="load_document",
                    confidence_score=1.0,
                    truncated=truncated,
                    source_diagnostics=source_diag,
                    partial_index=partial_flag,
                    structured_table_count=table_count,
                    issue_count=issue_count,
                    structured_table_hint=None,
                )
            )
        return tuple(snippets)

    @staticmethod
    def _resolve_upload_page_chunk(
        *,
        business_profile,
        upload_id: uuid.UUID,
        page_index: int,
    ) -> KnowledgeUploadChunk | None:
        """
        Locate a chunk for the requested page index (1-based) within an upload.
        Falls back to the closest available chunk when the requested index is out
        of range.
        """

        target_index = max(0, page_index - 1)
        base_qs = apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                upload__business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
                upload_id=upload_id,
            ).select_related("upload")
        )

        try:
            chunk = base_qs.get(chunk_index=target_index)
            return chunk
        except KnowledgeUploadChunk.DoesNotExist:
            pass

        chunk = base_qs.filter(chunk_index__gte=target_index).order_by("chunk_index").first()
        if chunk:
            return chunk
        return base_qs.order_by("-chunk_index").first()

    @staticmethod
    def _get_page_text_from_blocks(
        upload: KnowledgeUpload,
        page_number: int,
        max_chars: int | None = None,
    ) -> tuple[str, bool]:
        """
        Extract actual page text from KnowledgeUploadPageBlock entries.
        Returns (text, truncated_flag).
        """
        try:
            blocks = list(
                KnowledgeUploadPageBlock.objects.filter(
                    upload=upload,
                    page__page_number=page_number,
                )
                .select_related("page")
                .order_by("order_index")
            )

            if not blocks:
                return ("", False)

            page_parts: list[str] = []
            for block in blocks:
                if block.text:
                    page_parts.append(block.text)

            combined = "\n\n".join(page_parts).strip()

            if max_chars and len(combined) > max_chars:
                return (combined[:max_chars], True)

            return (combined, False)

        except Exception:
            return ("", False)

    @staticmethod
    def _get_structured_table_content_for_page(
        upload: KnowledgeUpload,
        page_number: int,
        max_chars: int | None = None,
    ) -> tuple[str, bool, bool]:
        """
        Get structured table content for a page from table row chunks.

        Returns (content, truncated_flag, has_tables).
        """
        try:
            table_ids = list(
                KnowledgeUploadTable.objects.filter(
                    upload=upload,
                    page__page_number=page_number,
                )
                .order_by("order_index")
                .values_list("id", flat=True)
            )

            if not table_ids:
                return ("", False, False)

            row_chunks = list(
                KnowledgeUploadChunk.objects.filter(
                    upload=upload,
                    metadata__table_id__in=[str(tid) for tid in table_ids],
                    metadata__table_chunk_role="row",
                )
                .order_by("chunk_index")
            )

            if not row_chunks:
                return ("", False, False)

            content_parts: list[str] = []
            current_table_id: str | None = None

            for chunk in row_chunks:
                chunk_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
                table_id = chunk_meta.get("table_id")

                if table_id != current_table_id and current_table_id is not None:
                    content_parts.append("\n---\n")
                current_table_id = table_id

                if chunk.content:
                    content_parts.append(chunk.content.strip())

            combined = "\n\n".join(content_parts).strip()
            truncated = False

            if max_chars and len(combined) > max_chars:
                combined = combined[:max_chars]
                truncated = True

            return (combined, truncated, True)

        except Exception as exc:
            logger.warning("Failed to get structured table content: %s", exc)
            return ("", False, False)

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
