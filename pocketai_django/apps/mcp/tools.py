"""
Tool registry and dispatcher for MCP orchestration.

This module will eventually expose two public artifacts:
1. TOOL_DEFINITIONS – JSON schemas advertised to the LLM provider.
2. execute_tool(...) – server-side implementation of each tool call.

For phase one we only anchor the structure so future phases can iterate without
touching unrelated parts of the codebase.
"""

from __future__ import annotations

import copy
import csv
import gzip
import base64
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from django.db import models
from django.db.models import Prefetch
from django.db.models.functions import Length
from django.core.cache import cache
from django.core import signing
from django.conf import settings

from apps.accounts.models import (
    AgentProfile,
    KnowledgeAuditAction,
    KnowledgeVisibility,
    KnowledgeStatus,
)
from apps.knowledge.models import (
    KnowledgeAuditEvent,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    KnowledgeUploadTableCell,
)
from apps.agent_runs.models import AgentRun, AgentRunEvent, AgentRunStatus
from apps.conversations.models import (
    Conversation,
    ConversationFile,
    ConversationFileChunk,
)
from apps.rag.contracts import (
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
)
from apps.rag.knowledge_search import KnowledgeSearchService
from apps.rag.knowledge_payloads import (
    diagnostic_warning_payload,
    issue_warning_payloads,
    serialize_knowledge_snippet,
)
from apps.knowledge.knowledge_access import apply_customer_visible_chunks, apply_customer_visible_uploads
from apps.knowledge.privacy import sha256_hex
from apps.rag.rag_logging import structured_log
from apps.core.logging_utils import log_start, log_success, log_warning, log_performance, LogEmoji
from apps.rag.tabular_limits import ToolRateLimit, enforce_tool_rate_limit
from core.metrics import latency_monitor
from core.tenancy import tenant_context
from .types import (
    ChunkPageBudgetExceeded,
    ChunkReadBudgetExceeded,
    SearchBudgetExceeded,
    ToolConstraintError,
    ToolExecutionContext,
    ToolRateLimitExceeded,
    CharacterBudgetExceeded,
)
from .tool_definitions import (
    DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
    GATEWAY_TOOL_DEFINITIONS,
    MCP_PROMPT_MAX_SNIPPETS_CAP,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
    SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT,
    SEARCH_PREFETCH_ABSOLUTE_CAP,
    get_tool_definitions,
)
from .gateway_tools import _mcp_call_tool_handler, _mcp_search_tools_handler
from .file_tools import (
    _pdf_extract_pages_handler,
    _pdf_extract_text_handler,
    _pdf_generate_handler,
    _pdf_merge_handler,
    _read_conversation_file_handler,
    _search_conversation_files_handler,
)
from .email_tools import (
    _email_create_draft_handler,
    _email_get_message_handler,
    _email_get_thread_handler,
    _email_search_handler,
    _email_send_draft_handler,
)
from .native_integration_tools import (
    _calendar_create_event_handler,
    _calendar_get_event_handler,
    _calendar_list_events_handler,
    _calendar_update_event_handler,
    _drive_get_file_handler,
    _drive_list_files_handler,
    _drive_search_files_handler,
    _hubspot_create_contact_handler,
    _hubspot_get_contact_handler,
    _hubspot_search_contacts_handler,
    _hubspot_search_deals_handler,
    _onedrive_get_file_handler,
    _onedrive_list_files_handler,
    _onedrive_search_files_handler,
    _slack_list_channels_handler,
    _slack_read_channel_handler,
    _slack_search_messages_handler,
    _slack_send_message_handler,
)
from .integration_tool_catalog import (
    get_email_integration_type_for_provider,
    get_email_integration_tool_names,
    get_email_integration_tools_for_provider,
    get_email_tool_enabled_map_for_account,
    get_native_integration_tool_metadata,
    get_native_integration_tool_names,
    get_native_integration_tool_registry,
    get_native_integration_tools_for_type,
    get_native_tool_enabled_map_for_account,
    is_email_tool_enabled_for_account,
    is_native_integration_tool_enabled_for_conversation,
    is_native_tool_enabled_for_account,
    list_connected_native_integration_types,
    list_enabled_email_tool_names,
    list_enabled_native_integration_tool_names,
    resolve_native_integration_account_for_tool,
)
from .agent_run_tools import (
    _continue_agent_run_handler,
    _get_agent_run_handler,
    _list_agent_runs_handler,
    _request_user_input_handler,
    _start_agent_run_handler,
)
from .task_tools import (
    _draft_task_handler,
    _list_tasks_handler,
    _pause_task_handler,
    _request_task_activation_handler,
    _update_task_handler,
)
from .memory_tools import (
    _forget_memory_handler,
    _save_memory_handler,
    _search_memory_handler,
)
from .context_retrieval_tools import _retrieve_earlier_context_handler
from .budget_guidance import build_repeat_search_guidance, search_budget_exceeded_payload
from .rag_observability import build_query_scope_observability, build_retrieval_observability
from .tool_artifacts import store_local_tool_output_artifact
from .models import McpToolOutputArtifact
from apps.accounts.feature_flags import FeatureFlagService

try:
    import duckdb  # type: ignore
except Exception:  # pragma: no cover
    duckdb = None  # type: ignore


logger = logging.getLogger(__name__)
MCP_LOG_PII_DEFAULT = False
MCP_LOG_SNIPPET_PREVIEWS_DEFAULT = False
MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT = False


# search_knowledge pagination (cursor) helpers
SEARCH_KNOWLEDGE_CURSOR_SALT = "mcp.search_knowledge.cursor.v1"
SEARCH_KNOWLEDGE_CURSOR_CACHE_PREFIX = "mcp:search_knowledge:cursor:v1"
SEARCH_KNOWLEDGE_CURSOR_HANDLE_CACHE_PREFIX = "mcp:search_knowledge:cursor_handle:v1"
def _search_cursor_cache_key(*, conversation: Conversation, session_id: str) -> str:
    return (
        f"{SEARCH_KNOWLEDGE_CURSOR_CACHE_PREFIX}:"
        f"{conversation.business_profile_id}:"
        f"{conversation.id}:"
        f"{session_id}"
    )


def _search_cursor_handle_cache_key(*, conversation: Conversation, handle: str) -> str:
    return (
        f"{SEARCH_KNOWLEDGE_CURSOR_HANDLE_CACHE_PREFIX}:"
        f"{conversation.business_profile_id}:"
        f"{conversation.id}:"
        f"{handle}"
    )


def _encode_search_cursor(*, session_id: str, offset: int) -> str:
    payload = {"sid": str(session_id), "o": int(offset)}
    return signing.dumps(payload, salt=SEARCH_KNOWLEDGE_CURSOR_SALT)


def _decode_search_cursor(token: str, *, max_age_seconds: int) -> dict[str, object] | None:
    try:
        decoded = signing.loads(token, salt=SEARCH_KNOWLEDGE_CURSOR_SALT, max_age=max_age_seconds)
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _search_cursor_ttl_seconds() -> int:
    try:
        cursor_ttl_seconds = int(getattr(settings, "MCP_SEARCH_PAGINATION_TTL_SECONDS", 3600) or 3600)
    except (TypeError, ValueError):
        cursor_ttl_seconds = 3600
    return max(60, cursor_ttl_seconds)


def _resolve_search_cursor_from_handle(
    context: ToolExecutionContext | None,
    conversation: Conversation,
    cursor_token: str | None,
) -> str | None:
    token = str(cursor_token or "").strip()
    if not token:
        return None
    cache_value = getattr(context, "search_cursor_handles", None)
    if isinstance(cache_value, dict):
        mapped = cache_value.get(token)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    if token.startswith("s_"):
        mapped = cache.get(_search_cursor_handle_cache_key(conversation=conversation, handle=token))
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    return token


def _store_search_cursor_handle(
    context: ToolExecutionContext | None,
    conversation: Conversation,
    cursor_signed: str | None,
    *,
    ttl_seconds: int | None = None,
) -> str | None:
    token = str(cursor_signed or "").strip()
    if not token:
        return None
    ttl = _search_cursor_ttl_seconds() if ttl_seconds is None else max(60, int(ttl_seconds))
    cache_value = getattr(context, "search_cursor_handles", None)
    reverse_cache_value = getattr(context, "search_cursor_reverse_handles", None)
    if not isinstance(cache_value, dict) or not isinstance(reverse_cache_value, dict):
        return token

    existing_handle = reverse_cache_value.get(token)
    if isinstance(existing_handle, str) and existing_handle.strip():
        cached_token = cache_value.get(existing_handle.strip())
        if isinstance(cached_token, str) and cached_token == token:
            cache.set(
                _search_cursor_handle_cache_key(conversation=conversation, handle=existing_handle.strip()),
                token,
                ttl,
            )
            return existing_handle.strip()

    handle = f"s_{uuid.uuid4().hex[:20]}"
    cache_value[handle] = token
    reverse_cache_value[token] = handle
    cache.set(_search_cursor_handle_cache_key(conversation=conversation, handle=handle), token, ttl)

    while len(cache_value) > 500:
        oldest_handle = next(iter(cache_value))
        oldest_cursor = cache_value.pop(oldest_handle, None)
        if isinstance(oldest_cursor, str):
            reverse_cache_value.pop(oldest_cursor, None)
    while len(reverse_cache_value) > 500:
        oldest_cursor_key = next(iter(reverse_cache_value))
        oldest_handle_value = reverse_cache_value.pop(oldest_cursor_key, None)
        if isinstance(oldest_handle_value, str):
            cache_value.pop(oldest_handle_value, None)

    return handle


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




def execute_tool(
    name: str,
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext | None = None,
) -> Mapping[str, object]:
    """
    Execute the requested tool call and return the serialized result.

    The `context` parameter carries per-turn business constraints (chunk-read
    budgets, ingestion warnings, etc.) so the orchestrator can enforce them no
    matter which tool the LLM selects.
    """

    normalized_name = (name or "").strip()
    handler = _TOOL_HANDLERS.get(normalized_name)
    if not handler:
        return {
            "tool": normalized_name or "unknown_tool",
            "status": "error",
            "error": "unsupported_tool",
            "error_code": "unsupported_tool",
            "hint": "Unsupported tool. Use search_knowledge or read_knowledge.",
        }
    ctx = context or ToolExecutionContext()
    # Track tool-level read calls (one per tool invocation, regardless of how many
    # internal chunks/pages the handler touches).
    if normalized_name == "read_knowledge":
        try:
            ctx.reserve_read()
        except Exception:
            # Budget tracking should never break tool execution.
            pass
    business_id = getattr(conversation, "business_profile_id", None)
    try:
        with tenant_context(business_id):
            result = handler(arguments, conversation=conversation, context=ctx)
    except ToolConstraintError as exc:
        if isinstance(exc, SearchBudgetExceeded):
            result = search_budget_exceeded_payload(ctx, reason="tool_constraint")
            if str(exc).strip():
                result["detail"] = str(exc).strip()
        else:
            status = "constraint_error"
            error_code = "constraint_error"
            hint_text = str(exc) or "Tool constraint exceeded. Narrow the request and try again."
            if isinstance(exc, ToolRateLimitExceeded):
                status = "throttled"
                error_code = "rate_limited"
            elif isinstance(exc, CharacterBudgetExceeded):
                status = "throttled"
                error_code = "prompt_budget_exceeded"
            elif isinstance(exc, ChunkReadBudgetExceeded):
                status = "throttled"
                error_code = "chunk_read_budget_exceeded"
            elif isinstance(exc, ChunkPageBudgetExceeded):
                status = "throttled"
                error_code = "chunk_page_budget_exceeded"
            result = {
                "tool": normalized_name,
                "status": status,
                "error": error_code,
                "error_code": error_code,
            }
            if hint_text:
                result["hint"] = hint_text
    except Exception:
        logger.exception(
            "mcp.tool_failed tool=%s business=%s conversation=%s",
            normalized_name,
            getattr(conversation, "business_profile_id", None),
            getattr(conversation, "id", None),
        )
        result = {
            "tool": normalized_name,
            "status": "error",
            "error": "tool_failed",
            "error_code": "tool_failed",
            "hint": "Tool execution failed unexpectedly. Try a narrower request.",
        }

    # Layer 2: include per-turn budget snapshot in every knowledge tool response.
    if (
        bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
        and normalized_name in {"search_knowledge", "read_knowledge"}
        and isinstance(result, Mapping)
    ):
        enriched = dict(result)
        enriched["budget"] = ctx.budget_snapshot()
        return enriched

    return result


def _tool_schema_name(tool_def: Mapping[str, object]) -> str:
    function_block = tool_def.get("function")
    if not isinstance(function_block, Mapping):
        return ""
    return str(function_block.get("name") or "").strip()


# ---------------------------------------------------------------------------
# Shared helpers and tool handlers


ToolHandler = Callable[[Mapping[str, object], Conversation, ToolExecutionContext], Mapping[str, object]]


@lru_cache(maxsize=1)
def _knowledge_service() -> KnowledgeSearchService:
    """
    Lazily construct the shared KnowledgeSearchService.

    The underlying service is stateless with respect to conversations, so it is
    safe to reuse across tool calls within a process.
    """

    return KnowledgeSearchService()


@lru_cache(maxsize=1)
def _portal_file_embedding_service():
    """
    Lazily construct the shared embedding service for conversation-file retrieval.

    Uses the same provider selection as the knowledge base (OpenAI → local → None).
    """

    try:
        from apps.rag.embeddings import build_embedding_service
    except Exception:  # pragma: no cover - defensive
        return None
    return build_embedding_service()


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_priority(raw: object) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    aliases = {
        "low": "low",
        "medium": "medium",
        "med": "medium",
        "high": "high",
        "urgent": "high",
        "critical": "critical",
    }
    normalized = aliases.get(value, value)
    if normalized in {"low", "medium", "high", "critical"}:
        return normalized
    return None


@dataclass(frozen=True, slots=True)
class AgentKnowledgeScope:
    mode: str  # all|documents
    explicit_upload_ids: frozenset[str] = frozenset()

    @property
    def restricted(self) -> bool:
        return self.mode != "all"


_AGENT_SCOPE_SENTINEL: object = object()


def _agent_knowledge_scope(conversation: Conversation, context: ToolExecutionContext) -> AgentKnowledgeScope:
    cached = getattr(context, "_agent_knowledge_scope", _AGENT_SCOPE_SENTINEL)
    if isinstance(cached, AgentKnowledgeScope):
        return cached

    agent_id = getattr(conversation, "agent_profile_id", None)
    if not agent_id:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    agent = AgentProfile.objects.filter(
        id=agent_id,
        business_profile=conversation.business_profile,
    ).first()
    if agent is None:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    has_doc_rules = agent.allowed_documents.filter(business_profile=conversation.business_profile).exists()
    if not has_doc_rules:
        scope = AgentKnowledgeScope(mode="all")
        context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
        return scope

    explicit_upload_ids: set[str] = set()
    if has_doc_rules:
        explicit_upload_ids.update(
            str(value)
            for value in apply_customer_visible_uploads(
                agent.allowed_documents.filter(
                    business_profile=conversation.business_profile,
                    status=KnowledgeStatus.ACTIVE,
                )
            ).values_list("id", flat=True)
        )

    scope = AgentKnowledgeScope(
        mode="documents",
        explicit_upload_ids=frozenset(explicit_upload_ids),
    )
    context._agent_knowledge_scope = scope  # type: ignore[attr-defined]
    return scope


def _agent_scope_allows_upload(
    *,
    scope: AgentKnowledgeScope,
    conversation: Conversation,
    upload_id: uuid.UUID,
) -> bool:
    if not scope.restricted:
        return True
    return str(upload_id) in scope.explicit_upload_ids


def _apply_agent_scope_to_upload_queryset(queryset, scope: AgentKnowledgeScope):
    if not scope.restricted:
        return queryset
    if not scope.explicit_upload_ids:
        return queryset.none()
    return queryset.filter(id__in=list(scope.explicit_upload_ids)).distinct()


def _scope_upload_ids_to_uuids(scope: Iterable[str] | None) -> list[uuid.UUID] | None:
    if scope is None:
        return None
    ids: list[uuid.UUID] = []
    for value in scope:
        try:
            ids.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return ids


def _query_intent(query: str) -> dict[str, object]:
    """
    Lightweight heuristic to classify the query and suggest search/read defaults.
    """
    text = (query or "").strip()
    lowered = text.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", lowered) if t]
    has_digits = any(ch.isdigit() for ch in text)
    identifier_like = has_digits or any(sym in text for sym in ("-", "_"))
    table_hit = any(
        kw in lowered
        for kw in (
            "table",
            "sheet",
            "csv",
            "grid",
            "column",
            "row",
            "spreadsheet",
            "report",
            "statement",
        )
    )
    length = len(tokens)
    if identifier_like and length <= 6:
        intent = "identifier"
    elif table_hit:
        intent = "table"
    elif length >= 14:
        intent = "long"
    else:
        intent = "short"
    return {
        "intent": intent,
        "tokens": length,
        "has_digits": has_digits,
        "table": table_hit,
    }


def _snippet_text_for_sufficiency(payload: Mapping[str, object]) -> str:
    for key in ("content", "summary", "snippet", "preview", "text", "raw_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _snippet_has_truncation(payload: Mapping[str, object]) -> bool:
    if payload.get("truncated") or payload.get("partial_index") or payload.get("truncation_note"):
        return True
    diagnostics = payload.get("source_diagnostics")
    if not isinstance(diagnostics, Mapping):
        return False
    return any(
        diagnostics.get(key)
        for key in (
            "table_truncated",
            "truncated_rows",
            "truncated_columns",
            "truncated_tables",
            "truncated_entities",
            "table_partial_tables",
            "partial_index",
        )
    )


def _snippet_has_table_truncation(payload: Mapping[str, object]) -> bool:
    diagnostics = payload.get("source_diagnostics")
    if not isinstance(diagnostics, Mapping):
        return False
    return any(
        diagnostics.get(key)
        for key in (
            "table_truncated",
            "truncated_rows",
            "truncated_columns",
            "truncated_tables",
            "table_partial_tables",
        )
    )


def _compute_read_required(
    payload: Mapping[str, object],
) -> tuple[bool, list[str]]:
    read_state = str(payload.get("read_state") or "summary").strip().lower()
    if read_state not in {"summary", "preview"}:
        return False, []

    reasons: list[str] = []
    snippet_text = _snippet_text_for_sufficiency(payload)
    if not snippet_text:
        reasons.append("preview_empty")

    if _snippet_has_truncation(payload):
        reasons.append("content_truncated")

    if _snippet_has_table_truncation(payload):
        reasons.append("table_truncated")

    return bool(reasons), reasons


def _match_knowledge_entry(
    context: ToolExecutionContext | None,
    identifiers: Sequence[str],
) -> Mapping[str, object] | None:
    """
    Locate snippet metadata associated with the requested chunk/upload.
    """

    if not context or not identifiers:
        return None
    normalized = {str(value).strip().lower() for value in identifiers if value}
    if not normalized:
        return None
    for entry in getattr(context, "knowledge_results", []):
        if not isinstance(entry, Mapping):
            continue
        candidate_ids = {
            str(entry.get("chunk_id") or "").strip().lower(),
            str(entry.get("upload_id") or "").strip().lower(),
            str(entry.get("id") or "").strip().lower(),
        }
        if normalized & {cid for cid in candidate_ids if cid}:
            return entry
    return None


def _detect_full_page_intent(
    conversation: Conversation,
    requested_mode: str | None,
    *,
    document_entry: Mapping[str, object] | None = None,
    upload: KnowledgeUpload | None = None,
) -> bool:
    """
    Decide whether to allow a full-page read. Prefers excerpts unless:
    - The visitor explicitly asks for a page/section.
    - The referenced table is small (few rows/columns) and not truncated.
    """

    def _coerce_int(value: object) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    if requested_mode:
        return requested_mode.strip().lower() == "full_page"

    latest = (
        conversation.messages.order_by("-sent_at", "-created_at")
        .first()
    )
    body = latest.body if latest else ""
    text = (body or "").lower()
    explicit_keywords = (
        "full page",
        "entire page",
        "whole page",
        "page ",
        "صفحة",
        "sheet",
        "section",
        "document",
        "pdf",
    )
    if any(keyword in text for keyword in explicit_keywords):
        return True

    diag = document_entry.get("source_diagnostics") if isinstance(document_entry, Mapping) else {}
    truncated = bool(diag.get("table_truncated"))
    total_rows = _coerce_int(diag.get("table_total_rows") or diag.get("table_indexed_rows"))
    if not total_rows and upload:
        metadata = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
        table_stats = metadata.get("table_stats") if isinstance(metadata, Mapping) else None
        if isinstance(table_stats, Mapping):
            total_rows = _coerce_int(table_stats.get("total_rows"))
            truncated = truncated or bool(table_stats.get("partial_index"))

    if total_rows and total_rows <= 20 and not truncated:
        return True

    return False


def _budget_allows_full_page(
    context: ToolExecutionContext,
    *,
    business_profile,
    service: KnowledgeSearchService,
) -> bool:
    if context.char_budget_per_turn is None:
        return True
    inline_cap = service.inline_char_limit_for_business(business_profile)
    remaining = max(0, context.char_budget_per_turn - context.characters_used)
    threshold = max(800, int(inline_cap * 0.6))
    return remaining >= threshold


def _apply_seen_item_filter(
    snippets: list[dict[str, object]],
    context: ToolExecutionContext,
    mark_as_seen: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Compute seen-item metadata without filtering results.

    We track which items were previously shown so the LLM can decide
    whether to repeat or summarize. We no longer suppress repeats.
    """
    if not snippets:
        return snippets, {"shown": 0, "total_found": 0, "already_seen": 0, "has_more": False}

    already_seen_count = 0
    for snippet in snippets:
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        if not chunk_id:
            continue
        if context.is_chunk_seen(chunk_id):
            already_seen_count += 1
        if mark_as_seen:
            context.mark_chunk_shown(chunk_id)

    completeness = {
        "shown": len(snippets),
        "total_found": len(snippets),
        "already_seen": already_seen_count,
        "has_more": False,
    }

    return snippets, completeness


def _mark_snippets_as_seen(snippets: list[dict[str, object]], context: ToolExecutionContext) -> None:
    """Mark snippets as seen after they've been finalized for the response."""
    for snippet in snippets:
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        if chunk_id:
            context.mark_chunk_shown(chunk_id)


def _apply_seen_row_filter(
    rows: list[dict[str, object]],
    document_id: str,
    context: ToolExecutionContext,
    mark_as_seen: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Compute seen-row metadata without filtering results."""
    if not rows:
        return rows, {"shown": 0, "total_found": 0, "already_seen": 0, "has_more": False}

    already_seen_count = 0
    for row in rows:
        row_index = row.get("row_index")
        if row_index is None:
            continue
        if context.is_row_seen(document_id, row_index):
            already_seen_count += 1
        if mark_as_seen:
            context.mark_row_shown(document_id, row_index)

    completeness = {
        "shown": len(rows),
        "total_found": len(rows),
        "already_seen": already_seen_count,
        "has_more": False,
    }

    return rows, completeness


def _mark_rows_as_seen(rows: list[dict[str, object]], document_id: str, context: ToolExecutionContext) -> None:
    """Mark rows as seen after they've been finalized for the response."""
    for row in rows:
        row_index = row.get("row_index")
        if row_index is not None:
            context.mark_row_shown(document_id, row_index)


def _serialize_snippets(snippets: Sequence[object]) -> list[dict[str, object]]:
    """
    Convert KnowledgeSnippet instances into prompt/diagnostic-friendly dicts.
    """

    payloads: list[dict[str, object]] = []
    for snippet in snippets:
        try:
            payloads.append(serialize_knowledge_snippet(snippet))
        except Exception:
            continue
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, object]] = []
    for entry in payloads:
        evidence_group_id = str(entry.get("evidence_group_id") or "").strip()
        chunk_id = str(entry.get("chunk_id") or "").strip()
        entry_id = str(entry.get("id") or "").strip()
        upload_id = str(entry.get("upload_id") or "").strip() or None
        identity = evidence_group_id or chunk_id or entry_id
        if not identity:
            deduped.append(entry)
            continue
        key = (identity, upload_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _sanitize_snippet_payloads_for_prompt(
    snippet_payloads: Sequence[Mapping[str, object]],
    *,
    conversation: Conversation,
) -> list[dict[str, object]]:
    if not snippet_payloads:
        return []
    sanitized: list[dict[str, object]] = []
    for payload in snippet_payloads:
        if not isinstance(payload, Mapping):
            continue
        out = dict(payload)
        # Never pass extracted identifier values to the LLM; they can leak user data.
        out.pop("identifiers", None)
        sanitized.append(out)
    return sanitized


def _estimate_tokens(characters: int) -> int:
    """
    Rough heuristic to approximate tokens for logging purposes.
    """

    if characters <= 0:
        return 0
    return max(1, math.ceil(characters / 4))


TOTAL_COLUMN_KEYWORDS = (
    "total",
    "sum",
    "overall",
    "اجمالي",
    "إجمالي",
    "الاجمالي",
    "المجموع",
)
PRIMARY_TOTAL_TERMS = (
    "total",
    "overall",
    "sum",
    "اجمالي",
    "إجمالي",
    "الاجمالي",
    "المجموع",
)


def _normalize_column_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


def _normalize_identifier_value(value: object) -> str:
    """
    Normalize identifier-like values (invoice/order/ticket IDs) for strict matching.

    We remove whitespace and lowercase to avoid common ingestion artefacts like
    padding while preserving punctuation/hyphens.
    """

    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.strip("`\"'")
    return re.sub(r"\s+", "", text).lower()


_IDENTIFIER_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _extract_identifier_candidate(text: object) -> str | None:
    raw = _coerce_str(text).strip()
    if not raw:
        return None
    cleaned = raw.strip("`\"'")
    if not cleaned:
        return None
    if _IDENTIFIER_EMAIL_RE.match(cleaned):
        return cleaned
    if cleaned.isdigit() and len(cleaned) >= 6:
        return cleaned
    digit_runs = re.findall(r"\d{6,}", cleaned)
    if digit_runs:
        return max(digit_runs, key=len)
    token_runs = re.findall(r"[A-Za-z0-9][A-Za-z0-9_/-]{7,}", cleaned)
    if token_runs:
        return max(token_runs, key=len)
    return None


def _pick_best_identifier_column(
    columns: Sequence[str],
    *,
    query_text: str,
    identifier_value: str,
) -> str | None:
    candidates: list[str] = []
    for col in columns:
        if not isinstance(col, str):
            continue
        col_clean = col.strip()
        if not col_clean:
            continue
        if _column_suggests_identifier(col_clean):
            candidates.append(col_clean)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    query_norm = _normalize_column_name(query_text)
    ident_norm = _normalize_identifier_value(identifier_value)
    is_email = "@" in ident_norm
    is_digits = ident_norm.isdigit()

    best: str | None = None
    best_score = -1
    for col in candidates:
        col_norm = _normalize_column_name(col)
        if not col_norm:
            continue
        score = 0

        if is_email:
            if any(term in col_norm for term in ("email", "e-mail", "mail")):
                score += 200
        if is_digits:
            if "invoice" in col_norm:
                score += 80
            if "serial" in col_norm:
                score += 50
            if "order" in col_norm:
                score += 60
            if "ticket" in col_norm:
                score += 60
        if "invoice" in query_norm and "invoice" in col_norm:
            score += 60
        if "order" in query_norm and "order" in col_norm:
            score += 60
        if "ticket" in query_norm and "ticket" in col_norm:
            score += 60
        if "serial" in query_norm and "serial" in col_norm:
            score += 25
        if any(term in query_norm for term in ("id", "ref", "#")) and any(term in col_norm for term in ("id", "ref", "#")):
            score += 15
        if any(term in col_norm for term in ("id", "ref", "reference", "number", "no", "#")):
            score += 10

        if score > best_score:
            best_score = score
            best = col

    if best is None:
        return None
    if best_score >= 20:
        return best
    return None


def _column_suggests_identifier(column: object) -> bool:
    normalized = _normalize_column_name(column)
    if not normalized:
        return False
    if any(term in normalized for term in ("email", "e-mail", "mail")):
        return True
    if any(term in normalized for term in ("phone", "mobile", "msisdn")):
        return True
    if any(term in normalized for term in ("invoice", "order", "ticket")):
        if any(term in normalized for term in ("id", "serial", "number", "no", "ref", "reference", "#")):
            return True
        return True
    if "serial" in normalized or "reference" in normalized or re.search(r"(?:^|[\s_-])ref(?:$|[\s_-])", normalized):
        return True
    if re.search(r"(?:^|[\s_-])id(?:$|[\s_-])", normalized):
        return True
    if " code" in normalized or normalized.endswith("code") or "sku" in normalized:
        return True
    if "number" in normalized or normalized.endswith(" no") or normalized.endswith(" #"):
        return True
    return False


def _should_force_exact_identifier_match(column: object, value: object) -> bool:
    """
    Decide whether we should force op=eq (and reject contains/prefix) for this filter.
    """

    normalized = _normalize_identifier_value(value)
    if not normalized:
        return False
    if "@" in normalized:
        return True
    if normalized.isdigit() and len(normalized) >= 6:
        return True
    if len(normalized) >= 8 and any(ch.isdigit() for ch in normalized):
        return True
    if _column_suggests_identifier(column) and len(normalized) >= 4:
        return True
    return False


def _total_column_priority(value: object) -> int:
    normalized = _normalize_column_name(value)
    if not normalized:
        return 0
    if normalized in PRIMARY_TOTAL_TERMS:
        return 3
    for term in PRIMARY_TOTAL_TERMS:
        if normalized.startswith(f"{term} ") or normalized.endswith(f" {term}"):
            return 2
    if any(keyword in normalized for keyword in TOTAL_COLUMN_KEYWORDS):
        return 1
    return 0


def _is_total_column_label(value: object) -> bool:
    return _total_column_priority(value) > 0


def _snippet_payload_metrics(snippets: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """
    Compute lightweight telemetry for snippet payloads so we can benchmark prompt costs.
    """

    total_chars = 0
    chunk_count = 0
    upload_count = 0
    table_rich = 0
    issue_rich = 0
    truncated_tables = 0
    read_state_counter: Counter[str] = Counter()

    for entry in snippets:
        content = entry.get("content")
        if not isinstance(content, str):
            content = entry.get("summary") if isinstance(entry.get("summary"), str) else ""
        total_chars += len(content or "")
        if entry.get("chunk_id"):
            chunk_count += 1
        else:
            upload_count += 1
        structured_table_count = 0
        issue_count = 0
        try:
            structured_table_count = int(entry.get("structured_table_count") or 0)
            issue_count = int(entry.get("issue_count") or 0)
        except (TypeError, ValueError):
            structured_table_count = 0
            issue_count = 0
        if structured_table_count:
            table_rich += 1
        if issue_count:
            issue_rich += 1
        diag = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
        if diag and diag.get("table_truncated"):
            truncated_tables += 1
        read_state = str(entry.get("read_state") or "summary")
        read_state_counter[read_state] += 1

    metrics = {
        "snippet_count": len(snippets),
        "chunk_snippet_count": chunk_count,
        "upload_snippet_count": upload_count,
        "char_count": total_chars,
        "token_estimate": _estimate_tokens(total_chars),
        "table_snippet_count": table_rich,
        "issue_snippet_count": issue_rich,
        "table_truncated_count": truncated_tables,
        "read_state_breakdown": dict(read_state_counter),
    }
    return metrics


def _log_tool_metrics(
    *,
    tool: str,
    conversation: Conversation,
    snippets: Sequence[Mapping[str, object]],
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """
    Emit structured telemetry for MCP tools without changing runtime behavior.
    """
    metrics = _snippet_payload_metrics(snippets)
    if extra:
        merged_extra = dict(extra)
    else:
        merged_extra = {}
    merged_extra.update(metrics)
    structured_log(
        "mcp",
        f"tool.{tool}",
        merged_extra,
        context={"business": conversation.business_profile_id, "conversation": conversation.id},
    )
    tags = {
        "tool": tool,
        "business": str(conversation.business_profile_id),
    }
    latency_monitor.observe("mcp.tool.char_count", metrics.get("char_count"), tags=tags)
    latency_monitor.observe("mcp.tool.snippet_count", metrics.get("snippet_count"), tags=tags)
    return metrics


def _snippet_preview_text(payload: Mapping[str, object]) -> str:
    candidates = [
        payload.get("raw_text"),
        payload.get("text"),
        payload.get("preview"),
        payload.get("content"),
        payload.get("summary"),
        payload.get("snippet"),
    ]
    for value in candidates:
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed[:200]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        first_row = rows[0]
        if isinstance(first_row, Mapping):
            cells = first_row.get("cells")
            if isinstance(cells, list):
                values: list[str] = []
                for cell in cells[:6]:
                    if isinstance(cell, Mapping):
                        text = cell.get("text") or cell.get("value")
                        if isinstance(text, str) and text.strip():
                            values.append(text.strip())
                if values:
                    return " | ".join(values)[:200]
    return ""


def _log_snippet_payloads(
    *,
    tool: str,
    conversation: Conversation,
    snippet_payloads: Sequence[Mapping[str, object]],
    meta: Mapping[str, object] | None = None,
) -> None:
    preview_items: list[dict[str, object]] = []
    include_previews = _mcp_log_snippet_previews_enabled()
    include_pii = _mcp_log_pii_enabled()
    include_full_content = _mcp_log_full_snippet_content_enabled()
    for payload in snippet_payloads[:5]:
        upload_id = payload.get("upload_id")
        chunk_id = payload.get("chunk_id") or payload.get("id")
        preview_text = _snippet_preview_text(payload) if include_previews else ""
        preview: str | None = None
        preview_hash: str | None = None
        preview_len: int | None = None
        if preview_text:
            preview_len = len(preview_text)
            if include_pii:
                preview = preview_text
            else:
                preview_hash = sha256_hex(preview_text)

        item_dict = {
            "label": payload.get("public_label") or payload.get("title") or payload.get("label"),
            "upload_id": str(upload_id) if upload_id else None,
            "chunk_id": str(chunk_id) if chunk_id else None,
            "read_state": payload.get("read_state"),
            "read_required": bool(payload.get("read_required")),
            "read_required_reasons": payload.get("read_required_reasons"),
            "is_table_chunk": bool(payload.get("is_table_chunk")),
            "score": payload.get("score"),
            "preview": preview,
            "preview_sha256": preview_hash,
            "preview_len": preview_len,
        }

        # Add full content when enabled
        if include_full_content:
            content = payload.get("content")
            if content is not None and content != "":
                content_text = content if isinstance(content, str) else str(content)
                item_dict["full_content_len"] = len(content_text)
                if include_pii:
                    item_dict["full_content"] = content_text
                else:
                    item_dict["full_content_sha256"] = sha256_hex(content_text)

            summary = payload.get("summary")
            if summary is not None and summary != "":
                summary_text = summary if isinstance(summary, str) else str(summary)
                item_dict["summary_len"] = len(summary_text)
                if include_pii:
                    item_dict["summary"] = summary_text
                else:
                    item_dict["summary_sha256"] = sha256_hex(summary_text)

            rows = payload.get("rows")
            if isinstance(rows, list) and rows:
                item_dict["rows_count"] = len(rows)
                if include_pii:
                    item_dict["rows"] = rows

        preview_items.append(item_dict)
    detail = dict(meta or {})
    if "query" in detail:
        query_value = _coerce_str(detail.pop("query")).strip()
        detail.update(_log_safe_text_fields("query", query_value or None))
    detail["snippet_count"] = len(snippet_payloads)
    if preview_items:
        detail["snippets"] = [{k: v for k, v in entry.items() if v not in (None, "")} for entry in preview_items]
    structured_log(
        "mcp",
        f"{tool}.snippets",
        detail,
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )


def _maybe_throttle_full_page(
    context: ToolExecutionContext,
    business_profile,
    service: KnowledgeSearchService,
) -> dict[str, object] | None:
    """
    Determine if a full-page request should be downgraded to an excerpt.
    """

    limit = context.char_budget_per_turn
    if limit is not None and limit > 0:
        remaining = max(0, limit - context.characters_used)
        inline_cap = service.inline_char_limit_for_business(business_profile)
        threshold = max(1200, int(inline_cap * 0.75))
        if remaining < threshold:
            return {
                "reason": "char_budget_low",
                "remaining_characters": remaining,
                "threshold": threshold,
            }
    page_limit = context.max_chunk_pages_per_turn
    if page_limit is not None and page_limit > 0:
        throttle_floor = max(1, int(page_limit * 0.5))
        if context.chunk_pages_used > throttle_floor:
            return {
                "reason": "page_window_throttle",
                "used_pages": context.chunk_pages_used,
                "page_limit": page_limit,
            }
    return None


def _extract_no_result_reason(diagnostics: Mapping[str, object] | None) -> str:
    valid_reasons = {"not_found", "not_applicable_to_segment", "insufficient_evidence"}
    diag = diagnostics or {}
    reason = str(diag.get("no_result_reason") or "").strip().lower()
    if reason in valid_reasons:
        return reason
    contract = diag.get("auto_decision_contract")
    if isinstance(contract, Mapping):
        contract_reason = str(contract.get("no_result_reason") or "").strip().lower()
        if contract_reason in valid_reasons:
            return contract_reason
    return ""


def _extract_conflict_detected(diagnostics: Mapping[str, object] | None) -> bool:
    diag = diagnostics or {}
    if bool(diag.get("conflict_detected")):
        return True
    contract = diag.get("auto_decision_contract")
    if isinstance(contract, Mapping):
        return bool(contract.get("conflict_detected"))
    return False


def _is_uuid_ref_id(value: object) -> bool:
    try:
        uuid.UUID(str(value or "").strip())
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _scope_invalid_read_retry_limit() -> int:
    try:
        configured = int(getattr(settings, "MCP_INVALID_READ_REF_RETRY_LIMIT", 2) or 2)
    except (TypeError, ValueError):
        configured = 2
    return max(1, min(5, configured))


def _build_ingestion_warnings(
    snippet_payloads: Sequence[Mapping[str, object]],
    knowledge_reads: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """
    Derive ingestion warnings from snippet diagnostics, mirroring the legacy path.
    """

    if not knowledge_reads:
        return []
    relevant_ids: set[str] = set()
    for read in knowledge_reads:
        identifier = read.get("id")
        if identifier:
            relevant_ids.add(str(identifier))
    if not relevant_ids:
        return []

    warnings: list[dict[str, object]] = []
    for entry in snippet_payloads:
        entry_id = str(entry.get("id") or "")
        if entry_id not in relevant_ids:
            continue
        label = entry.get("public_label") or entry.get("title") or "Knowledge source"
        upload_id = str(entry.get("upload_id") or entry_id)
        # Issue-derived warnings
        warnings.extend(
            issue_warning_payloads(
                entry.get("issues"),
                label=label,  # type: ignore[arg-type]
                upload_id=upload_id,
            )
        )
        diagnostics = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
        partial_index = bool(entry.get("partial_index"))
        truncation_note_val = entry.get("truncation_note")
        truncation_note = truncation_note_val if isinstance(truncation_note_val, str) else None
        diag_warning = diagnostic_warning_payload(
            label=label,  # type: ignore[arg-type]
            upload_id=upload_id,
            diagnostics=diagnostics,
            partial_index=partial_index,
            truncation_note=truncation_note,
        )
        if diag_warning:
            warnings.append(diag_warning)  # type: ignore[arg-type]
    return warnings


def _convert_to_agentic_search_response(
    legacy_payload: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext | None = None,
) -> dict[str, object]:
    """
    Convert legacy search_knowledge response to agentic format.

    Phase 1 (EvidenceRefs): return pointers only (optionally with short previews).

    The agentic format returns a compact list of "refs" the model can read via
    read_knowledge(). This intentionally avoids duplicating facts in multiple
    representations (table row + text restatement) and keeps the prompt small.
    """
    snippets = legacy_payload.get("snippets", [])
    refs: list[dict[str, object]] = []
    agentic_read_v2_enabled = (
        bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
        and bool(getattr(settings, "MCP_AGENTIC_READ_V2_ENABLED", False))
    )
    preview_full_enabled = bool(getattr(settings, "MCP_AGENTIC_SEARCH_PREVIEWS_ENABLED", False))
    preview_hybrid_enabled = bool(getattr(settings, "MCP_AGENTIC_SEARCH_PREVIEWS_HYBRID_ENABLED", False))
    try:
        preview_chars_cap = int(getattr(settings, "MCP_PROMPT_SNIPPET_CONTENT_CHARS", 1200) or 1200)
    except (TypeError, ValueError):
        preview_chars_cap = 1200
    preview_chars_cap = max(0, preview_chars_cap)
    hybrid_preview_max_items = 10
    hybrid_preview_chars_cap = min(preview_chars_cap, 400) if preview_chars_cap else 0
    previews_attached = 0

    def _preview_text(snippet: Mapping[str, object], *, max_chars: int) -> tuple[str, bool]:
        if max_chars <= 0:
            return "", False
        raw = snippet.get("summary") or snippet.get("content") or ""
        if not isinstance(raw, str):
            raw = str(raw or "")
        text = raw.strip()
        if not text:
            return "", False
        if len(text) <= max_chars:
            return text, False
        return text[:max_chars].rstrip() + "…", True

    def _is_table_direct(snippet: Mapping[str, object]) -> bool:
        stage = str(snippet.get("search_stage") or "").strip().lower()
        source = str(snippet.get("source") or "").strip().lower()
        return stage in {"table_direct", "table_blended"} or source == "table_direct"

    def _looks_like_chunk_label(value: object) -> bool:
        text = str(value or "").strip().lower()
        return bool(text and "chunk " in text)

    def _strip_locator_suffix(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\s+[–-]\s+chunk\s+\d+\s*$", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s+[–-]\s+table\s+preview\s*$", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s+[–-]\s+table\s+\d+\s*$", "", text, flags=re.IGNORECASE).strip()
        return text

    def _document_name(snippet: Mapping[str, object], *, fallback_title: object) -> str:
        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        for candidate in (
            snippet.get("document"),
            snippet.get("document_name"),
            snippet.get("file_name"),
            snippet.get("source_file"),
            diagnostics.get("document_name"),
            diagnostics.get("file_name"),
            fallback_title,
            diagnostics.get("table_title"),
        ):
            text = _strip_locator_suffix(candidate)
            if text and text.lower() not in {"file upload", "table_direct", "table direct"}:
                return text[:180]
        return "Knowledge"

    def _anchor_key(snippet: Mapping[str, object]) -> str:
        """Canonical dedupe key for EvidenceRefs (avoid duplicates across search stages)."""
        diagnostics = snippet.get("source_diagnostics") if isinstance(snippet.get("source_diagnostics"), Mapping) else {}
        table_id = diagnostics.get("table_id")
        # Preserve row_index=0 (0 is valid but falsy).
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        if table_id and row_index is not None:
            return f"table:{table_id}:{row_index}"
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        upload_id = str(snippet.get("upload_id") or "").strip()
        if upload_id:
            return f"upload:{upload_id}"
        return f"fallback:{sha256_hex(json.dumps(dict(snippet), sort_keys=True, default=str)[:800])}"

    def _evidence_group_key(snippet: Mapping[str, object]) -> str:
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        if evidence_group_id:
            return f"evidence:{evidence_group_id}"
        return _anchor_key(snippet)

    def _representation(snippet: Mapping[str, object]) -> str:
        raw = str(snippet.get("representation") or "").strip().lower()
        if raw in {"text", "table", "json"}:
            return raw
        if bool(snippet.get("is_table_chunk")):
            return "table"
        if str(snippet.get("entity_type") or "").strip():
            return "json"
        return "text"

    def _evidence_tokens(text: str) -> set[str]:
        normalized = re.sub(r"[^a-z0-9%$ ]+", " ", (text or "").lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return set()
        return {
            token
            for token in normalized.split(" ")
            if token and (len(token) >= 3 or token.isdigit())
        }

    def _evidence_conflict(snippets_for_group: Sequence[Mapping[str, object]]) -> bool:
        if len(snippets_for_group) < 2:
            return False
        by_representation: dict[str, Mapping[str, object]] = {}
        for snippet in snippets_for_group:
            by_representation.setdefault(_representation(snippet), snippet)
        if len(by_representation) < 2:
            return False
        reps = list(by_representation.keys())
        for i, left_rep in enumerate(reps):
            for right_rep in reps[i + 1 :]:
                left = by_representation[left_rep]
                right = by_representation[right_rep]
                left_text = str(left.get("content") or left.get("summary") or "")
                right_text = str(right.get("content") or right.get("summary") or "")
                left_tokens = _evidence_tokens(left_text)
                right_tokens = _evidence_tokens(right_text)
                if min(len(left_tokens), len(right_tokens)) < 4:
                    continue
                union = left_tokens | right_tokens
                if not union:
                    continue
                overlap = len(left_tokens & right_tokens) / len(union)
                if overlap < 0.25:
                    return True
        return False

    def _coerce_int(value: object) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _snippet_row_index(snippet: Mapping[str, object]) -> int | None:
        diagnostics_local = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        row_index_local = diagnostics_local.get("row_index")
        if row_index_local is None:
            row_index_local = diagnostics_local.get("table_row_index")
        return _coerce_int(row_index_local)

    def _snippet_char_estimate(snippet: Mapping[str, object]) -> int:
        diagnostics_local = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        content = snippet.get("content") or ""
        summary = snippet.get("summary") or ""
        char_estimate_local = len(content) if content else len(summary) * 3
        limit_hint = 0
        for key in ("inline_char_limit", "page_char_limit"):
            try:
                limit_hint = max(limit_hint, int(diagnostics_local.get(key) or 0))
            except (TypeError, ValueError):
                continue
        if limit_hint:
            char_estimate_local = max(
                char_estimate_local,
                min(limit_hint, int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)),
            )
        if bool(snippet.get("is_table_chunk")) and _snippet_row_index(snippet) is None:
            row_count = (
                diagnostics_local.get("table_total_rows")
                or diagnostics_local.get("table_row_count")
                or snippet.get("row_count")
            )
            column_count = diagnostics_local.get("table_column_count") or snippet.get("column_count")
            if row_count and column_count:
                try:
                    table_size_estimate = int(row_count) * int(column_count) * 25 + int(row_count) * 32
                    char_estimate_local = max(char_estimate_local, table_size_estimate)
                except (TypeError, ValueError):
                    pass
        return max(0, int(char_estimate_local))

    def _suggest_max_chars_for_estimate(char_estimate: object, *, max_chars_allowed: int) -> int:
        """
        Convert a rough char estimate into a safe `max_chars` suggestion for read_knowledge.

        We intentionally add headroom so the model doesn't under-allocate and get truncated.
        """
        try:
            estimate = int(char_estimate or 0)
        except (TypeError, ValueError):
            estimate = 0
        max_chars_allowed_int = max(1, int(max_chars_allowed))

        if estimate <= 0:
            return min(int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT), max_chars_allowed_int)

        # +20% headroom + a small constant for JSON/table framing overhead.
        suggested = int(estimate * 1.2) + 200
        suggested = max(500, suggested)
        return min(max_chars_allowed_int, suggested)

    def _suggest_table_read_chars(
        *,
        base_suggested: int,
        row_count: object,
        column_count: object,
        char_estimate_local: int,
        row_index: object,
        max_chars_allowed: int,
    ) -> int:
        rows = _coerce_int(row_count) or 0
        cols = _coerce_int(column_count) or 0
        # Prefer explicit column counts when available; otherwise use a conservative default.
        cols = cols if cols > 0 else 5
        row_index_int = _coerce_int(row_index)
        if row_index_int is not None and row_index_int >= 0:
            estimated_row_payload_chars = max(
                int(char_estimate_local),
                int(char_estimate_local + cols * 22 + 180),
            )
            tuned = _suggest_max_chars_for_estimate(
                estimated_row_payload_chars,
                max_chars_allowed=max_chars_allowed,
            )
            return max(500, min(int(max_chars_allowed), int(tuned)))
        if rows <= 0:
            return int(base_suggested)
        estimated_table_chars = max(
            int(char_estimate_local),
            int(rows * max(80, cols * 22) + 400),
        )
        tuned = _suggest_max_chars_for_estimate(
            estimated_table_chars,
            max_chars_allowed=max_chars_allowed,
        )
        if rows >= 25:
            tuned = max(tuned, 1800)
        if rows >= 40:
            tuned = max(tuned, 2800)
        if rows >= 80:
            tuned = max(tuned, 4500)
        return max(500, min(int(max_chars_allowed), int(tuned)))

    # Evidence planner (Phase 1): convert search snippets into lightweight refs.
    # Dedupe by canonical anchors so the model doesn't see the same fact twice, but
    # do not drop entire categories of evidence based on search stage (agentic mode
    # can page/iterate if it needs more).
    raw_snippets: list[Mapping[str, object]] = [
        snippet for snippet in snippets if isinstance(snippet, Mapping)
    ]
    table_direct_present = any(_is_table_direct(snippet) for snippet in raw_snippets)
    candidate_snippets = raw_snippets

    seen_evidence_keys: set[str] = set()
    planned_snippets: list[Mapping[str, object]] = []
    evidence_variants: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    conflict_keys: set[str] = set()
    for snippet in candidate_snippets:
        key = _evidence_group_key(snippet)
        evidence_variants[key].append(snippet)
        if key in seen_evidence_keys:
            continue
        seen_evidence_keys.add(key)
        planned_snippets.append(snippet)
    for key, group_snippets in evidence_variants.items():
        if _evidence_conflict(group_snippets):
            conflict_keys.add(key)

    def _canonical_table_id(raw_table_id: object) -> str:
        value = str(raw_table_id or "").strip()
        if not value:
            return ""
        try:
            return str(uuid.UUID(value))
        except (TypeError, ValueError):
            return ""

    table_row_ref_counts: Counter[str] = Counter()
    table_row_hit_scores: dict[str, dict[int, float]] = defaultdict(dict)
    table_estimated_rows: dict[str, int] = {}
    table_estimated_columns: dict[str, int] = {}
    for snippet in planned_snippets:
        if not bool(snippet.get("is_table_chunk")):
            continue
        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        table_id = diagnostics.get("table_id")
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        if not table_id or row_index is None:
            continue
        canonical_table_id = _canonical_table_id(table_id)
        if not canonical_table_id:
            continue
        row_index_int = _coerce_int(row_index)
        if row_index_int is None or row_index_int < 0:
            continue
        table_row_ref_counts[canonical_table_id] += 1

        score_val = 0.0
        try:
            raw_score = snippet.get("confidence_score")
            if raw_score is not None:
                score_val = float(raw_score)
        except (TypeError, ValueError):
            score_val = 0.0
        prior = table_row_hit_scores[canonical_table_id].get(int(row_index_int))
        if prior is None or score_val > float(prior):
            table_row_hit_scores[canonical_table_id][int(row_index_int)] = float(score_val)

        est_rows = _coerce_int(
            diagnostics.get("table_total_rows")
            or diagnostics.get("table_row_count")
            or snippet.get("row_count")
        )
        if est_rows is not None and est_rows > 0:
            table_estimated_rows[canonical_table_id] = max(
                int(table_estimated_rows.get(canonical_table_id, 0)),
                int(est_rows),
            )
        est_cols = _coerce_int(diagnostics.get("table_column_count") or snippet.get("column_count"))
        if est_cols is not None and est_cols > 0:
            table_estimated_columns[canonical_table_id] = max(
                int(table_estimated_columns.get(canonical_table_id, 0)),
                int(est_cols),
            )
    tables_with_multiple_row_refs = {
        table_id
        for table_id, count in table_row_ref_counts.items()
        if int(count) >= 2
    }

    # Table-row promotion is intentionally disabled on the active path.
    # We keep distinct row refs visible to the model instead of collapsing
    # semantically different row hits into one table-level winner.
    promoted_table_candidates: set[str] = set()
    promote_table_context = False
    promoted_table_ids: set[str] = set()
    precomputed_table_anchor_manifests: dict[str, dict[str, object]] = {}

    def _build_multi_anchor_manifest(table_id: str) -> dict[str, object] | None:
        row_scores = table_row_hit_scores.get(table_id)
        if not isinstance(row_scores, Mapping) or not row_scores:
            return None
        row_items: list[tuple[int, float]] = []
        for raw_index, raw_score in row_scores.items():
            try:
                idx = int(raw_index)
            except (TypeError, ValueError):
                continue
            if idx < 0:
                continue
            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 0.0
            row_items.append((idx, score))
        if not row_items:
            return None
        row_items.sort(key=lambda item: item[0])

        clusters: list[list[tuple[int, float]]] = []
        current: list[tuple[int, float]] = []
        prev_idx: int | None = None
        for idx, score in row_items:
            if prev_idx is not None and current and (idx - prev_idx) >= 4:
                clusters.append(current)
                current = []
            current.append((idx, score))
            prev_idx = idx
        if current:
            clusters.append(current)

        anchor_candidates: list[tuple[float, int]] = []
        for cluster in clusters:
            # Highest score wins; tie-breaker is the lowest row index.
            best_idx, best_score = max(cluster, key=lambda item: (float(item[1]), -int(item[0])))
            anchor_candidates.append((float(best_score), int(best_idx)))
        anchor_candidates.sort(key=lambda item: (-float(item[0]), int(item[1])))

        anchor_rows = [int(idx) for _score, idx in anchor_candidates[:2]]
        if not anchor_rows:
            return None
        matched_row_index = int(anchor_rows[0])

        manifest: dict[str, object] = {
            "ref_id": str(table_id),
            "table_id": str(table_id),
            "matched_row_index": matched_row_index,
            "anchors": [{"row_index": int(idx)} for idx in anchor_rows],
        }
        est_rows = _coerce_int(table_estimated_rows.get(table_id))
        if est_rows is not None and est_rows > 0:
            manifest["estimated_rows"] = int(est_rows)
        est_cols = _coerce_int(table_estimated_columns.get(table_id))
        if est_cols is not None and est_cols > 0:
            manifest["estimated_columns"] = int(est_cols)
        return manifest

    for table_id in promoted_table_candidates:
        manifest = _build_multi_anchor_manifest(str(table_id))
        if isinstance(manifest, Mapping):
            precomputed_table_anchor_manifests[str(table_id)] = dict(manifest)

    promoted_table_anchor_manifests: dict[str, dict[str, object]] = {}

    # Preserve the service-provided RAG order. The retriever has already fused
    # table-direct, lexical, vector, alias, and context signals; re-sorting here
    # by confidence can promote generic anchors over exact table rows.
    for snippet in planned_snippets:

        # Determine type
        is_table = bool(snippet.get("is_table_chunk"))
        content_type = "table" if is_table else "text"
        representation = _representation(snippet)
        evidence_type = str(snippet.get("evidence_type") or "").strip().lower()

        # Get IDs
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        upload_id = str(snippet.get("upload_id") or "")
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        evidence_key = _evidence_group_key(snippet)

        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        char_estimate = _snippet_char_estimate(snippet)

        row_count = diagnostics.get("table_total_rows") or diagnostics.get("table_row_count") or snippet.get("row_count")
        column_count = diagnostics.get("table_column_count") or snippet.get("column_count")
        table_id = diagnostics.get("table_id")
        # Preserve row_index=0 (0 is valid but falsy).
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        canonical_table_id = _canonical_table_id(table_id) if (content_type == "table" and table_id) else ""
        if (
            content_type == "table"
            and canonical_table_id
            and row_index is None
            and canonical_table_id in tables_with_multiple_row_refs
        ):
            continue
        # Strict one-ref-per-promoted-table: skip chunk-only table refs when the table
        # is already eligible for promotion via row hits (we'll emit the table ref instead).
        if (
            content_type == "table"
            and canonical_table_id
            and canonical_table_id in promoted_table_candidates
            and row_index is None
        ):
            continue

        suggested_max_chars = _suggest_max_chars_for_estimate(
            char_estimate,
            max_chars_allowed=int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX),
        )
        if content_type == "table":
            suggested_max_chars = _suggest_table_read_chars(
                base_suggested=suggested_max_chars,
                row_count=row_count,
                column_count=column_count,
                char_estimate_local=char_estimate,
                row_index=row_index,
                max_chars_allowed=int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX),
            )

        # EvidenceRef fields
        entity_name = snippet.get("entity_name")
        title = snippet.get("title") or snippet.get("public_label") or "Untitled"
        label_parts: list[str] = []
        if isinstance(entity_name, str) and entity_name.strip():
            label_parts.append(entity_name.strip())
        if isinstance(title, str) and title.strip():
            label_parts.append(title.strip())
        label = " — ".join(label_parts) if label_parts else str(title or "Untitled")
        if isinstance(label, str) and len(label) > 240:
            label = f"{label[:240].rstrip()}…"

        kind = "text_anchor"
        promote_table_ref = bool(
            content_type == "table"
            and table_id
            and row_index is not None
            and promote_table_context
            and canonical_table_id in promoted_table_candidates
        )
        if content_type == "table":
            kind = "table_row" if (diagnostics.get("table_id") and row_index is not None) else "table_chunk"
            if promote_table_ref:
                kind = "table_chunk"

        coverage_hint: dict[str, object] = {}
        if evidence_group_id:
            coverage_hint["evidence_group_id"] = evidence_group_id
        if content_type == "table":
            if table_id:
                coverage_hint["table_id"] = str(table_id)
            if row_index is not None:
                coverage_hint["row_index"] = row_index
            if row_count is not None:
                coverage_hint["estimated_rows"] = row_count
            if column_count is not None:
                coverage_hint["estimated_columns"] = column_count
            page_number = snippet.get("page_number")
            if page_number is not None:
                try:
                    coverage_hint["page"] = int(page_number)
                except (TypeError, ValueError):
                    pass
        else:
            page_number = snippet.get("page_number")
            if page_number is not None:
                try:
                    coverage_hint["page"] = int(page_number)
                except (TypeError, ValueError):
                    pass
            else:
                chunk_index = snippet.get("chunk_index")
                if isinstance(chunk_index, int):
                    coverage_hint["offset"] = chunk_index

        ref_id = chunk_id
        if promote_table_ref and canonical_table_id:
            promoted_title = ""
            for candidate in (
                diagnostics.get("table_title"),
                diagnostics.get("section_heading"),
            ):
                candidate_text = str(candidate or "").strip()
                if candidate_text:
                    promoted_title = candidate_text
                    break
            if promoted_title:
                label_parts = []
                if isinstance(entity_name, str) and entity_name.strip():
                    normalized_entity_name = entity_name.strip()
                    if normalized_entity_name.lower() != promoted_title.lower():
                        label_parts.append(normalized_entity_name)
                label_parts.append(promoted_title)
                label = " — ".join(label_parts) if label_parts else promoted_title
            elif _looks_like_chunk_label(title):
                order_index = _coerce_int(diagnostics.get("table_order_index"))
                fallback_title = f"Table {order_index}" if order_index is not None and order_index >= 0 else "Table"
                label_parts = []
                if isinstance(entity_name, str) and entity_name.strip():
                    label_parts.append(entity_name.strip())
                label_parts.append(fallback_title)
                label = " — ".join(label_parts)
            if canonical_table_id not in promoted_table_anchor_manifests:
                manifest = precomputed_table_anchor_manifests.get(canonical_table_id)
                if isinstance(manifest, Mapping):
                    promoted_table_anchor_manifests[canonical_table_id] = dict(manifest)
                else:
                    matched_row_index = _coerce_int(row_index)
                    if matched_row_index is not None and matched_row_index >= 0:
                        promoted_table_anchor_manifests[canonical_table_id] = {
                            "ref_id": canonical_table_id,
                            "table_id": canonical_table_id,
                            "matched_row_index": int(matched_row_index),
                        }
            if canonical_table_id in promoted_table_ids:
                continue
            promoted_table_ids.add(canonical_table_id)
            ref_id = canonical_table_id
        if not ref_id:
            continue
        why: list[str] = []
        stage = str(snippet.get("search_stage") or "").strip().lower()
        if stage:
            why.append(f"search_stage:{stage}")
        if _is_table_direct(snippet):
            why.append("match:table_direct")
        if kind.startswith("table"):
            if promote_table_ref:
                why.append("kind:table_context")
            else:
                why.append("kind:table")
        else:
            why.append("kind:text")
        document = _document_name(snippet, fallback_title=title)
        ref_item: dict[str, object] = {
            "id": ref_id,
            "label": label,
            "document": document,
            "kind": kind,
            "read_chars": suggested_max_chars,
        }
        include_preview = False
        preview_cap = preview_chars_cap
        if preview_full_enabled and preview_chars_cap:
            include_preview = True
        elif (not preview_full_enabled) and preview_hybrid_enabled and hybrid_preview_chars_cap:
            # Hybrid mode: only attach previews to the top-ranked refs.
            include_preview = len(refs) < hybrid_preview_max_items
            preview_cap = hybrid_preview_chars_cap
        if include_preview:
            preview, preview_truncated = _preview_text(snippet, max_chars=preview_cap)
            if preview:
                ref_item["preview"] = preview
                if preview_truncated:
                    ref_item["preview_truncated"] = True
                previews_attached += 1
        conflict_flag = bool(diagnostics.get("evidence_conflict")) or (evidence_key in conflict_keys)
        if conflict_flag:
            why.append("evidence_conflict")
        if promote_table_ref and isinstance(coverage_hint, dict):
            matched_row_index: int | None = None
            anchor_row_indexes: list[int] = []
            if canonical_table_id:
                manifest = precomputed_table_anchor_manifests.get(canonical_table_id)
                if isinstance(manifest, Mapping):
                    matched_row_index = _coerce_int(manifest.get("matched_row_index"))
                    raw_anchors = manifest.get("anchors")
                    if isinstance(raw_anchors, list):
                        for entry in raw_anchors:
                            parsed = _coerce_int(entry.get("row_index")) if isinstance(entry, Mapping) else _coerce_int(entry)
                            if parsed is None or parsed < 0:
                                continue
                            anchor_row_indexes.append(int(parsed))
            if matched_row_index is None:
                matched_row_index = _coerce_int(coverage_hint.get("row_index"))
            if matched_row_index is not None:
                coverage_hint["matched_row_index"] = int(matched_row_index)
            if anchor_row_indexes:
                seen_anchor_rows: set[int] = set()
                coverage_hint["anchor_row_indexes"] = [
                    row_index_value
                    for row_index_value in anchor_row_indexes
                    if not (row_index_value in seen_anchor_rows or seen_anchor_rows.add(row_index_value))
                ][:5]
            coverage_hint.pop("row_index", None)
        refs.append(ref_item)

    if context is not None and promoted_table_anchor_manifests:
        table_manifest_cache = getattr(context, "table_row_anchor_manifests", None)
        if isinstance(table_manifest_cache, dict):
            for table_ref_id, manifest in promoted_table_anchor_manifests.items():
                table_manifest_cache[str(table_ref_id)] = dict(manifest)
            while len(table_manifest_cache) > 100:
                oldest_key = next(iter(table_manifest_cache))
                table_manifest_cache.pop(oldest_key, None)

    # Build agentic response
    status = legacy_payload.get("status", "ok")
    total_found = legacy_payload.get("completeness", {}).get("total_found", len(refs))

    agentic_response: dict[str, object] = {
        "tool": "search_knowledge",
        "status": status if refs else "empty",
        "refs": refs,
    }

    # Provide a compact read budget so the LLM can plan max_chars for read_knowledge.
    if refs:
        total_suggested = 0
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            try:
                total_suggested += int(ref.get("read_chars") or 0)
            except (TypeError, ValueError):
                continue
        max_chars_allowed = int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)
        agentic_response["read_budget"] = {
            "suggested_chars": min(total_suggested, max_chars_allowed),
            "max_chars": max_chars_allowed,
        }

    # NOTE: In agentic mode, we may dedupe/collapse a legacy "page" of N snippets
    # down to fewer refs (e.g., 10 legacy snippets -> 3 refs). The legacy
    # completeness metadata refers to snippet paging, not ref paging, which is
    # confusing in debugging. Keep both counts and make `shown` consistent with
    # the actual returned refs.
    completeness_in = legacy_payload.get("completeness")
    completeness_out: dict[str, object] = {}
    if isinstance(completeness_in, Mapping) and completeness_in:
        completeness_out = dict(completeness_in)

    legacy_shown = _coerce_int(completeness_out.get("shown"))
    if legacy_shown is None:
        legacy_shown = len(raw_snippets)
    legacy_already_seen = _coerce_int(completeness_out.get("already_seen"))
    if legacy_already_seen is None:
        legacy_already_seen = 0

    planned_already_seen = legacy_already_seen
    if context is not None:
        planned_already_seen = 0
        for snippet in planned_snippets:
            if not isinstance(snippet, Mapping):
                continue
            chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
            if chunk_id and context.is_chunk_seen(chunk_id):
                planned_already_seen += 1

    completeness_out.setdefault("total_found", total_found)
    # Make agentic completeness consistent with what we actually returned.
    completeness_out["shown"] = len(refs)
    completeness_out["already_seen"] = planned_already_seen
    # Preserve the legacy counts for debug visibility.
    completeness_out["legacy_shown"] = legacy_shown
    completeness_out["legacy_already_seen"] = legacy_already_seen
    completeness_out["planned_unique_snippets"] = len(planned_snippets)
    completeness_out["raw_snippet_count"] = len(raw_snippets)

    if "has_more" not in completeness_out and "has_more" in legacy_payload:
        completeness_out["has_more"] = bool(legacy_payload.get("has_more"))

    if completeness_out:
        agentic_response["completeness"] = completeness_out

    # Pagination hints (cursor-based "next page" support). Keep these in one
    # canonical object so the public tool payload does not duplicate control
    # fields at the top level.
    pagination_out: dict[str, object] = {
        "shown": len(refs),
        "total": total_found,
    }
    if "has_more" in completeness_out:
        pagination_out["has_more"] = bool(completeness_out.get("has_more"))
    next_cursor = legacy_payload.get("next_cursor")
    if isinstance(next_cursor, str) and next_cursor.strip():
        pagination_out["next_cursor"] = (
            _store_search_cursor_handle(context, conversation, next_cursor.strip()) or next_cursor.strip()
        )
    agentic_response["pagination"] = pagination_out

    # Log the conversion for debugging
    structured_log(
        "mcp",
        "search.agentic_conversion",
        {
            "limit_requested": _coerce_int(legacy_payload.get("limit")) or 0,
            "legacy_snippet_count": len(snippets),
            "legacy_completeness_shown": _coerce_int(completeness_in.get("shown")) if isinstance(completeness_in, Mapping) else None,
            "agentic_ref_count": len(refs),
            "total_found": total_found,
            "agentic_read_v2_enabled": agentic_read_v2_enabled,
            "table_direct_present": table_direct_present,
            "table_context_promote_enabled": promote_table_context,
            "table_context_promote_candidates": len(promoted_table_candidates),
            "table_context_promoted_refs": len(promoted_table_ids),
            "planner_dropped": max(0, len(raw_snippets) - len(planned_snippets)),
            "planned_unique_snippets": len(planned_snippets),
            "evidence_conflict_groups": len(conflict_keys),
            "previews_full_enabled": preview_full_enabled,
            "previews_hybrid_enabled": preview_hybrid_enabled,
            "previews_attached_count": previews_attached,
            "table_anchor_manifests_cached": len(promoted_table_anchor_manifests),
            "rank_preserved": True,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )

    return agentic_response


def _fuse_batched_search_runs(
    runs: Sequence[Mapping[str, object]],
    *,
    clip_limit: int | None,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    def _snippet_dedupe_key(snippet: Mapping[str, object]) -> str:
        evidence_group_id = str(snippet.get("evidence_group_id") or "").strip()
        if evidence_group_id:
            return f"evidence:{evidence_group_id}"
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "").strip()
        if chunk_id:
            return f"chunk:{chunk_id}"
        upload_id = str(snippet.get("upload_id") or "").strip()
        if upload_id:
            return f"upload:{upload_id}"
        content = snippet.get("content")
        if isinstance(content, str) and content.strip():
            return f"content:{sha256_hex(content)}"
        summary = snippet.get("summary")
        if isinstance(summary, str) and summary.strip():
            return f"summary:{sha256_hex(summary)}"
        return json.dumps(snippet, sort_keys=True, default=str)

    if not runs:
        return [], None
    if len(runs) == 1:
        snippets = [
            dict(snippet)
            for snippet in runs[0].get("snippets", [])
            if isinstance(snippet, Mapping)
        ]
        if clip_limit:
            snippets = snippets[:clip_limit]
        return snippets, None

    fusion = {"method": "rrf_dedupe", "runs": len(runs)}
    fused: dict[str, dict[str, object]] = {}
    # Use rank fusion across query variants so later variants can still surface
    # the best shared evidence before we clip to the outward limit.
    rrf_k = 60.0
    for run_index, run in enumerate(runs):
        snippets = run.get("snippets", [])
        if not isinstance(snippets, Sequence) or isinstance(snippets, (str, bytes, bytearray)):
            continue
        for rank, snippet in enumerate(snippets):
            if not isinstance(snippet, Mapping):
                continue
            dedup_key = _snippet_dedupe_key(snippet)
            raw_confidence = snippet.get("confidence_score")
            try:
                confidence = float(raw_confidence) if raw_confidence is not None else 0.0
            except (TypeError, ValueError):
                confidence = 0.0
            entry = fused.get(dedup_key)
            rrf_increment = 1.0 / (rrf_k + float(rank) + 1.0)
            if entry is None:
                fused[dedup_key] = {
                    "snippet": dict(snippet),
                    "rrf_score": rrf_increment,
                    "best_confidence": confidence,
                    "best_rank": int(rank),
                    "best_run_index": int(run_index),
                }
                continue
            entry["rrf_score"] = float(entry.get("rrf_score") or 0.0) + rrf_increment
            if confidence > float(entry.get("best_confidence") or 0.0):
                entry["best_confidence"] = confidence
                entry["snippet"] = dict(snippet)
                entry["best_rank"] = int(rank)
                entry["best_run_index"] = int(run_index)
            elif confidence == float(entry.get("best_confidence") or 0.0):
                prior_run = int(entry.get("best_run_index") or 0)
                prior_rank = int(entry.get("best_rank") or 0)
                if (run_index, rank) < (prior_run, prior_rank):
                    entry["snippet"] = dict(snippet)
                    entry["best_rank"] = int(rank)
                    entry["best_run_index"] = int(run_index)

    ordered_entries = sorted(
        fused.values(),
        key=lambda entry: (
            -float(entry.get("rrf_score") or 0.0),
            -float(entry.get("best_confidence") or 0.0),
            int(entry.get("best_run_index") or 0),
            int(entry.get("best_rank") or 0),
        ),
    )
    if clip_limit is not None:
        ordered_entries = ordered_entries[:clip_limit]
    deduped_snippets = [dict(entry["snippet"]) for entry in ordered_entries]
    return deduped_snippets, fusion


def _search_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    new_contract_enabled = bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
    rag_agentic_enabled = bool(getattr(feature_state, "rag_agentic_mode", False)) and new_contract_enabled

    pagination_enabled = bool(getattr(settings, "MCP_SEARCH_PAGINATION_ENABLED", True))
    cursor_ttl_seconds = _search_cursor_ttl_seconds()
    try:
        pagination_prefetch_min = int(getattr(settings, "MCP_SEARCH_PAGINATION_PREFETCH_MIN", 50) or 50)
    except (TypeError, ValueError):
        pagination_prefetch_min = 50
    pagination_prefetch_min = max(0, pagination_prefetch_min)

    exclude_seen = bool(getattr(settings, "MCP_SEARCH_EXCLUDE_SEEN_ENABLED", False))
    raw_exclude_seen = arguments.get("exclude_seen")
    if isinstance(raw_exclude_seen, bool):
        exclude_seen = raw_exclude_seen

    def _excluded_chunk_ids() -> set[str]:
        if not exclude_seen:
            return set()
        try:
            shown = context.get_all_shown_this_conversation()
            chunk_ids = shown.get("chunk_ids", set())
            if isinstance(chunk_ids, set):
                return {str(cid) for cid in chunk_ids if cid}
        except Exception:
            pass
        # Best-effort fallback.
        return {
            str(cid)
            for cid in (getattr(context, "seen_chunk_ids", set()) | getattr(context, "newly_shown_chunk_ids", set()))
            if cid
        }

    def _page_snippets(
        snippets: Sequence[Mapping[str, object]],
        *,
        offset: int,
        page_size: int,
        excluded_chunk_ids: set[str],
    ) -> tuple[list[dict[str, object]], int, bool, int]:
        out: list[dict[str, object]] = []
        excluded = 0
        idx = max(0, int(offset))
        size = max(1, int(page_size))

        def _chunk_id(entry: Mapping[str, object]) -> str:
            return str(entry.get("chunk_id") or entry.get("id") or "").strip()

        while idx < len(snippets) and len(out) < size:
            entry = snippets[idx]
            idx += 1
            if not isinstance(entry, Mapping):
                continue
            cid = _chunk_id(entry)
            if cid and cid in excluded_chunk_ids:
                excluded += 1
                continue
            out.append(dict(entry))

        has_more = False
        if idx < len(snippets):
            if not excluded_chunk_ids:
                has_more = True
            else:
                for j in range(idx, len(snippets)):
                    entry = snippets[j]
                    if not isinstance(entry, Mapping):
                        continue
                    cid = _chunk_id(entry)
                    if cid and cid not in excluded_chunk_ids:
                        has_more = True
                        break

        return out, idx, has_more, excluded

    def _read_budget_for_refs(refs: Sequence[Mapping[str, object]]) -> dict[str, int] | None:
        if not refs:
            return None
        total_suggested = 0
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            try:
                total_suggested += int(ref.get("read_chars") or 0)
            except (TypeError, ValueError):
                continue
        max_chars_allowed = int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)
        return {
            "suggested_chars": min(int(total_suggested), int(max_chars_allowed)),
            "max_chars": int(max_chars_allowed),
        }

    def _extract_agentic_manifests(
        refs: Sequence[Mapping[str, object]],
    ) -> dict[str, dict[str, object]]:
        """
        Persist only the manifests needed to resolve table_chunk refs.

        These manifests live on ToolExecutionContext and are populated by
        _convert_to_agentic_search_response. Cursor paging needs them to be
        present so read_knowledge can hydrate table anchors without requiring a re-search.
        """
        table_manifests: dict[str, dict[str, object]] = {}
        table_cache = getattr(context, "table_row_anchor_manifests", None)
        if not isinstance(table_cache, dict):
            return table_manifests

        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            kind = str(ref.get("kind") or "").strip().lower()
            ref_id = str(ref.get("id") or "").strip()
            if not ref_id:
                continue
            if kind == "table_chunk" and isinstance(table_cache, dict):
                manifest = table_cache.get(ref_id)
                if isinstance(manifest, Mapping):
                    table_manifests[ref_id] = dict(manifest)
        return table_manifests

    def _enforce_search_rate_limit() -> Mapping[str, object] | None:
        window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
        try:
            calls_per_minute = int(getattr(settings, "MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE", 120) or 0)
        except (TypeError, ValueError):
            calls_per_minute = 120
        calls_per_minute = 0 if calls_per_minute < 0 else calls_per_minute
        try:
            enforce_tool_rate_limit(
                business_profile=conversation.business_profile,
                tool="search_knowledge",
                rate_limit=ToolRateLimit(
                    calls_per_minute=None if calls_per_minute <= 0 else calls_per_minute,
                    window_seconds=window_seconds,
                    scope="business",
                ),
            )
        except ToolRateLimitExceeded as exc:
            return {
                "tool": "search_knowledge",
                "status": "throttled",
                "error": "rate_limited",
                "error_code": "rate_limited",
                "snippets": [],
                "throttle_notice": {"type": "rate_limited", "message": str(exc)},
            }
        return None

    raw_cursor = _coerce_str(arguments.get("cursor")).strip()
    if raw_cursor:
        resolved_cursor = _resolve_search_cursor_from_handle(context, conversation, raw_cursor) or raw_cursor
        # Cursor paging: bypass duplicate-intent reuse and serve next page from server cache.
        limited = _enforce_search_rate_limit()
        if limited is not None:
            return limited
        try:
            context.reserve_search()
        except SearchBudgetExceeded:
            return search_budget_exceeded_payload(context, reason="cursor_paging_limit")

        if not pagination_enabled:
            return {
                "tool": "search_knowledge",
                "status": "constraint_error",
                "error": "pagination_disabled",
                "error_code": "pagination_disabled",
            }

        decoded = _decode_search_cursor(resolved_cursor, max_age_seconds=cursor_ttl_seconds)
        if not decoded:
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "invalid_cursor",
                "error_code": "invalid_cursor",
                "snippets": [],
            }
        session_id = str(decoded.get("sid") or "").strip()
        try:
            offset = int(decoded.get("o") or 0)
        except (TypeError, ValueError):
            offset = 0
        if not session_id:
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "invalid_cursor",
                "error_code": "invalid_cursor",
                "snippets": [],
            }

        cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
        session = cache.get(cache_key)
        if not isinstance(session, Mapping):
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "cursor_expired",
                "error_code": "cursor_expired",
                "snippets": [],
            }

        raw_limit = arguments.get("limit")
        try:
            page_size = int(raw_limit) if raw_limit is not None else None
        except (TypeError, ValueError):
            page_size = None
        if page_size is None:
            try:
                page_size = int(session.get("page_size") or 0) or SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT
            except (TypeError, ValueError):
                page_size = SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT
        page_size = max(1, min(int(page_size), MCP_PROMPT_MAX_SNIPPETS_CAP))

        session_version = 1
        try:
            session_version = int(session.get("version") or 1)
        except (TypeError, ValueError):
            session_version = 1

        # Agentic ref paging (v2): return up to limit refs (not snippet rows) and page over refs.
        if session_version >= 2:
            refs_full = session.get("refs")
            if isinstance(refs_full, list):
                offset_refs = max(0, int(offset))
                refs_total_found = session.get("refs_total_found")
                try:
                    refs_total_found = int(refs_total_found) if refs_total_found is not None else len(refs_full)
                except (TypeError, ValueError):
                    refs_total_found = len(refs_full)

                snippet_total_found = session.get("snippets_total_found")
                try:
                    snippet_total_found_int = int(snippet_total_found) if snippet_total_found is not None else None
                except (TypeError, ValueError):
                    snippet_total_found_int = None

                # Rehydrate anchor manifests so read_knowledge can resolve table_chunk refs.
                manifests_table = session.get("table_row_anchor_manifests")
                if isinstance(manifests_table, Mapping):
                    cache_value = getattr(context, "table_row_anchor_manifests", None)
                    if isinstance(cache_value, dict):
                        for key, value in dict(manifests_table).items():
                            if isinstance(value, Mapping):
                                cache_value[str(key)] = dict(value)

                refs_page = [dict(ref) for ref in refs_full[offset_refs : offset_refs + page_size] if isinstance(ref, Mapping)]
                next_offset = offset_refs + len(refs_page)
                has_more_refs = bool(next_offset < refs_total_found)
                next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset) if has_more_refs else None

                completeness_base = session.get("completeness_base")
                completeness: dict[str, object] = dict(completeness_base) if isinstance(completeness_base, Mapping) else {}
                # For transparency, keep snippet totals even though we page over refs.
                if snippet_total_found_int is not None:
                    completeness.setdefault("total_found", snippet_total_found_int)
                    completeness["snippets_total_found"] = snippet_total_found_int
                completeness["refs_total_found"] = int(refs_total_found)
                completeness["paging_mode"] = "refs"
                completeness["ref_offset"] = int(offset_refs)
                completeness["shown"] = len(refs_page)
                completeness["has_more"] = bool(has_more_refs)

                payload: dict[str, object] = {
                    "tool": "search_knowledge",
                    "query": str(session.get("query") or "").strip(),
                    "limit": page_size,
                    "query_intent": session.get("query_intent"),
                    "status": "ok" if refs_page else "not_found",
                    "diagnostics": {"cursor_used": True, "offset": offset_refs, "paging_mode": "refs"},
                    "refs": refs_page,
                    "completeness": completeness,
                    "pagination": {
                        "shown": len(refs_page),
                        "total": int(refs_total_found),
                        "has_more": bool(has_more_refs),
                    },
                }
                read_budget = _read_budget_for_refs(refs_page)
                if read_budget:
                    payload["read_budget"] = read_budget
                if next_cursor:
                    payload["pagination"]["next_cursor"] = (
                        _store_search_cursor_handle(
                            context,
                            conversation,
                            next_cursor,
                            ttl_seconds=cursor_ttl_seconds,
                        )
                        or next_cursor
                    )

                structured_log(
                    "mcp",
                    "search.agentic_paging",
                    {
                        "session_version": int(session_version),
                        "paging_mode": "refs",
                        "cursor_used": True,
                        "offset": int(offset_refs),
                        "limit": int(page_size),
                        "snippets_total_found": snippet_total_found_int,
                        "refs_total_found": int(refs_total_found),
                        "returned_refs": len(refs_page),
                        "has_more": bool(has_more_refs),
                    },
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                    logger_obj=logger,
                )

                return payload

        results_full = session.get("results")
        if not isinstance(results_full, list):
            results_full = []

        excluded_chunk_ids = _excluded_chunk_ids()
        page_snippets, next_offset, has_more, excluded_count = _page_snippets(
            results_full,
            offset=offset,
            page_size=page_size,
            excluded_chunk_ids=excluded_chunk_ids,
        )
        next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset) if has_more else None

        _page_snippets_out, completeness = _apply_seen_item_filter(page_snippets, context, mark_as_seen=False)
        page_snippets = _page_snippets_out
        completeness["total_found"] = len(results_full)
        completeness["has_more"] = bool(has_more)
        if excluded_count:
            completeness["excluded_seen"] = excluded_count
        if completeness.get("shown", 0) > 0 and not completeness.get("has_more"):
            if completeness.get("already_seen") == completeness.get("shown"):
                completeness["all_previously_shown"] = True
                completeness["message"] = (
                    f"All {completeness['shown']} matching results have already been shown in this conversation. "
                    "Try a different search term or ask the user if they need something specific."
                )

        query_text = str(session.get("query") or "").strip()
        query_intent = session.get("query_intent")
        if not page_snippets and results_full and exclude_seen:
            completeness["all_previously_shown"] = True
            completeness["message"] = (
                "No new results: all remaining matches were already shown earlier in this conversation. "
                "Use the earlier results, or change the query to find different matches."
            )
        payload: dict[str, object] = {
            "tool": "search_knowledge",
            "query": query_text,
            "limit": page_size,
            "query_intent": query_intent,
            "status": "ok" if page_snippets else "not_found",
            "diagnostics": {"cursor_used": True, "offset": offset},
            "snippets": page_snippets,
            "completeness": completeness,
            "has_more": bool(has_more),
        }
        if next_cursor:
            payload["next_cursor"] = next_cursor

        # Convert to agentic format when enabled.
        if rag_agentic_enabled:
            return _convert_to_agentic_search_response(
                payload,
                conversation=conversation,
                context=context,
            )
        return payload

    # Build queries list.
    # - `query` is the primary, single-query interface (back-compat and simpler).
    # - `queries[]` allows multiple variants for fanout.
    raw_queries_param = arguments.get("queries")
    raw_query_param = _coerce_str(arguments.get("query")).strip()
    queries: list[str] = []
    seen_queries: set[str] = set()

    def _append_query(candidate: str) -> None:
        normalized = candidate.strip()
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen_queries:
            return
        seen_queries.add(lowered)
        queries.append(normalized)

    if raw_query_param:
        _append_query(raw_query_param)
    if isinstance(raw_queries_param, (list, tuple)):
        for candidate in raw_queries_param:
            candidate_str = _coerce_str(candidate).strip()
            if candidate_str:
                _append_query(candidate_str)

    primary_query = queries[0] if queries else ""

    # =========================================================================
    # DIAGNOSTIC: Log document context state at search start
    # =========================================================================
    structured_log(
        "mcp",
        "search.context_state",
        {
            "query": primary_query,
            "primary_upload_id": context.primary_upload_id if context else None,
            "primary_document_title": context.get_primary_document_title() if context else None,
            "referenced_upload_ids": list(context.referenced_upload_ids)[:5] if context else [],
            "search_history_count": len(context.search_history) if context else 0,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )

    # =========================================================================
    # Document-continuity query rewriting removed
    # =========================================================================
    # Search queries must not be mutated with a previous/active document title.
    # Explicit document scoping should be represented as tool scope/filters, not
    # by rewriting "query" into "File name: query".
    rewrite_result = None
    rewrite_error: str | None = None
    rewrite_enabled = False

    query_scope_observability = build_query_scope_observability(
        context=context,
        rewrite_result=rewrite_result,
        rewrite_enabled=bool(rewrite_enabled),
        rewrite_error=rewrite_error,
    )
    structured_log(
        "mcp",
        "search.scope_decision",
        query_scope_observability,
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )

    # Fanout variants are controlled via MCP_SEARCH_MAX_QUERY_VARIANTS.
    # Keep this fully env-configurable so operators can tune recall/cost tradeoffs.
    query_variant_limit = max(
        1,
        int(getattr(settings, "MCP_SEARCH_MAX_QUERY_VARIANTS", DEFAULT_MAX_SEARCH_QUERY_VARIANTS)),
    )
    fanout_budget_ms = max(
        0,
        int(getattr(settings, "MCP_SEARCH_FANOUT_BUDGET_MS", 0) or 0),
    )

    def _prune_queries(values: Sequence[str]) -> list[str]:
        if len(values) <= query_variant_limit:
            return list(values)
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in values:
            normalized = entry.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(entry)
            if len(deduped) >= query_variant_limit:
                break
        if not deduped and values:
            deduped.append(values[0])
        return deduped

    optimized_queries = _prune_queries(queries)
    if len(optimized_queries) < len(queries):
        structured_log(
            "mcp",
            "search.query_pruned",
            {
                "original_count": len(queries),
                "kept": len(optimized_queries),
                "limit": query_variant_limit,
            },
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )
    queries = optimized_queries

    if not queries:
        return {
            "tool": "search_knowledge",
            "status": "error",
            "error": "query is required",
            "snippets": [],
        }

    # Layer 3: duplicate-search telemetry.
    # We still measure similar intents/result sets so operators can inspect search
    # thrash, but we no longer short-circuit live retrieval with stale refs.
    result_fingerprint_top_k = 8

    def _normalize_intent_text(values: Sequence[str]) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw in values:
            token = str(raw or "").strip().lower()
            if not token:
                continue
            if token in seen:
                continue
            seen.add(token)
            parts.append(token)
        return " | ".join(parts)

    def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float | None:
        if not a or not b:
            return None
        if len(a) != len(b):
            return None
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b, strict=False):
            try:
                xf = float(x)
                yf = float(y)
            except (TypeError, ValueError):
                return None
            dot += xf * yf
            norm_a += xf * xf
            norm_b += yf * yf
        if norm_a <= 0.0 or norm_b <= 0.0:
            return None
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

    def _response_top_ids(response: Mapping[str, object], *, top_k: int) -> list[str]:
        ids: list[str] = []
        refs = response.get("refs")
        if isinstance(refs, list):
            for entry in refs:
                if not isinstance(entry, Mapping):
                    continue
                ref_id = str(entry.get("evidence_group_id") or entry.get("id") or "").strip()
                if ref_id:
                    ids.append(ref_id)
                if len(ids) >= top_k:
                    break
            return ids[:top_k]

        snippets_local = response.get("snippets")
        if isinstance(snippets_local, list):
            for entry in snippets_local:
                if not isinstance(entry, Mapping):
                    continue
                ref_id = str(
                    entry.get("evidence_group_id")
                    or entry.get("chunk_id")
                    or entry.get("id")
                    or ""
                ).strip()
                if ref_id:
                    ids.append(ref_id)
                if len(ids) >= top_k:
                    break
        return ids[:top_k]

    def _response_result_fingerprint(response: Mapping[str, object], *, top_k: int) -> tuple[str, list[str]]:
        top_ids = _response_top_ids(response, top_k=top_k)
        if not top_ids:
            return "", []
        digest = hashlib.sha256("|".join(top_ids).encode("utf-8")).hexdigest()[:16]
        return digest, top_ids

    search_budget_reserved = False

    def _reserve_search_budget_once():
        nonlocal search_budget_reserved
        if search_budget_reserved:
            return None
        limited = _enforce_search_rate_limit()
        if limited is not None:
            return limited
        try:
            context.reserve_search()
        except SearchBudgetExceeded:
            return search_budget_exceeded_payload(context, reason="per_turn_limit")
        search_budget_reserved = True
        return None

    intent_text = _normalize_intent_text(queries)
    intent_embedding: list[float] | None = None
    duplicate_intent_diagnostics: dict[str, object] | None = None
    if new_contract_enabled:
        embedder = _portal_file_embedding_service()
        if embedder and intent_text:
            try:
                embedded = embedder.embed_text(intent_text)
                if isinstance(embedded, list) and embedded:
                    intent_embedding = [float(v) for v in embedded]
            except Exception:
                intent_embedding = None

        history = getattr(context, "search_history", None) or []
        best_match: Mapping[str, object] | None = None
        best_similarity: float | None = None
        if intent_text and isinstance(history, list) and history:
            # Only compare against a small recent window to avoid unbounded work.
            for entry in reversed(history[-12:]):
                if not isinstance(entry, Mapping):
                    continue
                prior_response = entry.get("response")
                if not isinstance(prior_response, Mapping):
                    continue
                prior_intent = str(entry.get("intent") or entry.get("query") or "").strip().lower()
                if not prior_intent:
                    continue

                similarity: float | None = None
                if intent_embedding is not None and embedder:
                    prior_embedding = entry.get("embedding")
                    if not isinstance(prior_embedding, list) or not prior_embedding:
                        try:
                            embedded = embedder.embed_text(prior_intent)
                            if isinstance(embedded, list) and embedded:
                                prior_embedding = [float(v) for v in embedded]
                                # Cache embedding for future comparisons (not returned to the LLM).
                                try:
                                    entry["embedding"] = prior_embedding
                                except Exception:
                                    pass
                        except Exception:
                            prior_embedding = None
                    if isinstance(prior_embedding, list) and prior_embedding:
                        similarity = _cosine_similarity(intent_embedding, prior_embedding)

                if similarity is None:
                    similarity = 1.0 if prior_intent == intent_text else 0.0

                if best_similarity is None or similarity > best_similarity:
                    best_similarity = similarity
                    best_match = entry

            if best_match is not None and best_similarity is not None and best_similarity >= 0.85:
                duplicate_intent_diagnostics = {
                    "duplicate_intent_similarity": round(float(best_similarity), 4),
                    "duplicate_intent_query": str(best_match.get("intent") or best_match.get("query") or "").strip(),
                }

    raw_limit = arguments.get("limit")
    try:
        page_size_requested = int(raw_limit) if raw_limit is not None else None
    except (TypeError, ValueError):
        page_size_requested = None
    if page_size_requested is None:
        # Server-side default when callers omit `limit`. Without this, the underlying
        # search service may treat limit=None as unbounded and return huge result sets.
        page_size_requested = SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT

    page_size = max(1, min(int(page_size_requested), MCP_PROMPT_MAX_SNIPPETS_CAP))
    fetch_limit = page_size
    if pagination_enabled and pagination_prefetch_min:
        # Prefetch more candidates than page_size so cursor pagination has results to serve.
        # Use SEARCH_PREFETCH_ABSOLUTE_CAP (not MCP_PROMPT_MAX_SNIPPETS_CAP) to avoid
        # clamping prefetch to the same value as page_size, which made pagination a no-op.
        fetch_limit = max(page_size, min(int(pagination_prefetch_min), SEARCH_PREFETCH_ABSOLUTE_CAP))
    requested_limit = fetch_limit

    service = _knowledge_service()
    identifier_filter: dict[str, object] | None = None
    locked_key = None
    locked_value = None

    agent_scope = _agent_knowledge_scope(conversation, context)
    agent_explicit_upload_ids = _scope_upload_ids_to_uuids(agent_scope.explicit_upload_ids) if agent_scope.explicit_upload_ids else None
    combined_upload_ids: list[uuid.UUID] | None = agent_explicit_upload_ids if agent_scope.restricted else None

    def _effective_limit(base_limit: int | None) -> int | None:
        # Agentic contract: intent classification is advisory (routing/logging),
        # and must not override the caller's explicit result size request.
        limit_val = base_limit
        if limit_val is not None:
            limit_val = max(1, min(int(limit_val), MCP_PROMPT_MAX_SNIPPETS_CAP))
        return limit_val

    def _log_search_performance(
        *,
        snippets: Sequence[Mapping[str, object]],
        diagnostics: Mapping[str, object] | None,
        intent: str | None,
        limit_value: int | None,
        status: str,
        note: str | None = None,
    ) -> None:
        diag = dict(diagnostics or {})
        snippet_count = len(snippets)
        diag.setdefault("snippet_count", snippet_count)
        warn_ms = int(getattr(settings, "MCP_SLO_SEARCH_WARN_MS", 1200) or 0)
        detail = {
            "status": status,
            "intent": intent,
            "path": diag.get("path"),
            "snippet_count": snippet_count,
            "limit": limit_value,
            "agent_scope_mode": agent_scope.mode,
            "agent_scope_explicit_uploads": len(agent_scope.explicit_upload_ids) if agent_scope.restricted else None,
            "effective_scope_uploads": len(combined_upload_ids) if combined_upload_ids is not None else None,
            "total_ms": diag.get("total_duration_ms"),
            "alias_ms": diag.get("alias_duration_ms"),
            "vector_ms": diag.get("vector_duration_ms"),
            "lexical_ms": diag.get("fts_duration_ms"),
            "rerank_ms": diag.get("rerank_duration_ms"),
            "table_ms": diag.get("table_duration_ms"),
            "table_context_ms": diag.get("table_context_ms"),
            "table_presence_ms": diag.get("table_presence_ms"),
            "chunk_candidates": diag.get("chunk_candidate_count"),
            "alias_hits": diag.get("alias_hits"),
            "table_reason": diag.get("table_reason"),
            "cache_hit": diag.get("cache_hit"),
            "cache_scope": diag.get("cache_scope"),
            "read_required": sum(1 for payload in snippets if isinstance(payload, Mapping) and payload.get("read_required")),
        }
        if note:
            detail["note"] = note
        total_ms = detail.get("total_ms")
        slow = bool(
            warn_ms
            and isinstance(total_ms, (int, float))
            and float(total_ms) >= float(warn_ms)
        )
        if slow:
            detail["slo"] = "slow"
            detail["slo_warn_ms"] = warn_ms
        structured_log(
            "mcp",
            "search.performance",
            detail,
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            level=logging.WARNING if slow else logging.INFO,
        )

    def _build_search_session_context() -> dict[str, object] | None:
        return None

    def _execute_single_query(
        query_text: str,
        *,
        intent_info_override: Mapping[str, object] | None = None,
        intent_override: str | None = None,
        limit_override: int | None = None,
        precomputed_result: object | None = None,
    ) -> Mapping[str, object]:
        # Thin MCP wrapper: execute a single RAG search and translate the result
        # into MCP's stable tool contract. Retrieval planning, ranking, fusion,
        # and fallback decisions must remain in apps.rag.knowledge_search.
        intent_info = intent_info_override or _query_intent(query_text)
        intent = intent_override or intent_info.get("intent")
        normalized_query = query_text.lower()
        aggregation_keywords = (
            "total",
            "sum",
            "overall",
            "aggregate",
            "اجمالي",
            "إجمالي",
            "الاجمالي",
            "المجموع",
        )
        aggregation_query = any(keyword in normalized_query for keyword in aggregation_keywords)
        limit_for_run = limit_override if limit_override is not None else _effective_limit(requested_limit)
        search_cache_key = _search_cache_key(
            query_text,
            limit_for_run,
            identifier_filter,
            locked_key,
            str(locked_value) if locked_value is not None else None,
        )
        cached_result = None
        if context.search_cache and search_cache_key in context.search_cache:
            cached_result = copy.deepcopy(context.search_cache[search_cache_key])
        if cached_result:
            structured_log(
                "mcp",
                "search.cache_hit",
                {
                    "query": query_text,
                    "intent": intent,
                    "limit": limit_for_run,
                },
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
            )
            cached_result["query"] = query_text
            cached_result["limit_used"] = limit_for_run
            cached_result["cache_hit"] = True
            cached_result.setdefault("query_intent", intent)
            cached_result.setdefault("intent_signal", intent_info)
            cached_result.setdefault("snippets", [])
            cached_result["snippets"] = [dict(snippet) for snippet in cached_result.get("snippets", [])]
            cached_result["cache_hit"] = True
            return cached_result

        if precomputed_result is not None:
            result = precomputed_result
        else:
            result = service.search(
                business_profile=conversation.business_profile,
                query=query_text,
                limit=limit_for_run,
                identifier_filter=identifier_filter,
                allowed_upload_ids=combined_upload_ids,
                allowed_explicit_upload_ids=agent_explicit_upload_ids,
                session_context=_build_search_session_context(),
            )
        combined_snippets: list[object] = list(getattr(result, "snippets", []) or [])
        snippet_payloads = _serialize_snippets(combined_snippets)

        # Track referenced documents so later turns can keep document context.
        doc_context_enabled = str(getattr(settings, "RAG_DOCUMENT_CONTEXT_ENABLED", "true")).lower() in {"1", "true", "yes"}
        if doc_context_enabled:
            for payload in snippet_payloads:
                upload_id = payload.get("upload_id") or payload.get("document_id")
                if upload_id:
                    # Extract document title from snippet
                    title = payload.get("title", "")
                    if title and " – " in title:
                        # Format is often "Document Name – chunk N"
                        title = title.split(" – ")[0].strip()
                    elif title and " - " in title:
                        title = title.split(" - ")[0].strip()

                    context.track_document_reference(
                        upload_id=str(upload_id),
                        title=title,
                        stage=payload.get("search_stage", "unknown"),
                        confidence=payload.get("confidence_score") if isinstance(payload.get("confidence_score"), (int, float)) else None,
                    )

        read_required = False
        read_required_reasons_summary: set[str] = set()
        for payload in snippet_payloads:
            payload_read_required, reasons = _compute_read_required(payload)
            payload["read_required"] = payload_read_required
            if reasons:
                payload["read_required_reasons"] = reasons
                read_required_reasons_summary.update(reasons)
            if payload_read_required:
                read_required = True
            chunk_id = payload.get("chunk_id") or payload.get("id")
            upload_id = payload.get("upload_id")
            chunk_index = payload.get("chunk_index")

            # FIXED: Use actual page number from metadata, not chunk_index + 1
            # Per Codex review: text.page now means PDF page number, not chunk index
            payload_meta = payload.get("metadata") or {}
            if isinstance(payload_meta, dict):
                actual_page = (
                    payload_meta.get("table_page_number") or
                    payload_meta.get("chunk_page") or
                    payload_meta.get("page_number") or
                    payload.get("page_number")
                )
            else:
                actual_page = payload.get("page_number")

            is_table_payload = bool(
                payload.get("is_table_chunk")
                or payload.get("structured_table_count")
                or payload.get("table_read_only")
                or (isinstance(payload_meta, dict) and payload_meta.get("is_table_chunk"))
            )
            read_id = str(chunk_id or "").strip() if is_table_payload else str(upload_id or chunk_id or "").strip()
            # Build read_hint with page (if known) or offset (for chunk-based access)
            mode_hint = "full_page" if is_table_payload else ("full_page" if intent == "identifier" else "excerpt")
            read_hint: dict[str, object] = {
                # For table chunks, prefer the chunk id so readers can upgrade to full-page table content.
                "document_id": read_id,
                "mode": mode_hint,
            }

            if actual_page:
                try:
                    page_num = int(actual_page)
                    if page_num >= 1:
                        read_hint["page"] = page_num
                except (TypeError, ValueError):
                    pass

            # Fallback: use offset if no page number known
            if "page" not in read_hint and isinstance(chunk_index, int):
                read_hint["offset"] = chunk_index

            payload["read_hint"] = read_hint
        snippet_payloads = _sanitize_snippet_payloads_for_prompt(snippet_payloads, conversation=conversation)
        log_meta = {"query": query_text, "intent": intent, "read_required": read_required}
        if read_required_reasons_summary:
            log_meta["read_required_reasons"] = sorted(read_required_reasons_summary)
        _log_snippet_payloads(
            tool="search_knowledge",
            conversation=conversation,
            snippet_payloads=snippet_payloads,
            meta=log_meta,
        )
        _log_search_performance(
            snippets=snippet_payloads,
            diagnostics=result.diagnostics,
            intent=intent,
            limit_value=limit_for_run,
            status=result.status,
        )
        result_diagnostics = dict(result.diagnostics or {})
        result_diagnostics.setdefault("query_scope", dict(query_scope_observability))

        payload = {
            "tool": "search_knowledge",
            "query": query_text,
            "limit": limit_for_run,
            "limit_used": limit_for_run,
            "query_intent": intent,
            "intent_signal": intent_info,
            "status": result.status,
            "diagnostics": result_diagnostics,
            "snippets": snippet_payloads,
        }

        payload["read_required_summary"] = {
            "any": read_required,
            "reasons": sorted(read_required_reasons_summary),
        }
        if search_cache_key:
            _bounded_cache_store(context.search_cache, search_cache_key, copy.deepcopy(payload))
        return payload

    resolved_runs: list[tuple[int, Mapping[str, object]]] = []
    pending_specs: list[tuple[int, str, Mapping[str, object], str | None, int | None]] = []
    non_cached_queries = 0
    for idx, query_text in enumerate(queries):
        intent_info = _query_intent(query_text)
        intent = intent_info.get("intent")
        limit_for_run = _effective_limit(requested_limit)
        search_cache_key = _search_cache_key(
            query_text,
            limit_for_run,
            identifier_filter,
            locked_key,
            str(locked_value) if locked_value is not None else None,
        )
        cached_result = None
        if context.search_cache and search_cache_key in context.search_cache:
            cached_result = copy.deepcopy(context.search_cache[search_cache_key])
        if cached_result:
            structured_log(
                "mcp",
                "search.cache_hit",
                {
                    "query": query_text,
                    "intent": intent,
                    "limit": limit_for_run,
                },
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
            )
            cached_result["query"] = query_text
            cached_result["limit_used"] = limit_for_run
            cached_result.setdefault("query_intent", intent)
            cached_result.setdefault("intent_signal", intent_info)
            cached_result.setdefault("snippets", [])
            cached_result["snippets"] = [dict(snippet) for snippet in cached_result.get("snippets", [])]
            cached_result["cache_hit"] = True
            resolved_runs.append((idx, cached_result))
            continue
        pending_specs.append((idx, query_text, intent_info, intent, limit_for_run))
        non_cached_queries += 1

    if non_cached_queries > 0:
        # Enforce limits only when this call needs a backend search.
        # Pure cache reuses (same intent/query in the same turn) should not
        # consume per-turn search budget.
        limited = _reserve_search_budget_once()
        if limited is not None:
            return limited

    executor: ThreadPoolExecutor | None = None
    futures: list[tuple[int, str, Mapping[str, object], str | None, int | None, object]] = []
    fanout_start = time.perf_counter()
    fanout_parallel_enabled = bool(getattr(settings, "MCP_SEARCH_FANOUT_PARALLEL", False))
    max_parallel_workers = int(getattr(settings, "MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS", 4) or 4)
    max_parallel_workers = max(1, min(8, max_parallel_workers))
    use_parallel = bool(fanout_parallel_enabled and non_cached_queries > 1 and fanout_budget_ms <= 0)
    if use_parallel:
        executor = ThreadPoolExecutor(
            max_workers=min(non_cached_queries, max_parallel_workers),
            thread_name_prefix="mcp_search",
        )
    try:
        for idx, query_text, intent_info, intent, limit_for_run in pending_specs:
            if not executor and fanout_budget_ms:
                elapsed_ms = int((time.perf_counter() - fanout_start) * 1000)
                if resolved_runs and elapsed_ms >= fanout_budget_ms:
                    structured_log(
                        "mcp",
                        "search.fanout_budget_exceeded",
                        {
                            "budget_ms": fanout_budget_ms,
                            "elapsed_ms": elapsed_ms,
                            "queries_planned": len(pending_specs),
                            "queries_run": len(resolved_runs),
                        },
                        context={
                            "conversation": conversation.id,
                            "business": conversation.business_profile_id,
                        },
                        logger_obj=logger,
                        level=logging.WARNING,
                    )
                    break
            if executor:
                future = executor.submit(
                    service.search,
                    business_profile=conversation.business_profile,
                    query=query_text,
                    limit=limit_for_run,
                    identifier_filter=identifier_filter,
                    allowed_upload_ids=combined_upload_ids,
                    allowed_explicit_upload_ids=agent_explicit_upload_ids,
                    session_context=_build_search_session_context(),
                )
                futures.append((idx, query_text, intent_info, intent, limit_for_run, future))
            else:
                result = service.search(
                    business_profile=conversation.business_profile,
                    query=query_text,
                    limit=limit_for_run,
                    identifier_filter=identifier_filter,
                    allowed_upload_ids=combined_upload_ids,
                    allowed_explicit_upload_ids=agent_explicit_upload_ids,
                    session_context=_build_search_session_context(),
                )
                run_payload = _execute_single_query(
                    query_text,
                    intent_info_override=intent_info,
                    intent_override=intent,
                    limit_override=limit_for_run,
                    precomputed_result=result,
                )
                resolved_runs.append((idx, run_payload))
                if run_payload.get("status") not in {"ok", "not_found"}:
                    if len(queries) > 1:
                        run_payload = dict(run_payload)
                        run_payload["batched_queries"] = tuple(queries)
                    return run_payload
        if executor:
            for idx, query_text, intent_info, intent, limit_for_run, future in sorted(futures, key=lambda entry: entry[0]):
                result = future.result()
                run_payload = _execute_single_query(
                    query_text,
                    intent_info_override=intent_info,
                    intent_override=intent,
                    limit_override=limit_for_run,
                    precomputed_result=result,
                )
                resolved_runs.append((idx, run_payload))
                if run_payload.get("status") not in {"ok", "not_found"}:
                    if len(queries) > 1:
                        run_payload = dict(run_payload)
                        run_payload["batched_queries"] = tuple(queries)
                    return run_payload
    finally:
        if executor:
            executor.shutdown(wait=True)

    resolved_runs.sort(key=lambda entry: entry[0])
    runs = [payload for _, payload in resolved_runs]

    if not runs:
        return {
            "tool": "search_knowledge",
            "query": "",
            "limit": requested_limit,
            "status": "not_found",
            "snippets": [],
            "error": "no_query",
        }

    primary_run = runs[0]
    limit_cap = primary_run.get("limit_used")
    clip_limit = int(limit_cap) if isinstance(limit_cap, int) and limit_cap > 0 else None
    deduped_snippets, fusion = _fuse_batched_search_runs(
        runs,
        clip_limit=clip_limit,
    )

    results_full = deduped_snippets
    total_found = len(results_full)

    excluded_chunk_ids = _excluded_chunk_ids()
    page_snippets, next_offset, has_more, excluded_count = _page_snippets(
        results_full,
        offset=0,
        page_size=page_size,
        excluded_chunk_ids=excluded_chunk_ids,
    )

    next_cursor = None
    session_id = None
    if pagination_enabled and has_more and results_full and not rag_agentic_enabled:
        session_id = str(uuid.uuid4())
        cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
        cache.set(
            cache_key,
            {
                "version": 1,
                "query": primary_run.get("query"),
                "query_intent": primary_run.get("query_intent") or primary_run.get("intent"),
                "page_size": page_size,
                "results": results_full,
            },
            cursor_ttl_seconds,
        )
        next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset)

    _page_snippets_out, completeness = _apply_seen_item_filter(page_snippets, context, mark_as_seen=False)
    page_snippets = _page_snippets_out
    completeness["total_found"] = total_found
    completeness["has_more"] = bool(has_more)
    if excluded_count:
        completeness["excluded_seen"] = excluded_count
    if completeness.get("shown", 0) > 0 and not completeness.get("has_more"):
        if completeness.get("already_seen") == completeness.get("shown"):
            completeness["all_previously_shown"] = True
            completeness["message"] = (
                f"All {completeness['shown']} matching results have already been shown in this conversation. "
                "Try a different search term or ask the user if they need something specific."
            )

    # Mark the returned page as seen (for follow-up paging within this turn).
    _mark_snippets_as_seen(page_snippets, context)

    # Log seen-item tracking results for debugging
    if completeness.get("already_seen", 0) > 0 or completeness.get("clipped", 0) > 0:
        structured_log(
            "mcp",
            "search.seen_tracking",
            {
                "shown": completeness.get("shown", 0),
                "already_seen": completeness.get("already_seen", 0),
                "clipped": completeness.get("clipped", 0),
                "total_found": completeness.get("total_found", 0),
                "all_previously_shown": completeness.get("all_previously_shown", False),
            },
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
        )

    def _normalized_run_status(run: Mapping[str, object] | None) -> str:
        if not isinstance(run, Mapping):
            return ""
        return str(run.get("status") or "").strip().lower()

    # Agentic RAG should not block on "needs_clarification". Always return best-effort evidence
    # (if any) and let the assistant handle ambiguity/conflicts in the response.
    status_source_run: Mapping[str, object] = next(
        (run for run in runs if _normalized_run_status(run) == "ok"),
        primary_run,
    )
    if page_snippets:
        final_status = "ok"
    else:
        non_default_status_run = next(
            (
                run
                for run in runs
                if _normalized_run_status(run) not in {"", "not_found", "needs_clarification"}
            ),
            None,
        )
        if non_default_status_run is not None:
            status_source_run = non_default_status_run
            final_status = _normalized_run_status(non_default_status_run)
        else:
            status_source_run = runs[-1]
            final_status = _normalized_run_status(status_source_run) or "not_found"
            if final_status == "needs_clarification":
                final_status = "not_found"

    for snippet in page_snippets:
        context.add_retrieval_candidate(snippet)

    metrics = _log_tool_metrics(
        tool="search_knowledge",
        conversation=conversation,
        snippets=page_snippets,
        extra={
            "status": final_status,
            "primary_status": primary_run.get("status"),
            "status_source_query": status_source_run.get("query"),
            "limit": page_size,
            "query_length": len(str(primary_run.get("query") or "")),
            "fusion": fusion,
            "fanout_budget_ms": fanout_budget_ms,
            "fanout_parallel_enabled": bool(fanout_parallel_enabled),
            "fanout_parallel_used": bool(use_parallel),
            "queries_planned": len(queries),
            "queries_run": len(runs),
        },
    )
    context.reserve_characters(int(metrics.get("char_count", 0)))

    diag = dict(status_source_run.get("diagnostics") or {})
    try:
        diag["final_status_source_index"] = int(runs.index(status_source_run))
    except ValueError:
        diag["final_status_source_index"] = 0
    diag["final_status_source_query"] = status_source_run.get("query")
    if final_status == "not_found":
        no_result_reason = _extract_no_result_reason(diag)
        if not no_result_reason:
            for run in runs:
                run_diag = run.get("diagnostics")
                if not isinstance(run_diag, Mapping):
                    continue
                candidate_reason = _extract_no_result_reason(run_diag)
                if candidate_reason:
                    no_result_reason = candidate_reason
                    break
        if no_result_reason:
            diag["no_result_reason"] = no_result_reason
    diag["batched_runs"] = [
        {
            "query": run.get("query"),
            "status": run.get("status"),
            "snippet_count": len(run.get("snippets", [])),
            "cache_hit": bool(run.get("cache_hit")),
        }
        for run in runs
    ]
    diag["batched_queries"] = queries

    query_intent = (
        status_source_run.get("query_intent")
        or status_source_run.get("intent")
        or primary_run.get("query_intent")
        or primary_run.get("intent")
    )

    if not page_snippets and total_found and exclude_seen:
        completeness["all_previously_shown"] = True
        completeness["message"] = (
            f"No new results: the top {total_found} matches were already shown earlier in this conversation. "
            "Use the earlier results, or change the query to find different matches."
        )

    payload = {
        "tool": "search_knowledge",
        "query": primary_run.get("query"),
        "limit": page_size,
        "query_intent": query_intent,
        "intent_signal": primary_run.get("intent_signal"),
        "status": final_status,
        "diagnostics": diag,
        "snippets": page_snippets,
    }

    # Always include completeness metadata for transparent decisions
    payload["completeness"] = completeness
    payload["has_more"] = bool(has_more)
    if next_cursor:
        payload["next_cursor"] = next_cursor

    if fusion:
        payload["fusion"] = fusion
    if len(queries) > 1:
        payload["batched_queries"] = tuple(queries)

    # Convert to agentic format when enabled, and persist search intent metadata
    # for semantic dedup within this user turn.
    if rag_agentic_enabled:
        if final_status in {"ok", "not_found"} and results_full:
            # Agentic search must return up to `limit` unique refs. The legacy flow
            # converts only the first snippet page, which can collapse to fewer refs
            # (e.g., 10 snippets -> 3 refs) and starve the model of evidence breadth.
            #
            # Fix: derive refs from the full candidate set (prefetch window) and page
            # over refs (not snippets). This preserves stable pagination and avoids
            # skipping refs when dedupe collapses multiple snippets into one ref.
            full_payload = dict(payload)
            full_payload["snippets"] = [dict(snippet) for snippet in results_full if isinstance(snippet, Mapping)]
            agentic_full = _convert_to_agentic_search_response(
                full_payload,
                conversation=conversation,
                context=context,
            )
            refs_full_raw = agentic_full.get("refs")
            refs_full: list[dict[str, object]] = (
                [dict(ref) for ref in refs_full_raw if isinstance(ref, Mapping)]
                if isinstance(refs_full_raw, list)
                else []
            )
            refs_total_found = len(refs_full)
            refs_page = refs_full[:page_size]
            has_more_refs = bool(refs_total_found > len(refs_page))

            # Cache a ref paging session for cursor paging.
            next_cursor = None
            session_id = None
            if pagination_enabled and has_more_refs and refs_full:
                session_id = str(uuid.uuid4())
                cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
                manifests_table = _extract_agentic_manifests(refs_full)
                completeness_base = agentic_full.get("completeness")
                completeness_base_out = dict(completeness_base) if isinstance(completeness_base, Mapping) else {}
                completeness_base_out["refs_total_found"] = int(refs_total_found)
                completeness_base_out["paging_mode"] = "refs"
                completeness_base_out["snippets_total_found"] = int(total_found)
                cache.set(
                    cache_key,
                    {
                        "version": 2,
                        "query": payload.get("query"),
                        "query_intent": payload.get("query_intent"),
                        "page_size": page_size,
                        "snippets_total_found": int(total_found),
                        "refs_total_found": int(refs_total_found),
                        "refs": refs_full,
                        "completeness_base": completeness_base_out,
                        "table_row_anchor_manifests": manifests_table,
                    },
                    cursor_ttl_seconds,
                )
                next_cursor = _encode_search_cursor(session_id=session_id, offset=len(refs_page))

            completeness_out = dict(agentic_full.get("completeness") or {}) if isinstance(agentic_full.get("completeness"), Mapping) else {}
            # Normalize agentic completeness to what we actually returned.
            completeness_out.setdefault("total_found", int(total_found))  # snippet candidates
            completeness_out["snippets_total_found"] = int(total_found)
            completeness_out["refs_total_found"] = int(refs_total_found)
            completeness_out["paging_mode"] = "refs"
            completeness_out["ref_offset"] = 0
            completeness_out["shown"] = len(refs_page)
            completeness_out["has_more"] = bool(has_more_refs)

            final_response = {
                **{k: v for k, v in dict(agentic_full).items() if k not in {"refs", "next_cursor", "has_more", "total_found", "read_budget", "read_budget_hint", "completeness", "pagination"}},
                "tool": "search_knowledge",
                "query": payload.get("query"),
                "limit": int(page_size),
                "query_intent": payload.get("query_intent"),
                "status": agentic_full.get("status") if refs_page else "not_found",
                "refs": refs_page,
                "completeness": completeness_out,
                "pagination": {
                    "shown": len(refs_page),
                    "total": int(refs_total_found),
                    "has_more": bool(has_more_refs),
                },
            }
            read_budget = _read_budget_for_refs(refs_page)
            if read_budget:
                final_response["read_budget"] = read_budget
            if next_cursor:
                final_response["pagination"]["next_cursor"] = (
                    _store_search_cursor_handle(
                        context,
                        conversation,
                        next_cursor,
                        ttl_seconds=cursor_ttl_seconds,
                    )
                    or next_cursor
                )

            structured_log(
                "mcp",
                "search.agentic_paging",
                {
                    "session_version": 2,
                    "session_stored": bool(next_cursor),
                    "paging_mode": "refs",
                    "cursor_used": False,
                    "offset": 0,
                    "limit": int(page_size),
                    "snippets_total_found": int(total_found),
                    "refs_total_found": int(refs_total_found),
                    "returned_refs": len(refs_page),
                    "has_more": bool(has_more_refs),
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )
        else:
            final_response = _convert_to_agentic_search_response(
                payload,
                conversation=conversation,
                context=context,
            )
    else:
        final_response = payload

    if isinstance(final_response, dict):
        diagnostics_out = final_response.get("diagnostics")
        if not isinstance(diagnostics_out, dict):
            diagnostics_out = {}
        diagnostics_out.setdefault("query_scope", dict(query_scope_observability))
        retrieval_observability = build_retrieval_observability(
            query_scope=query_scope_observability,
            diagnostics=diagnostics_out,
            completeness=(
                final_response.get("completeness")
                if isinstance(final_response.get("completeness"), Mapping)
                else {}
            ),
            refs=final_response.get("refs"),
            snippets=final_response.get("snippets"),
            status=str(final_response.get("status") or final_status or ""),
        )
        if retrieval_observability:
            final_response["retrieval_observability"] = retrieval_observability
            diagnostics_out["retrieval_observability"] = retrieval_observability
        if diagnostics_out:
            final_response["diagnostics"] = diagnostics_out
        structured_log(
            "mcp",
            "search.observability",
            retrieval_observability,
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )

    result_fingerprint = ""
    result_top_ids: list[str] = []
    if isinstance(final_response, Mapping):
        result_fingerprint, result_top_ids = _response_result_fingerprint(
            final_response,
            top_k=result_fingerprint_top_k,
        )

    duplicate_result_diagnostics: dict[str, object] | None = None
    if new_contract_enabled and result_fingerprint:
        history = getattr(context, "search_history", None) or []
        if isinstance(history, list) and history:
            fingerprint_match: Mapping[str, object] | None = None
            for entry in reversed(history[-12:]):
                if not isinstance(entry, Mapping):
                    continue
                prior_response = entry.get("response")
                if not isinstance(prior_response, Mapping):
                    continue
                prior_fingerprint = str(entry.get("result_fingerprint") or "").strip()
                prior_top_ids_raw = entry.get("result_top_ids")
                if not prior_fingerprint:
                    prior_fingerprint, prior_top_ids = _response_result_fingerprint(
                        prior_response,
                        top_k=result_fingerprint_top_k,
                    )
                    prior_top_ids_raw = prior_top_ids
                if prior_fingerprint != result_fingerprint:
                    continue
                normalized_prior_top_ids = [
                    str(value).strip()
                    for value in (prior_top_ids_raw if isinstance(prior_top_ids_raw, list) else [])
                    if str(value).strip()
                ][:result_fingerprint_top_k]
                if normalized_prior_top_ids and normalized_prior_top_ids != result_top_ids[:result_fingerprint_top_k]:
                    continue
                fingerprint_match = entry
                break

            if fingerprint_match is not None:
                duplicate_result_diagnostics = {
                    "duplicate_result_fingerprint": result_fingerprint,
                    "duplicate_result_top_ids": result_top_ids[:result_fingerprint_top_k],
                }

    if isinstance(final_response, dict):
        diagnostics = final_response.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        if duplicate_intent_diagnostics:
            diagnostics.update(duplicate_intent_diagnostics)
            try:
                similarity_value = duplicate_intent_diagnostics.get("duplicate_intent_similarity")
                similarity_float = float(similarity_value) if similarity_value is not None else None
            except (TypeError, ValueError):
                similarity_float = None
            final_response["search_repeat_guidance"] = build_repeat_search_guidance(
                context,
                similarity=similarity_float,
            )
        if duplicate_result_diagnostics:
            diagnostics.update(duplicate_result_diagnostics)
        if diagnostics:
            final_response["diagnostics"] = diagnostics

    try:
        history_entry = {
            "intent": intent_text,
            "embedding": intent_embedding,
            "response": copy.deepcopy(final_response),
            "result_fingerprint": result_fingerprint,
            "result_top_ids": list(result_top_ids[:result_fingerprint_top_k]),
        }
        context.search_history.append(history_entry)
        if len(context.search_history) > 25:
            context.search_history = context.search_history[-25:]
    except Exception:
        pass

    return final_response


_AGENTIC_READ_CURSOR_V2_SALT = b"mcp.read_cursor.v2"
_AGENTIC_READ_CURSOR_V2_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign_agentic_read_cursor_v2(payload: Mapping[str, object]) -> str:
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    body = _b64url_encode(json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    sig = hmac.new(secret, _AGENTIC_READ_CURSOR_V2_SALT + body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(sig)}"


def _verify_agentic_read_cursor_v2(cursor: str) -> dict[str, object]:
    raw = (cursor or "").strip()
    if not raw or "." not in raw:
        raise ValueError("invalid cursor")
    body_b64, sig_b64 = raw.split(".", 1)
    secret = str(getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    expected = hmac.new(secret, _AGENTIC_READ_CURSOR_V2_SALT + body_b64.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64url_decode(sig_b64)
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if not hmac.compare_digest(expected, provided):
        raise ValueError("invalid cursor")
    try:
        payload = json.loads(_b64url_decode(body_b64).decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid cursor")
    try:
        exp = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        raise ValueError("invalid cursor")
    if exp and exp <= int(time.time()):
        raise ValueError("expired cursor")
    return payload


def _agentic_read_v2_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Agentic Read V2 engine for refs-first retrieval.

    Internal interface (called by read_knowledge wrapper):
      - items=[{id,cursor?}...], max_chars=...

    Public interface (enforced by read_knowledge):
      - refs=[{id,cursor?}...], max_chars=...

    The tool selects the retrieval strategy internally (page blocks vs table rows vs
    chunk-window fallback) and returns deterministic continuation cursors when the
    requested content doesn't fit.
    """
    raw_items = arguments.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_items",
            "error_code": "missing_items",
            "evidence": [],
            "hint": "refs[] is required (use ids/cursors from search_knowledge/read_knowledge).",
        }

    # Per-call output cap: stay under MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS minus margin.
    try:
        raw_prompt_output_limit = getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", 12000)
        prompt_output_limit = int(raw_prompt_output_limit if raw_prompt_output_limit is not None else 12000)
    except (TypeError, ValueError):
        prompt_output_limit = 12000
    try:
        raw_safety_margin = (
            getattr(settings, "MCP_READ_KNOWLEDGE_MAX_CHARS_MARGIN", None)
            or getattr(settings, "MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", 800)
        )
        safety_margin = int(raw_safety_margin if raw_safety_margin is not None else 800)
    except (TypeError, ValueError):
        safety_margin = 800
    safe_prompt_limit = max(200, prompt_output_limit - max(0, safety_margin))

    remaining_turn_budget: int | None = None
    if context.char_budget_per_turn is not None:
        remaining_turn_budget = max(0, int(context.char_budget_per_turn) - int(context.characters_used or 0))

    max_chars_allowed = max(200, min(20000, safe_prompt_limit))
    if remaining_turn_budget is not None:
        max_chars_allowed = max(0, min(max_chars_allowed, remaining_turn_budget))
    if max_chars_allowed < 200:
        return {
            "tool": "read_knowledge",
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "evidence": [],
            "hint": "Prompt budget exceeded for this turn. Answer from collected evidence or ask a narrower question.",
        }

    raw_max_chars = arguments.get("max_chars")
    try:
        requested_max_chars = int(raw_max_chars)
    except (TypeError, ValueError):
        requested_max_chars = None
    if requested_max_chars is None:
        max_chars = max_chars_allowed
    else:
        max_chars = max(200, requested_max_chars)
        if max_chars > max_chars_allowed:
            return {
                "tool": "read_knowledge",
                "status": "constraint_error",
                "error": "max_chars_exceeded",
                "error_code": "max_chars_exceeded",
                "evidence": [],
                "requested_max_chars": max_chars,
                "max_chars_allowed": max_chars_allowed,
                "hint": (
                    f"max_chars is too high for this chat (requested {max_chars}, max {max_chars_allowed}). "
                    f"Retry with max_chars <= {max_chars_allowed}, or read fewer items."
                ),
            }

    try:
        raw_overflow_margin = int(getattr(settings, "MCP_READ_KNOWLEDGE_OVERFLOW_MARGIN", 200) or 0)
    except (TypeError, ValueError):
        raw_overflow_margin = 200
    overflow_margin = max(0, min(int(raw_overflow_margin), max(0, int(max_chars_allowed) - int(max_chars))))
    overflow_remaining = int(overflow_margin)

    # The backend chooses the correct representation and paging strategy.
    # `mode` is intentionally not part of the public agentic contract.
    mode = "auto"

    # Dedup items by (id,cursor) while preserving order.
    ordered_items: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str | None, int | None, int | None]] = set()
    for entry in raw_items:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or "").strip()
        if not item_id:
            continue
        cursor = entry.get("cursor")
        cursor_str = str(cursor).strip() if isinstance(cursor, str) and cursor.strip() else None
        row_start_raw = entry.get("row_start")
        row_limit_raw = entry.get("row_limit")
        row_start_value: int | None = None
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start_value = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start_value = None
        row_limit_value: int | None = None
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit_value = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit_value = None
        if row_limit_value is not None:
            row_limit_value = max(1, min(200, int(row_limit_value)))

        key = (item_id, cursor_str, row_start_value, row_limit_value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out_item: dict[str, object] = {"id": item_id, "cursor": cursor_str}
        if row_start_value is not None:
            out_item["row_start"] = row_start_value
        if row_limit_value is not None:
            out_item["row_limit"] = row_limit_value
        ordered_items.append(out_item)

    # ── Repeat-read detection (loop prevention) ──────────────────────────
    # Allow cursor-continuation reads (same ID + cursor = new page of content).
    # Block non-cursor re-reads of the same ref within a single turn.
    already_read_ids: set[str] = getattr(context, "read_ref_ids_this_turn", None) or set()
    filtered_items: list[dict[str, object]] = []
    blocked_items: list[dict[str, object]] = []
    for item in ordered_items:
        item_id = str(item["id"])
        has_cursor = bool(item.get("cursor"))
        has_row_start = item.get("row_start") is not None
        if item_id in already_read_ids and not has_cursor and not has_row_start:
            blocked_items.append({"id": item_id, "error": "Already read this turn. Answer from available evidence."})
        else:
            filtered_items.append(item)

    if not filtered_items:
        # ALL refs were already read this turn (non-cursor)
        return {
            "tool": "read_knowledge",
            "status": "already_read",
            "error_code": "already_read",
            "evidence": [],
            "blocked": blocked_items,
            "hint": "All requested refs were already read this turn. Answer from the evidence you have.",
        }

    if blocked_items:
        # Some refs blocked, continue with the rest
        ordered_items = filtered_items
    # ── End repeat-read detection ────────────────────────────────────────

    if not ordered_items:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_items",
            "error_code": "missing_items",
            "evidence": [],
            "hint": "items[] must contain at least one {id} from search_knowledge results.",
        }

    # NOTE: Some unit tests pass a lightweight conversation stub (SimpleNamespace)
    # instead of a real BusinessProfile instance. Avoid blowing up inside ORM
    # lookups by normalizing to a UUID early and returning a structured error
    # when missing/invalid.
    business_uuid: uuid.UUID | None = None
    raw_business_id = getattr(conversation, "business_profile_id", None)
    try:
        if raw_business_id is not None:
            business_uuid = uuid.UUID(str(raw_business_id))
    except (TypeError, ValueError, AttributeError):
        business_uuid = None
    business = getattr(conversation, "business_profile", None)
    upload_block_cache: dict[str, list[dict[str, object]]] = {}
    _heading_month_tokens = {
        "jan",
        "january",
        "feb",
        "february",
        "mar",
        "march",
        "apr",
        "april",
        "may",
        "jun",
        "june",
        "jul",
        "july",
        "aug",
        "august",
        "sep",
        "sept",
        "september",
        "oct",
        "october",
        "nov",
        "november",
        "dec",
        "december",
        "ongoing",
        "present",
    }

    def _upload_title(upload: KnowledgeUpload | None) -> str:
        if not upload:
            return "Untitled"
        title = (
            getattr(upload, "display_name", None)
            or getattr(upload, "filename", None)
            or getattr(upload, "source_name", None)
            or getattr(upload, "external_reference", None)
            or getattr(upload, "slug", None)
            or str(getattr(upload, "id", "") or "")
        )
        title_text = str(title or "").strip() or "Untitled"
        return title_text

    def _collapse_ws(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _parse_block_anchor(value: object) -> tuple[int, int] | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        match = re.search(r"p(?P<page>\d+)-b(?P<block>\d+)", raw)
        if not match:
            return None
        try:
            return int(match.group("page")), int(match.group("block"))
        except (TypeError, ValueError):
            return None

    def _load_ordered_page_blocks(upload_id: str) -> list[dict[str, object]]:
        cached = upload_block_cache.get(upload_id)
        if cached is not None:
            return cached
        try:
            from apps.knowledge.models import KnowledgeUploadPageBlock

            rows = list(
                KnowledgeUploadPageBlock.objects.filter(upload_id=upload_id)
                .exclude(text="")
                .order_by("page__page_number", "order_index")
                .values(
                    "page__page_number",
                    "order_index",
                    "text",
                    "section_heading",
                    "heading_path",
                )
            )
        except Exception:
            rows = []
        normalized: list[dict[str, object]] = []
        for row in rows:
            try:
                page_number = int(row.get("page__page_number") or 0)
                order_index = int(row.get("order_index") or 0)
            except (TypeError, ValueError):
                continue
            normalized.append(
                {
                    "page_number": page_number,
                    "order_index": order_index,
                    "text": str(row.get("text") or ""),
                    "section_heading": str(row.get("section_heading") or ""),
                    "heading_path": list(row.get("heading_path") or []),
                }
            )
        upload_block_cache[upload_id] = normalized
        return normalized

    def _classify_heading_block(block: Mapping[str, object]) -> dict[str, object] | None:
        raw_text = str(block.get("text") or "")
        text = _collapse_ws(raw_text)
        if not text:
            return None

        normalized_path = [_collapse_ws(item) for item in (block.get("heading_path") or []) if _collapse_ws(item)]
        explicit_heading = _collapse_ws(block.get("section_heading") or "")
        if normalized_path or explicit_heading:
            label = normalized_path[-1] if normalized_path else explicit_heading
            return {
                "page_number": int(block.get("page_number") or 0),
                "order_index": int(block.get("order_index") or 0),
                "label": label,
                "level": max(1, len(normalized_path) or 1),
                "signature": "|".join(item.lower() for item in (normalized_path or [label]) if item),
                "heuristic": False,
            }

        if len(text) > 180:
            return None
        stripped = text.lstrip()
        if not stripped:
            return None
        if stripped.startswith(("●", "•", "-", "–", "—", "➔", "*")):
            return None
        if stripped[0].islower():
            return None
        if text[-1:] in {".", ";", "?", "!"}:
            return None

        tokens = re.findall(r"[A-Za-z0-9&/+'().-]+", text)
        if not tokens or len(tokens) > 22:
            return None

        lower_tokens = [token.lower() for token in tokens]
        has_digits = any(ch.isdigit() for ch in text)
        has_month = any(token in _heading_month_tokens for token in lower_tokens)
        pipe_count = text.count("|")
        comma_count = text.count(",")
        colon_count = text.count(":")

        level = 0
        if len(tokens) <= 6 and not has_digits and not has_month and pipe_count == 0 and comma_count <= 1 and colon_count == 0:
            level = 1
        elif len(tokens) <= 12 and not has_digits and not has_month and comma_count <= 1 and colon_count == 0:
            level = 1
        elif len(tokens) <= 20 and (has_digits or has_month or pipe_count > 0 or comma_count > 0) and colon_count <= 1:
            level = 2

        if level <= 0:
            return None

        return {
            "page_number": int(block.get("page_number") or 0),
            "order_index": int(block.get("order_index") or 0),
            "label": text,
            "level": level,
            "signature": text.lower(),
            "heuristic": True,
        }

    def _resolve_text_section_span(
        *,
        upload_id: str,
        chunk_record: KnowledgeUploadChunk | None,
        chunk_meta: Mapping[str, object],
        fallback_page_number: int,
    ) -> dict[str, int] | None:
        blocks = _load_ordered_page_blocks(upload_id)
        if not blocks:
            return None

        block_index_by_key = {
            (int(block["page_number"]), int(block["order_index"])): idx
            for idx, block in enumerate(blocks)
        }

        raw_block_anchors = chunk_meta.get("block_anchors")
        parsed_anchors: list[tuple[int, int]] = []
        if isinstance(raw_block_anchors, list):
            for entry in raw_block_anchors:
                parsed = _parse_block_anchor(entry)
                if parsed is not None:
                    parsed_anchors.append(parsed)
        parsed_anchors = [anchor for anchor in parsed_anchors if anchor in block_index_by_key]
        parsed_anchors.sort()

        if parsed_anchors:
            anchor_start_key = parsed_anchors[0]
            anchor_end_key = parsed_anchors[-1]
        else:
            canonical_anchor = _parse_block_anchor(chunk_meta.get("canonical_anchor_id"))
            if canonical_anchor and canonical_anchor in block_index_by_key:
                anchor_start_key = canonical_anchor
                anchor_end_key = canonical_anchor
            else:
                anchor_start_key = (int(fallback_page_number), 0)
                anchor_end_key = (int(fallback_page_number), 0)
                if anchor_start_key not in block_index_by_key:
                    return None

        anchor_start_idx = block_index_by_key.get(anchor_start_key)
        anchor_end_idx = block_index_by_key.get(anchor_end_key)
        if anchor_start_idx is None or anchor_end_idx is None:
            return None
        if anchor_end_idx < anchor_start_idx:
            anchor_start_idx, anchor_end_idx = anchor_end_idx, anchor_start_idx

        headings: list[dict[str, object]] = []
        heading_index_by_pos: dict[tuple[int, int], int] = {}
        for idx, block in enumerate(blocks):
            candidate = _classify_heading_block(block)
            if candidate is None:
                continue
            candidate["idx"] = idx
            headings.append(candidate)
            heading_index_by_pos[(int(candidate["page_number"]), int(candidate["order_index"]))] = idx

        if not headings:
            return None

        anchor_major: list[dict[str, object]] = [
            heading
            for heading in headings
            if anchor_start_idx <= int(heading["idx"]) <= anchor_end_idx and int(heading["level"]) == 1
        ]
        if anchor_major:
            start_heading = anchor_major[-1]
        else:
            prior_major = [
                heading
                for heading in headings
                if int(heading["idx"]) <= anchor_start_idx and int(heading["level"]) == 1
            ]
            if prior_major:
                start_heading = prior_major[-1]
            else:
                anchor_any = [
                    heading
                    for heading in headings
                    if anchor_start_idx <= int(heading["idx"]) <= anchor_end_idx
                ]
                if anchor_any:
                    start_heading = anchor_any[-1]
                else:
                    prior_any = [heading for heading in headings if int(heading["idx"]) <= anchor_start_idx]
                    if not prior_any:
                        return None
                    start_heading = prior_any[-1]

        start_idx = int(start_heading["idx"])
        start_level = int(start_heading["level"])
        end_idx = len(blocks) - 1
        start_signature = str(start_heading.get("signature") or "")
        for heading in headings:
            idx = int(heading["idx"])
            if idx <= start_idx:
                continue
            level = int(heading["level"])
            signature = str(heading.get("signature") or "")
            if level <= start_level and signature != start_signature:
                end_idx = idx - 1
                break

        if end_idx < start_idx:
            return None

        start_block = blocks[start_idx]
        end_block = blocks[end_idx]
        return {
            "start_page_number": int(start_block["page_number"]),
            "start_block_order": int(start_block["order_index"]),
            "end_page_number": int(end_block["page_number"]),
            "end_block_order": int(end_block["order_index"]),
        }

    def _cursor_payload_base(*, item_id: str, kind: str) -> dict[str, object]:
        exp = int(time.time()) + _AGENTIC_READ_CURSOR_V2_TTL_SECONDS
        return {
            "v": 2,
            "exp": exp,
            "conversation_id": str(conversation.id),
            "business_id": str(conversation.business_profile_id),
            "item_id": item_id,
            "kind": kind,
        }

    def _decode_cursor(item_id: str, cursor: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
        try:
            payload = _verify_agentic_read_cursor_v2(cursor)
        except ValueError as exc:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": str(exc) or "Invalid cursor."}
        if str(payload.get("conversation_id") or "") != str(conversation.id):
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different conversation."}
        if str(payload.get("business_id") or "") != str(conversation.business_profile_id):
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different business."}
        if str(payload.get("item_id") or "") != item_id:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor does not match the requested id."}
        if int(payload.get("v") or 0) != 2:
            return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Unsupported cursor version."}
        return payload, None

    def _resolve_cursor_from_handle(cursor_token: str | None) -> str | None:
        token = str(cursor_token or "").strip()
        if not token:
            return None
        cache_value = getattr(context, "read_cursor_handles", None)
        if isinstance(cache_value, dict):
            mapped = cache_value.get(token)
            if isinstance(mapped, str) and mapped.strip():
                return mapped.strip()
        return token

    def _store_cursor_handle(cursor_signed: str | None) -> str | None:
        token = str(cursor_signed or "").strip()
        if not token:
            return None
        cache_value = getattr(context, "read_cursor_handles", None)
        reverse_cache_value = getattr(context, "read_cursor_reverse_handles", None)
        if not isinstance(cache_value, dict) or not isinstance(reverse_cache_value, dict):
            return token

        existing_handle = reverse_cache_value.get(token)
        if isinstance(existing_handle, str) and existing_handle.strip():
            cached_token = cache_value.get(existing_handle.strip())
            if isinstance(cached_token, str) and cached_token == token:
                return existing_handle.strip()

        handle = f"c_{uuid.uuid4().hex[:20]}"
        cache_value[handle] = token
        reverse_cache_value[token] = handle

        while len(cache_value) > 500:
            oldest_handle = next(iter(cache_value))
            oldest_cursor = cache_value.pop(oldest_handle, None)
            if isinstance(oldest_cursor, str):
                reverse_cache_value.pop(oldest_cursor, None)
        while len(reverse_cache_value) > 500:
            oldest_cursor_key = next(iter(reverse_cache_value))
            oldest_handle_value = reverse_cache_value.pop(oldest_cursor_key, None)
            if isinstance(oldest_handle_value, str):
                cache_value.pop(oldest_handle_value, None)

        return handle

    def _load_table_anchor_manifest(*, ref_id: str, table_id: str | None = None) -> dict[str, object] | None:
        cache_value = getattr(context, "table_row_anchor_manifests", None)
        if not isinstance(cache_value, dict):
            return None
        candidate_keys: list[str] = []
        ref_key = str(ref_id or "").strip()
        table_key = str(table_id or "").strip()
        if ref_key:
            candidate_keys.append(ref_key)
        if table_key and table_key not in candidate_keys:
            candidate_keys.append(table_key)
        for key in candidate_keys:
            raw_manifest = cache_value.get(key)
            if not isinstance(raw_manifest, Mapping):
                continue
            anchors: list[int] = []
            raw_anchors = raw_manifest.get("anchors")
            if isinstance(raw_anchors, list):
                for entry in raw_anchors:
                    if isinstance(entry, Mapping):
                        parsed = _coerce_int(entry.get("row_index"))
                    else:
                        parsed = _coerce_int(entry)
                    if parsed is None or parsed < 0:
                        continue
                    anchors.append(int(parsed))
            # Deduplicate anchors while preserving order.
            if anchors:
                seen_rows: set[int] = set()
                anchors = [row for row in anchors if (row not in seen_rows and not seen_rows.add(row))]

            matched_row_index = _coerce_int(raw_manifest.get("matched_row_index"))
            if matched_row_index is None:
                matched_row_index = -1
            if matched_row_index < 0:
                if anchors:
                    matched_row_index = int(anchors[0])
                else:
                    continue

            # Ensure anchors includes matched_row_index, and keep it first.
            if matched_row_index in anchors:
                anchors = [matched_row_index] + [row for row in anchors if row != matched_row_index]
            else:
                anchors = [matched_row_index] + anchors
            anchors = anchors[:5]

            out: dict[str, object] = {
                "ref_id": key,
                "table_id": table_key or str(raw_manifest.get("table_id") or "").strip(),
                "matched_row_index": int(matched_row_index),
            }
            if anchors:
                out["anchors"] = list(anchors)

            try:
                estimated_rows = int(raw_manifest.get("estimated_rows") or 0)
            except (TypeError, ValueError):
                estimated_rows = 0
            if estimated_rows > 0:
                out["estimated_rows"] = int(estimated_rows)

            try:
                estimated_columns = int(raw_manifest.get("estimated_columns") or 0)
            except (TypeError, ValueError):
                estimated_columns = 0
            if estimated_columns > 0:
                out["estimated_columns"] = int(estimated_columns)

            return out
        return None

    def _resolve_target(item_id: str) -> tuple[
        KnowledgeUploadChunk | None,
        KnowledgeUpload | None,
        KnowledgeUploadTable | None,
        KnowledgeUploadTableRow | None,
        dict[str, object] | None,
    ]:
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

    agent_scope = _agent_knowledge_scope(conversation, context)

    def _enforce_access(upload_id: str, *, item_id: str) -> dict[str, object] | None:
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

    def _read_page_blocks_segment(
        *,
        item_id: str,
        upload: KnowledgeUpload,
        upload_id: str,
        page_number: int,
        start_order: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
    ) -> tuple[str, dict[str, object] | None, bool]:
        try:
            from apps.knowledge.models import (
                KnowledgeUploadPage,
                KnowledgeUploadPageBlock,
            )
        except Exception:
            return "", None, True

        page_obj = (
            KnowledgeUploadPage.objects.filter(upload_id=upload_id, page_number=page_number)
            .only("id")
            .first()
        )
        if not page_obj:
            return "", None, True

        blocks_qs = (
            KnowledgeUploadPageBlock.objects.filter(page_id=page_obj.id)
            .exclude(text="")
            .order_by("order_index")
            .values_list("order_index", "text")
        )

        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        # Only prepend a separator when resuming at an element boundary.
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        started = False
        for order_index, text in blocks_qs.iterator():  # type: ignore[attr-defined]
            try:
                order_int = int(order_index)
            except (TypeError, ValueError):
                continue
            if order_int < int(start_order):
                continue
            raw_text = str(text or "")
            if not raw_text:
                continue

            chunk_text = raw_text
            offset = 0
            if not started:
                started = True
                offset = max(0, int(start_offset))
                if offset:
                    chunk_text = chunk_text[offset:]
            separator = "\n\n" if (out_parts or prepend_sep) else ""
            # If we can't fit the separator + at least one character, stop and continue on next call.
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="page_blocks"),
                    "upload_id": upload_id,
                    "page_number": int(page_number),
                    "block_order": int(order_int),
                    "char_offset": int(offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(chunk_text) <= remaining:
                out_parts.append(chunk_text)
                remaining -= len(chunk_text)
                # Continue to next block
                continue

            # Partial block
            out_parts.append(chunk_text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="page_blocks"),
                "upload_id": upload_id,
                "page_number": int(page_number),
                "block_order": int(order_int),
                "char_offset": int(offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete

    def _read_section_span_segment(
        *,
        item_id: str,
        upload_id: str,
        start_page_number: int,
        start_block_order: int,
        end_page_number: int,
        end_block_order: int,
        current_page_number: int,
        current_block_order: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
    ) -> tuple[str, dict[str, object] | None, bool]:
        blocks = _load_ordered_page_blocks(upload_id)
        if not blocks:
            return "", None, True

        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        started = False
        current_page = int(current_page_number)
        current_order = int(current_block_order)
        for block in blocks:
            try:
                page_int = int(block.get("page_number") or 0)
                order_int = int(block.get("order_index") or 0)
            except (TypeError, ValueError):
                continue
            if (page_int, order_int) < (int(start_page_number), int(start_block_order)):
                continue
            if (page_int, order_int) > (int(end_page_number), int(end_block_order)):
                continue
            if (page_int, order_int) < (current_page, current_order):
                continue
            raw_text = str(block.get("text") or "")
            if not raw_text:
                continue

            chunk_text = raw_text
            offset = 0
            if not started:
                started = True
                offset = max(0, int(start_offset))
                if offset:
                    chunk_text = chunk_text[offset:]

            separator = "\n\n" if (out_parts or prepend_sep) else ""
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="section_span"),
                    "upload_id": upload_id,
                    "start_page_number": int(start_page_number),
                    "start_block_order": int(start_block_order),
                    "end_page_number": int(end_page_number),
                    "end_block_order": int(end_block_order),
                    "current_page_number": int(page_int),
                    "current_block_order": int(order_int),
                    "char_offset": int(offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(chunk_text) <= remaining:
                out_parts.append(chunk_text)
                remaining -= len(chunk_text)
                continue

            out_parts.append(chunk_text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="section_span"),
                "upload_id": upload_id,
                "start_page_number": int(start_page_number),
                "start_block_order": int(start_block_order),
                "end_page_number": int(end_page_number),
                "end_block_order": int(end_block_order),
                "current_page_number": int(page_int),
                "current_block_order": int(order_int),
                "char_offset": int(offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete


    def _read_tabular_rows_segment_facts(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        start_row_index: int = 0,
        budget_chars: int,
        max_rows: int | None = None,
        business_profile,
        selection_mode: str | None = None,
    ) -> tuple[dict[str, object], dict[str, object] | None, bool]:
        """
        Facts-first table read for the LLM.

        Contract:
        - `start_row_index` is a 0-based row offset within the visible table body
          (header/separator rows excluded by ingestion metadata).
        - Returns only the table facts + paging signals needed to continue via
          `row_start` + `row_limit` (no table cursors).
        """

        budget_limit = max(0, int(budget_chars))
        complete = True

        payload: dict[str, object] = {
            "type": "table",
            "table_id": str(table_id),
            "columns": [],
            "rows": [],
            "row_offset": 0,
            "rows_shown": 0,
            "total_rows": 0,
        }
        if isinstance(selection_mode, str) and selection_mode.strip():
            payload["selection_mode"] = selection_mode.strip()

        try:
            table_uuid = uuid.UUID(str(table_id))
        except (TypeError, ValueError):
            return payload, None, True

        table = (
            KnowledgeUploadTable.objects.filter(
                id=table_uuid,
                upload_id=upload_id,
                upload__business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .only("id", "order_index", "title", "section_heading", "column_schema")
            .first()
        )
        if not table:
            return payload, None, True

        def _dedupe_column_labels(raw_columns: Sequence[str]) -> list[str]:
            deduped: list[str] = []
            seen: dict[str, int] = {}
            for index, raw_column in enumerate(raw_columns):
                label = str(raw_column or "").strip() or f"column_{index + 1}"
                key = label.lower()
                count = seen.get(key, 0) + 1
                seen[key] = count
                deduped.append(label if count == 1 else f"{label}_{count}")
            return deduped

        # Column labels: prefer column_schema if it looks like a list of strings.
        raw_schema = table.column_schema if isinstance(getattr(table, "column_schema", None), list) else []
        columns: list[str] = []
        for entry in raw_schema:
            if isinstance(entry, str) and entry.strip():
                columns.append(entry.strip())
            elif isinstance(entry, Mapping):
                for key in ("column", "name", "label", "key", "column_key"):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip():
                        columns.append(value.strip())
                        break
        columns = columns[:200]

        # Visible rows are non-header rows only (in ingestion metadata).
        non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
            metadata__row_type__in=["header", "section_header"]
        )

        # If schema is missing/empty, infer columns from the first visible row's cells.
        if not columns:
            row_obj = (
                table.rows.filter(non_header_q)
                .order_by("row_index")
                .only("id", "row_index")
                .first()
            )
            if row_obj:
                cell_qs = (
                    KnowledgeUploadTableCell.objects.filter(row_id=row_obj.id)
                    .order_by("column_index")
                    .values_list("column_index", "column_key")
                )
                inferred: list[tuple[int, str]] = []
                for col_idx, col_key in cell_qs:
                    try:
                        idx = int(col_idx)
                    except (TypeError, ValueError):
                        continue
                    label = str(col_key or "").strip() or f"column_{idx + 1}"
                    inferred.append((idx, label))
                inferred.sort(key=lambda item: item[0])
                columns = [label for _idx, label in inferred][:200]

        columns = _dedupe_column_labels(columns[:200])
        payload["columns"] = columns

        try:
            start_row = max(0, int(start_row_index))
        except (TypeError, ValueError):
            start_row = 0
        payload["row_offset"] = start_row

        try:
            total_rows = int(table.rows.filter(non_header_q).count())
        except Exception:
            total_rows = 0
        payload["total_rows"] = total_rows

        # Bound reads: even for "list everything", never scan unbounded rows in one call.
        try:
            scan_cap = int(max(50, min(500, int(budget_chars) // 30 or 50)))
        except Exception:
            scan_cap = 200
        limit_rows = scan_cap
        if isinstance(max_rows, int) and max_rows > 0:
            limit_rows = min(limit_rows, int(max_rows))
        limit_rows = max(1, int(limit_rows))

        row_qs = (
            table.rows.filter(non_header_q)
            .order_by("row_index")
            .only("id", "row_index", "metadata")
        )
        row_qs = row_qs[start_row : start_row + limit_rows]
        row_qs = row_qs.prefetch_related(
            Prefetch(
                "cells",
                queryset=KnowledgeUploadTableCell.objects.order_by("column_index").only(
                    "row_id",
                    "column_index",
                    "column_key",
                    "raw_text",
                ),
            )
        )

        def _normalize_cell_text(value: str) -> str:
            normalized = str(value or "").strip().lower()
            normalized = re.sub(r"\\s+", " ", normalized)
            return normalized

        def _is_uniform_section_separator(values: Sequence[str]) -> tuple[bool, str]:
            non_empty = [str(cell or "").strip() for cell in values if str(cell or "").strip()]
            if len(non_empty) < 4:
                return False, ""
            normalized = [_normalize_cell_text(cell) for cell in non_empty]
            first = normalized[0] if normalized else ""
            if not first:
                return False, ""
            if any(cell != first for cell in normalized[1:]):
                return False, ""
            return True, non_empty[0]

        scope_overlay_reasons = {
            "scope_explicit_span",
            "scope_repeated_value_span",
            "scope_sparse_expansion",
            "scope_edge_completion",
        }
        column_index_lookup: dict[str, int] = {}
        for idx, label in enumerate(columns):
            normalized = _normalize_column_name(label)
            if normalized and normalized not in column_index_lookup:
                column_index_lookup[normalized] = idx
            lowered = str(label or "").strip().lower()
            if lowered and lowered not in column_index_lookup:
                column_index_lookup[lowered] = idx

        index_offset: int | None = None
        rows_out: list[list[str]] = []

        def _payload_size(candidate_rows: Sequence[Sequence[str]]) -> int:
            candidate: dict[str, object] = {
                "type": "table",
                "table_id": str(table_id),
                "columns": list(columns),
                "rows": [list(row) for row in candidate_rows],
                "row_offset": int(start_row),
                "rows_shown": int(len(candidate_rows)),
                "total_rows": int(total_rows),
            }
            if isinstance(selection_mode, str) and selection_mode.strip():
                candidate["selection_mode"] = selection_mode.strip()
            try:
                return len(json.dumps(candidate, ensure_ascii=False, default=str))
            except Exception:
                return 0

        for row in row_qs:
            cell_lookup: dict[int, str] = {}
            for cell in row.cells.all():  # type: ignore[attr-defined]
                try:
                    idx = int(getattr(cell, "column_index", None) or 0)
                except (TypeError, ValueError):
                    continue
                text = str(getattr(cell, "raw_text", "") or "")
                cell_lookup[idx] = text

            if index_offset is None:
                keys = list(cell_lookup.keys())
                if 0 in cell_lookup:
                    index_offset = 0
                elif 1 in cell_lookup:
                    index_offset = 1
                elif keys:
                    index_offset = min(keys)
                else:
                    index_offset = 0

            values: list[str] = []
            for col_idx in range(len(columns)):
                values.append(str(cell_lookup.get(int(col_idx) + int(index_offset or 0), "")))

            # Collapse broad-span note rows (same text duplicated across many columns) to:
            #   [note_text, "", "", ...]
            is_uniform, uniform_label = _is_uniform_section_separator(values)
            if is_uniform and uniform_label:
                values = [uniform_label] + [""] * max(0, len(columns) - 1)

            row_meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
            inferred_scope_columns: list[str] = []
            raw_applies_to = row_meta.get("inferred_scope_columns")
            if isinstance(raw_applies_to, (list, tuple)):
                for scope_entry in raw_applies_to:
                    label = str(scope_entry or "").strip()
                    if label:
                        inferred_scope_columns.append(label)
            scope_reason = str(row_meta.get("scope_reason") or "").strip()
            scope_reason_key = scope_reason.lower()
            fee_value = str(row_meta.get("scope_value") or "").strip()

            effective_values = list(values)
            if (
                fee_value
                and inferred_scope_columns
                and (
                    scope_reason_key.startswith("inferred_")
                    or scope_reason_key in scope_overlay_reasons
                )
            ):
                for scope_label in inferred_scope_columns:
                    normalized_scope = _normalize_column_name(scope_label)
                    if not normalized_scope:
                        continue
                    col_pos = column_index_lookup.get(normalized_scope)
                    if col_pos is None:
                        col_pos = column_index_lookup.get(str(scope_label).strip().lower())
                    if col_pos is None or col_pos < 0 or col_pos >= len(effective_values):
                        continue
                    if str(effective_values[col_pos] or "").strip():
                        continue
                    effective_values[col_pos] = fee_value

            candidate_rows = [*rows_out, effective_values]
            if _payload_size(candidate_rows) > budget_limit:
                complete = False
                break
            rows_out = candidate_rows

        payload["rows"] = rows_out
        payload["rows_shown"] = len(rows_out)
        if (start_row + len(rows_out)) < int(total_rows or 0):
            payload["next_row_start"] = int(start_row + len(rows_out))

        return payload, None, complete

    def _table_payload_is_informative(table_payload: Mapping[str, object]) -> bool:
        rows_value = table_payload.get("rows")
        if not isinstance(rows_value, list) or not rows_value:
            return False
        for row in rows_value:
            if isinstance(row, (list, tuple)):
                if any(str(cell or "").strip() for cell in row):
                    return True
            elif str(row or "").strip():
                return True
        return False

    def _table_payload_size(table_payload: Mapping[str, object]) -> int:
        try:
            return len(json.dumps(dict(table_payload), ensure_ascii=False, default=str))
        except Exception:
            return 0

    def _merge_table_anchor_payloads(
        *,
        base_payload: Mapping[str, object],
        incoming_payload: Mapping[str, object],
        budget_chars: int,
        selection_mode: str,
    ) -> tuple[dict[str, object], int, bool]:
        merged_columns = list(base_payload.get("columns") or incoming_payload.get("columns") or [])
        if not merged_columns:
            merged_columns = list(incoming_payload.get("columns") or [])

        base_rows_raw = base_payload.get("rows")
        incoming_rows_raw = incoming_payload.get("rows")
        base_rows = [list(row) for row in base_rows_raw] if isinstance(base_rows_raw, list) else []
        incoming_rows = [list(row) for row in incoming_rows_raw] if isinstance(incoming_rows_raw, list) else []

        merged: dict[str, object] = {
            "type": "table",
            "table_id": str(base_payload.get("table_id") or incoming_payload.get("table_id") or ""),
            "columns": merged_columns,
            "rows": [],
            "row_offset": min(
                max(0, _coerce_int(base_payload.get("row_offset")) or 0),
                max(0, _coerce_int(incoming_payload.get("row_offset")) or 0),
            ),
            "rows_shown": 0,
            "total_rows": max(
                max(0, _coerce_int(base_payload.get("total_rows")) or 0),
                max(0, _coerce_int(incoming_payload.get("total_rows")) or 0),
            ),
            "selection_mode": selection_mode,
        }

        merged_rows: list[list[str]] = []
        seen_rows: set[tuple[str, ...]] = set()
        for row in [*base_rows, *incoming_rows]:
            normalized_row = tuple(str(cell or "") for cell in row)
            if normalized_row in seen_rows:
                continue
            candidate_rows = [*merged_rows, list(row)]
            candidate_payload = dict(merged)
            candidate_payload["rows"] = candidate_rows
            candidate_payload["rows_shown"] = len(candidate_rows)
            if _table_payload_size(candidate_payload) > max(0, int(budget_chars)):
                merged["rows"] = merged_rows
                merged["rows_shown"] = len(merged_rows)
                if (
                    int(merged["row_offset"]) + len(merged_rows)
                ) < int(merged.get("total_rows") or 0):
                    merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
                return merged, max(0, len(merged_rows) - len(base_rows)), True
            merged_rows = candidate_rows
            seen_rows.add(normalized_row)

        merged["rows"] = merged_rows
        merged["rows_shown"] = len(merged_rows)
        if (
            int(merged["row_offset"]) + len(merged_rows)
        ) < int(merged.get("total_rows") or 0):
            merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
        return merged, max(0, len(merged_rows) - len(base_rows)), False

    def _table_db_row_index_to_visible_row_start(
        *,
        table_id: str,
        upload_id: str,
        business_profile,
        db_row_index: int,
    ) -> int:
        """
        Convert a stored table row index into a visible `row_start` offset.

        The visible table body excludes header / section-header rows, so row refs
        and anchor reads must normalize DB row indexes before paging facts.
        """
        try:
            raw_idx = int(db_row_index)
        except (TypeError, ValueError):
            return 0
        if raw_idx < 0:
            return 0
        try:
            table_uuid = uuid.UUID(str(table_id))
        except (TypeError, ValueError):
            return max(0, raw_idx)

        non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
            metadata__row_type__in=["header", "section_header"]
        )

        base_qs = KnowledgeUploadTableRow.objects.filter(
            table_id=table_uuid,
            table__upload_id=upload_id,
            table__upload__business_profile=business_profile,
            table__upload__status=KnowledgeStatus.ACTIVE,
        )

        target = (
            base_qs.filter(non_header_q, row_index__gte=raw_idx)
            .order_by("row_index")
            .only("row_index")
            .first()
        )
        if target is None:
            target = (
                base_qs.filter(non_header_q, row_index__lte=raw_idx)
                .order_by("-row_index")
                .only("row_index")
                .first()
            )
        if target is None:
            return 0

        try:
            target_db_idx = int(getattr(target, "row_index", 0) or 0)
        except (TypeError, ValueError):
            target_db_idx = raw_idx

        try:
            visible_before = int(
                base_qs.filter(non_header_q, row_index__lt=int(target_db_idx)).count()
            )
        except Exception:
            visible_before = max(0, raw_idx)
        return max(0, int(visible_before))

    def _read_exact_table_row(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        db_row_index: int,
        budget_chars: int,
        business_profile,
    ) -> tuple[dict[str, object], bool]:
        visible_row_start = _table_db_row_index_to_visible_row_start(
            table_id=table_id,
            upload_id=upload_id,
            business_profile=business_profile,
            db_row_index=db_row_index,
        )
        table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=visible_row_start,
            budget_chars=budget_chars,
            max_rows=1,
            business_profile=business_profile,
            selection_mode="row_ref",
        )
        return table_payload, complete

    def _read_tabular_rows_with_anchor(
        *,
        item_id: str,
        upload_id: str,
        table_id: str,
        start_row_index: int,
        budget_chars: int,
        max_rows: int | None,
        business_profile,
        use_anchor: bool,
    ) -> tuple[dict[str, object], dict[str, object] | None, bool, bool, bool]:
        anchor_used = False
        fallback_used = False
        anchor_start_row = int(start_row_index)

        if use_anchor and anchor_start_row <= 0:
            manifest = _load_table_anchor_manifest(ref_id=item_id, table_id=table_id)
            if isinstance(manifest, Mapping):
                anchor_rows: list[int] = []
                primary_anchor = _coerce_int(manifest.get("matched_row_index"))
                if primary_anchor is not None and primary_anchor >= 0:
                    anchor_rows.append(int(primary_anchor))
                raw_anchors = manifest.get("anchors")
                if isinstance(raw_anchors, list):
                    for entry in raw_anchors:
                        parsed = _coerce_int(entry)
                        if parsed is None or parsed < 0:
                            continue
                        anchor_rows.append(int(parsed))
                if anchor_rows:
                    # Deduplicate while preserving order, and cap attempts.
                    seen_rows: set[int] = set()
                    anchor_rows = [row for row in anchor_rows if (row not in seen_rows and not seen_rows.add(row))]
                    anchor_used = True
                    merged_payload: dict[str, object] | None = None
                    merged_complete = True
                    merged_anchor_hits = 0
                    for row_idx in anchor_rows[:2]:
                        visible_row_start = _table_db_row_index_to_visible_row_start(
                            table_id=table_id,
                            upload_id=upload_id,
                            business_profile=business_profile,
                            db_row_index=int(row_idx),
                        )
                        anchor_start_row = max(0, int(visible_row_start) - 1)
                        table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
                            item_id=item_id,
                            upload_id=upload_id,
                            table_id=table_id,
                            start_row_index=anchor_start_row,
                            budget_chars=budget_chars,
                            max_rows=max_rows,
                            business_profile=business_profile,
                            selection_mode="anchor_match",
                        )
                        if not _table_payload_is_informative(table_payload):
                            merged_complete = False
                            continue
                        if merged_payload is None:
                            merged_payload = dict(table_payload)
                            merged_complete = bool(complete)
                            merged_anchor_hits = 1
                            continue
                        merged_payload, added_rows, merge_budget_exhausted = _merge_table_anchor_payloads(
                            base_payload=merged_payload,
                            incoming_payload=table_payload,
                            budget_chars=budget_chars,
                            selection_mode="anchor_merge",
                        )
                        if added_rows > 0:
                            merged_anchor_hits += 1
                        merged_complete = bool(merged_complete and complete and not merge_budget_exhausted)
                    if merged_payload is not None and _table_payload_is_informative(merged_payload):
                        if merged_anchor_hits <= 1:
                            merged_payload["selection_mode"] = "anchor_match"
                        return merged_payload, None, merged_complete, anchor_used, fallback_used
                    # Anchors were tried but didn't yield useful rows; fall back.
                    fallback_used = True

        table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=max(0, int(start_row_index)),
            budget_chars=budget_chars,
            max_rows=max_rows,
            business_profile=business_profile,
            selection_mode="row_range",
        )

        return table_payload, cursor_out, complete, anchor_used, fallback_used

    def _read_chunk_window_segment(
        *,
        item_id: str,
        upload_id: str,
        start_index: int,
        end_index: int,
        current_index: int,
        start_offset: int,
        budget_chars: int,
        prepend_sep: bool = False,
        business_profile,
    ) -> tuple[str, dict[str, object] | None, bool]:
        remaining = max(0, int(budget_chars))
        out_parts: list[str] = []
        cursor_next: dict[str, object] | None = None
        complete = True
        # Only prepend a separator when resuming at an element boundary.
        prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

        window_qs = (
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    upload_id=upload_id,
                    business_profile=business_profile,
                    upload__status=KnowledgeStatus.ACTIVE,
                    chunk_index__gte=int(start_index),
                    chunk_index__lte=int(end_index),
                )
            )
            .exclude(content="")
            .order_by("chunk_index")
            .values_list("chunk_index", "content")
        )

        cur_idx = int(current_index)
        offset = max(0, int(start_offset))
        started = False
        for chunk_index, content in window_qs.iterator():  # type: ignore[attr-defined]
            try:
                ci = int(chunk_index)
            except (TypeError, ValueError):
                continue
            if ci < cur_idx:
                continue
            raw = str(content or "")
            if not raw:
                continue
            text = raw
            local_offset = 0
            if not started:
                started = True
                local_offset = offset
                if local_offset:
                    text = text[local_offset:]

            separator = "\n\n" if (out_parts or prepend_sep) else ""
            needed_min = len(separator) + 1
            if remaining < needed_min:
                next_prepend_sep = True if out_parts else bool(prepend_sep)
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="chunk_window"),
                    "upload_id": upload_id,
                    "chunk_start": int(start_index),
                    "chunk_end": int(end_index),
                    "chunk_index": int(ci),
                    "char_offset": int(local_offset),
                }
                if next_prepend_sep:
                    cursor_next["prepend_sep"] = True
                complete = False
                break
            if separator:
                out_parts.append(separator)
                remaining -= len(separator)

            if len(text) <= remaining:
                out_parts.append(text)
                remaining -= len(text)
                continue

            out_parts.append(text[:remaining])
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="chunk_window"),
                "upload_id": upload_id,
                "chunk_start": int(start_index),
                "chunk_end": int(end_index),
                "chunk_index": int(ci),
                "char_offset": int(local_offset + remaining),
            }
            complete = False
            break

        content_out = "".join(out_parts)
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
        return content_out, ({"cursor": cursor_str} if cursor_str else None), complete

    def _read_artifact_segment(
        *,
        item_id: str,
        artifact_id: str,
        start_offset: int,
        budget_chars: int,
    ) -> tuple[str, dict[str, object] | None, bool, dict[str, object] | None]:
        """
        Read from a stored tool-output artifact (Phase 4 fallback).

        Artifact payload shape (response JSON):
          { "text": "...", "title": "...", "type": "text|table", "cursor_after": "opaque_or_null" }
        """

        try:
            artifact_uuid = uuid.UUID(str(artifact_id))
        except (TypeError, ValueError):
            return "", None, True, {"id": item_id, "error_code": "invalid_cursor", "hint": "Invalid artifact id."}

        artifact = (
            McpToolOutputArtifact.objects.filter(
                id=artifact_uuid,
                conversation=conversation,
                invoked_tool="read_knowledge",
            )
            .only("id", "response")
            .first()
        )
        if not artifact:
            return "", None, True, {"id": item_id, "error_code": "artifact_not_found", "hint": "Artifact not found for this conversation."}

        payload = artifact.response if isinstance(getattr(artifact, "response", None), Mapping) else {}
        full_text = payload.get("text")
        if not isinstance(full_text, str):
            full_text = str(full_text or "")
        if not full_text:
            return "", None, True, {"id": item_id, "error_code": "artifact_empty", "hint": "Artifact has no readable text."}

        title_override = payload.get("title")
        type_override = payload.get("type")

        start = max(0, int(start_offset or 0))
        remaining = max(0, int(budget_chars))
        out = full_text[start : start + remaining]
        end = start + len(out)

        cursor_after = payload.get("cursor_after")
        if end < len(full_text):
            cursor_next = {
                **_cursor_payload_base(item_id=item_id, kind="artifact"),
                "artifact_id": str(artifact.id),
                "char_offset": int(end),
            }
            cursor_str = _sign_agentic_read_cursor_v2(cursor_next)
            return out, {"cursor": cursor_str}, False, {"title": title_override, "type": type_override}

        if isinstance(cursor_after, str) and cursor_after.strip():
            # Once the artifact stream is consumed, resume the original knowledge cursor (if any).
            return out, {"cursor": cursor_after.strip()}, False, {"title": title_override, "type": type_override}

        return out, None, True, {"title": title_override, "type": type_override}

    contents: list[dict[str, object]] = []
    read: list[dict[str, object]] = []
    deferred: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []

    successfully_read_ids: set[str] = set()
    table_anchor_manifest_lookups = 0
    table_anchor_manifest_hits = 0
    table_anchor_fallback_reads = 0

    remaining_chars = max(0, int(max_chars))
    total_chars = 0

    for idx, entry in enumerate(ordered_items):
        item_id = str(entry.get("id") or "").strip()
        cursor_in = entry.get("cursor")
        row_start: int | None = None
        row_limit: int | None = None
        row_start_raw = entry.get("row_start")
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start = None
        row_limit_raw = entry.get("row_limit")
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit = None
        if row_limit is not None:
            row_limit = max(1, min(200, int(row_limit)))
        cursor_in_resolved = _resolve_cursor_from_handle(cursor_in if isinstance(cursor_in, str) else None)
        effective_remaining_chars = max(0, int(remaining_chars) + int(overflow_remaining))
        if effective_remaining_chars < 200:
            deferred.append(
                {
                    "id": item_id,
                    "reason": "not_enough_remaining_chars",
                    "hint": "Not enough remaining chars in this call; retry with a higher max_chars or fewer items.",
                }
            )
            continue

        # Greedy packing by priority: let higher-ranked refs consume the remaining
        # budget instead of forcing an equal-share slice that can starve the first
        # ref and create avoidable follow-up reads.
        per_item_budget = max(200, effective_remaining_chars)

        cursor_payload: dict[str, object] | None = None
        if isinstance(cursor_in_resolved, str) and cursor_in_resolved.strip():
            cursor_payload, cursor_error = _decode_cursor(item_id, cursor_in_resolved)
            if cursor_error:
                errors.append(cursor_error)
                read.append({"id": item_id, "status": "error"})
                continue

        chunk_record, upload_record, table_record, row_record, resolve_error = _resolve_target(item_id)
        if resolve_error:
            errors.append(resolve_error)
            read.append({"id": item_id, "status": "error"})
            continue

        upload = (
            upload_record
            or getattr(chunk_record, "upload", None)
            or getattr(table_record, "upload", None)
            or getattr(getattr(row_record, "table", None), "upload", None)
        )
        upload_id = str(getattr(upload, "id", "") or "")
        if not upload_id:
            errors.append({"id": item_id, "error_code": "not_found", "hint": "Upload not found."})
            read.append({"id": item_id, "status": "error"})
            continue

        access_error = _enforce_access(upload_id, item_id=item_id)
        if access_error:
            errors.append(access_error)
            read.append({"id": item_id, "status": "error"})
            continue

        # Block dataset/spreadsheet reads through read_knowledge (not supported in agentic KB tools).
        if _is_dataset_upload(upload) and (chunk_record is None or bool((chunk_record.metadata or {}).get("is_table_chunk"))):
            errors.append(
                {
                    "id": item_id,
                    "error_code": "wrong_tool_for_table",
                    "hint": "This item is a structured dataset/spreadsheet table and is not supported by this tool.",
                }
            )
            read.append({"id": item_id, "status": "error"})
            continue

        # Track for follow-up context.
        try:
            context.track_document_read(upload_id, title=_upload_title(upload))
        except Exception:
            pass

        payload: dict[str, object] = {"type": "text", "text": ""}
        payload_type = "text"
        evidence_kind = "text_excerpt"
        title = _upload_title(upload)
        cursor_used = (
            cursor_in_resolved
            if isinstance(cursor_in_resolved, str) and cursor_in_resolved.strip()
            else None
        )
        next_cursor: str | None = None
        complete = True
        evidence_coverage_hint: dict[str, object] | None = None

        # Strategy selection (AUTO):
        if cursor_payload:
            kind = str(cursor_payload.get("kind") or "")
            prepend_sep = bool(cursor_payload.get("prepend_sep"))
            if kind == "section_span":
                start_page_number = int(cursor_payload.get("start_page_number") or 1)
                start_block_order = int(cursor_payload.get("start_block_order") or 0)
                end_page_number = int(cursor_payload.get("end_page_number") or start_page_number)
                end_block_order = int(cursor_payload.get("end_block_order") or start_block_order)
                current_page_number = int(cursor_payload.get("current_page_number") or start_page_number)
                current_block_order = int(cursor_payload.get("current_block_order") or start_block_order)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_section_span_segment(
                    item_id=item_id,
                    upload_id=upload_id,
                    start_page_number=start_page_number,
                    start_block_order=start_block_order,
                    end_page_number=end_page_number,
                    end_block_order=end_block_order,
                    current_page_number=current_page_number,
                    current_block_order=current_block_order,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "page_blocks":
                page_number = int(cursor_payload.get("page_number") or 1)
                block_order = int(cursor_payload.get("block_order") or 0)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_page_blocks_segment(
                    item_id=item_id,
                    upload=upload,  # type: ignore[arg-type]
                    upload_id=upload_id,
                    page_number=page_number,
                    start_order=block_order,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "table_rows":
                payload_type = "table"
                evidence_kind = "table_rows"
                table_id = str(cursor_payload.get("table_id") or "").strip()
                raw_row_index = cursor_payload.get("row_index")
                try:
                    start_row_index = int(raw_row_index) if raw_row_index is not None else 0
                except (TypeError, ValueError):
                    start_row_index = 0
                effective_start_row = row_start if row_start is not None else start_row_index
                table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
                    item_id=item_id,
                    upload_id=upload_id,
                    table_id=table_id,
                    start_row_index=effective_start_row,
                    budget_chars=per_item_budget,
                    max_rows=row_limit,
                    business_profile=business,
                    selection_mode="row_range",
                )
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif kind == "chunk_window":
                start_index = int(cursor_payload.get("chunk_start") or 0)
                end_index = int(cursor_payload.get("chunk_end") or start_index)
                chunk_index = int(cursor_payload.get("chunk_index") or start_index)
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete = _read_chunk_window_segment(
                    item_id=item_id,
                    upload_id=upload_id,
                    start_index=start_index,
                    end_index=end_index,
                    current_index=chunk_index,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                    prepend_sep=prepend_sep,
                    business_profile=business,
                )
                payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            elif kind == "artifact":
                artifact_id = str(cursor_payload.get("artifact_id") or "").strip()
                char_offset = int(cursor_payload.get("char_offset") or 0)
                content_text, cursor_out, complete, artifact_meta = _read_artifact_segment(
                    item_id=item_id,
                    artifact_id=artifact_id,
                    start_offset=char_offset,
                    budget_chars=per_item_budget,
                )
                if artifact_meta and artifact_meta.get("error_code"):
                    errors.append(dict(artifact_meta))
                    read.append({"id": item_id, "status": "error"})
                    continue
                if artifact_meta:
                    title_override = artifact_meta.get("title")
                    if isinstance(title_override, str) and title_override.strip():
                        title = title_override.strip()
                    type_override = artifact_meta.get("type")
                    if isinstance(type_override, str) and type_override.strip():
                        payload_type = type_override.strip()
                        evidence_kind = "table_rows" if payload_type == "table" else "text_excerpt"
                if payload_type == "table":
                    # Artifact segments currently stream text; tables should not route here.
                    payload = {"type": "table", "columns": [], "rows": []}
                else:
                    payload["text"] = content_text
                next_cursor = cursor_out.get("cursor") if cursor_out else None
            else:
                errors.append({"id": item_id, "error_code": "invalid_cursor", "hint": "Unknown cursor kind."})
                read.append({"id": item_id, "status": "error"})
                continue
        else:
            # New read: decide best source.
            chunk_meta = chunk_record.metadata if chunk_record and isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
            is_table_chunk = bool(chunk_meta.get("is_table_chunk") or chunk_meta.get("table_chunk_role"))
            table_role = str(chunk_meta.get("table_chunk_role") or "").strip().lower()
            table_id = str(chunk_meta.get("table_id") or "").strip()

            if row_record is not None:
                payload_type = "table"
                evidence_kind = "table_rows"
                table_obj = getattr(row_record, "table", None)
                table_id = str(getattr(table_obj, "id", "") or "").strip()
                row_index_raw = getattr(row_record, "row_index", 0)
                try:
                    row_index = int(row_index_raw) if row_index_raw is not None else 0
                except (TypeError, ValueError):
                    row_index = 0

                table_title = str(getattr(table_obj, "title", "") or "").strip() or str(
                    getattr(table_obj, "section_heading", "") or ""
                ).strip()
                if not table_title:
                    order_index = getattr(table_obj, "order_index", None)
                    table_title = f"Table {order_index}" if order_index else "Table"
                title = table_title

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                    table_payload["upgraded_from_ref"] = "table_row"
                    payload = table_payload
                else:
                    table_payload, complete = _read_exact_table_row(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        db_row_index=row_index,
                        budget_chars=per_item_budget,
                        business_profile=business,
                    )
                    payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif table_record is not None:
                payload_type = "table"
                evidence_kind = "table_rows"
                table_id = str(getattr(table_record, "id", "") or "").strip()
                table_title = str(getattr(table_record, "title", "") or "").strip() or str(getattr(table_record, "section_heading", "") or "").strip()
                if not table_title:
                    order_index = getattr(table_record, "order_index", None)
                    table_title = f"Table {order_index}" if order_index else "Table"
                title = table_title

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                else:
                    table_anchor_manifest_lookups += 1
                    table_payload, _cursor_out, complete, anchor_used, fallback_used = _read_tabular_rows_with_anchor(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=0,
                        budget_chars=per_item_budget,
                        max_rows=None,
                        business_profile=business,
                        use_anchor=True,
                    )
                    if anchor_used:
                        table_anchor_manifest_hits += 1
                    if fallback_used:
                        table_anchor_fallback_reads += 1
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif upload_record is not None and chunk_record is None:
                page_number = 1
                has_page_blocks = False
                try:
                    from apps.knowledge.models import KnowledgeUploadPageBlock
                    has_page_blocks = KnowledgeUploadPageBlock.objects.filter(
                        upload_id=upload_id,
                        page__page_number=page_number,
                    ).exclude(text="").exists()
                except Exception:
                    has_page_blocks = False

                if has_page_blocks:
                    content_text, cursor_out, complete = _read_page_blocks_segment(
                        item_id=item_id,
                        upload=upload,  # type: ignore[arg-type]
                        upload_id=upload_id,
                        page_number=page_number,
                        start_order=0,
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                else:
                    errors.append(
                        {
                            "id": item_id,
                            "error_code": "not_found",
                            "hint": "No readable content found for that id.",
                        }
                    )
                    read.append({"id": item_id, "status": "error"})
                    continue
            elif mode != "excerpt" and is_table_chunk and table_id and (mode == "table_rows" or table_role not in {"row"}):
                payload_type = "table"
                evidence_kind = "table_rows"
                # Resolve table title for friendlier output.
                try:
                    table_obj = (
                        KnowledgeUploadTable.objects.filter(id=table_id)
                        .only("id", "title", "section_heading", "order_index")
                        .first()
                    )
                    if table_obj:
                        table_title = (table_obj.title or table_obj.section_heading or "").strip()
                        if not table_title:
                            table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                        title = table_title
                except Exception:
                    pass

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                else:
                    table_anchor_manifest_lookups += 1
                    table_payload, _cursor_out, complete, anchor_used, fallback_used = _read_tabular_rows_with_anchor(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=0,
                        budget_chars=per_item_budget,
                        max_rows=None,
                        business_profile=business,
                        use_anchor=True,
                    )
                    if anchor_used:
                        table_anchor_manifest_hits += 1
                    if fallback_used:
                        table_anchor_fallback_reads += 1
                payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            elif mode != "excerpt" and is_table_chunk and table_id and table_role == "row":
                payload_type = "table"
                evidence_kind = "table_rows"
                # Resolve table title for friendlier output (row-chunk reads should still
                # look like "Table X", not just the upload title).
                try:
                    table_obj = (
                        KnowledgeUploadTable.objects.filter(id=table_id)
                        .only("id", "title", "section_heading", "order_index")
                        .first()
                    )
                    if table_obj:
                        table_title = (table_obj.title or table_obj.section_heading or "").strip()
                        if not table_title:
                            table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                        title = table_title
                except Exception:
                    pass

                if row_start is not None or row_limit is not None:
                    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=int(row_start or 0),
                        budget_chars=per_item_budget,
                        max_rows=row_limit,
                        business_profile=business,
                        selection_mode="row_range",
                    )
                    table_payload["upgraded_from_ref"] = "table_row"
                    payload = table_payload
                else:
                    row_index_raw = chunk_meta.get("table_row_index")
                    try:
                        row_index_value = int(row_index_raw) if row_index_raw is not None else 0
                    except (TypeError, ValueError):
                        row_index_value = 0

                    table_payload, complete = _read_exact_table_row(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        db_row_index=row_index_value,
                        budget_chars=per_item_budget,
                        business_profile=business,
                    )
                    payload = table_payload
                # Tables page via row_start/row_limit (no cursors).
                next_cursor = None
            else:
                # Prefer page blocks when a page can be resolved; fall back to a chunk window.
                page_number = 1
                if chunk_record:
                    meta_page = chunk_meta.get("table_page_number") or chunk_meta.get("chunk_page") or chunk_meta.get("page_number")
                    if meta_page:
                        try:
                            parsed_page = int(meta_page)
                            if parsed_page >= 1:
                                page_number = parsed_page
                        except (TypeError, ValueError):
                            pass

                section_span = _resolve_text_section_span(
                    upload_id=upload_id,
                    chunk_record=chunk_record,
                    chunk_meta=chunk_meta,
                    fallback_page_number=page_number,
                )
                if section_span is not None:
                    content_text, cursor_out, complete = _read_section_span_segment(
                        item_id=item_id,
                        upload_id=upload_id,
                        start_page_number=int(section_span["start_page_number"]),
                        start_block_order=int(section_span["start_block_order"]),
                        end_page_number=int(section_span["end_page_number"]),
                        end_block_order=int(section_span["end_block_order"]),
                        current_page_number=int(section_span["start_page_number"]),
                        current_block_order=int(section_span["start_block_order"]),
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                else:
                    has_page_blocks = False
                    try:
                        from apps.knowledge.models import KnowledgeUploadPageBlock

                        has_page_blocks = KnowledgeUploadPageBlock.objects.filter(
                            upload_id=upload_id,
                            page__page_number=page_number,
                        ).exclude(text="").exists()
                    except Exception:
                        has_page_blocks = False

                # If page blocks exist for the resolved page, use them.
                if section_span is None and has_page_blocks:
                    content_text, cursor_out, complete = _read_page_blocks_segment(
                        item_id=item_id,
                        upload=upload,  # type: ignore[arg-type]
                        upload_id=upload_id,
                        page_number=page_number,
                        start_order=0,
                        start_offset=0,
                        budget_chars=per_item_budget,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                elif section_span is None and chunk_record and chunk_record.chunk_index is not None:
                    neighbor = 1
                    start_index = max(0, int(chunk_record.chunk_index) - neighbor)
                    end_index = int(chunk_record.chunk_index) + neighbor
                    content_text, cursor_out, complete = _read_chunk_window_segment(
                        item_id=item_id,
                        upload_id=upload_id,
                        start_index=start_index,
                        end_index=end_index,
                        current_index=start_index,
                        start_offset=0,
                        budget_chars=per_item_budget,
                        business_profile=business,
                    )
                    payload["text"] = content_text
                    next_cursor = cursor_out.get("cursor") if cursor_out else None
                elif section_span is None:
                    errors.append({"id": item_id, "error_code": "not_found", "hint": "No readable content found for that id."})
                    read.append({"id": item_id, "status": "error"})
                    continue

        # If we couldn't read anything for this item, mark as deferred rather than returning empty content.
        has_payload = False
        if payload_type == "table":
            rows_value = payload.get("rows")
            has_payload = isinstance(rows_value, list) and bool(rows_value)
        else:
            text_value = payload.get("text")
            has_payload = isinstance(text_value, str) and bool(text_value)
        if not has_payload:
            if not complete:
                # Budget was too small to fit even one row/block — data exists but didn't fit.
                deferred.append(
                    {
                        "id": item_id,
                        "reason": "budget_too_small",
                        "hint": "Budget too small to fit content. Retry with a higher max_chars (at least the suggested_max_chars from search).",
                    }
                )
            else:
                deferred.append(
                    {
                        "id": item_id,
                        "reason": "empty",
                        "hint": "No readable content was available for this item.",
                    }
                )
            read.append({"id": item_id, "status": "deferred"})
            continue

        successfully_read_ids.add(item_id)

        try:
            if payload_type == "table":
                item_chars = len(json.dumps(payload, ensure_ascii=False, default=str))
            else:
                item_chars = len(str(payload.get("text") or ""))
        except Exception:
            item_chars = 0

        overflow_used = max(0, item_chars - max(0, int(remaining_chars)))
        if overflow_used:
            overflow_remaining = max(0, int(overflow_remaining) - int(overflow_used))
        remaining_chars = max(0, remaining_chars - item_chars)
        total_chars += item_chars

        table_more_rows_available = bool(
            payload_type == "table"
            and isinstance(payload.get("next_row_start"), (int, float))
            and not bool(next_cursor)
        )

        evidence_entry: dict[str, object] = {
            "id": item_id,
            "document_id": upload_id,
            "title": title,
            "type": payload_type,
            "kind": evidence_kind,
            "payload": payload,
            "chars": item_chars,
            "complete": bool(complete and not next_cursor),
            # Back-compat: orchestrator coverage ledger expects "truncated" on items.
            # For paged table reads, "more rows available" is not the same as a risky
            # truncation; keep completion false but avoid overstating truncation.
            "truncated": bool((not complete or bool(next_cursor)) and not table_more_rows_available),
        }
        if table_more_rows_available:
            evidence_entry["more_rows_available"] = True
        if isinstance(evidence_coverage_hint, Mapping) and evidence_coverage_hint:
            evidence_entry["coverage_hint"] = dict(evidence_coverage_hint)
        if cursor_used:
            evidence_entry["cursor_used"] = cursor_used
        next_cursor_handle = _store_cursor_handle(next_cursor) if next_cursor else None
        if next_cursor_handle:
            evidence_entry["next_cursor"] = next_cursor_handle
        contents.append(evidence_entry)
        context.add_read_evidence(evidence_entry)

        is_truncated = not complete or bool(next_cursor_handle)
        read_entry: dict[str, object] = {
            "id": item_id,
            "status": "full" if not is_truncated else ("more_available" if table_more_rows_available else "truncated"),
            "chars": item_chars,
        }
        if is_truncated:
            # Build informative hint so the LLM can decide whether to
            # follow up.  Include row labels already fetched so it can
            # judge if the answer is already complete.
            hint_parts: list[str] = []
            if payload_type == "table":
                next_row_start = payload.get("next_row_start")
                if isinstance(next_row_start, (int, float)):
                    if table_more_rows_available:
                        hint_parts.append(f"More rows available with row_start={int(next_row_start)} if needed.")
                    else:
                        hint_parts.append(f"Continue with row_start={int(next_row_start)}.")
                hint_parts.append(f"Max chars allowed: {int(max_chars_allowed)}.")
            else:
                hint_parts.append(
                    f"Only use next_cursor if you still need data not yet returned. "
                    f"Max chars allowed: {int(max_chars_allowed)}."
                )
            read_entry["hint"] = " ".join(hint_parts)
        read.append(read_entry)
        continue

    # ---------------------------------------------------------------------
    # Phase 4: Artifact fallback when the *final* tool payload would exceed the
    # prompt tool-output cap. This prevents orchestrator-side truncation and
    # enables deterministic paging via `kind=artifact` cursors.
    # ---------------------------------------------------------------------

    try:
        artifact_retention_days = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_RETENTION_DAYS", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS", 30)
            or 30
        )
    except (TypeError, ValueError):
        artifact_retention_days = 30
    artifact_retention_days = max(1, min(365, artifact_retention_days))
    try:
        artifact_max_per_conversation = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_MAX_PER_CONVERSATION", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION", 200)
            or 200
        )
    except (TypeError, ValueError):
        artifact_max_per_conversation = 200
    artifact_max_per_conversation = max(0, min(5000, artifact_max_per_conversation))

    output_limit = max(0, int(prompt_output_limit))

    PROMPT_VIEW_INLINE_MIN_CHARS = 200
    PROMPT_VIEW_INLINE_MAX_CHARS = 1600
    if output_limit:
        # Reserve room for JSON framing + cursors + budget metadata when the prompt cap is small.
        PROMPT_VIEW_INLINE_MAX_CHARS = min(PROMPT_VIEW_INLINE_MAX_CHARS, max(PROMPT_VIEW_INLINE_MIN_CHARS, output_limit // 2))

    def _response_status() -> str:
        if errors or deferred or any(str(entry.get("status") or "") in {"truncated", "partial", "artifact"} for entry in read):
            return "truncated" if contents else "error"
        return "ok"

    def _compact_read_summaries_for_prompt(entries: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        """
        Keep read summaries lean for prompt injection.

        We preserve control-flow entries (truncated/error/deferred/covered/artifact/etc.)
        and only keep "full" entries when they carry actionable metadata (hint/cursor/artifact).
        """

        compact_entries: list[dict[str, object]] = []
        for entry in entries:
            status_raw = str(entry.get("status") or "").strip()
            status_key = status_raw.lower()
            has_hint = isinstance(entry.get("hint"), str) and bool(str(entry.get("hint") or "").strip())
            has_cursor = isinstance(entry.get("next_cursor"), str) and bool(str(entry.get("next_cursor") or "").strip())
            has_artifact = isinstance(entry.get("artifact_id"), str) and bool(str(entry.get("artifact_id") or "").strip())
            keep_entry = status_key != "full" or has_hint or has_cursor or has_artifact
            if not keep_entry:
                continue

            out: dict[str, object] = {}
            item_id = entry.get("id")
            if isinstance(item_id, str) and item_id.strip():
                out["id"] = item_id.strip()
            elif item_id is not None:
                out["id"] = item_id

            if status_raw:
                out["status"] = status_raw

            chars_value = entry.get("chars")
            if isinstance(chars_value, bool):
                chars_value = None
            if isinstance(chars_value, (int, float)):
                out["chars"] = int(chars_value)
            elif isinstance(chars_value, str) and chars_value.strip():
                try:
                    out["chars"] = int(chars_value.strip())
                except (TypeError, ValueError):
                    pass

            error_code = entry.get("error_code")
            if isinstance(error_code, str) and error_code.strip():
                out["error_code"] = error_code.strip()

            if has_artifact:
                out["artifact_id"] = str(entry.get("artifact_id")).strip()
            if has_cursor:
                out["next_cursor"] = str(entry.get("next_cursor")).strip()
            if has_hint:
                out["hint"] = str(entry.get("hint")).strip()

            if out:
                compact_entries.append(out)

        return compact_entries

    def _build_response(*, total_chars_value: int, hint: str | None = None) -> dict[str, object]:
        read_out = _compact_read_summaries_for_prompt(read)
        payload: dict[str, object] = {
            "tool": "read_knowledge",
            "status": _response_status(),
            "evidence": contents,
            "deferred": deferred,
            "max_chars": int(max_chars),
            "max_chars_allowed": int(max_chars_allowed),
            "total_chars": int(total_chars_value),
            "budget": context.budget_snapshot(),
        }
        if read_out:
            payload["read"] = read_out
        if errors:
            payload["errors"] = errors
        if hint:
            payload["hint"] = hint
        elif not contents and (deferred or errors):
            payload["hint"] = (
                "No content could be read. Ensure ids/cursors come from tool results, "
                "increase max_chars (up to max_chars_allowed), or retry with fewer items."
            )
        return payload

    def _payload_len_with_budget(payload: Mapping[str, object]) -> int:
        try:
            return len(json.dumps(dict(payload), ensure_ascii=False, default=str))
        except Exception:
            return 0

    def _attach_artifact_for_item(item: dict[str, object], trace: dict[str, object]) -> bool:
        item_id = str(item.get("id") or "").strip()
        payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
        if str(payload_obj.get("type") or "") != "text":
            return False
        full_text = payload_obj.get("text")
        if not item_id or not isinstance(full_text, str) or not full_text:
            return False
        if isinstance(item.get("artifact_id"), str) and str(item.get("artifact_id") or "").strip():
            return False

        preview_len = min(len(full_text), PROMPT_VIEW_INLINE_MAX_CHARS)
        if len(full_text) >= PROMPT_VIEW_INLINE_MIN_CHARS:
            preview_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, preview_len)
        preview_text = full_text[:preview_len]
        cursor_after_raw = item.get("next_cursor")
        cursor_after = _resolve_cursor_from_handle(cursor_after_raw if isinstance(cursor_after_raw, str) else None)
        cursor_used_local = item.get("cursor_used")
        if isinstance(cursor_used_local, str) and cursor_used_local.strip():
            # Avoid nested artifacts: if this segment was already read from an artifact cursor,
            # we can always clip + continue with that cursor instead of creating a new artifact.
            try:
                used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
            except ValueError:
                used_payload = None
            if isinstance(used_payload, dict) and str(used_payload.get("kind") or "") == "artifact":
                return False

        artifact_payload: dict[str, object] = {
            "kind": "agentic_read_v2",
            "item_id": item_id,
            "title": item.get("title"),
            "type": item.get("type"),
            "text": full_text,
        }
        if isinstance(cursor_after, str) and cursor_after.strip():
            artifact_payload["cursor_after"] = cursor_after.strip()
        if isinstance(cursor_used_local, str) and cursor_used_local.strip():
            artifact_payload["cursor_used"] = cursor_used_local.strip()

        artifact_id = store_local_tool_output_artifact(
            conversation=conversation,
            invoked_tool="read_knowledge",
            request={"refs": [{"id": item_id, **({"cursor": cursor_used_local} if cursor_used_local else {})}]},
            response=artifact_payload,
            status="ok",
            is_error=False,
            retention_days=artifact_retention_days,
            max_per_conversation=artifact_max_per_conversation,
        )
        if not artifact_id:
            return False

        cursor_next = {
            **_cursor_payload_base(item_id=item_id, kind="artifact"),
            "artifact_id": artifact_id,
            "char_offset": int(preview_len),
        }
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next)

        item["artifact_id"] = artifact_id
        payload_obj = dict(payload_obj)
        payload_obj["text"] = preview_text
        item["payload"] = payload_obj
        item["chars"] = len(preview_text)
        item["next_cursor"] = _store_cursor_handle(cursor_str) or cursor_str
        item["complete"] = False
        item["truncated"] = True

        trace["status"] = "artifact"
        trace["chars"] = len(full_text)
        trace["artifact_id"] = artifact_id
        return True

    # Start with the raw v2 output; if it exceeds the prompt cap once budgets are added,
    # convert the largest content entries to artifacts until it fits.
    total_chars_final = int(total_chars)
    response_hint: str | None = None
    response_candidate = _build_response(total_chars_value=total_chars_final)
    if output_limit and _payload_len_with_budget(response_candidate) > output_limit:
        read_by_id: dict[str, dict[str, object]] = {}
        for entry in read:
            if isinstance(entry, Mapping) and entry.get("id"):
                read_by_id[str(entry.get("id"))] = entry  # type: ignore[assignment]

        # Convert biggest items first (best shrink per artifact).
        contents_sorted = sorted(
            (item for item in contents if isinstance(item, Mapping)),
            key=lambda item: int(item.get("chars") or 0),
            reverse=True,
        )
        converted_any = False
        for item in contents_sorted:
            item_id = str(item.get("id") or "").strip()
            trace = read_by_id.get(item_id)
            if not trace or not isinstance(item, dict):
                continue
            if not _attach_artifact_for_item(item, trace):
                continue
            converted_any = True
            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = _build_response(total_chars_value=total_chars_final)
            if _payload_len_with_budget(response_candidate) <= output_limit:
                break

        if converted_any:
            response_hint = (
                "Some content was stored as an artifact to fit prompt limits. "
                "Use next_cursor to continue reading until complete."
            )

    response_candidate = _build_response(total_chars_value=total_chars_final, hint=response_hint)
    if output_limit and response_hint and _payload_len_with_budget(response_candidate) > output_limit:
        # Hints are nice-to-have; when the prompt cap is very small, prefer staying under
        # the hard tool-output limit over including extra explanatory text.
        response_hint = None
        response_candidate = _build_response(total_chars_value=total_chars_final)

    # If we're still above the prompt cap (e.g., cursor/budget overhead), clip artifact previews
    # until the JSON payload fits. This doesn't lose evidence because the full text is stored
    # in the artifact and `next_cursor` continues deterministically.
    if output_limit:
        guard_loops = 0
        payload_chars = _payload_len_with_budget(response_candidate)
        while payload_chars > output_limit and guard_loops < 20:
            guard_loops += 1
            artifact_streams: list[tuple[dict[str, object], str, int]] = []
            for item in contents:
                if not isinstance(item, dict):
                    continue
                payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                if str(payload_obj.get("type") or "") != "text" or not isinstance(payload_obj.get("text"), str):
                    continue
                artifact_id_direct = item.get("artifact_id")
                if isinstance(artifact_id_direct, str) and artifact_id_direct.strip():
                    artifact_streams.append((item, artifact_id_direct.strip(), 0))
                    continue
                cursor_used_local = item.get("cursor_used")
                if not (isinstance(cursor_used_local, str) and cursor_used_local.strip()):
                    continue
                try:
                    used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
                except ValueError:
                    continue
                if str(used_payload.get("kind") or "") != "artifact":
                    continue
                artifact_id = str(used_payload.get("artifact_id") or "").strip()
                if not artifact_id:
                    continue
                try:
                    base_offset = int(used_payload.get("char_offset") or 0)
                except (TypeError, ValueError):
                    base_offset = 0
                artifact_streams.append((item, artifact_id, max(0, base_offset)))

            if not artifact_streams:
                break
            target, target_artifact_id, base_offset = max(
                artifact_streams,
                key=lambda it: len(str(((it[0].get("payload") or {}) if isinstance(it[0].get("payload"), Mapping) else {}).get("text") or "")),
            )
            current_payload = target.get("payload") if isinstance(target.get("payload"), Mapping) else {}
            current_text = current_payload.get("text")
            if not isinstance(current_text, str) or len(current_text) <= PROMPT_VIEW_INLINE_MIN_CHARS:
                break

            excess = max(1, payload_chars - output_limit)
            new_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, len(current_text) - excess - 25)
            if new_len >= len(current_text):
                new_len = max(PROMPT_VIEW_INLINE_MIN_CHARS, len(current_text) - 25)
            clipped_text = current_text[:new_len]
            current_payload = dict(current_payload)
            current_payload["text"] = clipped_text
            target["payload"] = current_payload
            target["chars"] = len(clipped_text)

            item_id = str(target.get("id") or "").strip()
            if item_id and target_artifact_id:
                cursor_next = {
                    **_cursor_payload_base(item_id=item_id, kind="artifact"),
                    "artifact_id": target_artifact_id,
                    "char_offset": int(base_offset + len(clipped_text)),
                }
                cursor_signed = _sign_agentic_read_cursor_v2(cursor_next)
                target["next_cursor"] = _store_cursor_handle(cursor_signed) or cursor_signed
                target["complete"] = False
                target["truncated"] = True

            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = _build_response(total_chars_value=total_chars_final, hint=response_hint)
            payload_chars = _payload_len_with_budget(response_candidate)

    response = response_candidate

    try:
        artifact_items = sum(1 for entry in read if isinstance(entry, Mapping) and str(entry.get("status") or "") == "artifact")
        truncated_items = sum(1 for entry in read if isinstance(entry, Mapping) and str(entry.get("status") or "") in {"truncated", "partial"})
        response_chars = _payload_len_with_budget(response)
        structured_log(
            "mcp",
            "read_knowledge.agentic_v2",
            {
                "items": len(ordered_items),
                "contents": len(contents),
                "truncated_items": int(truncated_items),
                "artifact_items": int(artifact_items),
                "deferred": len(deferred),
                "errors": len(errors),
                "total_chars": int(total_chars_final),
                "max_chars": int(max_chars),
                "overflow_margin": int(overflow_margin),
                "overflow_used": int(max(0, int(overflow_margin) - int(overflow_remaining))),
                "output_chars": int(response_chars),
                "output_limit": int(output_limit or 0),
                "table_anchor_manifest_lookups": int(table_anchor_manifest_lookups),
                "table_anchor_manifest_hits": int(table_anchor_manifest_hits),
                "table_anchor_fallback_reads": int(table_anchor_fallback_reads),
            },
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
        )
    except Exception:
        # Logging must never break the tool boundary.
        pass

    try:
        context.reserve_characters(int(total_chars_final))
    except CharacterBudgetExceeded as exc:
        return {
            "tool": "read_knowledge",
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "evidence": [],
            "throttle_notice": {"type": "prompt_budget", "message": str(exc)},
            "hint": "Prompt budget exceeded. Ask a narrower question or request fewer items.",
        }

    # Record only refs that actually returned content for repeat-read detection.
    # Deferred/errored refs must remain retryable within the same turn.
    for ref_id in successfully_read_ids:
        context.read_ref_ids_this_turn.add(ref_id)

    return response


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


def _read_knowledge_agentic_wrapper(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Agentic read_knowledge contract.

    Supported interface:
      read_knowledge(refs=[{id,cursor?}...], max_chars=...)

    This wrapper rejects legacy knobs in agentic mode to keep the contract small and predictable.
    """

    def _has_value(key: str) -> bool:
        value = arguments.get(key)
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    # Accept both "refs" (new) and "items" (compat alias) to reduce brittleness.
    raw_refs = arguments.get("refs")
    if not isinstance(raw_refs, list):
        raw_refs = arguments.get("items")

    if not isinstance(raw_refs, list) or not raw_refs:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "missing_refs",
            "error_code": "missing_refs",
            "evidence": [],
            "hint": "refs[] is required (use ids/cursors from search_knowledge/read_knowledge).",
        }

    # Reject legacy parameters (besides UI-only metadata) to keep the contract tight.
    # NOTE: `mode` is deprecated (kept only as a no-op compat field). The backend
    # chooses the correct representation (tables -> table_rows, text -> excerpts)
    # and paging strategy.
    allowed = {"refs", "items", "max_chars", "mode", "__ui"}
    extra = [key for key in arguments.keys() if key not in allowed and _has_value(str(key))]
    if extra:
        return {
            "tool": "read_knowledge",
            "status": "constraint_error",
            "error": "unsupported_parameters",
            "error_code": "unsupported_parameters",
            "evidence": [],
            "unsupported_fields": extra,
            "hint": "Unsupported parameters for read_knowledge. Use only refs[] + max_chars (+ optional __ui).",
        }

    # Normalize "refs" into the internal "items" shape used by the read engine.
    items: list[dict[str, object]] = []
    seen_item_keys: set[tuple[str, str | None, int | None, int | None]] = set()
    unresolved_invalid_ids: list[str] = []
    unresolved_invalid_errors: list[dict[str, object]] = []
    for entry in raw_refs:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or entry.get("ref") or "").strip()
        if not item_id:
            continue
        cursor = entry.get("cursor")
        cursor_value = cursor.strip() if isinstance(cursor, str) and cursor.strip() else None
        row_start_raw = entry.get("row_start")
        if row_start_raw is None:
            row_start_raw = entry.get("start_row")
        row_limit_raw = entry.get("row_limit")
        if row_limit_raw is None:
            row_limit_raw = entry.get("max_rows")

        row_start_value: int | None = None
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start_value = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start_value = None

        row_limit_value: int | None = None
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit_value = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit_value = None
        if row_limit_value is not None:
            row_limit_value = max(1, min(200, int(row_limit_value)))
        if _is_uuid_ref_id(item_id):
            canonical_id = str(uuid.UUID(item_id))
            item_key = (canonical_id, cursor_value, row_start_value, row_limit_value)
            if item_key in seen_item_keys:
                continue
            seen_item_keys.add(item_key)
            out: dict[str, object] = {"id": canonical_id}
            if cursor_value:
                out["cursor"] = cursor_value
            if row_start_value is not None:
                out["row_start"] = row_start_value
            if row_limit_value is not None:
                out["row_limit"] = row_limit_value
            items.append(out)
            continue

        unresolved_invalid_ids.append(item_id)
        unresolved_invalid_errors.append(
            {
                "id": item_id,
                "error_code": "invalid_id",
                "hint": "id must be a valid UUID from search_knowledge results.",
            }
        )

    retry_tracker = getattr(context, "invalid_read_ref_attempts", None)
    if not isinstance(retry_tracker, dict):
        retry_tracker = {}
        context.invalid_read_ref_attempts = retry_tracker
    retry_limit = _scope_invalid_read_retry_limit()
    retry_limit_hit = False
    for unresolved_id in unresolved_invalid_ids:
        tracker_key = re.sub(r"\s+", " ", str(unresolved_id or "").strip().lower())[:160]
        if not tracker_key:
            continue
        attempt_count = int(retry_tracker.get(tracker_key, 0) or 0) + 1
        retry_tracker[tracker_key] = attempt_count
        if attempt_count >= retry_limit:
            retry_limit_hit = True

    if not items:
        if retry_limit_hit:
            structured_log(
                "mcp",
                "read_knowledge.invalid_refs",
                {
                    "stage": "retry_limit_blocked",
                    "invalid_ref_count": len(unresolved_invalid_ids),
                    "repaired_ref_count": 0,
                    "retry_limit": retry_limit,
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )
            return {
                "tool": "read_knowledge",
                "status": "blocked",
                "error": "invalid_ref_retry_limit",
                "error_code": "invalid_ref_retry_limit",
                "evidence": [],
                "errors": unresolved_invalid_errors,
                "budget": context.budget_snapshot(),
                "hint": (
                    "Repeated non-UUID refs were blocked to prevent retry loops. "
                    "Use UUID refs returned by search_knowledge/read_knowledge only."
                ),
            }
        structured_log(
            "mcp",
            "read_knowledge.invalid_refs",
            {
                "stage": "invalid_refs_rejected",
                "invalid_ref_count": len(unresolved_invalid_ids),
                "repaired_ref_count": 0,
            },
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "invalid_refs",
            "error_code": "invalid_refs",
            "evidence": [],
            "errors": unresolved_invalid_errors,
            "budget": context.budget_snapshot(),
            "hint": (
                "refs[] must contain UUID ids from search_knowledge results. "
                "If refs came from a category label, run search_knowledge first and use the returned UUID refs."
            ),
        }

    # Pass through to the agentic read engine.
    engine_args: dict[str, object] = {
        "items": items,
        "max_chars": arguments.get("max_chars"),
    }
    engine_result = _agentic_read_v2_handler(engine_args, conversation, context)
    if unresolved_invalid_errors:
        patched_result = dict(engine_result)
        existing_errors = patched_result.get("errors")
        merged_errors = (
            [dict(item) for item in existing_errors if isinstance(item, Mapping)]
            if isinstance(existing_errors, list)
            else []
        )
        merged_errors.extend(unresolved_invalid_errors)
        patched_result["errors"] = merged_errors
        hint_text = str(patched_result.get("hint") or "").strip()
        reminder = "Ignored non-UUID refs and continued with valid UUID refs."
        patched_result["hint"] = f"{hint_text} {reminder}".strip() if hint_text else reminder
        return patched_result
    return engine_result


def _read_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Public read_knowledge entrypoint.

    Enforces the refs-first agentic contract.
    """
    return _read_knowledge_agentic_wrapper(arguments, conversation, context)


# ---------------------------------------------------------------------------
# Backwards-compatible stub factory (for undefined tools)


def _require_provider_stub(tool_name: str, *, preflight: Callable[[ToolExecutionContext], None] | None = None) -> ToolHandler:
    """
    Produce a placeholder handler that raises a helpful error.

    This lets us wire up the dispatcher surface without accidentally invoking
    partially implemented logic.
    """

    def _handler(arguments: Mapping[str, object], conversation: Conversation, context: ToolExecutionContext) -> Mapping[str, object]:
        if preflight:
            preflight(context)
        del arguments, conversation
        raise NotImplementedError(f"MCP tool '{tool_name}' execution is pending.")

    return _handler


def _enforce_single_chunk_read(context: ToolExecutionContext) -> None:
    context.reserve_chunk_reads(1)



# Voice tools live in the voice app to keep this registry focused.
try:  # pragma: no cover - optional feature gate
    from apps.voice.mcp_tools import initiate_phone_call_tool as _initiate_phone_call_handler
except Exception:  # pragma: no cover - voice feature may be disabled in some deployments
    _initiate_phone_call_handler = None  # type: ignore[assignment]


_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "retrieve_earlier_context": _retrieve_earlier_context_handler,
    "mcp_search_tools": _mcp_search_tools_handler,
    "mcp_call_tool": _mcp_call_tool_handler,
    "request_user_input": _request_user_input_handler,
    "start_agent_run": _start_agent_run_handler,
    "list_agent_runs": _list_agent_runs_handler,
    "get_agent_run": _get_agent_run_handler,
    "continue_agent_run": _continue_agent_run_handler,
    "list_tasks": _list_tasks_handler,
    "draft_task": _draft_task_handler,
    "update_task": _update_task_handler,
    "request_task_activation": _request_task_activation_handler,
    "pause_task": _pause_task_handler,
    "search_memory": _search_memory_handler,
    "save_memory": _save_memory_handler,
    "forget_memory": _forget_memory_handler,
    "search_knowledge": _search_knowledge_handler,
    "search_conversation_files": _search_conversation_files_handler,
    "read_knowledge": _read_knowledge_handler,
    "read_conversation_file": _read_conversation_file_handler,
    "pdf_generate": _pdf_generate_handler,
    "pdf_merge": _pdf_merge_handler,
    "pdf_extract_pages": _pdf_extract_pages_handler,
    "pdf_extract_text": _pdf_extract_text_handler,
    "email_search": _email_search_handler,
    "email_get_message": _email_get_message_handler,
    "email_get_thread": _email_get_thread_handler,
    "email_create_draft": _email_create_draft_handler,
    "email_send_draft": _email_send_draft_handler,
    # Native integrations — Google Calendar
    "calendar_list_events": _calendar_list_events_handler,
    "calendar_get_event": _calendar_get_event_handler,
    "calendar_create_event": _calendar_create_event_handler,
    "calendar_update_event": _calendar_update_event_handler,
    # Native integrations — Google Drive
    "drive_search_files": _drive_search_files_handler,
    "drive_get_file": _drive_get_file_handler,
    "drive_list_files": _drive_list_files_handler,
    # Native integrations — OneDrive
    "onedrive_search_files": _onedrive_search_files_handler,
    "onedrive_get_file": _onedrive_get_file_handler,
    "onedrive_list_files": _onedrive_list_files_handler,
    # Native integrations — Slack
    "slack_list_channels": _slack_list_channels_handler,
    "slack_read_channel": _slack_read_channel_handler,
    "slack_send_message": _slack_send_message_handler,
    "slack_search_messages": _slack_search_messages_handler,
    # Native integrations — HubSpot
    "hubspot_search_contacts": _hubspot_search_contacts_handler,
    "hubspot_get_contact": _hubspot_get_contact_handler,
    "hubspot_create_contact": _hubspot_create_contact_handler,
    "hubspot_search_deals": _hubspot_search_deals_handler,
    **({"initiate_phone_call": _initiate_phone_call_handler} if _initiate_phone_call_handler else {}),
}
