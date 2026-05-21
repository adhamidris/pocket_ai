from __future__ import annotations

import logging
import uuid
from typing import Sequence

from django.db.models import Q

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.knowledge.access.visibility import apply_customer_visible_uploads
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadTableRow
from apps.rag.contracts import KNOWLEDGE_READ_STATE_SUMMARY, KnowledgeSnippet


logger = logging.getLogger(__name__)


class TableFallbackSnippetMixin:

    def _fallback_snippets(
        self,
        *,
        business_profile,
        limit: int,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        # Prefer top table rows as factual fallback; if none, fall back to recent uploads.
        table_rows_qs = KnowledgeUploadTableRow.objects.filter(
            table__upload__business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        ).exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return tuple()
            table_rows_qs = table_rows_qs.filter(table__upload_id__in=allowed_upload_ids)
        else:
            scope_clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                scope_clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
            if scope_clauses:
                clause = scope_clauses[0]
                for extra in scope_clauses[1:]:
                    clause |= extra
                table_rows_qs = table_rows_qs.filter(clause)
        table_rows_qs = self._filter_queryable_table_uploads(
            table_rows_qs,
            format_lookup="table__upload__ingestion_metadata__format",
        )
        table_rows_qs = (
            table_rows_qs.order_by("-created_at")
            .select_related("table__upload__business_profile")
            .prefetch_related("cells")[: limit * 3]
        )
        table_rows = list(table_rows_qs)
        snippets: list[KnowledgeSnippet] = []
        for row in table_rows:
            table = row.table
            upload = getattr(table, "upload", None)
            if not upload or upload.visibility == KnowledgeVisibility.INTERNAL:
                continue
            cells = sorted(row.cells.all(), key=lambda c: c.column_index)
            structured = []
            for cell in cells:
                structured.append(
                    {"column": cell.column_key or f"column_{cell.column_index + 1}", "value": cell.raw_text}
                )
            summary_parts = [f"{entry['column']}: {entry['value']}" for entry in structured if entry["value"]]
            summary = "; ".join(summary_parts[:8]) or (row.raw_text or "")
            content = "\n".join(summary_parts) or summary
            structured_table = {
                "title": table.title or table.section_heading or "Table",
                "columns": [entry["column"] for entry in structured],
                "rows": [[entry["value"] for entry in structured]],
                "metadata": {
                    "sheet_name": (table.metadata or {}).get("sheet_name"),
                    "row_index": row.row_index,
                    "table_order_index": table.order_index,
                },
            }
            snippets.append(
                KnowledgeSnippet(
                    id=uuid.uuid4(),
                    title=structured_table["title"],
                    summary=summary or structured_table["title"],
                    source="table_fallback",
                    public_label=structured_table["title"],
                    structured_tables=(structured_table,),
                    issues=tuple(),
                    page_summaries=tuple(),
                    read_state=KNOWLEDGE_READ_STATE_SUMMARY,
                    topic_hints=tuple(),
                    is_pinned=False,
                    content=content,
                    content_mode="abstract",
                    entity_type=table.section_heading or "table_row",
                    entity_name=summary_parts[0] if summary_parts else structured_table["title"],
                    entity_business=getattr(upload.business_profile, "name", None),
                    is_table_chunk=True,
                    table_id=str(table.id),
                    aliases=tuple(),
                    search_stage="fallback",
                    confidence_score=0.0,
                    truncated=False,
                    source_diagnostics={
                        "reason": "fallback",
                        "table_id": str(table.id),
                        "row_index": row.row_index,
                    },
                    partial_index=False,
                    structured_table_count=1,
                    issue_count=0,
                    structured_table_hint=structured_table["title"],
                )
            )
            if len(snippets) >= limit:
                break
        if len(snippets) < limit:
            remaining = limit - len(snippets)
            uploads_qs = KnowledgeUpload.objects.filter(
                business_profile=business_profile,
                status=KnowledgeStatus.ACTIVE,
            )
            if allowed_upload_ids is not None:
                uploads_qs = uploads_qs.filter(id__in=allowed_upload_ids)
            else:
                clauses: list[Q] = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    uploads_qs = uploads_qs.filter(clause)
            uploads = apply_customer_visible_uploads(uploads_qs).order_by("-updated_at")[:remaining]
            for upload in uploads:
                trunc_metrics = self._truncation_metrics(upload)
                label = self._public_label(upload)
                structured = self._structured_exports(upload)
                table_count = len(structured["tables"])
                issue_count = len(structured["issues"])
                source_diag: dict[str, object] = {"reason": "fallback"}
                if trunc_metrics:
                    source_diag.update(trunc_metrics)
                snippets.append(
                    KnowledgeSnippet(
                        id=upload.id,
                        title=label,
                        summary=self._summarize_upload(upload),
                        source=upload.source_name or upload.source_type,
                        public_label=label,
                        structured_tables=structured["tables"],
                        issues=structured["issues"],
                        page_summaries=structured["pages"],
                        read_state=KNOWLEDGE_READ_STATE_SUMMARY,
                        topic_hints=self._topic_hints(upload),
                        is_pinned=self._is_pinned(upload),
                        content_mode="abstract",
                        entity_type=None,
                        entity_name=None,
                        entity_business=None,
                        is_table_chunk=False,
                        aliases=tuple(),
                        search_stage="fallback",
                        confidence_score=0.0,
                        truncated=False,
                        source_diagnostics=source_diag,
                        partial_index=bool(trunc_metrics.get("partial_index")) if trunc_metrics else False,
                        structured_table_count=table_count,
                        issue_count=issue_count,
                        structured_table_hint=None,
                    )
                )
        if not snippets:
            logger.warning("Knowledge load returned no snippets for business=%s", business_profile.id)
        return tuple(snippets)
