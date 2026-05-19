from __future__ import annotations

from typing import Mapping

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.knowledge_access import apply_customer_visible_chunks
from apps.knowledge.models import KnowledgeUploadChunk, KnowledgeUploadTable
from apps.rag.contracts import (
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
    KnowledgeSnippet,
)
from apps.rag.knowledge_search import KnowledgeSearchService


def _agentic_table_chunk_snippets(
    *,
    chunk_record: KnowledgeUploadChunk,
    business,
    max_chars: int | None,
    service: KnowledgeSearchService,
) -> list[KnowledgeSnippet] | None:
    chunk_meta = chunk_record.metadata if isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
    if not chunk_meta.get("is_table_chunk"):
        return None

    table_role = str(chunk_meta.get("table_chunk_role") or "").strip().lower()
    table_row_index = chunk_meta.get("table_row_index")
    if table_role == "row" or table_row_index is not None:
        return list(
            service.load_chunk_contents(
                business_profile=business,
                chunk_ids=[str(chunk_record.id)],
                neighbor=0,
                max_chars=max_chars,
            )
        )

    table_id = chunk_meta.get("table_id")
    if not table_id:
        return None

    row_chunks = list(
        apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                upload=chunk_record.upload,
                business_profile=business,
                upload__status=KnowledgeStatus.ACTIVE,
                metadata__table_id=str(table_id),
                metadata__table_chunk_role="row",
            )
        ).order_by("chunk_index")
    )
    if not row_chunks:
        return None

    content_parts = [row.content.strip() for row in row_chunks if row.content]
    combined = "\n\n".join(content_parts).strip()
    if not combined:
        return None

    truncated = False
    if max_chars and len(combined) > max_chars:
        combined = combined[:max_chars]
        truncated = True

    table_title = None
    table_order_index = None
    page_number = None
    try:
        table_obj = (
            KnowledgeUploadTable.objects.filter(id=table_id)
            .select_related("page")
            .only("id", "title", "section_heading", "order_index", "page__page_number")
            .first()
        )
        if table_obj:
            table_title = table_obj.title or table_obj.section_heading
            table_order_index = table_obj.order_index
            page_number = table_obj.page.page_number if table_obj.page else None
    except Exception:
        table_obj = None

    if not table_title:
        table_title = f"Table {table_order_index}" if table_order_index else "Table"

    summary = combined.splitlines()[0][:280] if combined else table_title
    trunc_metrics = service._truncation_metrics(chunk_record.upload)
    partial_index = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False
    source_diag: dict[str, object] = {
        "table_id": str(table_id),
        "table_row_count": len(row_chunks),
        "table_chunk_role": table_role or "preview",
        "table_read_only": True,
    }
    if truncated:
        source_diag["partial_content"] = True
    if trunc_metrics:
        source_diag.update(trunc_metrics)

    snippet = KnowledgeSnippet(
        id=chunk_record.id,
        title=table_title,
        summary=summary,
        source=chunk_record.upload.get_source_type_display(),
        content=combined,
        content_mode="table_rows",
        public_label=table_title,
        structured_tables=tuple(),
        issues=tuple(),
        page_summaries=tuple(),
        read_state=KNOWLEDGE_READ_STATE_PREVIEW if truncated else KNOWLEDGE_READ_STATE_FULL,
        topic_hints=tuple(),
        is_pinned=False,
        upload_id=chunk_record.upload_id,
        chunk_id=chunk_record.id,
        chunk_index=chunk_record.chunk_index,
        page_number=page_number,
        page_mode=None,
        entity_type=chunk_meta.get("entity_type"),
        entity_name=chunk_meta.get("entity_name"),
        entity_business=chunk_meta.get("entity_business"),
        is_table_chunk=True,
        aliases=tuple(chunk_meta.get("aliases") or ()),
        search_stage="table_rows",
        confidence_score=None,
        truncated=truncated,
        source_diagnostics=source_diag,
        partial_index=partial_index,
        structured_table_count=1,
        issue_count=0,
        structured_table_hint=None,
    )

    return [snippet]
