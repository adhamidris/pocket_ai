from __future__ import annotations

import uuid
from typing import Mapping

from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.conversations.models import Conversation
from apps.knowledge.access.visibility import apply_customer_visible_chunks, apply_customer_visible_uploads
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
)

from ..knowledge_support.scope import _agent_knowledge_scope, _agent_scope_allows_upload
from ..types import ToolExecutionContext


def _resolve_target(
    item_id: str,
    *,
    conversation: Conversation,
) -> tuple[
    KnowledgeUploadChunk | None,
    KnowledgeUpload | None,
    KnowledgeUploadTable | None,
    KnowledgeUploadTableRow | None,
    dict[str, object] | None,
]:
    business_uuid = _business_uuid(conversation)
    if business_uuid is None:
        return (
            None,
            None,
            None,
            None,
            {
                "id": item_id,
                "error_code": "invalid_business_profile",
                "hint": "conversation.business_profile_id must be a UUID.",
            },
        )
    try:
        identifier = uuid.UUID(item_id)
    except (TypeError, ValueError):
        return None, None, None, None, {"id": item_id, "error_code": "invalid_id", "hint": "id must be a valid UUID from search_knowledge results."}

    chunk_record = (
        apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                id=identifier,
                business_profile_id=business_uuid,
                upload__status=KnowledgeStatus.ACTIVE,
            )
        )
        .select_related("upload")
        .first()
    )
    if chunk_record:
        upload = getattr(chunk_record, "upload", None)
        return chunk_record, upload, None, None, None

    upload_record = apply_customer_visible_uploads(
        KnowledgeUpload.objects.filter(
            id=identifier,
            business_profile_id=business_uuid,
            status=KnowledgeStatus.ACTIVE,
        )
    ).first()
    if upload_record:
        return None, upload_record, None, None, None

    table_record = (
        KnowledgeUploadTable.objects.filter(
            id=identifier,
            upload__business_profile_id=business_uuid,
            upload__status=KnowledgeStatus.ACTIVE,
        )
        .exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
        .select_related("upload")
        .only(
            "id",
            "upload_id",
            "title",
            "section_heading",
            "order_index",
            "upload__id",
            "upload__display_name",
            "upload__source_name",
            "upload__external_reference",
            "upload__slug",
        )
        .first()
    )
    if table_record:
        return None, None, table_record, None, None

    row_record = (
        KnowledgeUploadTableRow.objects.filter(
            id=identifier,
            table__upload__business_profile_id=business_uuid,
            table__upload__status=KnowledgeStatus.ACTIVE,
        )
        .exclude(table__upload__visibility=KnowledgeVisibility.INTERNAL)
        .select_related("table", "table__upload")
        .only(
            "id",
            "row_index",
            "table__id",
            "table__title",
            "table__section_heading",
            "table__order_index",
            "table__upload_id",
            "table__upload__id",
            "table__upload__display_name",
            "table__upload__source_name",
            "table__upload__external_reference",
            "table__upload__slug",
        )
        .first()
    )
    if row_record:
        return None, None, None, row_record, None

    return None, None, None, None, {"id": item_id, "error_code": "not_found", "hint": "Document not found for this business."}


def _business_uuid(conversation: Conversation) -> uuid.UUID | None:
    raw_business_id = getattr(conversation, "business_profile_id", None)
    try:
        return uuid.UUID(str(raw_business_id))
    except (TypeError, ValueError):
        return None


def _enforce_access(
    upload_id: str,
    *,
    item_id: str,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> dict[str, object] | None:
    agent_scope = _agent_knowledge_scope(conversation, context)
    if upload_id and not _agent_scope_allows_upload(scope=agent_scope, conversation=conversation, upload_id=upload_id):
        return {"id": item_id, "error_code": "forbidden_document", "hint": "This agent is not permitted to access that document."}
    return None


def _is_dataset_upload(upload: KnowledgeUpload | None) -> bool:
    if not upload:
        return False
    try:
        ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
    except Exception:
        ingestion_meta = {}
    dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, Mapping) else None
    dataset_enabled = bool(isinstance(dataset_meta, Mapping) and dataset_meta.get("enabled"))
    format_hint = str(ingestion_meta.get("format") or "").strip().lower()
    native_tabular = format_hint in {"csv", "tsv", "xls", "xlsx", "jsonl"}
    if dataset_enabled or native_tabular:
        return True
    # Legacy heuristic: uploads that have tables but no pages are usually spreadsheets/datasets.
    try:
        if upload.tables.exists() and not upload.pages.exists():
            return True
    except Exception:
        pass
    return False
