from __future__ import annotations

import uuid
from typing import Sequence

from django.db.models import Prefetch

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import apply_customer_visible_uploads
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadIssue,
    KnowledgeUploadTable,
)
from apps.rag.contracts import (
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_PREVIEW,
)
from apps.rag.content.chunk_loading import ChunkContentLoadingMixin
from apps.rag.content.page_window import PageWindowLoadingMixin
from apps.rag.content.read_support import ContentReadSupportMixin


class ContentReadingMixin(ContentReadSupportMixin, PageWindowLoadingMixin, ChunkContentLoadingMixin):

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
