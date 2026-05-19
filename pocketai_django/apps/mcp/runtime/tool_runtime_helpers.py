"""
Runtime logging, audit, and small cache helpers for MCP tools.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Mapping

from django.conf import settings

from apps.accounts.models import KnowledgeAuditAction
from apps.conversations.models import Conversation
from apps.knowledge.models import KnowledgeAuditEvent, KnowledgeUpload
from apps.knowledge.privacy import sha256_hex

from ..types import ToolExecutionContext


logger = logging.getLogger(__name__)


MCP_LOG_PII_DEFAULT = False
MCP_LOG_SNIPPET_PREVIEWS_DEFAULT = False
MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT = False


def _mcp_log_pii_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_PII", MCP_LOG_PII_DEFAULT))


def _mcp_log_snippet_previews_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_SNIPPET_PREVIEWS", MCP_LOG_SNIPPET_PREVIEWS_DEFAULT))


def _mcp_log_full_snippet_content_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_FULL_SNIPPET_CONTENT", MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT))


def _log_safe_text_fields(field: str, value: str | None) -> dict[str, object]:
    if value is None:
        return {}
    text = str(value)
    if not text:
        return {}
    if _mcp_log_pii_enabled():
        return {field: text, f"{field}_len": len(text)}
    return {f"{field}_sha256": sha256_hex(text), f"{field}_len": len(text)}


def _record_knowledge_audit_event_once(
    *,
    context: ToolExecutionContext,
    conversation: Conversation,
    upload: KnowledgeUpload,
    action: str,
    engine: str | None,
    status: str | None,
    metadata: Mapping[str, object] | None = None,
) -> None:
    if not upload or not upload.id:
        return
    action_value = str(action or "").strip() or KnowledgeAuditAction.READ
    engine_value = str(engine or "").strip()
    status_value = str(status or "").strip()
    fingerprint = (str(conversation.id), str(upload.id), action_value)
    try:
        if fingerprint in context.audit_event_fingerprints:
            return
        context.audit_event_fingerprints.add(fingerprint)
    except Exception:
        pass
    safe_metadata: dict[str, object] = {
        "tool": "read_knowledge",
        "engine": engine_value or None,
        "status": status_value or None,
        "conversation_id": str(conversation.id),
    }
    try:
        label = (
            getattr(upload, "display_name", None)
            or getattr(upload, "source_name", None)
            or getattr(upload, "external_reference", None)
            or ""
        )
        safe_metadata.update(_log_safe_text_fields("upload_label", str(label).strip() or None))
    except Exception:
        pass
    if metadata:
        for key, value in dict(metadata).items():
            if value in (None, "", [], {}):
                continue
            safe_metadata[key] = value
    try:
        KnowledgeAuditEvent.objects.create(
            business_profile=upload.business_profile,
            upload=upload,
            upload_id_snapshot=upload.id,
            actor_agent=conversation.agent_profile,
            action=action_value,
            description="Knowledge accessed via tool call.",
            metadata=safe_metadata,
        )
    except Exception:
        logger.exception(
            "knowledge.audit_event_failed business=%s upload=%s action=%s",
            getattr(upload, "business_profile_id", None),
            getattr(upload, "id", None),
            action_value,
        )


def _has_prompt_evidence(envelope: Mapping[str, object]) -> bool:
    evidence = envelope.get("evidence") if isinstance(envelope.get("evidence"), Mapping) else {}
    rows = evidence.get("rows") if isinstance(evidence.get("rows"), list) else []
    snippets = evidence.get("snippets") if isinstance(evidence.get("snippets"), list) else []
    return bool(rows or snippets)


def _bounded_cache_store(cache: dict, key, payload: Mapping[str, object], *, limit: int = 16) -> None:
    cache[key] = copy.deepcopy(payload)
    while len(cache) > limit:
        oldest_key = next(iter(cache))
        cache.pop(oldest_key, None)


def _search_cache_key(
    query: str,
    limit: int | None,
    identifier_filter: Mapping[str, object] | None,
    locked_key: str | None,
    locked_value: str | None,
) -> tuple[str, int, str, str | None, str | None]:
    normalized_query = (query or "").strip().lower()
    safe_limit = int(limit or 0)
    filter_blob = json.dumps(identifier_filter or {}, sort_keys=True, default=str)
    return (normalized_query, safe_limit, filter_blob, locked_key, locked_value)


def _read_cache_key(
    document_id: str,
    page_index: int,
    mode: str,
    neighbor_window: int,
    token_budget: int | None,
) -> tuple[str, int, str, int, int | None]:
    normalized_id = str(document_id)
    normalized_mode = (mode or "excerpt").strip().lower()
    return (normalized_id, int(page_index), normalized_mode, int(neighbor_window), token_budget)
