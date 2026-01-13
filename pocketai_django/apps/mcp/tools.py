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
import hashlib
import json
import re
import threading
import time
import uuid
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from django.db import models
from django.db.models import Prefetch
from django.core.cache import cache
from django.conf import settings

from apps.accounts.models import (
    AgentProfile,
    KnowledgeAuditAction,
    KnowledgeAuditEvent,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    KnowledgeUploadTableCell,
)
from apps.conversations.models import Conversation
from apps.rag.ai_orchestrator import (
    ActionType,
    AiOrchestratorService,
    KnowledgeSearchService,
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
)
from apps.rag.dataset_router import find_datasets_for_identifier, match_upload_for_identifier
from apps.knowledge.knowledge_access import apply_customer_visible_chunks, apply_customer_visible_uploads
from apps.knowledge.privacy import column_suggests_pii, redact_free_text, redact_value_for_preview, sha256_hex
from apps.rag.rag_logging import structured_log
from apps.core.logging_utils import log_start, log_success, log_warning, log_performance, LogEmoji
from apps.rag.tabular_limits import ToolRateLimit, enforce_tool_rate_limit, resolve_tabular_tool_limits
from core.metrics import latency_monitor
from core.tenancy import tenant_context
from .identifier_registry import IdentifierGuardrail, IdentifierRegistryService
from .types import (
    ChunkPageBudgetExceeded,
    ChunkReadBudgetExceeded,
    SearchBudgetExceeded,
    ToolConstraintError,
    ToolExecutionContext,
    ToolRateLimitExceeded,
    CharacterBudgetExceeded,
)
from .schemas.agentic_rag import (
    SearchResultItem,
    SearchResponse,
    build_search_response,
    build_error_response as build_agentic_error_response,
)
from apps.accounts.feature_flags import FeatureFlagService

try:
    import duckdb  # type: ignore
except Exception:  # pragma: no cover
    duckdb = None  # type: ignore


logger = logging.getLogger(__name__)
IDENTIFIER_MAPPING_CACHE_TTL = 300
# Default to one query per user turn for latency predictability.
# Additional query variants (fanout) can be enabled via `MCP_SEARCH_MAX_QUERY_VARIANTS`.
DEFAULT_MAX_SEARCH_QUERY_VARIANTS = 1
MCP_LOG_PII_DEFAULT = False
MCP_LOG_SNIPPET_PREVIEWS_DEFAULT = False
MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT = False
MCP_TEXT_PII_REDACTION_DEFAULT = True
MCP_TEXT_PII_REDACTION_ALLOW_VERIFIED_DEFAULT = False


def _mcp_log_pii_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_PII", MCP_LOG_PII_DEFAULT))


def _mcp_log_snippet_previews_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_SNIPPET_PREVIEWS", MCP_LOG_SNIPPET_PREVIEWS_DEFAULT))


def _mcp_log_full_snippet_content_enabled() -> bool:
    return bool(getattr(settings, "MCP_LOG_FULL_SNIPPET_CONTENT", MCP_LOG_FULL_SNIPPET_CONTENT_DEFAULT))


def _text_pii_redaction_enabled() -> bool:
    return bool(getattr(settings, "MCP_TEXT_PII_REDACTION_ENABLED", MCP_TEXT_PII_REDACTION_DEFAULT))


def _text_pii_redaction_allow_verified() -> bool:
    return bool(getattr(settings, "MCP_TEXT_PII_REDACTION_ALLOW_VERIFIED", MCP_TEXT_PII_REDACTION_ALLOW_VERIFIED_DEFAULT))


def _should_redact_text_pii(conversation: Conversation) -> bool:
    """
    Decide whether to redact PII patterns from unstructured snippet text.

    Default is conservative: redact always (unstructured docs can contain many
    identities and we cannot safely scope them to a single verified subject yet).
    """

    if not _text_pii_redaction_enabled():
        return False
    if not _text_pii_redaction_allow_verified():
        return True
    policy = _verified_lookup_policy(conversation)
    if not bool(policy.get("enabled")):
        return True
    verified, _source = _conversation_is_verified_for_lookup(
        conversation,
        allow_customer_match=bool(policy.get("allow_customer_match")),
    )
    return not verified


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


def _function_schema(
    *,
    name: str,
    description: str,
    properties: Mapping[str, Mapping[str, object]],
    required: tuple[str, ...] = (),
) -> Mapping[str, object]:
    """Helper to keep tool definitions concise and consistent."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        },
    }


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


TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    _function_schema(
        name="search_knowledge",
        description="Search the knowledge base using a natural-language query.",
        properties={
            "query": {
                "type": "string",
                "description": "Visitor question or keywords to search for.",
            },
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of alias queries to batch with the primary query.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of snippets to return (1-8).",
                "minimum": 1,
                "maximum": 8,
                "default": 5,
            },
        },
        required=("query",),
    ),
    _function_schema(
        name="read_document",
        description=(
            "Read text or layout from a document (PDF, DOCX, TXT). "
            "In agentic mode prefer ids[] from search_knowledge; for page reads use document_id + pages."
        ),
        properties={
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of chunk IDs from search_knowledge results (agentic mode).",
                "minItems": 1,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum total characters to return across all ids (agentic mode).",
                "minimum": 500,
                "maximum": 20000,
                "default": 8000,
            },
            "document_id": {
                "type": "string",
                "description": "UUID of the document upload.",
            },
            "pages": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "List of 1-based page numbers to read.",
                "minItems": 1,
            },
            "page": {
                "type": "integer",
                "description": "Single 1-based page number to read (use when only one page is needed).",
                "minimum": 1,
            },
            "offset": {
                "type": "integer",
                "description": "0-based chunk offset hint used to resolve a nearby page when no page number is known.",
                "minimum": 0,
            },
            "mode": {
                "type": "string",
                "enum": ["excerpt", "full_page"],
                "description": "excerpt returns a window around the chunk; full_page returns the whole page text.",
                "default": "excerpt",
            },
            "neighbor_window": {
                "type": "integer",
                "description": "Number of neighbor chunks to include (0-3).",
                "minimum": 0,
                "maximum": 3,
                "default": 0,
            },
        },
        required=(),
    ),
    _function_schema(
        name="query_dataset",
        description="Query a structured dataset (CSV, Excel, JSONL) using SQL-like operations (filter, sort, aggregate).",
        properties={
            "dataset_id": {
                "type": "string",
                "description": "UUID of the dataset upload.",
            },
            "query": {
                "type": "string",
                "description": "Optional free-text search across cells.",
            },
            "filters": {
                "type": "array",
                "description": "Structured filters (ANDed).",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "op": {"type": "string", "enum": ["eq", "contains", "startswith", "endswith", "gt", "gte", "lt", "lte", "in"]},
                        "value": {"type": "string"},
                        "values": {"type": "array", "items": {"type": "string"}},
                        "case_sensitive": {"type": "boolean", "default": False},
                    },
                    "required": ["column", "op"],
                },
            },
            "select_columns": {
                "type": "array",
                "items": {"type": "string"},
            },
            "sort_by": {"type": "string"},
            "sort_direction": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
            "limit": {"type": "integer", "default": 20, "maximum": 50},
            "offset": {"type": "integer", "default": 0},
            "aggregate": {
                "type": "object",
                "properties": {
                    "operation": {"type": "string", "enum": ["count", "sum", "min", "max", "group_by"]},
                    "column": {"type": "string"},
                    "group_by": {"type": "string"},
                    "top_groups": {"type": "integer", "default": 20},
                },
                "required": ["operation"],
            },
            "mode": {
                "type": "string",
                "enum": ["rows", "row_total", "column_sum"],
                "default": "rows",
            },
            "value_column": {"type": "string"},
        },
        required=("dataset_id",),
    ),
    _function_schema(
        name="list_tables",
        description="List queryable dataset/spreadsheet uploads (CSV/XLSX/JSONL) so you can grab their document IDs before table queries.",
        properties={
            "query": {
                "type": "string",
                "description": "Optional filter for upload name, sheet name, or table title.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "Maximum number of uploads to return (1-10).",
            },
        },
    ),
    # read_knowledge REMOVED: Use read_document (text/PDFs) or query_dataset (tables/CSVs)
    # Legacy handler remains at _read_knowledge_handler() for backward compatibility
    _function_schema(
        name="create_case",
        description="Create a structured customer case with diagnosis and suggested actions.",
        properties={
            "title": {"type": "string", "description": "Short case title provided to internal teams."},
            "description": {"type": "string", "description": "Detailed summary of the issue."},
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Relative urgency.",
            },
            "ai_diagnosis": {"type": "string", "description": "What you believe is happening."},
            "ai_actions_taken": {"type": "string", "description": "What you already did for the visitor."},
            "ai_suggested_actions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Follow-up steps you recommend.",
            },
        },
        required=("title", "description", "priority", "ai_diagnosis", "ai_actions_taken"),
    ),
    _function_schema(
        name="update_case_status",
        description="Update the status of the currently linked case.",
        properties={
            "case_id": {"type": "string", "description": "UUID of the case to update."},
            "status": {
                "type": "string",
                "enum": ["open", "closed"],
                "description": "New lifecycle status (open/closed).",
            },
            "note": {"type": "string", "description": "Optional explanation surfaced to humans."},
        },
        required=("case_id", "status"),
    ),
    _function_schema(
        name="update_case_details",
        description="Revise the title/description/priority of an existing case when new facts arrive.",
        properties={
            "case_id": {"type": "string", "description": "UUID of the case to update."},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "priority": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "allow_description_overwrite": {
                "type": "boolean",
                "description": "Set true only when the prior description is now incorrect.",
                "default": False,
            },
        },
        required=("case_id",),
    ),
    _function_schema(
        name="add_case_history",
        description="Log a case history entry documenting progress or clarifications.",
        properties={
            "case_id": {"type": "string"},
            "summary": {"type": "string", "description": "What changed or what was confirmed."},
        },
        required=("case_id", "summary"),
    ),
    _function_schema(
        name="flag_escalation",
        description="Escalate a conversation for human follow-up.",
        properties={
            "reason": {"type": "string", "description": "Why the escalation is needed."},
            "details": {"type": "string", "description": "Context to hand off to the human team."},
        },
        required=("reason",),
    ),
    _function_schema(
        name="create_customer",
        description="Create or match a customer record when identifiers are provided.",
        properties={
            "full_name": {"type": "string"},
            "email": {"type": "string"},
            "phone": {"type": "string"},
            "metadata": {
                "type": "object",
                "description": "Optional extra context (company, notes, etc.).",
            },
        },
        required=("full_name",),
    ),
    _function_schema(
        name="update_customer",
        description="Update an existing customer profile when the visitor confirms a change.",
        properties={
            "customer_id": {"type": "string"},
            "full_name": {"type": "string"},
            "metadata": {"type": "object"},
        },
        required=("customer_id",),
    ),
    _function_schema(
        name="create_lead",
        description="Capture a sales lead discovered in chat.",
        properties={
            "title": {"type": "string"},
            "description": {"type": "string"},
            "source": {"type": "string"},
        },
        required=("title", "description"),
    ),
    _function_schema(
        name="create_appointment",
        description="Schedule or request an appointment for the visitor.",
        properties={
            "topic": {"type": "string"},
            "preferred_time": {"type": "string", "description": "ISO timestamp or natural language slot."},
            "notes": {"type": "string"},
        },
        required=("topic",),
    ),
    _function_schema(
        name="get_document_structure",
        description="Get the complete structure of a document including all tables, column headers, and row labels (item names). Use this AFTER search_knowledge when answering 'list all', 'show every', or comprehensive queries to discover ALL items in a document.",
        properties={
            "document_id": {
                "type": "string",
                "description": "UUID of the document upload (from search_knowledge snippets[].read_hint.document_id).",
            },
            "table_id": {
                "type": "string",
                "description": "Optional: Filter to a specific table by ID.",
            },
            "include_row_labels": {
                "type": "boolean",
                "description": "Include first-column values as item names/identifiers. Default: true.",
                "default": True,
            },
        },
        required=("document_id",),
    ),
)


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
    if normalized_name in {"table_aggregate", "dataset_query"}:
        return {
            "tool": normalized_name,
            "status": "error",
            "error": "deprecated_tool",
            "error_code": "deprecated_tool",
            "hint": "This tool is deprecated. Use query_dataset instead.",
        }

    handler = _TOOL_HANDLERS.get(normalized_name)
    if not handler:
        return {
            "tool": normalized_name or "unknown_tool",
            "status": "error",
            "error": "unsupported_tool",
            "error_code": "unsupported_tool",
            "hint": "Unsupported tool. Use search_knowledge, read_document, or list_tables.",
        }
    ctx = context or ToolExecutionContext()
    business_id = getattr(conversation, "business_profile_id", None)
    try:
        with tenant_context(business_id):
            return handler(arguments, conversation=conversation, context=ctx)
    except ToolConstraintError as exc:
        status = "constraint_error"
        error_code = "constraint_error"
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
        elif isinstance(exc, SearchBudgetExceeded):
            status = "throttled"
            error_code = "search_budget_exceeded"
        return {
            "tool": normalized_name,
            "status": status,
            "error": error_code,
            "error_code": error_code,
            "hint": str(exc) or "Tool constraint exceeded. Narrow the request and try again.",
        }
    except Exception:
        logger.exception(
            "mcp.tool_failed tool=%s business=%s conversation=%s",
            normalized_name,
            getattr(conversation, "business_profile_id", None),
            getattr(conversation, "id", None),
        )
        return {
            "tool": normalized_name,
            "status": "error",
            "error": "tool_failed",
            "error_code": "tool_failed",
            "hint": "Tool execution failed unexpectedly. Try a narrower request.",
        }


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


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)


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


def _identifier_guard(context: ToolExecutionContext, conversation: Conversation):
    guard = getattr(context, "identifier_gate", None)
    if isinstance(guard, IdentifierGuardrail):
        return guard
    try:
        guard = IdentifierGuardrail.from_conversation(conversation)
    except Exception:
        return None
    context.identifier_gate = guard
    return guard


def _record_identifier_check(context: ToolExecutionContext, decision) -> None:
    if decision is None:
        return
    payload = getattr(decision, "as_dict", lambda: None)()
    if payload is None:
        return
    if payload.get("status") == "ok" and not payload.get("required_keys") and not payload.get("blocked_uploads"):
        return
    context.identifier_checks.append(payload)
    context.identifier_filters.append(payload)
    if not context.identifier_hashes and isinstance(payload, dict):
        hashes = payload.get("provided_hashes")
        if isinstance(hashes, Mapping):
            context.identifier_hashes = dict(hashes)


def _identifier_mapping_cache_key(
    guard: IdentifierGuardrail | None,
    locked_key: str | None,
    locked_value: str | None,
) -> tuple[tuple[tuple[str, str], ...], str | None, str | None] | None:
    if not guard:
        return None
    provided = getattr(guard, "provided_identifiers", None)
    if not isinstance(provided, Mapping) or not provided:
        return None
    normalized_pairs: list[tuple[str, str]] = []
    for key, value in provided.items():
        text = str(value).strip()
        if not text:
            continue
        normalized_pairs.append((str(key), text))
    if not normalized_pairs:
        return None
    normalized_pairs.sort()
    return (tuple(normalized_pairs), locked_key, locked_value)


def _identifier_mapping_cache_token(
    cache_key: tuple[tuple[tuple[str, str], ...], str | None, str | None] | None,
    business_profile_id: object | None = None,
) -> str | None:
    if not cache_key:
        return None
    try:
        fingerprint = json.dumps(
            {"business_id": str(business_profile_id) if business_profile_id else None, "cache_key": cache_key},
            sort_keys=True,
            default=str,
        )
    except TypeError:
        return None
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
    return f"mcp:idmap:{digest}"


def _record_identifier_event_once(
    context: ToolExecutionContext,
    *,
    business_profile,
    decision,
    tool: str,
    conversation: Conversation,
    upload_ids: Sequence[str] | None,
) -> None:
    if not decision:
        return
    required = tuple(sorted(decision.required_keys or ()))
    provided = tuple(sorted(decision.provided_keys or ()))
    uploads = tuple(sorted(str(uid) for uid in (upload_ids or ()) if uid))
    fingerprint = (decision.status, required, provided, uploads)
    if fingerprint in context.identifier_event_fingerprints:
        return
    context.identifier_event_fingerprints.add(fingerprint)
    IdentifierRegistryService.record_event(
        business_profile=business_profile,
        decision=decision,
        tool=tool,
        conversation=conversation,
        upload_ids=list(uploads),
    )


@dataclass(frozen=True, slots=True)
class AgentKnowledgeScope:
    mode: str  # all|documents|collections|mixed
    explicit_upload_ids: frozenset[str] = frozenset()
    collection_ids: frozenset[str] = frozenset()

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
    has_collection_rules = agent.allowed_collections.filter(business_profile=conversation.business_profile).exists()
    if not (has_doc_rules or has_collection_rules):
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

    collection_ids: set[str] = set()
    if has_collection_rules:
        collection_ids.update(
            str(value)
            for value in agent.allowed_collections.filter(business_profile=conversation.business_profile).values_list("id", flat=True)
            if value
        )

    if has_doc_rules and has_collection_rules:
        mode = "mixed"
    elif has_collection_rules:
        mode = "collections"
    else:
        mode = "documents"

    scope = AgentKnowledgeScope(
        mode=mode,
        explicit_upload_ids=frozenset(explicit_upload_ids),
        collection_ids=frozenset(collection_ids),
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
    if str(upload_id) in scope.explicit_upload_ids:
        return True
    if not scope.collection_ids:
        return False
    return apply_customer_visible_uploads(
        KnowledgeUpload.objects.filter(
            business_profile=conversation.business_profile,
            status=KnowledgeStatus.ACTIVE,
            id=upload_id,
            collections__id__in=list(scope.collection_ids),
        )
    ).exists()


def _apply_agent_scope_to_upload_queryset(queryset, scope: AgentKnowledgeScope):
    if not scope.restricted:
        return queryset
    clauses: list[models.Q] = []
    if scope.explicit_upload_ids:
        clauses.append(models.Q(id__in=list(scope.explicit_upload_ids)))
    if scope.collection_ids:
        clauses.append(models.Q(collections__id__in=list(scope.collection_ids)))
    if not clauses:
        return queryset.none()
    combined = clauses[0]
    for clause in clauses[1:]:
        combined |= clause
    return queryset.filter(combined).distinct()


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

    Reuses the legacy orchestrator's serializer so ingestion warnings and
    downstream diagnostics behave consistently across MCP and non-MCP paths.
    """

    payloads: list[dict[str, object]] = []
    for snippet in snippets:
        try:
            payloads.append(AiOrchestratorService._serialize_snippet(snippet))  # type: ignore[arg-type]
        except Exception:
            continue
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, object]] = []
    for entry in payloads:
        chunk_id = str(entry.get("chunk_id") or "").strip()
        entry_id = str(entry.get("id") or "").strip()
        upload_id = str(entry.get("upload_id") or "").strip() or None
        identity = chunk_id or entry_id
        if not identity:
            deduped.append(entry)
            continue
        key = (identity, upload_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped[:8]


def _sanitize_snippet_payloads_for_prompt(
    snippet_payloads: Sequence[Mapping[str, object]],
    *,
    conversation: Conversation,
) -> list[dict[str, object]]:
    if not snippet_payloads:
        return []
    redact_text = _should_redact_text_pii(conversation)
    needs_copy = redact_text
    if not needs_copy:
        for payload in snippet_payloads:
            if isinstance(payload, Mapping) and "identifiers" in payload:
                needs_copy = True
                break
    if not needs_copy:
        return [dict(payload) for payload in snippet_payloads if isinstance(payload, Mapping)]

    redacted_keys = ("title", "public_label", "summary", "content", "truncation_note")
    redacted_list_keys = ("pageSummaries", "aliases", "topic_hints")

    sanitized: list[dict[str, object]] = []
    for payload in snippet_payloads:
        if not isinstance(payload, Mapping):
            continue
        out = dict(payload)
        # Never pass identifier mappings to the LLM; they may contain PII.
        out.pop("identifiers", None)
        if redact_text:
            for key in redacted_keys:
                value = out.get(key)
                if isinstance(value, str) and value:
                    out[key] = redact_free_text(value)
            for key in redacted_list_keys:
                value = out.get(key)
                if isinstance(value, list):
                    out[key] = [
                        redact_free_text(item) if isinstance(item, str) and item else item
                        for item in value
                    ]
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


def _tabular_privacy_enabled() -> bool:
    return bool(getattr(settings, "MCP_TABULAR_PRIVACY_ENABLED", True))


def _tabular_pii_redaction_enabled() -> bool:
    return bool(getattr(settings, "MCP_TABULAR_PII_REDACTION_ENABLED", True))


def _verified_lookup_policy(conversation: Conversation) -> dict[str, object]:
    """
    Resolve verified-lookup policy.

    Global defaults come from settings, but tenants can override via
    BusinessProfile.metadata["verified_lookup"] (or "verified_lookup_policy").
    """

    enabled = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_ENABLED", True))
    require_for_pii = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII", True))
    allow_customer_match = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH", True))

    business = getattr(conversation, "business_profile", None)
    meta = getattr(business, "metadata", None) if business else None
    if isinstance(meta, Mapping):
        cfg = meta.get("verified_lookup") or meta.get("verified_lookup_policy") or {}
        if isinstance(cfg, Mapping):
            if cfg.get("enabled") is not None:
                enabled = bool(cfg.get("enabled"))
            if cfg.get("require_for_pii") is not None:
                require_for_pii = bool(cfg.get("require_for_pii"))
            if cfg.get("requireForPii") is not None:
                require_for_pii = bool(cfg.get("requireForPii"))
            if cfg.get("allow_customer_match") is not None:
                allow_customer_match = bool(cfg.get("allow_customer_match"))
            if cfg.get("allowCustomerMatch") is not None:
                allow_customer_match = bool(cfg.get("allowCustomerMatch"))

    return {
        "enabled": enabled,
        "require_for_pii": require_for_pii,
        "allow_customer_match": allow_customer_match,
    }


def _conversation_is_verified_for_lookup(
    conversation: Conversation,
    *,
    allow_customer_match: bool,
) -> tuple[bool, str | None]:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    marker = metadata.get("verified_lookup") if isinstance(metadata, Mapping) else None
    if marker is True:
        return True, "metadata_flag"
    if isinstance(marker, Mapping):
        status = str(marker.get("status") or marker.get("state") or "").strip().lower()
        if status in {"verified", "ok", "passed"}:
            return True, "metadata_status"
        if marker.get("verified") is True:
            return True, "metadata_verified"
    if allow_customer_match and getattr(conversation, "customer_id", None):
        return True, "customer_match"
    return False, None


def _column_is_sensitive(column: str) -> bool:
    return bool(column_suggests_pii(column) or _column_suggests_person_name(column))


def _extract_requested_columns(table_args: Mapping[str, object]) -> list[str]:
    columns: list[str] = []

    def _push(value: object) -> None:
        text = _coerce_str(value).strip()
        if text and text not in columns:
            columns.append(text)

    for key in ("select_columns", "columns"):
        raw = table_args.get(key)
        if isinstance(raw, (list, tuple)):
            for entry in raw[:100]:
                _push(entry)

    for key in ("match_column", "sort_by", "value_column"):
        if key in table_args:
            _push(table_args.get(key))

    aggregate = table_args.get("aggregate") if isinstance(table_args.get("aggregate"), Mapping) else None
    if aggregate:
        _push(aggregate.get("column"))
        _push(aggregate.get("group_by"))
        _push(aggregate.get("groupBy"))

    filters = table_args.get("filters")
    if isinstance(filters, list):
        for entry in filters[:50]:
            if not isinstance(entry, Mapping):
                continue
            _push(entry.get("column"))

    return columns


def _normalize_column_set(values: object) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [part.strip() for part in values.split(",")]
    if not isinstance(values, (list, tuple, set)):
        return set()
    out: set[str] = set()
    for value in values:
        normalized = _normalize_column_name(_coerce_str(value))
        if normalized:
            out.add(normalized)
    return out


def _resolve_tabular_column_policy(upload: KnowledgeUpload) -> dict[str, object]:
    """
    Resolve per-upload column privacy rules for tabular outputs.

    Returns a dict with:
      - allow: optional set[str] of normalized column names (shared allowlist)
      - deny: set[str] of normalized column names (internal/excluded)
      - force_mask: set[str] of normalized column names (business policy)
    """

    allow: set[str] = set()
    deny: set[str] = set()
    force_mask: set[str] = set()

    ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
    column_privacy = ingestion_meta.get("column_privacy") if isinstance(ingestion_meta, Mapping) else None
    if isinstance(column_privacy, Mapping):
        allow |= _normalize_column_set(column_privacy.get("shared_columns"))
        deny |= _normalize_column_set(column_privacy.get("internal_only_columns"))
        deny |= _normalize_column_set(column_privacy.get("excluded_columns"))

    upload_meta = upload.metadata if isinstance(getattr(upload, "metadata", None), Mapping) else {}
    table_privacy = upload_meta.get("table_privacy") if isinstance(upload_meta, Mapping) else None
    if isinstance(table_privacy, Mapping):
        allow |= _normalize_column_set(table_privacy.get("shared_columns"))
        deny |= _normalize_column_set(table_privacy.get("internal_only_columns"))
        deny |= _normalize_column_set(table_privacy.get("excluded_columns"))
        deny |= _normalize_column_set(table_privacy.get("sensitive_columns"))

    business = getattr(upload, "business_profile", None)
    if business and hasattr(business, "table_privacy_policy"):
        try:
            policy = business.table_privacy_policy() or {}
        except Exception:
            policy = {}
        if isinstance(policy, Mapping):
            force_mask |= _normalize_column_set(policy.get("required_columns"))

    return {"allow": allow or None, "deny": deny, "force_mask": force_mask}


def _column_suggests_person_name(column: str) -> bool:
    normalized = _normalize_column_name(column)
    if not normalized or "name" not in normalized:
        return False
    # Avoid masking business/entity labels like vendor/branch/product names.
    if any(token in normalized for token in ("vendor", "branch", "product", "material", "item")):
        return False
    if any(token in normalized for token in ("first name", "last name", "full name")):
        return True
    if any(token in normalized for token in ("customer", "client", "contact", "user", "person", "employee")):
        return True
    return False


def _redact_person_name(value: object) -> str:
    text = _coerce_str(value).strip()
    if not text:
        return ""
    # Keep the first character as a hint without leaking the full name.
    return text[:1] + "…"


def _mask_cell_value(value: object, *, column_name: str) -> str:
    if not _tabular_pii_redaction_enabled():
        return _coerce_str(value)
    if _column_suggests_person_name(column_name):
        return _redact_person_name(value)
    return redact_value_for_preview(value, column_name=column_name)


def _column_allowed(column_norm: str, *, allow: set[str] | None, deny: set[str]) -> bool:
    if not column_norm:
        return False
    if allow is not None and column_norm not in allow:
        return False
    if column_norm in deny:
        return False
    return True


def _column_should_mask(column: str, *, column_norm: str, force_mask: set[str]) -> bool:
    if not column_norm:
        return False
    if column_norm in force_mask:
        return True
    if column_suggests_pii(column):
        return True
    if _column_suggests_person_name(column):
        return True
    return False


def _sanitize_tabular_rows_for_prompt(
    rows: Sequence[Mapping[str, object]],
    *,
    upload: KnowledgeUpload,
    verified: bool,
    strict_pii: bool,
) -> list[dict[str, object]]:
    if not _tabular_privacy_enabled():
        return [dict(row) for row in rows if isinstance(row, Mapping)]

    policy = _resolve_tabular_column_policy(upload)
    allow = policy.get("allow") if isinstance(policy.get("allow"), set) else None
    deny = policy.get("deny") if isinstance(policy.get("deny"), set) else set()
    force_mask = policy.get("force_mask") if isinstance(policy.get("force_mask"), set) else set()

    sanitized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        row_out = dict(row)
        # Avoid leaking full row concatenations; the model should rely on cells.
        row_out.pop("row_text", None)

        cells_in = row.get("cells") if isinstance(row.get("cells"), list) else []
        cells_out: list[dict[str, object]] = []
        for cell in cells_in:
            if not isinstance(cell, Mapping):
                continue
            column = _coerce_str(cell.get("column")).strip()
            if not column:
                continue
            column_norm = _normalize_column_name(column)
            if not _column_allowed(column_norm, allow=allow, deny=deny):
                continue
            always_mask = column_norm in force_mask
            sensitive = _column_is_sensitive(column)
            cell_out = dict(cell)
            if strict_pii:
                if not verified and (always_mask or sensitive):
                    continue
                if always_mask:
                    if "raw_text" in cell_out:
                        cell_out["raw_text"] = _mask_cell_value(cell_out.get("raw_text"), column_name=column)
                        if "normalized_value" in cell_out:
                            cell_out["normalized_value"] = _normalize_column_name(cell_out.get("raw_text"))
                        if "numeric" in cell_out:
                            cell_out["numeric"] = None
                    if "value" in cell_out:
                        cell_out["value"] = _mask_cell_value(cell_out.get("value"), column_name=column)
                    cell_out["masked"] = True
            elif always_mask or sensitive:
                if "raw_text" in cell_out:
                    cell_out["raw_text"] = _mask_cell_value(cell_out.get("raw_text"), column_name=column)
                    if "normalized_value" in cell_out:
                        cell_out["normalized_value"] = _normalize_column_name(cell_out.get("raw_text"))
                    if "numeric" in cell_out:
                        cell_out["numeric"] = None
                if "value" in cell_out:
                    cell_out["value"] = _mask_cell_value(cell_out.get("value"), column_name=column)
                cell_out["masked"] = True
            cells_out.append(cell_out)
        row_out["cells"] = cells_out

        contributions_in = row.get("contributions") if isinstance(row.get("contributions"), list) else []
        if contributions_in:
            contributions_out: list[dict[str, object]] = []
            for entry in contributions_in:
                if not isinstance(entry, Mapping):
                    continue
                column = _coerce_str(entry.get("column")).strip()
                if not column:
                    continue
                column_norm = _normalize_column_name(column)
                if not _column_allowed(column_norm, allow=allow, deny=deny):
                    continue
                entry_out = dict(entry)
                always_mask = column_norm in force_mask
                sensitive = _column_is_sensitive(column)
                if strict_pii:
                    if not verified and (always_mask or sensitive):
                        continue
                    if always_mask:
                        entry_out["value"] = _mask_cell_value(entry_out.get("value"), column_name=column)
                        entry_out["display"] = _mask_cell_value(
                            entry_out.get("display") or entry_out.get("value"), column_name=column
                        )
                        entry_out["masked"] = True
                elif always_mask or sensitive:
                    entry_out["value"] = _mask_cell_value(entry_out.get("value"), column_name=column)
                    entry_out["display"] = _mask_cell_value(
                        entry_out.get("display") or entry_out.get("value"), column_name=column
                    )
                    entry_out["masked"] = True
                contributions_out.append(entry_out)
            row_out["contributions"] = contributions_out

        sanitized.append(row_out)
    return sanitized


def _sanitize_dataset_aggregate_for_prompt(
    aggregate_result: Mapping[str, object],
    *,
    upload: KnowledgeUpload,
    verified: bool,
    strict_pii: bool,
) -> dict[str, object]:
    if not aggregate_result:
        return {}
    if not _tabular_privacy_enabled():
        return dict(aggregate_result)

    policy = _resolve_tabular_column_policy(upload)
    allow = policy.get("allow") if isinstance(policy.get("allow"), set) else None
    deny = policy.get("deny") if isinstance(policy.get("deny"), set) else set()
    force_mask = policy.get("force_mask") if isinstance(policy.get("force_mask"), set) else set()

    out = dict(aggregate_result)
    op = _coerce_str(out.get("operation")).strip().lower()
    if op != "group_by":
        return out
    group_by = _coerce_str(out.get("group_by")).strip()
    group_norm = _normalize_column_name(group_by)
    if not group_by:
        return out
    if not _column_allowed(group_norm, allow=allow, deny=deny):
        out["groups"] = []
        out["redacted"] = True
        return out

    always_mask = group_norm in force_mask
    sensitive = _column_is_sensitive(group_by)
    if strict_pii:
        if not verified and (always_mask or sensitive):
            out["groups"] = []
            out["redacted"] = True
            return out
        if always_mask:
            groups_in = out.get("groups") if isinstance(out.get("groups"), list) else []
            groups_out: list[dict[str, object]] = []
            for entry in groups_in:
                if not isinstance(entry, Mapping):
                    continue
                entry_out = dict(entry)
                entry_out["value"] = _mask_cell_value(entry_out.get("value"), column_name=group_by)
                entry_out["masked"] = True
                groups_out.append(entry_out)
            out["groups"] = groups_out
        return out

    if always_mask or sensitive:
        groups_in = out.get("groups") if isinstance(out.get("groups"), list) else []
        groups_out: list[dict[str, object]] = []
        for entry in groups_in:
            if not isinstance(entry, Mapping):
                continue
            entry_out = dict(entry)
            entry_out["value"] = _mask_cell_value(entry_out.get("value"), column_name=group_by)
            entry_out["masked"] = True
            groups_out.append(entry_out)
        out["groups"] = groups_out
    return out


def _parse_numeric_value(value: str | None) -> float | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d\-,\.]", "", text)
    cleaned = cleaned.replace(",", "")
    if not cleaned or cleaned in {"-", ".", "-.", "-"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _format_numeric_display(value: float | None, raw: str | None = None) -> str | None:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if value is None:
        return None
    rounded = round(value)
    if abs(value - rounded) < 1e-6:
        return f"{rounded:,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


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
            "preview": redact_free_text(preview) if preview and not include_pii else preview,
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


def _search_hint(
    status: str,
    intent: str | None,
    snippets: Sequence[Mapping[str, object]],
    diagnostics: Mapping[str, object] | None,
) -> str | None:
    if status != "ok" or not snippets:
        if intent == "identifier":
            return "No confident match; ask for the exact identifier or a page/section name instead of guessing."
        return "No strong matches yet; ask the visitor for a clearer identifier, product name, or page reference."
    diag = diagnostics or {}
    if intent == "table":
        return (
            "These results look tabular. Use query_dataset for native datasets/spreadsheets (CSV/XLSX/JSONL). "
            "If the source is a document (PDF/DOCX/TXT) that visually contains a table, use read_document to read the relevant page—"
            "`is_table_chunk=true` can come from tables extracted from documents and is not a signal that the file is queryable like a spreadsheet. "
            "Use list_tables only to find dataset uploads (document_id + sheet hints) before table queries."
        )
    if diag.get("path") == "fallback":
        return "Fallback snippets in use; confirm details with the visitor or narrow the request before citing specifics."
    return None


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
            AiOrchestratorService._issue_warning_payloads(  # type: ignore[attr-defined]
                entry.get("issues"),
                label=label,  # type: ignore[arg-type]
                upload_id=upload_id,
            )
        )
        diagnostics = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
        partial_index = bool(entry.get("partial_index"))
        truncation_note_val = entry.get("truncation_note")
        truncation_note = truncation_note_val if isinstance(truncation_note_val, str) else None
        diag_warning = AiOrchestratorService._diagnostic_warning_payload(  # type: ignore[attr-defined]
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
) -> dict[str, object]:
    """
    Convert legacy search_knowledge response to agentic format.
    
    Agentic format returns metadata and short previews (no full content):
    - IDs, titles, types, char estimates, previews
    - LLM must call read_document() to get actual content
    
    This enables the clean 2-tool workflow: search → read → answer
    """
    snippets = legacy_payload.get("snippets", [])
    results: list[dict[str, object]] = []
    
    for snippet in snippets:
        if not isinstance(snippet, Mapping):
            continue
        
        # Determine type
        is_table = bool(snippet.get("is_table_chunk"))
        content_type = "table" if is_table else "text"
        
        # Get IDs
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        upload_id = str(snippet.get("upload_id") or "")
        
        # Estimate char count from summary/content if available
        content = snippet.get("content") or ""
        summary = snippet.get("summary") or ""
        char_estimate = len(content) if content else len(summary) * 3  # estimate full content

        diagnostics = snippet.get("source_diagnostics") if isinstance(snippet.get("source_diagnostics"), Mapping) else {}
        row_count = diagnostics.get("table_total_rows") or diagnostics.get("table_row_count") or snippet.get("row_count")
        column_count = diagnostics.get("table_column_count") or snippet.get("column_count")
        table_id = diagnostics.get("table_id")
        row_index = diagnostics.get("row_index") or diagnostics.get("table_row_index")
        
        read_hint = snippet.get("read_hint")
        read_id = ""
        if isinstance(read_hint, Mapping):
            read_id = str(read_hint.get("document_id") or "").strip()
        if not read_id:
            read_id = chunk_id or upload_id
        if not chunk_id and read_id:
            chunk_id = read_id

        result_item: dict[str, object] = {
            "id": chunk_id,
            "document_id": upload_id,
            "title": snippet.get("title") or snippet.get("public_label") or "Untitled",
            "type": content_type,
            "source": snippet.get("source_file") or snippet.get("source") or "",
            "char_estimate": char_estimate,
        }
        if read_id:
            result_item["read_id"] = read_id

        preview_source = summary or content
        if isinstance(preview_source, str) and preview_source.strip():
            preview = preview_source.strip()
            if len(preview) > 240:
                preview = f"{preview[:240].rstrip()}…"
            result_item["preview"] = preview

        if isinstance(read_hint, Mapping) and read_hint:
            result_item["read_hint"] = dict(read_hint)
        
        if content_type == "table":
            if row_count is not None:
                result_item["row_count"] = row_count
            if column_count is not None:
                result_item["column_count"] = column_count
            if table_id:
                result_item["table_id"] = table_id
            if row_index is not None:
                result_item["row_index"] = row_index
        
        results.append(result_item)
    
    # Build agentic response
    status = legacy_payload.get("status", "ok")
    total_found = legacy_payload.get("completeness", {}).get("total_found", len(results))
    
    agentic_response: dict[str, object] = {
        "tool": "search_knowledge",
        "status": status if results else "empty",
        "results": results,
        "total_found": total_found,
    }
    
    # Add hint only if empty
    if not results:
        agentic_response["hint"] = "No matching documents found. Try different search terms."
    
    # Log the conversion for debugging
    structured_log(
        "mcp",
        "search.agentic_conversion",
        {
            "legacy_snippet_count": len(snippets),
            "agentic_result_count": len(results),
            "total_found": total_found,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )
    
    return agentic_response


def _search_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    primary_query = _coerce_str(arguments.get("query")).strip()
    raw_extra_queries = arguments.get("queries")
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

    if primary_query:
        _append_query(primary_query)
    if isinstance(raw_extra_queries, (list, tuple)):
        for candidate in raw_extra_queries:
            candidate_str = _coerce_str(candidate).strip()
            if candidate_str:
                _append_query(candidate_str)

    query_variant_limit = max(
        1,
        int(getattr(settings, "MCP_SEARCH_MAX_QUERY_VARIANTS", DEFAULT_MAX_SEARCH_QUERY_VARIANTS)),
    )
    fanout_budget_ms = max(
        0,
        int(getattr(settings, "MCP_SEARCH_FANOUT_BUDGET_MS", 0) or 0),
    )
    rrf_k = max(
        1,
        int(getattr(settings, "MCP_SEARCH_FANOUT_RRF_K", 60) or 60),
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

    # MOVED: Server-side search enforcement (Phase 4) - after query validation
    # Per Codex review: only charge for valid non-empty queries
    context.reserve_search()

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
            "hint": str(exc),
        }

    raw_limit = arguments.get("limit")
    try:
        requested_limit = int(raw_limit) if raw_limit is not None else None
    except (TypeError, ValueError):
        requested_limit = None

    service = _knowledge_service()
    # Apply identifier value filter when an email is locked/provided to prevent cross-identifier leakage.
    identifier_filter: dict[str, object] | None = None
    allowed_uploads: set[str] | None = None
    guard = _identifier_guard(context, conversation)
    locked = getattr(guard, "locked_identifier", None) if guard else None
    locked_key = None
    locked_value = None
    if isinstance(locked, Mapping):
        locked_key = locked.get("key")
        locked_value = locked.get("value")
    cache_key = _identifier_mapping_cache_key(guard, locked_key, locked_value)
    cache_token = _identifier_mapping_cache_token(cache_key, conversation.business_profile_id)
    cached_mapping = None
    cache_payload: dict[str, object] | None = None
    if cache_key:
        cached_mapping = context.identifier_mapping_cache.get(cache_key)
        if cached_mapping is None and cache_token:
            cached_from_store = cache.get(cache_token)
            if isinstance(cached_from_store, dict):
                cached_mapping = cached_from_store
                context.identifier_mapping_cache[cache_key] = cached_mapping
    if cached_mapping is not None:
        identifier_filter = cached_mapping.get("identifier_filter")
        cached_allowed = cached_mapping.get("allowed_uploads")
        if isinstance(cached_allowed, (list, tuple, set)):
            allowed_uploads = {str(value) for value in cached_allowed if value}
    elif guard and guard.provided_identifiers:
        # Derive filter from active mappings for provided identifiers (dynamic, not email-only).
        from apps.accounts.models import IdentifierColumnMapping, IdentifierColumnStatus, IdentifierSchemaStatus

        mappings = IdentifierColumnMapping.objects.select_related("identifier").filter(
            business_profile=conversation.business_profile,
            status=IdentifierColumnStatus.ACTIVE,
            identifier__status=IdentifierSchemaStatus.ACTIVE,
            identifier__key__in=list(guard.provided_identifiers.keys()),
        )
        if mappings:
            allowed_uploads = {str(m.upload_id) for m in mappings if m.upload_id}
            # Use first mapping for value filter hint.
            first = mappings[0]
            value_for_filter = guard.provided_identifiers.get(first.identifier.key)
            # If locked value exists for this key, force it.
            if locked_key and first.identifier.key == locked_key and locked_value:
                value_for_filter = locked_value
            identifier_filter = {
                "column": first.column_normalized or first.column_name,
                "value": value_for_filter,
                "upload_ids": list(allowed_uploads),
            }
            cache_payload = {
                "allowed_uploads": list(allowed_uploads) if allowed_uploads else [],
                "identifier_filter": identifier_filter,
            }
    if cache_payload is not None:
        if cache_key:
            context.identifier_mapping_cache[cache_key] = cache_payload
        if cache_token:
            cache.set(cache_token, cache_payload, IDENTIFIER_MAPPING_CACHE_TTL)

    agent_scope = _agent_knowledge_scope(conversation, context)
    agent_collection_ids = _scope_upload_ids_to_uuids(agent_scope.collection_ids) if agent_scope.collection_ids else None
    agent_explicit_upload_ids = _scope_upload_ids_to_uuids(agent_scope.explicit_upload_ids) if agent_scope.explicit_upload_ids else None

    combined_upload_ids: list[uuid.UUID] | None
    if allowed_uploads is None:
        combined_upload_ids = None
    else:
        combined_allowed: set[str] = set()
        if not agent_scope.restricted:
            combined_allowed.update(allowed_uploads)
        else:
            if agent_scope.explicit_upload_ids:
                combined_allowed.update(set(allowed_uploads) & set(agent_scope.explicit_upload_ids))
            if agent_scope.collection_ids:
                candidate_ids = _scope_upload_ids_to_uuids(allowed_uploads) or []
                if candidate_ids:
                    with tenant_context(conversation.business_profile_id):
                        in_collections = apply_customer_visible_uploads(
                            KnowledgeUpload.objects.filter(
                                business_profile=conversation.business_profile,
                                status=KnowledgeStatus.ACTIVE,
                                id__in=candidate_ids,
                                collections__id__in=list(agent_scope.collection_ids),
                            )
                        ).values_list("id", flat=True)
                        combined_allowed.update(str(value) for value in in_collections if value)
        combined_upload_ids = _scope_upload_ids_to_uuids(combined_allowed)

    def _tuned_limit(intent: str | None, base_limit: int | None) -> int | None:
        limit_val = base_limit
        if intent == "identifier":
            limit_val = min(limit_val or 5, 4)
        elif intent == "table":
            limit_val = min(8, max(limit_val or 5, 6))
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
            "agent_scope_collections": len(agent_scope.collection_ids) if agent_scope.restricted else None,
            "effective_scope_uploads": len(combined_upload_ids) if combined_upload_ids is not None else None,
            "total_ms": diag.get("total_duration_ms"),
            "alias_ms": diag.get("alias_duration_ms"),
            "vector_ms": diag.get("vector_duration_ms"),
            "lexical_ms": diag.get("fts_duration_ms"),
            "rerank_ms": diag.get("rerank_duration_ms"),
            "rerank_ce_policy": diag.get("rerank_cross_encoder_policy"),
            "rerank_ce_attempted": diag.get("rerank_cross_encoder_attempted"),
            "rerank_ce_applied": diag.get("rerank_cross_encoder_applied"),
            "rerank_ce_pairs": diag.get("rerank_cross_encoder_pairs"),
            "rerank_ce_skip": diag.get("rerank_cross_encoder_skip_reason"),
            "table_ms": diag.get("table_duration_ms"),
            "table_context_ms": diag.get("table_context_ms"),
            "table_presence_ms": diag.get("table_presence_ms"),
            "snippet_rerank_ms": diag.get("snippet_rerank_ms"),
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

    def _execute_single_query(
        query_text: str,
        *,
        intent_info_override: Mapping[str, object] | None = None,
        intent_override: str | None = None,
        limit_override: int | None = None,
        precomputed_result: object | None = None,
    ) -> Mapping[str, object]:
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
        limit_for_run = limit_override if limit_override is not None else _tuned_limit(intent, requested_limit)
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
                allowed_collection_ids=agent_collection_ids,
                allowed_explicit_upload_ids=agent_explicit_upload_ids,
            )
        dataset_candidates: list[dict[str, object]] = []
        combined_snippets: list[object] = []
        routing_enabled = str(getattr(settings, "DATASET_KEY_INDEX_ROUTING_ENABLED", "true")).lower() in {"1", "true", "yes"}
        if routing_enabled and intent == "identifier":
            identifier_candidate = _extract_identifier_candidate(query_text)
            if identifier_candidate:
                hits = find_datasets_for_identifier(
                    business_profile=conversation.business_profile,
                    identifier_value=identifier_candidate,
                )
                if combined_upload_ids is not None:
                    hits = [hit for hit in hits if getattr(hit, "upload_id", None) in combined_upload_ids]
                if hits:
                    dataset_candidates = [
                        {
                            "upload_id": hit.upload_id,
                            "upload_name": hit.upload_name,
                            "sheet_name": hit.sheet_name,
                            "sheet_index": hit.sheet_index,
                            "column": hit.column,
                            "identifier_key": hit.identifier_key,
                            "identifier_required": hit.identifier_required,
                            "source": hit.source,
                        }
                        for hit in hits
                    ]
                    upload_ids = {hit.upload_id for hit in hits}
                    card_chunks = list(
                        apply_customer_visible_chunks(
                            KnowledgeUploadChunk.objects.filter(
                                business_profile=conversation.business_profile,
                                upload_id__in=upload_ids,
                                metadata__strategy="dataset_card",
                                upload__status=KnowledgeStatus.ACTIVE,
                            )
                        )
                        .select_related("upload")
                        .order_by("-updated_at")[:8]
                    )
                    hit_by_upload = {hit.upload_id: hit for hit in hits}
                    for chunk in card_chunks:
                        upload = chunk.upload
                        label = (getattr(upload, "display_name", None) or getattr(upload, "filename", None) or str(upload.id)).strip()
                        match = hit_by_upload.get(str(upload.id))
                        combined_snippets.append(
                            KnowledgeSnippet(
                                id=chunk.id,
                                title=label,
                                summary="Dataset candidate (key index match).",
                                source="dataset_key_index",
                                content=chunk.content or "",
                                public_label=label,
                                upload_id=upload.id,
                                chunk_id=chunk.id,
                                chunk_index=getattr(chunk, "chunk_index", None),
                                search_stage="dataset_key_index",
                                confidence_score=1.0,
                                source_diagnostics={
                                    "dataset_key_index": True,
                                    "matched_column": getattr(match, "column", None),
                                    "matched_sheet": getattr(match, "sheet_name", None),
                                    "matched_sheet_index": getattr(match, "sheet_index", None),
                                },
                            )
                        )
        combined_snippets.extend(list(getattr(result, "snippets", []) or []))
        snippet_payloads = _serialize_snippets(combined_snippets)
        if locked_key and locked_value:
            locked_val_norm = str(locked_value).strip()
            filtered_snippets = []
            for payload in snippet_payloads:
                identifiers = payload.get("identifiers") if isinstance(payload, Mapping) else None
                if identifiers and isinstance(identifiers, Mapping):
                    candidate = identifiers.get(locked_key)
                    if candidate and str(candidate).strip().lower() != locked_val_norm.lower():
                        continue
                filtered_snippets.append(payload)
            snippet_payloads = filtered_snippets
        decision = None
        if guard:
            decision = guard.evaluate_snippets(snippet_payloads)
            _record_identifier_check(context, decision)
            if decision.status == "identifier_conflict":
                _log_search_performance(
                    snippets=snippet_payloads,
                    diagnostics=result.diagnostics,
                    intent=intent,
                    limit_value=limit_for_run,
                    status="identifier_required",
                    note="identifier_conflict",
                )
                return {
                    "tool": "search_knowledge",
                    "query": query_text,
                    "limit": limit_for_run,
                    "limit_used": limit_for_run,
                    "limit": limit_for_run,
                    "limit_used": limit_for_run,
                    "query_intent": intent,
                    "intent_signal": intent_info,
                    "status": "identifier_required",
                    "error": "identifier_required",
                    "error_code": "identifier_required",
                    "diagnostics": dict(result.diagnostics or {}),
                    "snippets": [],
                    "identifier_gate": decision.as_dict(),
                    "required_identifiers": list(decision.required_keys),
                    "provided_identifiers": list(decision.provided_keys),
                    "hint": decision.hint,
                    "llm_hint": decision.hint,
                }
            if decision.status != "ok":
                structured_log(
                    "mcp",
                    "identifier.denied",
                    {
                        "tool": "search_knowledge",
                        "uploads": list(decision.blocked_uploads),
                        "required": list(decision.required_keys),
                        "provided": list(decision.provided_keys),
                    },
                    context={"business": conversation.business_profile_id},
                    logger_obj=logger,
                    level=logging.WARNING,
                )
                _log_search_performance(
                    snippets=snippet_payloads,
                    diagnostics=result.diagnostics,
                    intent=intent,
                    limit_value=limit_for_run,
                    status=decision.status,
                    note="identifier_gate_blocked",
                )
                return {
                    "tool": "search_knowledge",
                    "query": query_text,
                    "limit": limit_for_run,
                    "limit_used": limit_for_run,
                    "limit": limit_for_run,
                    "limit_used": limit_for_run,
                    "query_intent": intent,
                    "intent_signal": intent_info,
                    "status": decision.status,
                    "error": "identifier_required",
                    "error_code": "identifier_required",
                    "diagnostics": dict(result.diagnostics or {}),
                    "snippets": [],
                    "identifier_gate": decision.as_dict(),
                    "required_identifiers": list(decision.required_keys),
                    "provided_identifiers": list(decision.provided_keys),
                    "hint": decision.hint,
                    "llm_hint": decision.hint,
                }
        read_required = False
        read_required_reasons_summary: set[str] = set()
        if allowed_uploads is not None:
            snippet_payloads = [payload for payload in snippet_payloads if str(payload.get("upload_id") or "") in allowed_uploads]
            if not snippet_payloads:
                _log_snippet_payloads(
                    tool="search_knowledge",
                    conversation=conversation,
                    snippet_payloads=snippet_payloads,
                    meta={"query": query_text, "intent": intent, "read_required": read_required, "filtered": True},
                )
                _log_search_performance(
                    snippets=snippet_payloads,
                    diagnostics=result.diagnostics,
                    intent=intent,
                    limit_value=limit_for_run,
                    status="ok",
                    note="identifier_scope_filtered",
                )
                return {
                    "tool": "search_knowledge",
                    "query": query_text,
                    "limit": limit_for_run,
                    "limit_used": limit_for_run,
                    "limit": limit_for_run,
                    "limit_used": limit_for_run,
                    "query_intent": intent,
                    "intent_signal": intent_info,
                    "status": "ok",
                    "snippets": [],
                    "identifier_gate": decision.as_dict() if decision else None,
                    "hint": "No records found for this identifier.",
                }
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
                # For table chunks, prefer the chunk id so read_document can auto-upgrade to full_page table content.
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
        if guard and decision and decision.status == "ok":
            applied_filter = decision.as_dict()
            applied_filter["tool"] = "search_knowledge"
            context.identifier_filters.append(applied_filter)
            _record_identifier_event_once(
                context,
                business_profile=conversation.business_profile,
                decision=decision,
                tool="search_knowledge",
                conversation=conversation,
                upload_ids=[str(payload.get("upload_id") or "") for payload in snippet_payloads if payload.get("upload_id")],
            )
        elif guard and decision:
            _record_identifier_event_once(
                context,
                business_profile=conversation.business_profile,
                decision=decision,
                tool="search_knowledge",
                conversation=conversation,
                upload_ids=list(decision.blocked_uploads or ()),
            )
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
        payload = {
            "tool": "search_knowledge",
            "query": query_text,
            "limit": limit_for_run,
            "limit_used": limit_for_run,

            "query_intent": intent,
            "intent_signal": intent_info,
            "status": result.status,
            "diagnostics": dict(result.diagnostics or {}),
            "snippets": snippet_payloads,
            "hint": _search_hint(result.status, intent, snippet_payloads, result.diagnostics),
        }
        payload["read_required_summary"] = {
            "any": read_required,
            "reasons": sorted(read_required_reasons_summary),
        }
        if dataset_candidates:
            payload["dataset_candidates"] = dataset_candidates
            unique_uploads = {str(item.get("upload_id") or "") for item in dataset_candidates if item.get("upload_id")}
            if len([uid for uid in unique_uploads if uid]) > 1:
                payload["hint"] = (
                    "Multiple datasets may match this identifier. Ask which dataset/sheet to use or request one more "
                    "field (date/customer/etc.) to disambiguate before reading."
                )
        if search_cache_key:
            _bounded_cache_store(context.search_cache, search_cache_key, copy.deepcopy(payload))
        return payload

    resolved_runs: list[tuple[int, Mapping[str, object]]] = []
    pending_specs: list[tuple[int, str, Mapping[str, object], str | None, int | None]] = []
    non_cached_queries = 0
    for idx, query_text in enumerate(queries):
        intent_info = _query_intent(query_text)
        intent = intent_info.get("intent")
        limit_for_run = _tuned_limit(intent, requested_limit)
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
                )
                futures.append((idx, query_text, intent_info, intent, limit_for_run, future))
            else:
                result = service.search(
                    business_profile=conversation.business_profile,
                    query=query_text,
                    limit=limit_for_run,
                    identifier_filter=identifier_filter,
                    allowed_upload_ids=combined_upload_ids,
                    allowed_collection_ids=agent_collection_ids,
                    allowed_explicit_upload_ids=agent_explicit_upload_ids,
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
    deduped_snippets: list[dict[str, object]] = []
    fusion: dict[str, object] | None = None

    def _snippet_dedupe_key(snippet: Mapping[str, object]) -> str:
        content = snippet.get("content")
        if isinstance(content, str) and content.strip():
            return f"content:{sha256_hex(content)}"
        summary = snippet.get("summary")
        if isinstance(summary, str) and summary.strip():
            return f"summary:{sha256_hex(summary)}"
        identifier = snippet.get("chunk_id") or snippet.get("id") or snippet.get("upload_id")
        if identifier:
            return f"id:{identifier}"
        return json.dumps(snippet, sort_keys=True, default=str)

    if len(runs) > 1:
        rrf_scores: dict[str, float] = defaultdict(float)
        best_payload: dict[str, dict[str, object]] = {}
        best_rank: dict[str, int] = {}
        for run in runs:
            for rank, snippet in enumerate(run.get("snippets", []), start=1):
                key = _snippet_dedupe_key(snippet)
                rrf_scores[key] += 1.0 / (rrf_k + rank)
                current_best = best_rank.get(key)
                if current_best is None or rank < current_best:
                    best_rank[key] = rank
                    best_payload[key] = snippet
        ordered = sorted(
            rrf_scores.items(),
            key=lambda item: (-item[1], best_rank.get(item[0], 10**9)),
        )
        for key, _score in ordered:
            payload = best_payload.get(key)
            if payload:
                deduped_snippets.append(payload)
            if clip_limit and len(deduped_snippets) >= clip_limit:
                break
        fusion = {"method": "rrf", "k": rrf_k, "runs": len(runs)}
    else:
        seen_snippets: set[str] = set()
        for run in runs:
            for snippet in run.get("snippets", []):
                dedup_key = _snippet_dedupe_key(snippet)
                if dedup_key in seen_snippets:
                    continue
                seen_snippets.add(dedup_key)
                deduped_snippets.append(snippet)
                if clip_limit and len(deduped_snippets) >= clip_limit:
                    break
            if clip_limit and len(deduped_snippets) >= clip_limit:
                break

    total_found = len(deduped_snippets)

    # Apply prompt snippet limit BEFORE seen-item tracking
    # This ensures we only track snippets that will actually be shown to the user
    prompt_max_snippets = max(1, int(getattr(settings, "MCP_PROMPT_MAX_SNIPPETS", 6) or 6))
    clipped = 0
    if len(deduped_snippets) > prompt_max_snippets:
        clipped = len(deduped_snippets) - prompt_max_snippets
        deduped_snippets = deduped_snippets[:prompt_max_snippets]

    # Track seen items (no filtering)
    deduped_snippets, completeness = _apply_seen_item_filter(deduped_snippets, context, mark_as_seen=False)
    completeness["total_found"] = total_found
    completeness["shown"] = len(deduped_snippets)
    completeness["has_more"] = clipped > 0
    if clipped:
        completeness["clipped"] = clipped

    if completeness["shown"] > 0 and not completeness["has_more"]:
        if completeness["already_seen"] == completeness["shown"]:
            completeness["all_previously_shown"] = True
            completeness["message"] = (
                f"All {completeness['shown']} matching results have already been shown in this conversation. "
                "Try a different search term or ask the user if they need something specific."
            )

    # NOW mark the final clipped list as seen
    _mark_snippets_as_seen(deduped_snippets, context)

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

    for snippet in deduped_snippets:
        context.add_knowledge_result(snippet)

    metrics = _log_tool_metrics(
        tool="search_knowledge",
        conversation=conversation,
        snippets=deduped_snippets,
        extra={
            "status": primary_run.get("status"),
            "limit": limit_cap,
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

    final_status = "ok" if deduped_snippets else runs[-1].get("status") or "not_found"
    diag = dict(primary_run.get("diagnostics") or {})
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

    query_intent = primary_run.get("query_intent") or primary_run.get("intent")

    # Update hint if all results were previously shown
    hint = _search_hint(final_status, query_intent, deduped_snippets, diag)
    if completeness.get("all_previously_shown"):
        hint = completeness.get("message") or hint

    payload = {
        "tool": "search_knowledge",
        "query": primary_run.get("query"),
        "limit": limit_cap,
        "query_intent": query_intent,
        "intent_signal": primary_run.get("intent_signal"),
        "status": final_status,
        "diagnostics": diag,
        "snippets": deduped_snippets,
        "hint": hint,
    }

    # Always include completeness metadata for transparent decisions
    payload["completeness"] = completeness

    if fusion:
        payload["fusion"] = fusion
    if len(queries) > 1:
        payload["batched_queries"] = tuple(queries)
    
    # Check if agentic mode is enabled for this business
    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
    if feature_state.rag_agentic_mode:
        return _convert_to_agentic_search_response(payload, conversation=conversation)
    
    return payload


def _convert_to_agentic_read_response(
    legacy_payload: Mapping[str, object],
    *,
    conversation: Conversation,
    truncated_ids: Sequence[str] | None = None,
) -> dict[str, object]:
    """
    Convert legacy read_document response to agentic format.
    
    Agentic format returns structured content:
    - ID, title, full content, type
    - Used by LLM to formulate final answer
    """
    snippets = legacy_payload.get("snippets", [])
    contents: list[dict[str, object]] = []
    total_chars = 0
    
    for snippet in snippets:
        if not isinstance(snippet, Mapping):
            continue
        
        # Determine type
        is_table = bool(snippet.get("is_table_chunk"))
        content_type = "table" if is_table else "text"
        
        # Get content
        content = snippet.get("content") or snippet.get("summary") or ""
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        
        content_item: dict[str, object] = {
            "id": chunk_id,
            "title": snippet.get("title") or snippet.get("public_label") or "Untitled",
            "content": content,
            "type": content_type,
            "truncated": chunk_id in (truncated_ids or []),
        }
        
        contents.append(content_item)
        total_chars += len(str(content))
    
    # Build agentic response
    status = legacy_payload.get("status", "ok")
    
    agentic_response: dict[str, object] = {
        "tool": "read_document",
        "status": "partial" if truncated_ids else status,
        "contents": contents,
        "total_chars": total_chars,
    }
    
    if truncated_ids:
        agentic_response["truncated_ids"] = list(truncated_ids)
    
    # Carry forward any errors
    if legacy_payload.get("error"):
        agentic_response["error"] = legacy_payload.get("error")
    
    # Log the conversion
    structured_log(
        "mcp",
        "read.agentic_conversion",
        {
            "legacy_snippet_count": len(snippets),
            "agentic_content_count": len(contents),
            "total_chars": total_chars,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )
    
    return agentic_response


def _agentic_batch_read_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Agentic batch read handler.
    
    Accepts multiple IDs and a max_chars limit for token control.
    Delegates to the standard _read_document_handler for each ID.
    """
    raw_ids = arguments.get("ids")
    if not isinstance(raw_ids, (list, tuple)):
        # Fallback to single document_id
        return _read_document_handler(arguments, conversation, context)
    
    seen_ids: set[str] = set()
    ordered_ids: list[str] = []
    for raw_id in raw_ids:
        doc_id = str(raw_id).strip()
        if not doc_id or doc_id in seen_ids:
            continue
        seen_ids.add(doc_id)
        ordered_ids.append(doc_id)

    raw_max_chars = arguments.get("max_chars")
    if raw_max_chars is None:
        service = _knowledge_service()
        max_chars = service.inline_char_limit_for_business(conversation.business_profile)
    else:
        try:
            max_chars = int(raw_max_chars)
        except (TypeError, ValueError):
            max_chars = 8000
    max_chars = max(200, max_chars)
    
    all_contents: list[dict[str, object]] = []
    all_snippets: list[dict[str, object]] = []
    total_chars = 0
    truncated_ids: list[str] = []
    errors: list[dict[str, object]] = []
    
    for doc_id in ordered_ids:
        
        # Check if we've hit the char limit
        if total_chars >= max_chars:
            truncated_ids.append(doc_id)
            continue
        
        remaining = max_chars - total_chars

        # Call the standard handler for this ID
        single_args = dict(arguments)
        single_args["document_id"] = doc_id
        single_args["agentic_mode"] = True
        single_args.pop("ids", None)
        single_args["max_chars"] = remaining
        
        try:
            result = _read_document_handler(single_args, conversation, context)
        except Exception as e:
            errors.append({"id": doc_id, "error": str(e)})
            continue
        
        if result.get("status") == "error" or result.get("status") == "not_found":
            errors.append({"id": doc_id, "error": result.get("error", "unknown")})
            continue
        
        # Extract snippets and add to contents
        snippets = result.get("snippets", [])
        if isinstance(snippets, list):
            for entry in snippets:
                if isinstance(entry, Mapping):
                    all_snippets.append(dict(entry))
        for snippet in snippets:
            if not isinstance(snippet, Mapping):
                continue
            
            is_table = bool(snippet.get("is_table_chunk"))
            content = snippet.get("content") or snippet.get("summary") or ""
            content_len = len(str(content))
            
            # Check char budget
            remaining = max_chars - total_chars
            if remaining <= 0:
                truncated_ids.append(doc_id)
                break
            if content_len > remaining:
                truncated_ids.append(doc_id)
                all_contents.append({
                    "id": str(snippet.get("chunk_id") or snippet.get("id") or doc_id),
                    "title": snippet.get("title") or snippet.get("public_label") or "Untitled",
                    "content": str(content)[:remaining],
                    "type": "table" if is_table else "text",
                    "truncated": True,
                })
                total_chars += remaining
                break
            
            all_contents.append({
                "id": str(snippet.get("chunk_id") or snippet.get("id") or doc_id),
                "title": snippet.get("title") or snippet.get("public_label") or "Untitled",
                "content": content,
                "type": "table" if is_table else "text",
                "truncated": False,
            })
            total_chars += content_len
    
    # Build response
    response: dict[str, object] = {
        "tool": "read_document",
        "status": "partial" if truncated_ids else "ok",
        "contents": all_contents,
        "total_chars": total_chars,
    }
    if all_snippets:
        response["snippets"] = all_snippets
    
    if truncated_ids:
        response["truncated_ids"] = truncated_ids
    if errors:
        response["errors"] = errors
    
    structured_log(
        "mcp",
        "read.batch_complete",
        {
        "requested_ids": len(ordered_ids),
            "content_count": len(all_contents),
            "truncated_count": len(truncated_ids),
            "error_count": len(errors),
            "total_chars": total_chars,
            "max_chars": max_chars,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )
    
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
        confidence_score=1.0,
        truncated=truncated,
        source_diagnostics=source_diag,
        partial_index=partial_index,
        structured_table_count=1,
        issue_count=0,
        structured_table_hint=None,
    )

    return [snippet]


def _read_document_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    raw_id = arguments.get("document_id")
    document_id = _coerce_str(raw_id).strip()
    if not document_id:
        return {
            "tool": "read_document",
            "status": "error",
            "error": "document_id is required",
            "snippets": [],
        }
    try:
        identifier = uuid.UUID(document_id)
    except (TypeError, ValueError):
        return {
            "tool": "read_document",
            "status": "error",
            "error": "document_id must be a valid UUID",
            "snippets": [],
        }
    business = conversation.business_profile

    chunk_record = (
        apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                id=identifier,
                business_profile=business,
                upload__status=KnowledgeStatus.ACTIVE,
            )
        )
        .select_related("upload")
        .first()
    )
    upload_record = None
    gating_upload_id = None
    if chunk_record:
        gating_upload_id = chunk_record.upload_id
    else:
        upload_record = apply_customer_visible_uploads(
            KnowledgeUpload.objects.filter(
                id=identifier,
                business_profile=business,
                status=KnowledgeStatus.ACTIVE,
            )
        ).first()
        if not upload_record:
            return {
                "tool": "read_document",
                "status": "not_found",
                "error": "document not found for this business",
                "snippets": [],
            }
        gating_upload_id = upload_record.id

    agent_scope = _agent_knowledge_scope(conversation, context)
    if gating_upload_id and not _agent_scope_allows_upload(scope=agent_scope, conversation=conversation, upload_id=gating_upload_id):
        return {
            "tool": "read_document",
            "document_id": document_id,
            "status": "constraint_error",
            "error": "forbidden_document",
            "error_code": "forbidden_document",
            "snippets": [],
            "hint": "This agent is not permitted to access that document. Use `search_knowledge` to find allowed sources.",
        }

    if chunk_record:
        chunk_meta = chunk_record.metadata if isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
        if chunk_meta.get("is_table_chunk"):
            upload = chunk_record.upload
            ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
            format_hint = str(ingestion_meta.get("format") or "").strip().lower()
            dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, Mapping) else None
            dataset_enabled = bool(isinstance(dataset_meta, Mapping) and dataset_meta.get("enabled"))
            native_tabular = format_hint in {"csv", "tsv", "xls", "xlsx", "jsonl"}
            if dataset_enabled or native_tabular:
                upload_id = str(chunk_record.upload_id)
                structured_log(
                    "mcp",
                    "read_document.wrong_tool_for_table",
                    {
                        "document_id": document_id,
                        "upload_id": upload_id,
                        "chunk_id": str(chunk_record.id),
                    },
                    context={"conversation": conversation.id, "business": conversation.business_profile_id},
                    logger_obj=logger,
                    level=logging.WARNING,
                )
                return {
                    "tool": "read_document",
                    "document_id": document_id,
                    "upload_id": upload_id,
                    "status": "constraint_error",
                    "error": "wrong_tool_for_table",
                    "error_code": "wrong_tool_for_table",
                    "snippets": [],
                    "hint": (
                        "This upload is a structured dataset/spreadsheet table. Use `query_dataset` with "
                        f"document_id={upload_id} (and call `list_tables` if you need sheet options)."
                    ),
                }
    if upload_record and upload_record.tables.exists() and not upload_record.pages.exists():
        upload_id = str(upload_record.id)
        structured_log(
            "mcp",
            "read_document.wrong_tool_for_spreadsheet",
            {"document_id": document_id, "upload_id": upload_id},
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
            level=logging.WARNING,
        )
        return {
            "tool": "read_document",
            "document_id": document_id,
            "upload_id": upload_id,
            "status": "constraint_error",
            "error": "wrong_tool_for_table",
            "error_code": "wrong_tool_for_table",
            "snippets": [],
            "hint": (
                "This upload is a spreadsheet/structured table. Use `query_dataset` with "
                f"document_id={upload_id} (and call `list_tables` if you need sheet/table options)."
            ),
        }

    guard = _identifier_guard(context, conversation)
    locked = getattr(guard, "locked_identifier", None) if guard else None
    locked_key = None
    locked_value = None
    if isinstance(locked, Mapping):
        locked_key = locked.get("key")
        locked_value = locked.get("value")
    decision = None
    if guard and gating_upload_id:
        decision = guard.require_for_upload(str(gating_upload_id))
        _record_identifier_check(context, decision)
        if decision.status != "ok":
            structured_log(
                "mcp",
                "identifier.denied",
                {
                    "tool": "read_document",
                    "upload": gating_upload_id,
                    "required": list(decision.required_keys),
                    "provided": list(decision.provided_keys),
                },
                context={"business": conversation.business_profile_id},
                logger_obj=logger,
                level=logging.WARNING,
            )
            _record_identifier_event_once(
                context,
                business_profile=conversation.business_profile,
                decision=decision,
                tool="read_document",
                conversation=conversation,
                upload_ids=[str(gating_upload_id)],
            )
            error_code = "identifier_required"
            return {
                "tool": "read_document",
                "document_id": document_id,
                "status": decision.status,
                "error": error_code,
                "error_code": error_code,
                "snippets": [],
                "identifier_gate": decision.as_dict(),
                "required_identifiers": list(decision.required_keys),
                "provided_identifiers": list(decision.provided_keys),
                "hint": decision.hint,
                "llm_hint": decision.hint,
            }

    
    pages_arg = arguments.get("pages")
    page_arg = arguments.get("page")
    offset_value = arguments.get("offset")
    explicit_page_request = bool(pages_arg or page_arg is not None or offset_value is not None)

    agentic_mode = bool(arguments.get("agentic_mode"))
    raw_read_max_chars = arguments.get("max_chars")
    read_max_chars: int | None = None
    if raw_read_max_chars is not None:
        try:
            read_max_chars = max(200, int(raw_read_max_chars))
        except (TypeError, ValueError):
            read_max_chars = None

    service = _knowledge_service()
    upload_source = chunk_record.upload if chunk_record else upload_record
    knowledge_entry = _match_knowledge_entry(context, [str(identifier), str(gating_upload_id)])
    throttle_notice: dict[str, object] | None = None
    downgraded = False

    snippets: list[Any] = []
    mode: str | None = None
    token_budget: int | None = None
    neighbor_window = 1
    page_indices: list[int] = []
    used_table_override = False

    if chunk_record and agentic_mode and not explicit_page_request:
        table_snippets = _agentic_table_chunk_snippets(
            chunk_record=chunk_record,
            business=business,
            max_chars=read_max_chars,
            service=service,
        )
        if table_snippets:
            context.reserve_chunk_reads(1)
            snippets = list(table_snippets)
            mode = "excerpt"
            neighbor_window = 0
            page_indices = []
            used_table_override = True

    if not used_table_override:
        # Resolve pages to read
        if isinstance(pages_arg, list):
            for p in pages_arg:
                try:
                    page_indices.append(max(1, int(p)))
                except (TypeError, ValueError):
                    pass

        if not page_indices and page_arg is not None:
            try:
                page_indices.append(max(1, int(page_arg)))
            except (TypeError, ValueError):
                pass

        if not page_indices and offset_value is not None:
            try:
                offset_int = int(offset_value)
                page_indices.append(max(1, offset_int + 1))
            except (TypeError, ValueError):
                pass

        if not page_indices:
            page_indices = [1]

        # Deduplicate and sort
        page_indices = sorted(list(set(page_indices)))[:5]  # Cap at 5 pages per call to prevent abuse

        raw_mode = _coerce_str(arguments.get("mode")).strip().lower()
        mode = raw_mode if raw_mode in {"excerpt", "full_page"} else None

        raw_budget = arguments.get("token_budget")
        if raw_budget is not None:
            try:
                token_budget = max(0, int(raw_budget))
            except (TypeError, ValueError):
                token_budget = None

        neighbor = arguments.get("neighbor_window") or arguments.get("chunk_neighbor")
        try:
            neighbor_window = int(neighbor)
        except (TypeError, ValueError):
            neighbor_window = 1
        neighbor_window = max(0, min(3, neighbor_window))

        # Check if this is a table chunk from a PDF - these need full content, not summaries
        is_pdf_table_chunk = False
        if chunk_record:
            chunk_meta = chunk_record.metadata if isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
            if chunk_meta.get("is_table_chunk") or chunk_meta.get("table_chunk_role"):
                upload = chunk_record.upload
                ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
                format_hint = str(ingestion_meta.get("format") or "").strip().lower()
                # Only auto-upgrade for PDFs (not native tabular formats which use query_dataset)
                if format_hint not in {"csv", "tsv", "xls", "xlsx", "jsonl"}:
                    is_pdf_table_chunk = True

        if mode is None:
            # Auto-upgrade to full_page for PDF table chunks to get complete table data
            if is_pdf_table_chunk:
                mode = "full_page"
                structured_log(
                    "mcp",
                    "read_document.table_chunk_upgrade",
                    {"chunk_id": str(identifier), "reason": "pdf_table_chunk"},
                    context={"business": business.id, "conversation": conversation.id},
                    logger_obj=logger,
                )
            else:
                prefer_full_page = _detect_full_page_intent(
                    conversation,
                    None,
                    document_entry=knowledge_entry,
                    upload=upload_source,
                )
                mode = "full_page" if (prefer_full_page and _budget_allows_full_page(context, business_profile=business, service=service)) else "excerpt"

        if mode == "full_page":
            throttle_notice = _maybe_throttle_full_page(context, business, service)
            if throttle_notice:
                mode = "excerpt"
                throttle_notice["downgraded_from"] = "full_page"
                downgraded = True
        throttle_reason = throttle_notice.get("reason") if throttle_notice else None
        structured_log(
            "mcp",
            "read_document.throttle",
            {"reason": throttle_reason, "notice": throttle_notice} if throttle_notice else {"reason": throttle_reason},
            context={"business": business.id},
            logger_obj=logger,
        )

        # Enforce per-turn chunk budget only when actually loading the window.
        # Reserve for each page
        context.reserve_chunk_reads(len(page_indices))
        context.reserve_chunk_pages(len(page_indices))

        for page_idx in page_indices:
            # Check cache for each page
            # Note: We only check cache if single page requested to keep logic simple,
            # or we could loop interaction. For now, simplistic cache check for first page only
            # is too weak. But fixing cache for multi-page is complex.
            # We will skip cache read for multi-page for now or just proceed.

            if chunk_record:
                snippets.extend(
                    service.load_page_window(
                        business_profile=business,
                        chunk_id=identifier,
                        page_index=page_idx,
                        neighbor=neighbor_window,
                        mode=mode,
                        token_budget=token_budget,
                    )
                )
            else:
                snippets.extend(
                    service.load_page_window(
                        business_profile=business,
                        upload_id=upload_record.id,  # type: ignore
                        page_index=page_idx,
                        neighbor=neighbor_window,
                        mode=mode,
                        token_budget=token_budget,
                    )
                )


    snippet_payloads = _serialize_snippets(snippets)
    # Enforce locked identifier match for identity-bound fields; drop snippets that don't match.
    if locked_key and locked_value:
        locked_val_norm = str(locked_value).strip().lower()
        filtered = []
        for payload in snippet_payloads:
            identifiers = payload.get("identifiers") if isinstance(payload, Mapping) else None
            if identifiers and isinstance(identifiers, Mapping):
                candidate = identifiers.get(locked_key)
                if candidate and str(candidate).strip().lower() != locked_val_norm:
                    continue
            filtered.append(payload)
        snippet_payloads = filtered
    snippet_payloads = _sanitize_snippet_payloads_for_prompt(snippet_payloads, conversation=conversation)
    knowledge_reads: list[dict[str, object]] = []
    for payload in snippet_payloads:
        context.add_knowledge_result(payload)
        read_entry = {
            "id": payload.get("id"),
            "label": payload.get("public_label") or payload.get("title") or "Knowledge",
            "page": payload.get("page_number"),
            "mode": payload.get("page_mode"),
        }
        knowledge_reads.append(read_entry)
        context.add_knowledge_read(read_entry)
    if guard and decision and decision.status == "ok":
        applied_filter = decision.as_dict()
        applied_filter["tool"] = "read_document"
        context.identifier_filters.append(applied_filter)
        _record_identifier_event_once(
            context,
            business_profile=conversation.business_profile,
            decision=decision,
            tool="read_document",
            conversation=conversation,
            upload_ids=[str(gating_upload_id)] if gating_upload_id else None,
        )

    ingestion_warnings = _build_ingestion_warnings(snippet_payloads, knowledge_reads)
    for warning in ingestion_warnings:
        context.add_ingestion_warning(warning)

    metrics = _log_tool_metrics(
        tool="read_document",
        conversation=conversation,
        snippets=snippet_payloads,
        extra={
            "document_id": document_id,
            "chunk_reads_used": context.chunk_reads_used,
            "chunk_pages_used": context.chunk_pages_used,
            "mode": mode,
            "neighbor": neighbor_window,
            "pages": page_indices,
            "token_budget": token_budget,
            "snippet_count": len(snippet_payloads),
        },
    )
    try:
        context.reserve_characters(int(metrics.get("char_count", 0)))
    except CharacterBudgetExceeded as exc:
        return {
            "tool": "read_document",
            "document_id": document_id,
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "snippets": [],
            "throttle_notice": {"type": "prompt_budget", "message": str(exc)},
            "hint": "Prompt budget exceeded. Ask a narrower question or request fewer pages.",
        }
    _log_snippet_payloads(
        tool="read_document",
        conversation=conversation,
        snippet_payloads=snippet_payloads,
        meta={
            "document_id": document_id,
            "mode": mode,
            "pages": page_indices,
            "neighbor": neighbor_window,
            "token_budget": token_budget,
        },
    )

    payload = {
        "tool": "read_document",
        "document_id": document_id,
        "pages": page_indices,
        "mode": mode,
        "mode_downgraded": downgraded,
        "token_budget": token_budget,
        "status": "ok",
        "snippets": snippet_payloads,
        "knowledge_reads": knowledge_reads,
        "ingestion_warnings": ingestion_warnings,
        "throttle_notice": throttle_notice,
    }

    return payload


def _list_tables_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    query_input = _coerce_str(arguments.get("query")).strip()
    raw_limit = arguments.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else 5
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(10, limit))

    window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
    try:
        calls_per_minute = int(getattr(settings, "MCP_LIST_TABLES_CALLS_PER_MINUTE", 120) or 0)
    except (TypeError, ValueError):
        calls_per_minute = 120
    calls_per_minute = 0 if calls_per_minute < 0 else calls_per_minute
    try:
        enforce_tool_rate_limit(
            business_profile=conversation.business_profile,
            tool="list_tables",
            rate_limit=ToolRateLimit(
                calls_per_minute=None if calls_per_minute <= 0 else calls_per_minute,
                window_seconds=window_seconds,
                scope="business",
            ),
        )
    except ToolRateLimitExceeded as exc:
        return {
            "tool": "list_tables",
            "status": "throttled",
            "error": "rate_limited",
            "error_code": "rate_limited",
            "results": [],
            "throttle_notice": {"type": "rate_limited", "message": str(exc)},
            "hint": str(exc),
        }

    tabular_formats = ("csv", "tsv", "xls", "xlsx", "jsonl")
    uploads_qs = (
        apply_customer_visible_uploads(
            KnowledgeUpload.objects.filter(
                business_profile=conversation.business_profile,
                status=KnowledgeStatus.ACTIVE,
            )
        )
        .filter(
            models.Q(ingestion_metadata__dataset__enabled=True)
            | models.Q(ingestion_metadata__format__in=tabular_formats)
        )
        .filter(tables__isnull=False)
        .only(
            "id",
            "display_name",
            "source_name",
            "description",
            "slug",
            "external_reference",
            "updated_at",
            "status",
        )
        .select_related(None)
        .order_by("-updated_at")
        .distinct()
    )
    agent_scope = _agent_knowledge_scope(conversation, context)
    uploads_qs = _apply_agent_scope_to_upload_queryset(uploads_qs, agent_scope)
    if query_input:
        uploads_qs = uploads_qs.filter(
            models.Q(display_name__icontains=query_input)
            | models.Q(source_name__icontains=query_input)
            | models.Q(description__icontains=query_input)
            | models.Q(tables__title__icontains=query_input)
            | models.Q(tables__section_heading__icontains=query_input)
            | models.Q(tables__metadata__sheet_name__icontains=query_input)
        )

    table_prefetch = Prefetch(
        "tables",
        queryset=KnowledgeUploadTable.objects.only(
            "id",
            "upload_id",
            "order_index",
            "title",
            "section_heading",
            "metadata",
            "column_schema",
        )
        .order_by("order_index")[:TABLE_LIST_PREVIEW_LIMIT],
        to_attr="table_previews",
    )
    uploads = list(uploads_qs.prefetch_related(table_prefetch)[:limit])
    results: list[dict[str, object]] = []
    for upload in uploads:
        tables = list(getattr(upload, "table_previews", []))
        if not tables:
            continue
        seen_sheet_names: set[str] = set()
        sheet_names: list[str] = []
        preview_tables: list[dict[str, object]] = []
        for table in tables[:TABLE_LIST_PREVIEW_LIMIT]:
            metadata = table.metadata if isinstance(table.metadata, Mapping) else {}
            sheet_name_raw = metadata.get("sheet_name") if isinstance(metadata, Mapping) else None
            normalized_sheet = None
            if isinstance(sheet_name_raw, str) and sheet_name_raw.strip():
                normalized_sheet = sheet_name_raw.strip()
                if normalized_sheet not in seen_sheet_names:
                    sheet_names.append(normalized_sheet)
                    seen_sheet_names.add(normalized_sheet)
            column_schema = table.column_schema if isinstance(table.column_schema, (list, tuple)) else []
            title = table.title or table.section_heading
            preview_tables.append(
                {
                    "table_id": str(table.id),
                    "order_index": table.order_index,
                    "title": title or f"Table {table.order_index or 1}",
                    "sheet_name": normalized_sheet,
                    "column_count": len(column_schema),
                }
            )
        display_label = (
            upload.display_name
            or upload.source_name
            or upload.external_reference
            or upload.slug
            or str(upload.id)
        )
        results.append(
            {
                "upload_id": str(upload.id),
                "document_id": str(upload.id),
                "display_name": display_label,
                "table_count": len(tables),
                "sheet_names": sheet_names,
                "tables": preview_tables,
                "updated_at": upload.updated_at.isoformat() if upload.updated_at else None,
            }
        )
    structured_log(
        "mcp",
        "table.list",
        {
            **_log_safe_text_fields("query", query_input or None),
            "limit": limit,
            "matched_uploads": len(results),
        },
        context={
            "business": conversation.business_profile_id,
            "conversation": conversation.id,
        },
        logger_obj=logger,
    )
    status = "ok" if results else "not_found"
    return {
        "tool": "list_tables",
        "status": status,
        "query": query_input or None,
        "limit": limit,
        "results": results,
        "hint": None if results else "No table uploads match this query.",
    }


def _get_document_structure_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Return the complete structure of a document for LLM-driven enumeration.
    
    NOTE: In agentic mode (rag_agentic_mode=true), this tool is DEPRECATED.
    Use search(query) → read(ids) workflow instead.
    
    This tool enables the LLM to see ALL items in a document before formulating
    a response to "list all" or comprehensive queries. It returns:
    - Table names/titles
    - Column headers per table
    - Row labels (first column values) as item identifiers
    - Item counts per table
    
    Returns:
        Mapping with document info, tables structure, and row_labels for enumeration.
    """
    # Check if agentic mode is enabled - this tool is deprecated in agentic mode
    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
    if feature_state.rag_agentic_mode:
        structured_log(
            "mcp",
            "tool.deprecated_in_agentic_mode",
            {"tool": "get_document_structure"},
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
            level=logging.WARNING,
        )
        return {
            "tool": "get_document_structure",
            "status": "deprecated",
            "error": "This tool is not needed in agentic mode. Use search() then read(ids) to get document content.",
            "hint": "Search returns metadata. Call read(ids) with IDs from search results to get full content.",
        }
    
    document_id_raw = _coerce_str(arguments.get("document_id")).strip()
    table_id_raw = _coerce_str(arguments.get("table_id")).strip() or None
    include_row_labels = arguments.get("include_row_labels")
    if include_row_labels is None:
        include_row_labels = True
    else:
        include_row_labels = bool(include_row_labels)
    
    # Validate document_id
    if not document_id_raw:
        return {
            "tool": "get_document_structure",
            "status": "error",
            "error": "missing_document_id",
            "error_code": "missing_document_id",
            "hint": "Provide document_id from search_knowledge snippets[].read_hint.document_id.",
        }
    
    try:
        document_uuid = uuid.UUID(document_id_raw)
    except (TypeError, ValueError):
        return {
            "tool": "get_document_structure",
            "status": "error",
            "error": "invalid_document_id",
            "error_code": "invalid_document_id",
            "hint": "document_id must be a valid UUID. Use snippets[].read_hint.document_id from search_knowledge.",
        }
    
    # Rate limiting
    window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
    try:
        calls_per_minute = int(getattr(settings, "MCP_DOC_STRUCTURE_CALLS_PER_MINUTE", 30) or 0)
    except (TypeError, ValueError):
        calls_per_minute = 30
    calls_per_minute = 0 if calls_per_minute < 0 else calls_per_minute
    try:
        enforce_tool_rate_limit(
            business_profile=conversation.business_profile,
            tool="get_document_structure",
            rate_limit=ToolRateLimit(
                calls_per_minute=None if calls_per_minute <= 0 else calls_per_minute,
                window_seconds=window_seconds,
                scope="business",
            ),
        )
    except ToolRateLimitExceeded as exc:
        return {
            "tool": "get_document_structure",
            "status": "throttled",
            "error": "rate_limited",
            "error_code": "rate_limited",
            "hint": str(exc),
        }
    
    # Fetch the upload
    upload_qs = apply_customer_visible_uploads(
        KnowledgeUpload.objects.filter(
            business_profile=conversation.business_profile,
            status=KnowledgeStatus.ACTIVE,
            id=document_uuid,
        )
    ).only(
        "id",
        "display_name",
        "source_name",
        "description",
        "slug",
        "external_reference",
        "ingestion_metadata",
    )
    
    # Apply agent scope
    agent_scope = _agent_knowledge_scope(conversation, context)
    upload_qs = _apply_agent_scope_to_upload_queryset(upload_qs, agent_scope)
    
    upload = upload_qs.first()
    if not upload:
        return {
            "tool": "get_document_structure",
            "status": "not_found",
            "error": "document_not_found",
            "error_code": "document_not_found",
            "document_id": document_id_raw,
            "hint": "Document not found or not accessible. Use document_id from search_knowledge results.",
        }
    
    # Fetch tables for this upload
    tables_qs = KnowledgeUploadTable.objects.filter(
        upload=upload,
    ).select_related("page").only(
        "id",
        "order_index",
        "title",
        "section_heading",
        "column_schema",
        "metadata",
        "page__page_number",
    ).order_by("order_index")
    
    if table_id_raw:
        try:
            table_uuid = uuid.UUID(table_id_raw)
            tables_qs = tables_qs.filter(id=table_uuid)
        except (TypeError, ValueError):
            pass  # Ignore invalid table_id, just don't filter
    
    tables = list(tables_qs[:50])  # Cap at 50 tables per document
    
    if not tables:
        display_label = (
            upload.display_name
            or upload.source_name
            or upload.external_reference
            or upload.slug
            or str(upload.id)
        )
        return {
            "tool": "get_document_structure",
            "status": "ok",
            "document": {
                "document_id": str(upload.id),
                "display_name": display_label,
            },
            "tables": [],
            "total_tables": 0,
            "total_items": 0,
            "hint": "This document has no tables. Use read_document for text content.",
        }
    
    # Build structure for each table
    table_structures: list[dict[str, object]] = []
    total_items = 0
    ingestion_meta = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, Mapping) else {}
    page_count = ingestion_meta.get("page_count")
    try:
        page_count = int(page_count) if page_count is not None else None
    except (TypeError, ValueError):
        page_count = None
    
    for table in tables:
        column_schema = table.column_schema if isinstance(table.column_schema, (list, tuple)) else []
        columns: list[str] = []
        for col in column_schema:
            if isinstance(col, str):
                columns.append(col)
            elif isinstance(col, Mapping):
                col_name = col.get("name") or col.get("header") or col.get("column")
                if col_name:
                    columns.append(str(col_name))
        
        title = table.title or table.section_heading or f"Table {table.order_index or 1}"
        metadata = table.metadata if isinstance(table.metadata, Mapping) else {}
        sheet_name = metadata.get("sheet_name") if isinstance(metadata.get("sheet_name"), str) else None
        
        page_number: int | None = None
        if table.page and getattr(table.page, "page_number", None):
            try:
                page_number = int(table.page.page_number)
            except (TypeError, ValueError):
                page_number = None
        if page_number is None:
            raw_page = metadata.get("page_number") or metadata.get("page") or metadata.get("page_index")
            try:
                page_number = int(raw_page) if raw_page is not None else None
            except (TypeError, ValueError):
                page_number = None

        # Get row labels (first column values) for enumeration
        row_labels: list[str] = []
        row_count = 0
        
        rows_qs = KnowledgeUploadTableRow.objects.filter(table=table)
        row_count = rows_qs.count()
        max_row_labels = 200

        if include_row_labels and row_count:
            # Fetch rows and their first-column cell values (row labels).
            rows = list(rows_qs.only("id", "row_index", "page_number").order_by("row_index")[:max_row_labels])
            row_ids = [row.id for row in rows]
            cells_qs = KnowledgeUploadTableCell.objects.filter(
                row_id__in=row_ids,
                column_index=0,
            ).values("row_id", "raw_text")

            cell_map: dict[uuid.UUID, str] = {}
            for cell in cells_qs:
                value = cell.get("raw_text")
                if isinstance(value, str) and value.strip():
                    cell_map[cell["row_id"]] = value.strip()

            # Build row_labels in row order
            for row in rows:
                label = cell_map.get(row.id)
                if label:
                    row_labels.append(label)
                if page_number is None and row.page_number:
                    try:
                        page_number = int(row.page_number)
                    except (TypeError, ValueError):
                        page_number = None
        
        total_items += row_count
        
        table_structure: dict[str, object] = {
            "table_id": str(table.id),
            "title": title,
            "order_index": table.order_index,
            "columns": columns,
            "column_count": len(columns),
            "row_count": row_count,
        }
        if page_number:
            table_structure["page_number"] = page_number
        
        if sheet_name:
            table_structure["sheet_name"] = sheet_name
        
        if include_row_labels and row_labels:
            table_structure["row_labels"] = row_labels
            table_structure["labels_shown"] = len(row_labels)
            if row_count > max_row_labels and len(row_labels) < row_count:
                table_structure["labels_truncated"] = True
        
        table_structures.append(table_structure)
    
    display_label = (
        upload.display_name
        or upload.source_name
        or upload.external_reference
        or upload.slug
        or str(upload.id)
    )
    
    structured_log(
        "mcp",
        "document.structure",
        {
            "document_id": str(upload.id),
            "table_count": len(table_structures),
            "total_items": total_items,
            "include_row_labels": include_row_labels,
            "page_count": page_count,
        },
        context={
            "business": conversation.business_profile_id,
            "conversation": conversation.id,
        },
        logger_obj=logger,
    )
    
    return {
        "tool": "get_document_structure",
        "status": "ok",
        "document": {
            "document_id": str(upload.id),
            "display_name": display_label,
            "page_count": page_count,
        },
        "tables": table_structures,
        "total_tables": len(table_structures),
        "total_items": total_items,
    }


TABLE_LIST_PREVIEW_LIMIT = 8


def _table_row_cache_key(
    upload: KnowledgeUpload,
    *,
    table_index: int | None,
    sheet_name: str | None,
    match_column: str | None,
    match_values: Sequence[str] | None,
    query: str | None,
) -> str:
    def _canonical_column(value: str | None) -> str | None:
        normalized = _normalize_column_name(value)
        return normalized or None

    def _canonical_text(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        trimmed = value.strip().lower()
        return trimmed or None

    canonical_values = [
        _canonical_column(candidate)
        for candidate in (match_values or [])
        if candidate is not None
    ]
    fingerprint = json.dumps(
        {
            "table_index": table_index,
            "sheet_name": _canonical_text(sheet_name),
            "match_column": _canonical_column(match_column),
            "match_values": [value for value in canonical_values if value],
            "query": _canonical_text(query),
        },
        sort_keys=True,
    )
    return f"{upload.id}:{fingerprint}"


def _load_table_rows_for_cache(
    *,
    conversation: Conversation,
    upload: KnowledgeUpload,
    table_index: int | None = None,
    sheet_name: str | None = None,
    match_column: str | None = None,
    match_values: Sequence[str] | None = None,
    query: str | None = None,
    max_rows: int | None = None,
) -> list[dict[str, object]]:
    rows_qs = KnowledgeUploadTableRow.objects.filter(
        table__upload=upload,
        table__upload__business_profile=conversation.business_profile,
    )
    if table_index is not None:
        rows_qs = rows_qs.filter(table__order_index=table_index)
    if sheet_name:
        normalized_sheet = sheet_name.strip()
        rows_qs = rows_qs.filter(
            models.Q(table__metadata__sheet_name__iexact=normalized_sheet)
            | models.Q(table__title__iexact=normalized_sheet)
            | models.Q(table__section_heading__iexact=normalized_sheet)
        )
    if match_column:
        normalized_column = match_column.strip()
        rows_qs = rows_qs.filter(cells__column_key__iexact=normalized_column)
    if query:
        normalized_query = query.strip()
        rows_qs = rows_qs.filter(
            models.Q(raw_text__icontains=normalized_query) | models.Q(cells__raw_text__icontains=normalized_query)
        )
    if match_values:
        normalized_values = [value for value in match_values if value]
        if normalized_values:
            if match_column and _column_suggests_identifier(match_column):
                patterns: list[str] = []
                for value in normalized_values[:12]:
                    cleaned = _normalize_identifier_value(value)
                    if not cleaned:
                        continue
                    if len(cleaned) > 64:
                        cleaned = cleaned[:64]
                    escaped = [re.escape(ch) for ch in cleaned]
                    if not escaped:
                        continue
                    patterns.append(r"\s*".join(escaped))
                if patterns:
                    rows_qs = rows_qs.filter(cells__raw_text__iregex="|".join(patterns))
                else:
                    rows_qs = rows_qs.filter(
                        cells__raw_text__iregex="|".join(re.escape(value) for value in normalized_values)
                    )
            else:
                rows_qs = rows_qs.filter(cells__raw_text__iregex="|".join(re.escape(value) for value in normalized_values))
    rows_qs = rows_qs.select_related("table").only(
        "id",
        "row_index",
        "raw_text",
        "table__order_index",
        "table__title",
        "table__section_heading",
        "table__metadata",
    )
    cell_queryset = KnowledgeUploadTableCell.objects.only(
        "id",
        "row_id",
        "column_index",
        "column_key",
        "raw_text",
    ).order_by("column_index")
    rows_qs = rows_qs.prefetch_related(Prefetch("cells", queryset=cell_queryset))
    max_candidates = max_rows or 400
    payloads: list[dict[str, object]] = []
    for row in rows_qs.order_by("table__order_index", "row_index")[: max_candidates]:
        payloads.append(_serialize_table_row_for_cache(row))
    return payloads


def _serialize_table_row_for_cache(row: KnowledgeUploadTableRow) -> dict[str, object]:
    table = getattr(row, "table", None)
    table_metadata = getattr(table, "metadata", {}) if table else {}
    if not isinstance(table_metadata, Mapping):
        table_metadata = {}
    sheet_name = table_metadata.get("sheet_name") if isinstance(table_metadata, Mapping) else None
    cells = sorted(row.cells.all(), key=lambda c: c.column_index)
    cell_payloads: list[dict[str, object]] = []
    for cell in cells:
        column_label = cell.column_key or f"column_{cell.column_index + 1}"
        normalized_label = _normalize_column_name(column_label)
        raw_text = cell.raw_text or ""
        numeric_value = _parse_numeric_value(raw_text)
        total_priority = _total_column_priority(column_label)
        cell_payloads.append(
            {
                "column": column_label,
                "column_index": cell.column_index,
                "normalized": normalized_label,
                "raw_text": raw_text,
                "normalized_value": _normalize_column_name(raw_text),
                "numeric": numeric_value,
                "total_priority": total_priority,
                "is_total_column": total_priority > 0,
            }
        )
    return {
        "row_index": row.row_index,
        "table_order_index": table.order_index if table else None,
        "table_title": table.title if table else None,
        "table_section_heading": table.section_heading if table else None,
        "sheet_name": sheet_name,
        "row_text": row.raw_text or "",
        "cells": cell_payloads,
    }


def _row_matches_sheet_hint(row_payload: Mapping[str, object], sheet_name_input: str) -> bool:
    if not sheet_name_input:
        return True
    target = sheet_name_input.strip().lower()
    for candidate in (
        row_payload.get("sheet_name"),
        row_payload.get("table_title"),
        row_payload.get("table_section_heading"),
    ):
        if isinstance(candidate, str) and candidate.strip().lower() == target:
            return True
    return False


def _table_aggregate_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    start = time.perf_counter()
    raw_id = _coerce_str(arguments.get("document_id")).strip()
    if not raw_id:
        return {
            "tool": "table_aggregate",
            "status": "error",
            "error": "document_id is required",
        }
    try:
        identifier = uuid.UUID(raw_id)
    except (TypeError, ValueError):
        return {
            "tool": "table_aggregate",
            "status": "error",
            "error": "document_id must be a valid UUID",
        }
    upload = apply_customer_visible_uploads(
        KnowledgeUpload.objects.filter(
            id=identifier,
            business_profile=conversation.business_profile,
            status=KnowledgeStatus.ACTIVE,
        )
    ).first()
    if not upload:
        chunk = (
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    id=identifier,
                    business_profile=conversation.business_profile,
                    upload__status=KnowledgeStatus.ACTIVE,
                )
            )
            .select_related("upload")
            .first()
        )
        upload = chunk.upload if chunk else None
    if not upload:
        return {
            "tool": "table_aggregate",
            "status": "not_found",
            "error": "document not found for this business",
        }

    agent_scope = _agent_knowledge_scope(conversation, context)
    if upload.id and not _agent_scope_allows_upload(scope=agent_scope, conversation=conversation, upload_id=upload.id):
        return {
            "tool": "table_aggregate",
            "document_id": str(upload.id),
            "status": "constraint_error",
            "error": "forbidden_document",
            "error_code": "forbidden_document",
            "rows": [],
            "match_count": 0,
            "hint": "This agent is not permitted to access that document.",
        }

    guard = _identifier_guard(context, conversation)
    decision = None
    if guard and upload.id:
        decision = guard.require_for_upload(str(upload.id))
        _record_identifier_check(context, decision)
        if decision.status != "ok":
            structured_log(
                "mcp",
                "identifier.denied",
                {
                    "tool": "table_aggregate",
                    "upload": str(upload.id),
                    "required": list(decision.required_keys),
                    "provided": list(decision.provided_keys),
                },
                context={"business": conversation.business_profile_id},
                logger_obj=logger,
                level=logging.WARNING,
            )
            _record_identifier_event_once(
                context,
                business_profile=conversation.business_profile,
                decision=decision,
                tool="table_aggregate",
                conversation=conversation,
                upload_ids=[str(upload.id)],
            )
            error_code = "identifier_required"
            return {
                "tool": "table_aggregate",
                "document_id": str(upload.id),
                "status": decision.status,
                "error": error_code,
                "error_code": error_code,
                "rows": [],
                "match_count": 0,
                "identifier_gate": decision.as_dict(),
                "required_identifiers": list(decision.required_keys),
                "provided_identifiers": list(decision.provided_keys),
                "hint": decision.hint,
                "llm_hint": decision.hint,
            }

    tabular_limits = resolve_tabular_tool_limits(business_profile=conversation.business_profile, upload=upload)
    tool_limits = tabular_limits.table_aggregate
    try:
        enforce_tool_rate_limit(
            business_profile=conversation.business_profile,
            tool="table_aggregate",
            rate_limit=tool_limits.rate_limit,
            upload=upload,
        )
    except ToolRateLimitExceeded as exc:
        return {
            "tool": "table_aggregate",
            "document_id": str(upload.id),
            "status": "throttled",
            "error": "rate_limited",
            "error_code": "rate_limited",
            "rows": [],
            "match_count": 0,
            "throttle_notice": {"type": "rate_limited", "message": str(exc)},
            "hint": str(exc),
        }

    mode_raw = _coerce_str(arguments.get("mode")).strip().lower()
    value_column_input = _coerce_str(arguments.get("value_column")).strip()
    value_column_raw = _normalize_column_name(value_column_input)
    mode = mode_raw or ("column_sum" if value_column_raw else "row_total")
    if mode not in {"row_total", "column_sum"}:
        mode = "row_total"
    if mode == "column_sum" and not value_column_raw:
        return {
            "tool": "table_aggregate",
            "status": "error",
            "error": "value_column is required when mode=column_sum",
        }
    match_column_input = _coerce_str(arguments.get("match_column")).strip()
    match_value_input = _coerce_str(arguments.get("match_value")).strip()
    raw_match_values = arguments.get("match_values")
    match_column = _normalize_column_name(match_column_input)
    normalized_match_values: list[str] = []
    identifier_match_values: list[str] = []
    if isinstance(raw_match_values, (list, tuple)):
        for candidate in raw_match_values:
            normalized = _normalize_column_name(candidate)
            if normalized:
                normalized_match_values.append(normalized)
            normalized_identifier = _normalize_identifier_value(candidate)
            if normalized_identifier:
                identifier_match_values.append(normalized_identifier)
    match_value = _normalize_column_name(match_value_input)
    if match_value and match_value not in normalized_match_values:
        normalized_match_values.append(match_value)
    match_value_identifier = _normalize_identifier_value(match_value_input)
    if match_value_identifier and match_value_identifier not in identifier_match_values:
        identifier_match_values.append(match_value_identifier)
    if len(normalized_match_values) > 50:
        normalized_match_values = normalized_match_values[:50]
    if len(identifier_match_values) > 50:
        identifier_match_values = identifier_match_values[:50]
    query_input = _coerce_str(arguments.get("query")).strip()
    query = _normalize_column_name(query_input)
    if not query and not match_column:
        query = ""
    try:
        table_index = int(arguments.get("table_order_index"))
    except (TypeError, ValueError):
        table_index = None
    sheet_name_input = _coerce_str(arguments.get("sheet_name")).strip()
    try:
        row_limit = int(arguments.get("max_rows") or 50)
    except (TypeError, ValueError):
        row_limit = 50
    row_limit = max(1, min(200, row_limit))
    row_limit = min(row_limit, int(tool_limits.max_rows_returned))
    raw_columns = arguments.get("columns")
    column_filters: list[str] = []
    if isinstance(raw_columns, (list, tuple)):
        for entry in raw_columns:
            candidate = _coerce_str(entry).strip()
            if candidate:
                column_filters.append(candidate)
    if len(column_filters) > int(tool_limits.max_columns_returned):
        column_filters = column_filters[: int(tool_limits.max_columns_returned)]
    normalized_column_filters = { _normalize_column_name(value) for value in column_filters if _normalize_column_name(value) }
    match_policy = "contains"
    if match_column and normalized_match_values and _column_suggests_identifier(match_column_input):
        match_policy = "eq"
    allowed_identifier_values: set[str] = set()
    if match_policy == "eq" and match_column and normalized_match_values:
        allowed_identifier_values = set(
            identifier_match_values
            or [_normalize_identifier_value(v) for v in normalized_match_values if _normalize_identifier_value(v)]
        )
    prompt_cells_cap = max(4, int(getattr(settings, "MCP_PROMPT_TABLE_MAX_CELLS", 12) or 12))
    prompt_cells_exact = max(4, int(getattr(settings, "MCP_PROMPT_TABLE_MAX_CELLS_EXACT", 60) or 60))
    has_exact_identifier_filter = bool(match_policy == "eq" and match_column and normalized_match_values)
    row_cell_cap = max(prompt_cells_cap, prompt_cells_exact) if has_exact_identifier_filter else prompt_cells_cap

    def _clip_text(value: object, limit: int) -> str:
        text = _coerce_str(value)
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    total_value = 0.0
    matched_rows: list[dict[str, object]] = []
    cache = getattr(context, "table_row_cache", None)
    if cache is None:
        cache = {}
        context.table_row_cache = cache
    has_row_filters = bool(match_column_input or normalized_match_values or query_input)
    overscan_factor = 2 if has_row_filters else 3
    max_candidates = max(row_limit * overscan_factor, row_limit + 20)
    cache_key = _table_row_cache_key(
        upload,
        table_index=table_index,
        sheet_name=sheet_name_input,
        match_column=match_column,
        match_values=normalized_match_values,
        query=query_input,
    )
    cached_rows = cache.get(cache_key)
    cache_hit = cached_rows is not None
    if cached_rows is None:
        cached_rows = _load_table_rows_for_cache(
            conversation=conversation,
            upload=upload,
            table_index=table_index,
            sheet_name=sheet_name_input,
            match_column=match_column_input,
            match_values=normalized_match_values,
            query=query_input,
            max_rows=max_candidates,
        )
        cache[cache_key] = cached_rows
    evaluated_rows = len(cached_rows)

    for row_payload in cached_rows:
        if table_index is not None:
            row_table_index = row_payload.get("table_order_index")
            try:
                row_table_idx_int = int(row_table_index)
            except (TypeError, ValueError):
                continue
            if row_table_idx_int != table_index:
                continue
        if sheet_name_input:
            if not _row_matches_sheet_hint(row_payload, sheet_name_input):
                continue
        cells = list(row_payload.get("cells") or ())
        column_map = {
            cell.get("normalized"): cell
            for cell in cells
            if cell.get("normalized")
        }
        row_matches = True
        if match_column and normalized_match_values:
            candidate = column_map.get(match_column)
            if match_policy == "eq":
                candidate_value = (
                    _normalize_identifier_value(candidate.get("raw_text"))
                    if isinstance(candidate, Mapping)
                    else ""
                )
                if not candidate_value or candidate_value not in allowed_identifier_values:
                    row_matches = False
            else:
                candidate_value = candidate.get("normalized_value") if isinstance(candidate, Mapping) else None
                if not candidate_value or not any(value and value in candidate_value for value in normalized_match_values):
                    row_matches = False
        elif query:
            row_text = str(row_payload.get("row_text") or "")
            cell_text = " ".join(str(cell.get("raw_text") or "") for cell in cells)
            haystack = f"{row_text} {cell_text}".lower()
            if query not in haystack:
                row_matches = False
        if not row_matches:
            continue

        preview_cells: list[dict[str, object]] = []
        preview_column_norms: set[str] = set()
        contributions: list[dict[str, object]] = []
        value_numeric: float | None = None
        value_display: str | None = None
        match_cell_index: int | None = None
        for cell in cells:
            column_label = cell.get("column") or f"column_{(cell.get('column_index') or 0) + 1}"
            cell_value = cell.get("raw_text") or ""
            normalized_label = cell.get("normalized") or _normalize_column_name(column_label)
            if match_column and normalized_label == match_column:
                try:
                    match_cell_index = int(cell.get("column_index"))
                except (TypeError, ValueError):
                    match_cell_index = None
            is_total_col = bool(cell.get("is_total_column"))
            include_in_preview = True
            if normalized_column_filters:
                include_in_preview = normalized_label in normalized_column_filters
            elif is_total_col:
                include_in_preview = False
            if include_in_preview and len(preview_cells) < row_cell_cap:
                preview_cells.append(
                    {
                        "column": _clip_text(column_label, 80),
                        "value": _clip_text(cell_value, 160),
                    }
                )
                if normalized_label:
                    preview_column_norms.add(normalized_label)
            numeric_candidate = cell.get("numeric")
            if numeric_candidate is not None:
                numeric_float = float(numeric_candidate)
                include_numeric = False
                if mode == "column_sum":
                    include_numeric = bool(value_column_raw and normalized_label == value_column_raw)
                elif normalized_column_filters:
                    include_numeric = normalized_label in normalized_column_filters
                else:
                    include_numeric = not is_total_col
                if include_numeric:
                    display_text = _clip_text(cell_value.strip() or _format_numeric_display(numeric_float), 60)
                    if mode == "column_sum":
                        value_numeric = numeric_float
                        value_display = display_text
                    contributions.append(
                        {
                            "column": _clip_text(column_label, 80),
                            "value": numeric_float,
                            "display": display_text,
                            "is_total_column": is_total_col,
                        }
                    )

        # If only the identifier column matched, include adjacent cells for context (e.g., unlabeled name columns).
        if has_exact_identifier_filter and match_cell_index is not None:
            has_non_match = any(
                _normalize_column_name(entry.get("column")) != match_column
                for entry in preview_cells
                if isinstance(entry, Mapping)
            )
            if not has_non_match:
                cells_by_index = {
                    int(cell.get("column_index")): cell
                    for cell in cells
                    if cell.get("column_index") is not None
                }
                neighbor_candidates: list[Mapping[str, object]] = []
                for delta in (-1, 1, -2, 2):
                    neighbor = cells_by_index.get(match_cell_index + delta)
                    if isinstance(neighbor, Mapping):
                        neighbor_candidates.append(neighbor)

                def _append_neighbors(*, require_text: bool) -> None:
                    for neighbor in neighbor_candidates:
                        if len(preview_cells) >= row_cell_cap:
                            break
                        raw_text = _coerce_str(neighbor.get("raw_text")).strip()
                        if not raw_text:
                            continue
                        if require_text and neighbor.get("numeric") is not None:
                            continue
                        column_label = neighbor.get("column") or f"column_{(neighbor.get('column_index') or 0) + 1}"
                        normalized_label = _normalize_column_name(column_label)
                        if normalized_label and normalized_label in preview_column_norms:
                            continue
                        preview_cells.append(
                            {
                                "column": _clip_text(column_label, 80),
                                "value": _clip_text(raw_text, 160),
                            }
                        )
                        if normalized_label:
                            preview_column_norms.add(normalized_label)

                _append_neighbors(require_text=True)
                has_non_match = any(
                    _normalize_column_name(entry.get("column")) != match_column
                    for entry in preview_cells
                    if isinstance(entry, Mapping)
                )
                if not has_non_match:
                    _append_neighbors(require_text=False)

        numeric_value: float | None = None
        display_value: str | None = None
        if mode == "row_total":
            non_total_values = [entry["value"] for entry in contributions if not entry["is_total_column"]]
            if non_total_values:
                numeric_value = float(sum(non_total_values))
                display_value = _format_numeric_display(numeric_value)
            else:
                continue
        else:
            if not value_column_raw:
                continue
            if value_numeric is None:
                continue
            numeric_value = float(value_numeric)
            display_value = value_display or _format_numeric_display(numeric_value)
        total_value += numeric_value or 0.0
        contributions_sorted = sorted(contributions, key=lambda entry: abs(entry["value"]), reverse=True)
        output_contributions = [
            {"column": entry.get("column"), "value": entry.get("value"), "display": entry.get("display")}
            for entry in contributions_sorted[:60]
        ]
        matched_rows.append(
            {
                "row_index": row_payload.get("row_index"),
                "table_order_index": row_payload.get("table_order_index"),
                "sheet_name": row_payload.get("sheet_name"),
                "cells": preview_cells,
                "contributions": output_contributions,
                "contribution_count": len(contributions_sorted),
                "row_total": numeric_value,
                "row_total_display": display_value,
            }
        )
        if len(matched_rows) >= row_limit:
            break

    status = "ok" if matched_rows else "not_found"

    original_match_count = len(matched_rows)
    throttle_notice: dict[str, object] | None = None

    def _json_char_len(obj: object) -> int:
        try:
            return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))
        except Exception:
            return len(str(obj))

    hard_char_cap = 12_000
    remaining_turn = None
    if context.char_budget_per_turn is not None:
        remaining_turn = max(0, context.char_budget_per_turn - context.characters_used)
    max_payload_chars = hard_char_cap if remaining_turn is None else min(hard_char_cap, remaining_turn)

    prompt_rows_cap = max(3, int(getattr(settings, "MCP_PROMPT_TABLE_MAX_ROWS", 12)))
    prompt_contrib_cap = max(5, int(getattr(settings, "MCP_PROMPT_TABLE_MAX_CONTRIBUTIONS", 25)))

    def _row_payload(row: Mapping[str, object], *, max_cells: int, max_contributions: int) -> dict[str, object]:
        payload: dict[str, object] = {
            "row_index": row.get("row_index"),
            "table_order_index": row.get("table_order_index"),
            "sheet_name": row.get("sheet_name"),
            "row_total": row.get("row_total"),
            "row_total_display": row.get("row_total_display"),
            "contribution_count": row.get("contribution_count"),
        }
        cells = row.get("cells") if isinstance(row.get("cells"), list) else []
        if cells:
            payload["cells"] = [dict(cell) for cell in cells[:max_cells] if isinstance(cell, Mapping)]
        contributions = row.get("contributions") if isinstance(row.get("contributions"), list) else []
        if contributions:
            payload["contributions"] = [
                {
                    "column": entry.get("column"),
                    "value": entry.get("value"),
                    "display": entry.get("display"),
                }
                for entry in contributions[:max_contributions]
                if isinstance(entry, Mapping)
            ]
        return {k: v for k, v in payload.items() if v not in (None, "") and v != []}

    if matched_rows:
        # Soft cap for broad calls (no row filter + no explicit columns).
        if not has_row_filters and not column_filters:
            matched_rows = matched_rows[: max(5, min(prompt_rows_cap, 20))]

    dataset_enabled = False
    dataset_summary: dict[str, object] | None = None
    metadata = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
    dataset_meta = metadata.get("dataset") if isinstance(metadata, Mapping) else None
    if isinstance(dataset_meta, Mapping) and dataset_meta.get("enabled"):
        dataset_enabled = True
        sheets = dataset_meta.get("sheets")
        sheet_count = len(sheets) if isinstance(sheets, list) else None
        dataset_summary = {
            "storage_format": dataset_meta.get("storage_format"),
            "row_count": dataset_meta.get("row_count"),
            "preview_rows_indexed": dataset_meta.get("preview_rows_indexed"),
            "sheet_count": sheet_count,
        }

    base_payload = {
        "tool": "table_aggregate",
        "status": status,
        "document_id": str(upload.id),
        "dataset_mode": dataset_enabled or None,
        "dataset": dataset_summary,
        "mode": mode,
        "query": query_input or None,
        "match_column": match_column_input or None,
        "match_value": match_value_input or None,
        "match_values": normalized_match_values or None,
        "value_column": value_column_input or None,
        "sheet_name": sheet_name_input or None,
        "columns": column_filters or None,
        "evaluated_rows": evaluated_rows,
        "row_limit": row_limit,
        "cache_hit": cache_hit,
    }
    minimal_payload = {
        **base_payload,
        "duration_ms": 0,
        "match_count": 0,
        "rows": [],
        "total": None,
        "display_total": None,
        "hint": None,
    }
    base_overhead = _json_char_len({k: v for k, v in minimal_payload.items() if v not in (None, "") and v != []})
    if max_payload_chars is not None and base_overhead >= max_payload_chars:
        raise CharacterBudgetExceeded("Character budget too low to return table aggregate metadata.")

    rows_out: list[dict[str, object]] = []
    running_chars = base_overhead
    for row in matched_rows:
        if len(rows_out) >= max(1, prompt_rows_cap * 2):
            break
        row_out = _row_payload(row, max_cells=row_cell_cap, max_contributions=prompt_contrib_cap)
        row_chars = _json_char_len(row_out) + 1
        if rows_out and max_payload_chars is not None and running_chars + row_chars > max_payload_chars:
            break
        rows_out.append(row_out)
        running_chars += row_chars

    # Track seen rows (no filtering)
    rows_out, row_completeness = _apply_seen_row_filter(rows_out, str(upload.id), context, mark_as_seen=False)

    total_available = original_match_count
    row_completeness["total_found"] = total_available
    row_completeness["shown"] = len(rows_out)
    row_completeness["has_more"] = total_available > len(rows_out)
    if row_completeness["shown"] > 0 and not row_completeness["has_more"]:
        if row_completeness.get("already_seen", 0) == row_completeness["shown"]:
            row_completeness["all_previously_shown"] = True
            row_completeness["message"] = (
                f"All {row_completeness['shown']} matching rows have already been shown in this conversation."
            )

    if row_completeness["has_more"]:
        throttle_notice = {
            "reason": "prompt_budget",
            "message": (
                f"Showing {len(rows_out)} of {total_available} total matching rows due to prompt size limits."
            ),
            "original_match_count": original_match_count,
            "returned_match_count": len(rows_out),
        }

    duration_ms = int((time.perf_counter() - start) * 1000)

    hint = None
    if original_match_count == 0:
        if dataset_enabled:
            hint = (
                "No matching rows found in the indexed preview. This document is in dataset mode; "
                "tabular preview search only covers indexed preview rows."
            )
        else:
            hint = "No matching rows found."

    payload = {
        **base_payload,
        "duration_ms": duration_ms,
        "match_count": len(rows_out),
        "rows": rows_out,
        "total": total_value if original_match_count else None,
        "display_total": _format_numeric_display(total_value) if original_match_count else None,
        "hint": hint,
    }
    if throttle_notice:
        payload["throttle_notice"] = throttle_notice

    # Always include completeness metadata for transparent decisions
    payload["completeness"] = row_completeness

    def _payload_char_count(value: Mapping[str, object]) -> int:
        return _json_char_len({k: v for k, v in value.items() if v not in (None, "") and v != []})

    char_count = _payload_char_count(payload)
    if max_payload_chars is not None and char_count > max_payload_chars:
        notice = payload.get("throttle_notice") if isinstance(payload.get("throttle_notice"), Mapping) else None
        if notice:
            original_message = str(notice.get("message") or "").strip()
            for limit in (240, 160, 100, 60, 0):
                updated_notice = dict(notice)
                if limit <= 0:
                    updated_notice.pop("message", None)
                else:
                    updated_notice["message"] = _clip_text(original_message, limit)
                cleaned_notice = {k: v for k, v in updated_notice.items() if v not in (None, "") and v != []}
                if cleaned_notice:
                    payload["throttle_notice"] = cleaned_notice
                else:
                    payload.pop("throttle_notice", None)
                char_count = _payload_char_count(payload)
                if char_count <= max_payload_chars:
                    break

        if char_count > max_payload_chars and rows_out:
            while rows_out and char_count > max_payload_chars:
                rows_out.pop()
                payload["rows"] = rows_out
                payload["match_count"] = len(rows_out)
                notice = payload.get("throttle_notice") if isinstance(payload.get("throttle_notice"), Mapping) else None
                if notice:
                    notice_out = dict(notice)
                    if "returned_match_count" in notice_out:
                        notice_out["returned_match_count"] = len(rows_out)
                    payload["throttle_notice"] = notice_out
                char_count = _payload_char_count(payload)

    if isinstance(payload.get("completeness"), Mapping):
        row_completeness = dict(payload["completeness"])
        already_seen_count = 0
        for row in rows_out:
            row_index = row.get("row_index")
            if row_index is None:
                continue
            if context.is_row_seen(str(upload.id), row_index):
                already_seen_count += 1
        row_completeness["already_seen"] = already_seen_count
        row_completeness["shown"] = len(rows_out)
        row_completeness["total_found"] = total_available
        row_completeness["has_more"] = total_available > len(rows_out)
        if row_completeness["shown"] > 0 and not row_completeness["has_more"]:
            if row_completeness["already_seen"] == row_completeness["shown"]:
                row_completeness["all_previously_shown"] = True
                row_completeness["message"] = (
                    f"All {row_completeness['shown']} matching rows have already been shown in this conversation."
                )
            else:
                row_completeness.pop("all_previously_shown", None)
                row_completeness.pop("message", None)
        else:
            row_completeness.pop("all_previously_shown", None)
            row_completeness.pop("message", None)
        payload["completeness"] = row_completeness
        if row_completeness.get("all_previously_shown"):
            payload["hint"] = row_completeness.get("message") or payload.get("hint")
        char_count = _payload_char_count(payload)

    # Mark the final rows as seen (after any trimming)
    _mark_rows_as_seen(rows_out, str(upload.id), context)

    payload["char_count"] = char_count
    payload["token_estimate"] = _estimate_tokens(char_count)
    try:
        context.reserve_characters(char_count)
    except CharacterBudgetExceeded as exc:
        return {
            "tool": "table_aggregate",
            "document_id": str(upload.id),
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "rows": [],
            "match_count": 0,
            "throttle_notice": {"type": "prompt_budget", "message": str(exc)},
            "hint": "Prompt budget exceeded. Narrow filters/select_columns and retry.",
        }

    structured_log(
        "mcp",
        "table.aggregate",
        {
            "document_id": str(upload.id),
            "mode": mode,
            "status": payload.get("status"),
            "match_count": len(rows_out),
            "original_match_count": original_match_count,
            "evaluated_rows": evaluated_rows,
            "contribution_rows": sum(row.get("contribution_count", 0) for row in rows_out),
            "requested_columns": column_filters,
            "duration_ms": duration_ms,
            "row_limit": row_limit,
            "match_column": match_column_input or None,
            **_log_safe_text_fields("match_value", match_value_input or None),
            **_log_safe_text_fields("query", query_input or None),
            "sheet_name": sheet_name_input or None,
            "cache_hit": cache_hit,
            "char_count": char_count,
            "token_estimate": payload.get("token_estimate"),
            "truncated": bool(throttle_notice),
        },
        context={
            "business": conversation.business_profile_id,
            "conversation": conversation.id,
            "document_id": str(upload.id),
        },
        logger_obj=logger,
        level=(
            logging.WARNING
            if (
                int(duration_ms or 0)
                >= int(getattr(settings, "MCP_SLO_TABLE_AGGREGATE_WARN_MS", 1200) or 0)
                and int(getattr(settings, "MCP_SLO_TABLE_AGGREGATE_WARN_MS", 1200) or 0) > 0
            )
            else logging.INFO
        ),
    )
    return payload


def _dataset_query_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    start = time.perf_counter()
    raw_id = _coerce_str(arguments.get("dataset_id") or arguments.get("document_id")).strip()
    if not raw_id:
        return {"tool": "query_dataset", "status": "error", "error": "document_id is required"}
    try:
        identifier = uuid.UUID(raw_id)
    except (TypeError, ValueError):
        return {"tool": "query_dataset", "status": "error", "error": "document_id must be a valid UUID"}

    upload = apply_customer_visible_uploads(
        KnowledgeUpload.objects.filter(
            id=identifier,
            business_profile=conversation.business_profile,
            status=KnowledgeStatus.ACTIVE,
        )
    ).first()
    if not upload:
        chunk = (
            apply_customer_visible_chunks(
                KnowledgeUploadChunk.objects.filter(
                    id=identifier,
                    business_profile=conversation.business_profile,
                    upload__status=KnowledgeStatus.ACTIVE,
                )
            )
            .select_related("upload")
            .first()
        )
        upload = chunk.upload if chunk else None
    if not upload:
        return {
            "tool": "query_dataset",
            "status": "not_found",
            "error": "document not found for this business",
        }

    agent_scope = _agent_knowledge_scope(conversation, context)
    if upload.id and not _agent_scope_allows_upload(scope=agent_scope, conversation=conversation, upload_id=upload.id):
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "constraint_error",
            "error": "forbidden_document",
            "error_code": "forbidden_document",
            "rows": [],
            "match_count": 0,
            "hint": "This agent is not permitted to access that document.",
        }

    guard = _identifier_guard(context, conversation)
    decision = None
    if guard and upload.id:
        decision = guard.require_for_upload(str(upload.id))
        _record_identifier_check(context, decision)
        if decision.status != "ok":
            structured_log(
                "mcp",
                "identifier.denied",
                {
                    "tool": "query_dataset",
                    "upload": str(upload.id),
                    "required": list(decision.required_keys),
                    "provided": list(decision.provided_keys),
                },
                context={"business": conversation.business_profile_id},
                logger_obj=logger,
                level=logging.WARNING,
            )
            _record_identifier_event_once(
                context,
                business_profile=conversation.business_profile,
                decision=decision,
                tool="dataset_query",
                conversation=conversation,
                upload_ids=[str(upload.id)],
            )
            error_code = "identifier_required"
            return {
                "tool": "query_dataset",
                "document_id": str(upload.id),
                "status": decision.status,
                "error": error_code,
                "error_code": error_code,
                "rows": [],
                "match_count": 0,
                "identifier_gate": decision.as_dict(),
                "required_identifiers": list(decision.required_keys),
                "provided_identifiers": list(decision.provided_keys),
                "hint": decision.hint,
                "llm_hint": decision.hint,
            }

    ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
    dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, Mapping) else None
    
    # UNIFIED HANDLER: Fallback to legacy table handlers if not a rigorous dataset
    if not isinstance(dataset_meta, Mapping) or not dataset_meta.get("enabled"):
        # Not a rigorously ingested dataset (standard CSV/XLSX)
        # Use legacy table handlers which support on-the-fly pandas loading
        if arguments.get("query") and arguments.get("aggregate"):
             # Route to table aggregate
             # We must map arguments: document_id -> document_id (already unified)
             legacy_args = dict(arguments)
             legacy_args["document_id"] = str(upload.id)
             # _table_aggregate_handler handles the heavy lifting
             result = _table_aggregate_handler(legacy_args, conversation, context)
             
             # Align result tool name with new contract
             # We may need to wrap/coerce the result if _table_aggregate_handler returns "tool": "table_aggregate"
             new_result = dict(result)
             new_result["tool"] = "query_dataset" 
             return new_result
        else:
             # Route to table preview/list (simple read)
             legacy_args = dict(arguments) 
             legacy_args["document_id"] = str(upload.id)
             # _read_knowledge_handler's table preview logic is actually split.
             # We can use _table_preview_handler (if it exists) or rely on the logic in read_knowledge.
             # Checking tools.py, closest is _table_aggregate_handler for ANY table op if intent=table.
             # Let's see if we can just use _table_aggregate_handler for everything or if we need a preview specific one.
             # Wait, _table_aggregate_handler supports "no query" -> it previews.
             
             result = _table_aggregate_handler(legacy_args, conversation, context)
             new_result = dict(result)
             new_result["tool"] = "query_dataset"
             return new_result

    storage_format = str(dataset_meta.get("storage_format") or "").strip() or "csv_gz"
    if storage_format not in {"csv_gz", "jsonl_gz"}:
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "error",
            "error": f"Unsupported dataset storage_format={storage_format!r}",
        }

    sheet_name_input = _coerce_str(arguments.get("sheet_name")).strip()
    try:
        sheet_index_input = int(arguments.get("sheet_index")) if arguments.get("sheet_index") is not None else None
    except (TypeError, ValueError):
        sheet_index_input = None

    sheet_meta: Mapping[str, object] | None = None
    storage_rel_path: str | None = None
    available_sheets = dataset_meta.get("sheets")
    if isinstance(available_sheets, list) and available_sheets:
        normalized_request = _normalize_column_name(sheet_name_input) if sheet_name_input else ""
        for entry in available_sheets:
            if not isinstance(entry, Mapping):
                continue
            if sheet_index_input and int(entry.get("sheet_index") or 0) == sheet_index_input:
                sheet_meta = entry
                break
        if sheet_meta is None and normalized_request:
            for entry in available_sheets:
                if not isinstance(entry, Mapping):
                    continue
                candidate = _normalize_column_name(entry.get("sheet_name"))
                if candidate and candidate == normalized_request:
                    sheet_meta = entry
                    break
        if sheet_meta is None and normalized_request:
            for entry in available_sheets:
                if not isinstance(entry, Mapping):
                    continue
                candidate = _normalize_column_name(entry.get("sheet_name"))
                if candidate and normalized_request in candidate:
                    sheet_meta = entry
                    break
        if sheet_meta is None:
            sheet_meta = next((entry for entry in available_sheets if isinstance(entry, Mapping)), None)
        storage_rel_path = _coerce_str(sheet_meta.get("storage_path") if sheet_meta else None).strip() or None
    else:
        storage_rel_path = _coerce_str(dataset_meta.get("storage_path")).strip() or None

    if not storage_rel_path:
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "error",
            "error": "Dataset storage path missing. Re-ingest the upload.",
        }

    media_root = Path(getattr(settings, "MEDIA_ROOT", ".")).resolve()
    abs_path = (media_root / Path(storage_rel_path)).resolve()
    try:
        abs_path.relative_to(media_root)
    except ValueError:
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "error",
            "error": "Dataset storage path escapes MEDIA_ROOT.",
        }
    if not abs_path.exists():
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "error",
            "error": "Dataset file missing on disk. Re-ingest the upload.",
        }

    tabular_limits = resolve_tabular_tool_limits(business_profile=conversation.business_profile, upload=upload)
    tool_limits = tabular_limits.dataset_query
    try:
        enforce_tool_rate_limit(
            business_profile=conversation.business_profile,
            tool="dataset_query",
            rate_limit=tool_limits.rate_limit,
            upload=upload,
        )
    except ToolRateLimitExceeded as exc:
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "throttled",
            "error": "rate_limited",
            "error_code": "rate_limited",
            "rows": [],
            "match_count": 0,
            "total_matches": 0,
            "throttle_notice": {"type": "rate_limited", "message": str(exc)},
            "hint": str(exc),
        }

    query_input = _coerce_str(arguments.get("query")).strip()
    query_norm = query_input.lower().strip() if query_input else ""

    raw_select_columns = arguments.get("select_columns")
    select_columns_input: list[str] = []
    if isinstance(raw_select_columns, (list, tuple)):
        for entry in raw_select_columns:
            candidate = _coerce_str(entry).strip()
            if candidate:
                select_columns_input.append(candidate)
    max_select_columns = max(25, int(tool_limits.max_columns_returned) * 4)
    if len(select_columns_input) > max_select_columns:
        select_columns_input = select_columns_input[:max_select_columns]

    raw_filters = arguments.get("filters")
    filters_input: list[dict[str, object]] = []
    if isinstance(raw_filters, (list, tuple)):
        for entry in raw_filters:
            if not isinstance(entry, Mapping):
                continue
            column = _coerce_str(entry.get("column")).strip()
            op = _coerce_str(entry.get("op")).strip().lower()
            if not column or not op:
                continue
            payload: dict[str, object] = {"column": column, "op": op}
            value = entry.get("value")
            if value is not None:
                payload["value"] = _coerce_str(value)
            values = entry.get("values")
            if isinstance(values, (list, tuple)):
                payload["values"] = [_coerce_str(v) for v in values if _coerce_str(v).strip()]
            case_sensitive = entry.get("case_sensitive")
            if isinstance(case_sensitive, bool):
                payload["case_sensitive"] = case_sensitive
            filters_input.append(payload)
    if len(filters_input) > 10:
        filters_input = filters_input[:10]

    sort_by_input = _coerce_str(arguments.get("sort_by")).strip()
    sort_direction = _coerce_str(arguments.get("sort_direction")).strip().lower() or "asc"
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "asc"

    try:
        limit = int(arguments.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(50, limit))
    limit = min(limit, int(tool_limits.max_rows_returned))
    try:
        offset = int(arguments.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, offset)

    aggregate_input = arguments.get("aggregate") if isinstance(arguments.get("aggregate"), Mapping) else None
    aggregate_op = _coerce_str(aggregate_input.get("operation") if aggregate_input else None).strip().lower()
    if aggregate_op and aggregate_op not in {"count", "sum", "min", "max", "group_by"}:
        aggregate_op = ""
    aggregate_column = _coerce_str(aggregate_input.get("column") if aggregate_input else None).strip()
    group_by_column = _coerce_str(aggregate_input.get("group_by") if aggregate_input else None).strip()
    try:
        top_groups = int(aggregate_input.get("top_groups") or 20) if aggregate_input else 20
    except (TypeError, ValueError):
        top_groups = 20
    top_groups = max(1, min(50, top_groups))

    max_seconds = float(tool_limits.max_seconds)
    max_sort_window = int(tool_limits.max_sort_window)
    default_column_cap = int(tool_limits.default_columns)
    cell_value_chars = int(tool_limits.cell_value_chars)
    max_group_cap = int(tool_limits.max_groups)
    max_column_cap_default = int(tool_limits.max_columns_returned)
    max_column_cap_exact = int(getattr(tool_limits, "max_columns_returned_exact", max_column_cap_default))

    def _should_expand_columns_for_exact_lookup() -> bool:
        if query_norm or aggregate_op:
            return False
        if not filters_input:
            return False
        for flt in filters_input:
            column = _coerce_str(flt.get("column")).strip()
            if not column or not _column_suggests_identifier(column):
                continue
            op = _coerce_str(flt.get("op")).strip().lower()
            if op == "eq":
                value = _coerce_str(flt.get("value")).strip()
                if value and _should_force_exact_identifier_match(column, value):
                    return True
            elif op == "in":
                values = flt.get("values") if isinstance(flt.get("values"), list) else []
                values_clean = [_coerce_str(v).strip() for v in values if _coerce_str(v).strip()]
                for candidate in values_clean[:3]:
                    if _should_force_exact_identifier_match(column, candidate):
                        return True
        return False

    max_column_cap = max_column_cap_exact if _should_expand_columns_for_exact_lookup() else max_column_cap_default

    def _parse_datetime_value(value: str | None) -> float | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()

    def _coerce_sort_key(value: str | None) -> tuple[int, object]:
        if not isinstance(value, str):
            return (3, "")
        text = value.strip()
        if not text:
            return (3, "")
        numeric = _parse_numeric_value(text)
        if numeric is not None:
            return (0, float(numeric))
        dt = _parse_datetime_value(text)
        if dt is not None:
            return (1, float(dt))
        return (2, text.lower())

    def _compare_values(left: str | None, right: str | None, *, op: str) -> bool:
        left_text = _coerce_str(left)
        right_text = _coerce_str(right)
        left_numeric = _parse_numeric_value(left_text)
        right_numeric = _parse_numeric_value(right_text)
        if left_numeric is not None and right_numeric is not None:
            if op == "gt":
                return left_numeric > right_numeric
            if op == "gte":
                return left_numeric >= right_numeric
            if op == "lt":
                return left_numeric < right_numeric
            if op == "lte":
                return left_numeric <= right_numeric
        left_dt = _parse_datetime_value(left_text)
        right_dt = _parse_datetime_value(right_text)
        if left_dt is not None and right_dt is not None:
            if op == "gt":
                return left_dt > right_dt
            if op == "gte":
                return left_dt >= right_dt
            if op == "lt":
                return left_dt < right_dt
            if op == "lte":
                return left_dt <= right_dt
        if op == "gt":
            return left_text > right_text
        if op == "gte":
            return left_text >= right_text
        if op == "lt":
            return left_text < right_text
        if op == "lte":
            return left_text <= right_text
        return False

    def _json_char_len(obj: object) -> int:
        try:
            return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str))
        except Exception:
            return len(str(obj))

    hard_char_cap = 12_000
    remaining_turn = None
    if context.char_budget_per_turn is not None:
        remaining_turn = max(0, context.char_budget_per_turn - context.characters_used)
    max_payload_chars = hard_char_cap if remaining_turn is None else min(hard_char_cap, remaining_turn)

    def _clip_value(value: object, limit: int) -> str:
        text = _coerce_str(value)
        text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "…"

    dataset_summary: dict[str, object] = {
        "storage_format": storage_format,
        "row_count": dataset_meta.get("row_count"),
        "preview_rows_indexed": dataset_meta.get("preview_rows_indexed") or dataset_meta.get("preview_rows"),
    }
    sheet_name_out = None
    sheet_index_out = None
    if sheet_meta:
        sheet_name_out = sheet_meta.get("sheet_name")
        sheet_index_out = sheet_meta.get("sheet_index")
        dataset_summary["sheet_row_count"] = sheet_meta.get("row_count")
        dataset_summary["sheet_preview_rows_indexed"] = sheet_meta.get("preview_rows_indexed")
    if isinstance(available_sheets, list):
        dataset_summary["sheet_count"] = len([s for s in available_sheets if isinstance(s, Mapping)])
    suggested_keys = None
    if isinstance(sheet_meta, Mapping):
        suggested_keys = sheet_meta.get("suggested_key_columns")
    if suggested_keys is None:
        suggested_keys = dataset_meta.get("suggested_key_columns")
    if isinstance(suggested_keys, list):
        dataset_summary["suggested_keys"] = [
            str(entry.get("column") or "")
            for entry in suggested_keys[:6]
            if isinstance(entry, Mapping) and str(entry.get("column") or "").strip()
        ]

    duration_ms = 0
    scanned_rows = 0
    matched_total = 0
    truncated = False
    query_engine = "python"

    aggregate_result: dict[str, object] | None = None
    sum_total = 0.0
    sum_count = 0
    min_value: float | None = None
    max_value: float | None = None
    group_counter: Counter[str] | None = None
    overflow_group_count = 0
    if aggregate_op == "group_by":
        group_counter = Counter()
        overflow_group_count = 0

    want_sort = bool(sort_by_input)
    if want_sort and (offset + limit) > max_sort_window:
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "error",
            "error": "offset_too_large_for_sort",
            "error_code": "offset_too_large_for_sort",
            "hint": f"offset+limit is too large to sort safely (max window {max_sort_window}). Narrow the query or reduce offset.",
        }

    sort_window = max(1, min(max_sort_window, offset + limit))
    best_keys: list[tuple[tuple[int, object], int]] = []
    best_rows: list[tuple[int, object]] = []

    rows_out: list[dict[str, object]] = []

    def _row_payload(row_index: int, row_map: Mapping[str, object], *, columns: Sequence[str]) -> dict[str, object]:
        cells: list[dict[str, str]] = []
        for col in columns:
            cells.append({"column": col, "value": _clip_value(row_map.get(col, ""), cell_value_chars)})
        return {"row_index": row_index, "cells": cells}

    def _choose_columns(available_columns: Sequence[str], *, requested: Sequence[str] | None = None) -> list[str]:
        normalized_map = {_normalize_column_name(name): name for name in available_columns if _normalize_column_name(name)}
        chosen: list[str] = []
        if requested:
            for name in requested:
                actual = normalized_map.get(_normalize_column_name(name))
                if actual and actual not in chosen:
                    chosen.append(actual)
                if len(chosen) >= max_column_cap:
                    break
            return chosen

        priority: list[str] = []
        for flt in filters_input:
            col = _coerce_str(flt.get("column")).strip()
            actual = normalized_map.get(_normalize_column_name(col))
            if actual and actual not in priority:
                priority.append(actual)
        if sort_by_input:
            actual = normalized_map.get(_normalize_column_name(sort_by_input))
            if actual and actual not in priority:
                priority.append(actual)
        if aggregate_column:
            actual = normalized_map.get(_normalize_column_name(aggregate_column))
            if actual and actual not in priority:
                priority.append(actual)
        if group_by_column:
            actual = normalized_map.get(_normalize_column_name(group_by_column))
            if actual and actual not in priority:
                priority.append(actual)

        for entry in priority:
            if entry not in chosen:
                chosen.append(entry)
                if len(chosen) >= max_column_cap:
                    break
        for col in available_columns:
            if len(chosen) >= default_column_cap:
                break
            if col not in chosen:
                chosen.append(col)
            if len(chosen) >= max_column_cap:
                break
        return chosen

    def _matches_filters(row_map: Mapping[str, object]) -> bool:
        if not filters_input:
            return True
        for flt in filters_input:
            column = _coerce_str(flt.get("column")).strip()
            op = _coerce_str(flt.get("op")).strip().lower()
            if not column or not op:
                continue
            case_sensitive = bool(flt.get("case_sensitive"))
            raw_value = row_map.get(column)
            cell_value = _coerce_str(raw_value)
            if not case_sensitive:
                cell_cmp = cell_value.lower()
            else:
                cell_cmp = cell_value

            if op == "in":
                values = flt.get("values") if isinstance(flt.get("values"), list) else []
                candidates = []
                for entry in values[:50]:
                    text = _coerce_str(entry).strip()
                    if text:
                        candidates.append(text if case_sensitive else text.lower())
                if not candidates:
                    continue
                if cell_cmp not in candidates:
                    return False
                continue

            value = _coerce_str(flt.get("value")).strip()
            target = value if case_sensitive else value.lower()
            if op == "eq":
                if cell_cmp != target:
                    return False
            elif op == "contains":
                if target and target not in cell_cmp:
                    return False
            elif op == "startswith":
                if target and not cell_cmp.startswith(target):
                    return False
            elif op == "endswith":
                if target and not cell_cmp.endswith(target):
                    return False
            elif op in {"gt", "gte", "lt", "lte"}:
                if not _compare_values(cell_value, value, op=op):
                    return False
            else:
                return False
        return True

    deadline = start + max_seconds

    def _should_stop() -> bool:
        return time.perf_counter() >= deadline

    if storage_format == "csv_gz":
        with gzip.open(abs_path, "rt", encoding="utf-8", errors="ignore", newline="") as handle:
            reader = csv.reader(handle)
            header_row = next(reader, None)
            if not header_row:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "Dataset CSV header row missing.",
                }
            header = [str(value or "").strip() or f"column_{idx + 1}" for idx, value in enumerate(header_row)]
            if header and isinstance(header[0], str):
                header[0] = header[0].lstrip("\ufeff")

            normalized_header_map: dict[str, str] = {}
            for col in header:
                norm = _normalize_column_name(col)
                if norm and norm not in normalized_header_map:
                    normalized_header_map[norm] = col

            resolved_filters: list[dict[str, object]] = []
            for flt in filters_input:
                col_input = _coerce_str(flt.get("column")).strip()
                norm = _normalize_column_name(col_input)
                actual = normalized_header_map.get(norm)
                if not actual:
                    return {
                        "tool": "query_dataset",
                        "document_id": str(upload.id),
                        "status": "error",
                        "error": "unknown_column",
                        "error_code": "unknown_column",
                        "hint": f"Unknown column {col_input!r}.",
                        "available_columns": header[:50],
                    }
                resolved = dict(flt)
                resolved["column"] = actual
                resolved_filters.append(resolved)
            filters_input = resolved_filters

            sort_column_actual: str | None = None
            if sort_by_input:
                sort_column_actual = normalized_header_map.get(_normalize_column_name(sort_by_input))
                if not sort_column_actual:
                    return {
                        "tool": "query_dataset",
                        "document_id": str(upload.id),
                        "status": "error",
                        "error": "unknown_sort_column",
                        "error_code": "unknown_sort_column",
                        "hint": f"Unknown sort_by column {sort_by_input!r}.",
                        "available_columns": header[:50],
                    }

            if aggregate_op in {"sum", "min", "max"}:
                if not aggregate_column:
                    return {
                        "tool": "query_dataset",
                        "document_id": str(upload.id),
                        "status": "error",
                        "error": "aggregate_column_required",
                        "error_code": "aggregate_column_required",
                        "hint": f"aggregate.column is required when operation={aggregate_op}.",
                    }
                actual = normalized_header_map.get(_normalize_column_name(aggregate_column))
                if not actual:
                    return {
                        "tool": "query_dataset",
                        "document_id": str(upload.id),
                        "status": "error",
                        "error": "unknown_aggregate_column",
                        "error_code": "unknown_aggregate_column",
                        "hint": f"Unknown aggregate column {aggregate_column!r}.",
                        "available_columns": header[:50],
                    }
                aggregate_column = actual

            if aggregate_op == "group_by":
                if not group_by_column:
                    return {
                        "tool": "query_dataset",
                        "document_id": str(upload.id),
                        "status": "error",
                        "error": "group_by_required",
                        "error_code": "group_by_required",
                        "hint": "aggregate.group_by is required when operation=group_by.",
                    }
                actual = normalized_header_map.get(_normalize_column_name(group_by_column))
                if not actual:
                    return {
                        "tool": "query_dataset",
                        "document_id": str(upload.id),
                        "status": "error",
                        "error": "unknown_group_by_column",
                        "error_code": "unknown_group_by_column",
                        "hint": f"Unknown group_by column {group_by_column!r}.",
                        "available_columns": header[:50],
                    }
                group_by_column = actual

            selected_columns = _choose_columns(header, requested=select_columns_input if select_columns_input else None)
            if select_columns_input and not selected_columns:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "no_selectable_columns",
                    "error_code": "no_selectable_columns",
                    "hint": "None of the requested select_columns exist in this dataset.",
                    "available_columns": header[:50],
                }

            preview_only = not (filters_input or query_norm or aggregate_op or sort_column_actual)
            dataset_query_engine = str(getattr(settings, "DATASET_QUERY_ENGINE", "duckdb") or "duckdb").strip().lower() or "duckdb"
            use_duckdb = bool(
                duckdb is not None
                and dataset_query_engine in {"duckdb", "auto"}
                and not preview_only
            )
            if use_duckdb:
                con = None
                try:
                    def _sql_ident(name: str) -> str:
                        return '"' + name.replace('"', '""') + '"'

                    def _escape_like(value: str) -> str:
                        return (
                            value.replace("\\", "\\\\")
                            .replace("%", "\\%")
                            .replace("_", "\\_")
                        )

                    def _numeric_expr(col_ref: str) -> str:
                        return (
                            "try_cast(replace(regexp_replace("
                            + col_ref
                            + ", '[^0-9\\-,\\.]', '', 'g'), ',', '') as double)"
                        )

                    path_sql = abs_path.as_posix().replace("'", "''")
                    base_from = (
                        "(select row_number() over () as __row_index, * "
                        f"from read_csv('{path_sql}', header=true, all_varchar=true, delim=',', encoding='utf-8', ignore_errors=true)) as base"
                    )
                    row_index_ref = f"base.{_sql_ident('__row_index')}"

                    where_parts: list[str] = []
                    params: list[object] = []

                    if query_norm:
                        search_columns = list(selected_columns or header)[:32]
                        query_pattern = f"%{_escape_like(query_norm)}%"
                        or_parts: list[str] = []
                        for col in search_columns:
                            col_ref = f"lower(base.{_sql_ident(col)})"
                            or_parts.append(f"{col_ref} like ? escape '\\\\'")
                            params.append(query_pattern)
                        if or_parts:
                            where_parts.append("(" + " or ".join(or_parts) + ")")

                    op_map = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
                    for flt in filters_input:
                        column = _coerce_str(flt.get("column")).strip()
                        op = _coerce_str(flt.get("op")).strip().lower()
                        if not column or not op:
                            continue
                        col_raw = f"base.{_sql_ident(column)}"
                        case_sensitive = bool(flt.get("case_sensitive"))
                        col_cmp = col_raw if case_sensitive else f"lower({col_raw})"

                        if op == "in":
                            values = flt.get("values") if isinstance(flt.get("values"), list) else []
                            candidates: list[str] = []
                            for entry in values[:50]:
                                text = _coerce_str(entry).strip()
                                if text:
                                    candidates.append(text if case_sensitive else text.lower())
                            if not candidates:
                                continue
                            placeholders = ", ".join(["?"] * len(candidates))
                            where_parts.append(f"{col_cmp} in ({placeholders})")
                            params.extend(candidates)
                            continue

                        value_text = _coerce_str(flt.get("value")).strip()
                        value_cmp = value_text if case_sensitive else value_text.lower()

                        if op == "eq":
                            where_parts.append(f"{col_cmp} = ?")
                            params.append(value_cmp)
                            continue

                        if op in {"contains", "startswith", "endswith"}:
                            if not value_text:
                                continue
                            escaped = _escape_like(value_cmp)
                            if op == "contains":
                                pattern = f"%{escaped}%"
                            elif op == "startswith":
                                pattern = f"{escaped}%"
                            else:
                                pattern = f"%{escaped}"
                            where_parts.append(f"{col_cmp} like ? escape '\\\\'")
                            params.append(pattern)
                            continue

                        if op in {"gt", "gte", "lt", "lte"}:
                            comparator = op_map.get(op)
                            if not comparator:
                                continue
                            numeric_rhs = _parse_numeric_value(value_text)
                            if numeric_rhs is not None:
                                num_expr = _numeric_expr(col_raw)
                                where_parts.append(
                                    f"(case when {num_expr} is not null then {num_expr} {comparator} ? "
                                    f"else {col_raw} {comparator} ? end)"
                                )
                                params.append(float(numeric_rhs))
                                params.append(value_text)
                                continue
                            dt_rhs = _parse_datetime_value(value_text)
                            if dt_rhs is not None:
                                dt_expr = f"try_cast({col_raw} as timestamp)"
                                where_parts.append(
                                    f"(case when {dt_expr} is not null then {dt_expr} {comparator} try_cast(? as timestamp) "
                                    f"else {col_raw} {comparator} ? end)"
                                )
                                params.append(value_text)
                                params.append(value_text)
                                continue
                            where_parts.append(f"{col_raw} {comparator} ?")
                            params.append(value_text)
                            continue

                        # Unknown operator; let the python fallback handle it.
                        raise ValueError(f"Unsupported filter op={op!r} for duckdb engine.")

                    where_sql = " and ".join(where_parts) if where_parts else "true"

                    order_sql = f"order by {row_index_ref} asc"
                    if sort_column_actual:
                        sort_col = f"base.{_sql_ident(sort_column_actual)}"
                        num_expr = _numeric_expr(sort_col)
                        dt_expr = f"try_cast({sort_col} as timestamp)"
                        type_expr = f"(case when {num_expr} is not null then 0 when {dt_expr} is not null then 1 else 2 end)"
                        direction = "desc" if sort_direction == "desc" else "asc"
                        order_sql = (
                            "order by "
                            f"{type_expr} {direction}, "
                            f"{num_expr} {direction}, "
                            f"{dt_expr} {direction}, "
                            f"lower({sort_col}) {direction}, "
                            f"{row_index_ref} {direction}"
                        )

                    con = duckdb.connect(database=":memory:")  # type: ignore[misc]
                    try:
                        con.execute("set enable_progress_bar=false")
                    except Exception:
                        pass

                    def _fetchone(sql: str, query_params: Sequence[object]) -> tuple | None:
                        remaining = max(0.05, deadline - time.perf_counter())
                        timer = None
                        if hasattr(con, "interrupt") and remaining > 0:
                            timer = threading.Timer(remaining, con.interrupt)
                            timer.daemon = True
                            timer.start()
                        try:
                            return con.execute(sql, list(query_params)).fetchone()
                        finally:
                            if timer:
                                timer.cancel()

                    def _fetchall(sql: str, query_params: Sequence[object]) -> list[tuple]:
                        remaining = max(0.05, deadline - time.perf_counter())
                        timer = None
                        if hasattr(con, "interrupt") and remaining > 0:
                            timer = threading.Timer(remaining, con.interrupt)
                            timer.daemon = True
                            timer.start()
                        try:
                            return list(con.execute(sql, list(query_params)).fetchall())
                        finally:
                            if timer:
                                timer.cancel()

                    row_count_hint = sheet_meta.get("row_count") if isinstance(sheet_meta, Mapping) else dataset_meta.get("row_count")
                    try:
                        scanned_rows = int(row_count_hint or 0)
                    except (TypeError, ValueError):
                        scanned_rows = 0

                    if aggregate_op:
                        if aggregate_op == "count":
                            count_row = _fetchone(
                                f"select count(*) from {base_from} where {where_sql}",
                                params,
                            )
                            matched_total = int((count_row or (0,))[0] or 0)
                            aggregate_result = {"operation": "count", "count": matched_total}
                        elif aggregate_op == "sum":
                            if not aggregate_column:
                                raise ValueError("aggregate_column_required")
                            col_raw = f"base.{_sql_ident(aggregate_column)}"
                            num_expr = _numeric_expr(col_raw)
                            agg_row = _fetchone(
                                f"select count(*) as matched_total, sum({num_expr}) as sum_total, count({num_expr}) as sum_count "
                                f"from {base_from} where {where_sql}",
                                params,
                            )
                            matched_total = int((agg_row or (0, 0, 0))[0] or 0)
                            sum_total = float((agg_row or (0, 0, 0))[1] or 0.0)
                            sum_count = int((agg_row or (0, 0, 0))[2] or 0)
                            aggregate_result = {
                                "operation": "sum",
                                "column": aggregate_column or None,
                                "sum": sum_total,
                                "sum_display": _format_numeric_display(sum_total),
                                "numeric_match_count": sum_count,
                            }
                        elif aggregate_op in {"min", "max"}:
                            if not aggregate_column:
                                raise ValueError("aggregate_column_required")
                            col_raw = f"base.{_sql_ident(aggregate_column)}"
                            num_expr = _numeric_expr(col_raw)
                            func = "min" if aggregate_op == "min" else "max"
                            agg_row = _fetchone(
                                f"select count(*) as matched_total, {func}({num_expr}) as value "
                                f"from {base_from} where {where_sql}",
                                params,
                            )
                            matched_total = int((agg_row or (0, None))[0] or 0)
                            extremum = (agg_row or (0, None))[1]
                            extremum_value = float(extremum) if extremum is not None else None
                            aggregate_result = {
                                "operation": aggregate_op,
                                "column": aggregate_column or None,
                                aggregate_op: extremum_value,
                                f"{aggregate_op}_display": _format_numeric_display(extremum_value) if extremum_value is not None else None,
                            }
                        elif aggregate_op == "group_by":
                            if not group_by_column:
                                raise ValueError("group_by_required")
                            col_raw = f"base.{_sql_ident(group_by_column)}"
                            group_expr = (
                                "case when trim(coalesce(" + col_raw + ", '')) = '' "
                                "then '<empty>' else substr(" + col_raw + ", 1, 80) end"
                            )
                            count_row = _fetchone(
                                f"select count(*) from {base_from} where {where_sql}",
                                params,
                            )
                            matched_total = int((count_row or (0,))[0] or 0)
                            groups_rows = _fetchall(
                                f"select {group_expr} as value, count(*) as count "
                                f"from {base_from} where {where_sql} group by value order by count desc limit ?",
                                [*params, int(top_groups)],
                            )
                            groups_out = [{"value": _coerce_str(val), "count": int(cnt or 0)} for val, cnt in groups_rows]
                            distinct_row = _fetchone(
                                f"select count(*) from (select distinct {group_expr} as value from {base_from} where {where_sql} limit ?) t",
                                [*params, int(max_group_cap) + 1],
                            )
                            distinct_count = int((distinct_row or (0,))[0] or 0)
                            overflow_group_count = 1 if distinct_count > max_group_cap else 0
                            aggregate_result = {
                                "operation": "group_by",
                                "group_by": group_by_column or None,
                                "groups": groups_out,
                                "overflow_group_count": overflow_group_count or None,
                                "tracked_groups": min(distinct_count, max_group_cap),
                            }
                        query_engine = "duckdb"
                    else:
                        cols_sql = ", ".join([f"base.{_sql_ident(col)}" for col in selected_columns[:max_column_cap]])
                        select_cols_sql = f"{row_index_ref} as row_index" + (f", {cols_sql}" if cols_sql else "")
                        rows_sql = (
                            f"select {select_cols_sql} from {base_from} where {where_sql} "
                            f"{order_sql} limit ? offset ?"
                        )
                        rows_data = _fetchall(rows_sql, [*params, int(limit), int(offset)])
                        for row in rows_data:
                            if not row:
                                continue
                            row_index = int(row[0] or 0)
                            row_map = {
                                selected_columns[i]: _coerce_str(row[i + 1]) if (i + 1) < len(row) else ""
                                for i in range(len(selected_columns[:max_column_cap]))
                            }
                            rows_out.append(_row_payload(row_index, row_map, columns=selected_columns[:max_column_cap]))
                        try:
                            count_row = _fetchone(
                                f"select count(*) from {base_from} where {where_sql}",
                                params,
                            )
                            matched_total = int((count_row or (0,))[0] or 0)
                        except Exception:
                            truncated = True
                            matched_total = max(offset + len(rows_out), len(rows_out))
                        query_engine = "duckdb"
                except Exception as exc:
                    use_duckdb = False
                    query_engine = "python"
                    structured_log(
                        "mcp",
                        "dataset.duckdb_fallback",
                        {
                            "document_id": str(upload.id),
                            "storage_format": storage_format,
                            "error": str(exc)[:200],
                        },
                        context={
                            "business": conversation.business_profile_id,
                            "conversation": conversation.id,
                            "document_id": str(upload.id),
                        },
                        logger_obj=logger,
                        level=logging.WARNING,
                    )
                finally:
                    if con is not None:
                        try:
                            con.close()
                        except Exception:
                            pass
            if preview_only:
                for row_index, row in enumerate(reader, start=1):
                    if _should_stop():
                        truncated = True
                        break
                    if row_index <= offset:
                        continue
                    values = [str(item or "") for item in row]
                    if len(values) < len(header):
                        values.extend([""] * (len(header) - len(values)))
                    elif len(values) > len(header):
                        values = values[: len(header)]
                    row_map = {header[i]: values[i] for i in range(len(header))}
                    rows_out.append(_row_payload(row_index, row_map, columns=selected_columns[:max_column_cap]))
                    if len(rows_out) >= limit:
                        break
                row_count_hint = sheet_meta.get("row_count") if isinstance(sheet_meta, Mapping) else None
                if row_count_hint is None:
                    row_count_hint = dataset_meta.get("row_count")
                try:
                    matched_total = int(row_count_hint or 0)
                except (TypeError, ValueError):
                    matched_total = 0
            elif not use_duckdb:
                for row_index, row in enumerate(reader, start=1):
                    scanned_rows += 1
                    if _should_stop():
                        truncated = True
                        break
                    values = [str(item or "") for item in row]
                    if len(values) < len(header):
                        values.extend([""] * (len(header) - len(values)))
                    elif len(values) > len(header):
                        values = values[: len(header)]
                    row_map = {header[i]: values[i] for i in range(len(header))}

                    if query_norm:
                        hit = False
                        for val in values:
                            if query_norm in str(val or "").lower():
                                hit = True
                                break
                        if not hit:
                            continue

                    if not _matches_filters(row_map):
                        continue

                    matched_total += 1

                    if aggregate_op:
                        if aggregate_op == "count":
                            continue
                        if aggregate_op in {"sum", "min", "max"} and aggregate_column:
                            numeric = _parse_numeric_value(_coerce_str(row_map.get(aggregate_column)))
                            if numeric is None:
                                continue
                            value = float(numeric)
                            if aggregate_op == "sum":
                                sum_total += value
                                sum_count += 1
                            elif aggregate_op == "min":
                                min_value = value if min_value is None else min(min_value, value)
                            elif aggregate_op == "max":
                                max_value = value if max_value is None else max(max_value, value)
                        elif aggregate_op == "group_by" and group_by_column and group_counter is not None:
                            group_value = _clip_value(row_map.get(group_by_column, ""), 80)
                            if not group_value:
                                group_value = "<empty>"
                            if group_value not in group_counter and len(group_counter) >= max_group_cap:
                                overflow_group_count += 1
                            else:
                                group_counter[group_value] += 1
                        continue

                    if sort_column_actual:
                        sort_key = _coerce_sort_key(_coerce_str(row_map.get(sort_column_actual)))
                        full_key = (sort_key, row_index)
                        pos = bisect_left(best_keys, full_key)
                        best_keys.insert(pos, full_key)
                        best_rows.insert(pos, (row_index, list(values)))
                        if len(best_rows) > sort_window:
                            if sort_direction == "asc":
                                best_keys.pop()
                                best_rows.pop()
                            else:
                                best_keys.pop(0)
                                best_rows.pop(0)
                        continue

                    if matched_total <= offset:
                        continue
                    if len(rows_out) >= limit:
                        continue
                    rows_out.append(_row_payload(row_index, row_map, columns=selected_columns[:max_column_cap]))

                if sort_column_actual:
                    ordered = list(best_rows)
                    if sort_direction == "desc":
                        ordered = list(reversed(ordered))
                    slice_rows = ordered[offset: offset + limit]
                    for row_index, values in slice_rows:
                        row_map = {header[i]: _coerce_str(values[i]) if i < len(values) else "" for i in range(len(header))}
                        rows_out.append(_row_payload(row_index, row_map, columns=selected_columns[:max_column_cap]))

    else:  # jsonl_gz
        column_schema = []
        if isinstance(sheet_meta, Mapping) and isinstance(sheet_meta.get("column_schema"), list):
            column_schema = [str(col or "").strip() for col in sheet_meta.get("column_schema") if str(col or "").strip()]
        if not column_schema and isinstance(dataset_meta.get("column_schema"), list):
            column_schema = [str(col or "").strip() for col in dataset_meta.get("column_schema") if str(col or "").strip()]
        column_schema = column_schema[:200]

        normalized_schema_map: dict[str, str] = {}
        for col in column_schema:
            norm = _normalize_column_name(col)
            if norm and norm not in normalized_schema_map:
                normalized_schema_map[norm] = col

        resolved_filters: list[dict[str, object]] = []
        for flt in filters_input:
            col_input = _coerce_str(flt.get("column")).strip()
            norm = _normalize_column_name(col_input)
            actual = normalized_schema_map.get(norm)
            if not actual:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "unknown_column",
                    "error_code": "unknown_column",
                    "hint": f"Unknown column {col_input!r}.",
                    "available_columns": column_schema[:50],
                }
            resolved = dict(flt)
            resolved["column"] = actual
            resolved_filters.append(resolved)
        filters_input = resolved_filters

        sort_key_field = ""
        if sort_by_input:
            sort_key_field = normalized_schema_map.get(_normalize_column_name(sort_by_input)) or ""
            if not sort_key_field:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "unknown_sort_column",
                    "error_code": "unknown_sort_column",
                    "hint": f"Unknown sort_by column {sort_by_input!r}.",
                    "available_columns": column_schema[:50],
                }

        if aggregate_op in {"sum", "min", "max"}:
            if not aggregate_column:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "aggregate_column_required",
                    "error_code": "aggregate_column_required",
                    "hint": f"aggregate.column is required when operation={aggregate_op}.",
                }
            actual = normalized_schema_map.get(_normalize_column_name(aggregate_column))
            if not actual:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "unknown_aggregate_column",
                    "error_code": "unknown_aggregate_column",
                    "hint": f"Unknown aggregate column {aggregate_column!r}.",
                    "available_columns": column_schema[:50],
                }
            aggregate_column = actual

        if aggregate_op == "group_by":
            if not group_by_column:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "group_by_required",
                    "error_code": "group_by_required",
                    "hint": "aggregate.group_by is required when operation=group_by.",
                }
            actual = normalized_schema_map.get(_normalize_column_name(group_by_column))
            if not actual:
                return {
                    "tool": "query_dataset",
                    "document_id": str(upload.id),
                    "status": "error",
                    "error": "unknown_group_by_column",
                    "error_code": "unknown_group_by_column",
                    "hint": f"Unknown group_by column {group_by_column!r}.",
                    "available_columns": column_schema[:50],
                }
            group_by_column = actual

        selected_columns = _choose_columns(column_schema, requested=select_columns_input if select_columns_input else None)
        if select_columns_input and not selected_columns:
            return {
                "tool": "query_dataset",
                "document_id": str(upload.id),
                "status": "error",
                "error": "no_selectable_columns",
                "error_code": "no_selectable_columns",
                "hint": "None of the requested select_columns exist in this dataset schema.",
                "available_columns": column_schema[:50],
            }

        preview_only = not (filters_input or query_norm or aggregate_op or sort_key_field)

        with gzip.open(abs_path, "rt", encoding="utf-8", errors="ignore") as handle:
            for row_index, line in enumerate(handle, start=1):
                if preview_only:
                    if _should_stop():
                        truncated = True
                        break
                    if row_index <= offset:
                        continue
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(record, Mapping):
                        continue
                    row_map = {key: _coerce_str(record.get(key)) for key in column_schema}
                    rows_out.append(_row_payload(row_index, row_map, columns=selected_columns[:max_column_cap]))
                    if len(rows_out) >= limit:
                        break
                    continue

                scanned_rows += 1
                if _should_stop():
                    truncated = True
                    break
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                if not isinstance(record, Mapping):
                    continue
                row_map = {key: _coerce_str(record.get(key)) for key in column_schema}

                if query_norm:
                    hit = False
                    for val in record.values():
                        if query_norm in _coerce_str(val).lower():
                            hit = True
                            break
                    if not hit:
                        continue

                if not _matches_filters(row_map):
                    continue

                matched_total += 1

                if aggregate_op:
                    if aggregate_op == "count":
                        continue
                    if aggregate_op in {"sum", "min", "max"} and aggregate_column:
                        numeric = _parse_numeric_value(_coerce_str(record.get(aggregate_column)))
                        if numeric is None:
                            continue
                        value = float(numeric)
                        if aggregate_op == "sum":
                            sum_total += value
                            sum_count += 1
                        elif aggregate_op == "min":
                            min_value = value if min_value is None else min(min_value, value)
                        elif aggregate_op == "max":
                            max_value = value if max_value is None else max(max_value, value)
                    elif aggregate_op == "group_by" and group_by_column and group_counter is not None:
                        group_value = _clip_value(record.get(group_by_column, ""), 80)
                        if not group_value:
                            group_value = "<empty>"
                        if group_value not in group_counter and len(group_counter) >= max_group_cap:
                            overflow_group_count += 1
                        else:
                            group_counter[group_value] += 1
                    continue

                if sort_key_field:
                    sort_key = _coerce_sort_key(_coerce_str(record.get(sort_key_field)))
                    full_key = (sort_key, row_index)
                    pos = bisect_left(best_keys, full_key)
                    best_keys.insert(pos, full_key)
                    best_rows.insert(pos, (row_index, dict(record)))
                    if len(best_rows) > sort_window:
                        if sort_direction == "asc":
                            best_keys.pop()
                            best_rows.pop()
                        else:
                            best_keys.pop(0)
                            best_rows.pop(0)
                    continue

                if matched_total <= offset:
                    continue
                if len(rows_out) >= limit:
                    continue
                rows_out.append(_row_payload(row_index, row_map, columns=selected_columns[:max_column_cap]))

        if preview_only:
            matched_total = int(dataset_meta.get("row_count") or 0)

        if sort_key_field:
            ordered = list(best_rows)
            if sort_direction == "desc":
                ordered = list(reversed(ordered))
            slice_rows = ordered[offset: offset + limit]
            for row_index, record in slice_rows:
                row_map = {key: _coerce_str(record.get(key)) for key in column_schema}
                rows_out.append(_row_payload(row_index, row_map, columns=selected_columns[:max_column_cap]))

    duration_ms = int((time.perf_counter() - start) * 1000)

    status = "ok" if (rows_out or aggregate_op) else "not_found"

    hint = None
    if isinstance(available_sheets, list) and available_sheets and not sheet_name_input and sheet_index_input is None:
        sheet_names = [str(entry.get("sheet_name") or "") for entry in available_sheets[:6] if isinstance(entry, Mapping)]
        sheet_names = [name for name in sheet_names if name.strip()]
        if sheet_names:
            hint = "Multiple sheets detected. Specify sheet_name or sheet_index for more precise answers."

    if not hint and not filters_input and not query_norm and not aggregate_op:
        row_count_hint = sheet_meta.get("row_count") if isinstance(sheet_meta, Mapping) else dataset_meta.get("row_count")
        if row_count_hint and int(row_count_hint) > 20000:
            hint = "This is a large dataset. For accurate lookups, provide a specific identifier (e.g., order_id / ticket_id / email) and the column to match."

    payload: dict[str, object] = {
        "tool": "query_dataset",
        "status": status,
        "document_id": str(upload.id),
        "dataset_mode": True,
        "dataset": dataset_summary,
        "query_engine": query_engine,
        "storage_format": storage_format,
        "storage_path": storage_rel_path,
        "sheet_name": sheet_name_out,
        "sheet_index": sheet_index_out,
        "query": query_input or None,
        "filters": filters_input or None,
        "select_columns": select_columns_input or None,
        "sort_by": sort_by_input or None,
        "sort_direction": sort_direction,
        "offset": offset,
        "limit": limit,
        "rows": rows_out,
        "match_count": len(rows_out),
        "total_matches": matched_total,
        "scanned_rows": scanned_rows,
        "duration_ms": duration_ms,
        "truncated": truncated or None,
        "hint": hint,
    }

    if aggregate_op:
        if aggregate_result is None:
            if aggregate_op == "count":
                aggregate_result = {"operation": "count", "count": matched_total}
            elif aggregate_op == "sum":
                aggregate_result = {
                    "operation": "sum",
                    "column": aggregate_column or None,
                    "sum": sum_total,
                    "sum_display": _format_numeric_display(sum_total),
                    "numeric_match_count": sum_count,
                }
            elif aggregate_op == "min":
                aggregate_result = {
                    "operation": "min",
                    "column": aggregate_column or None,
                    "min": min_value,
                    "min_display": _format_numeric_display(min_value) if min_value is not None else None,
                }
            elif aggregate_op == "max":
                aggregate_result = {
                    "operation": "max",
                    "column": aggregate_column or None,
                    "max": max_value,
                    "max_display": _format_numeric_display(max_value) if max_value is not None else None,
                }
            elif aggregate_op == "group_by" and group_counter is not None:
                # Counter.most_common() has undefined order for equal counts.
                # Sort by count (desc), then by value (asc) for deterministic results.
                all_items = list(group_counter.items())
                all_items.sort(key=lambda x: (-x[1], str(x[0])))
                most_common = all_items[:top_groups]
                aggregate_result = {
                    "operation": "group_by",
                    "group_by": group_by_column or None,
                    "groups": [{"value": key, "count": count} for key, count in most_common],
                    "overflow_group_count": overflow_group_count or None,
                    "tracked_groups": len(group_counter),
                }
        if aggregate_result is not None:
            payload["aggregate_result"] = aggregate_result

    base_payload = dict(payload)
    base_payload.pop("rows", None)
    base_overhead = _json_char_len({k: v for k, v in base_payload.items() if v not in (None, "") and v != []})
    if max_payload_chars is not None and base_overhead >= max_payload_chars:
        raise CharacterBudgetExceeded("Character budget too low to return dataset query metadata.")

    # Re-trim rows if needed to fit into the remaining character budget.
    raw_rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    if raw_rows and max_payload_chars is not None:
        trimmed_rows: list[dict[str, object]] = []
        running = base_overhead
        for row in raw_rows:
            if not isinstance(row, Mapping):
                continue
            row_payload = dict(row)
            row_chars = _json_char_len(row_payload) + 1
            if trimmed_rows and running + row_chars > max_payload_chars:
                break
            trimmed_rows.append(row_payload)
            running += row_chars
        if len(trimmed_rows) < len(raw_rows):
            payload["rows"] = trimmed_rows
            payload["match_count"] = len(trimmed_rows)
            payload["throttle_notice"] = {
                "reason": "prompt_budget",
                "message": (
                    "Dataset rows were truncated to stay within prompt size limits. "
                    "Re-run read_knowledge with intent=table and narrower filters or fewer columns."
                ),
                "returned_match_count": len(trimmed_rows),
            }

    char_count = _json_char_len({k: v for k, v in payload.items() if v not in (None, "") and v != []})
    payload["char_count"] = char_count
    payload["token_estimate"] = _estimate_tokens(char_count)
    try:
        context.reserve_characters(char_count)
    except CharacterBudgetExceeded as exc:
        return {
            "tool": "query_dataset",
            "document_id": str(upload.id),
            "status": "throttled",
            "error": "prompt_budget_exceeded",
            "error_code": "prompt_budget_exceeded",
            "rows": [],
            "match_count": 0,
            "total_matches": matched_total,
            "throttle_notice": {"type": "prompt_budget", "message": str(exc)},
            "hint": "Prompt budget exceeded. Narrow filters/select_columns and retry.",
        }

    structured_log(
        "mcp",
        "dataset.query",
        {
            "document_id": str(upload.id),
            "query_engine": query_engine,
            "storage_format": storage_format,
            "sheet_index": sheet_index_out,
            "sheet_name": sheet_name_out,
            "filters_count": len(filters_input),
            **_log_safe_text_fields("query", query_input or None),
            "sort_by": sort_by_input or None,
            "sort_direction": sort_direction,
            "limit": limit,
            "offset": offset,
            "status": payload.get("status"),
            "match_count": payload.get("match_count"),
            "total_matches": matched_total,
            "scanned_rows": scanned_rows,
            "aggregate_op": aggregate_op or None,
            "duration_ms": duration_ms,
            "truncated": bool(payload.get("truncated")),
            "char_count": char_count,
            "token_estimate": payload.get("token_estimate"),
        },
        context={
            "business": conversation.business_profile_id,
            "conversation": conversation.id,
            "document_id": str(upload.id),
        },
        logger_obj=logger,
        level=(
            logging.WARNING
            if (
                int(duration_ms or 0)
                >= int(getattr(settings, "MCP_SLO_DATASET_QUERY_WARN_MS", 1500) or 0)
                and int(getattr(settings, "MCP_SLO_DATASET_QUERY_WARN_MS", 1500) or 0) > 0
            )
            else logging.INFO
        ),
    )
    return payload


def _read_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Single LLM-facing retrieval tool.

    Routes to one of:
    - _read_document_handler (text/PDF excerpts)
    - _table_aggregate_handler (small/preview tables)
    - _dataset_query_handler (dataset-mode uploads)
    """

    start = time.perf_counter()
    raw_id = _coerce_str(arguments.get("document_id")).strip()
    if not raw_id:
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "document_id is required",
            "error_code": "missing_document_id",
            "hint": "Use snippets[].read_hint.document_id from search_knowledge (do not guess document IDs).",
        }
    try:
        identifier = uuid.UUID(raw_id)
    except (TypeError, ValueError):
        return {
            "tool": "read_knowledge",
            "status": "error",
            "error": "document_id must be a valid UUID",
            "error_code": "invalid_document_id",
            "hint": "Use snippets[].read_hint.document_id from search_knowledge (do not guess document IDs).",
        }

    window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
    try:
        calls_per_minute = int(getattr(settings, "MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE", 120) or 0)
    except (TypeError, ValueError):
        calls_per_minute = 120
    calls_per_minute = 0 if calls_per_minute < 0 else calls_per_minute
    try:
        enforce_tool_rate_limit(
            business_profile=conversation.business_profile,
            tool="read_knowledge",
            rate_limit=ToolRateLimit(
                calls_per_minute=None if calls_per_minute <= 0 else calls_per_minute,
                window_seconds=window_seconds,
                scope="business",
            ),
        )
    except ToolRateLimitExceeded as exc:
        return {
            "tool": "read_knowledge",
            "status": "throttled",
            "engine": None,
            "document_id": raw_id,
            "evidence": {"snippets": [], "rows": []},
            "total_matches": 0,
            "truncated": False,
            "throttle_notice": {"type": "rate_limited", "message": str(exc)},
            "error": "rate_limited",
            "error_code": "rate_limited",
            "hint": str(exc),
        }

    business = conversation.business_profile
    chunk_record = (
        apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                id=identifier,
                business_profile=business,
                upload__status=KnowledgeStatus.ACTIVE,
            )
        )
        .select_related("upload")
        .first()
    )
    upload_record: KnowledgeUpload | None = chunk_record.upload if chunk_record else None
    if upload_record is None:
        upload_record = apply_customer_visible_uploads(
            KnowledgeUpload.objects.filter(
                id=identifier,
                business_profile=business,
                status=KnowledgeStatus.ACTIVE,
            )
        ).first()

    if upload_record is None:
        payload = {
            "tool": "read_knowledge",
            "status": "not_found",
            "error": "document not found for this business",
        }
        duration_ms = int((time.perf_counter() - start) * 1000.0)
        warn_ms = int(getattr(settings, "MCP_SLO_READ_KNOWLEDGE_WARN_MS", 1500) or 0)
        slow = bool(warn_ms and duration_ms >= warn_ms)
        structured_log(
            "mcp",
            "read_knowledge.performance",
            {
                "status": payload.get("status"),
                "engine": None,
                "engine_tool": None,
                "duration_ms": duration_ms,
                "requested_document_id": raw_id,
                "slo": "slow" if slow else None,
                "slo_warn_ms": warn_ms if slow else None,
            },
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            logger_obj=logger,
            level=logging.WARNING,
        )
        return payload

    agent_scope = _agent_knowledge_scope(conversation, context)
    if upload_record.id and not _agent_scope_allows_upload(scope=agent_scope, conversation=conversation, upload_id=upload_record.id):
        return {
            "tool": "read_knowledge",
            "status": "constraint_error",
            "engine": None,
            "document_id": raw_id,
            "evidence": {"snippets": [], "rows": []},
            "total_matches": 0,
            "truncated": False,
            "error": "forbidden_document",
            "error_code": "forbidden_document",
            "hint": "This agent is not permitted to access that document.",
        }

    chunk_meta = chunk_record.metadata if chunk_record and isinstance(getattr(chunk_record, "metadata", None), Mapping) else {}
    ingestion_meta = (
        upload_record.ingestion_metadata
        if isinstance(getattr(upload_record, "ingestion_metadata", None), Mapping)
        else {}
    )
    format_hint = str(ingestion_meta.get("format") or "").strip().lower()
    native_tabular = format_hint in {"csv", "tsv", "xls", "xlsx", "jsonl"}
    is_document_format = format_hint in {"pdf", "docx", "txt", "text"} or not format_hint
    dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, Mapping) else None
    dataset_enabled = bool(isinstance(dataset_meta, Mapping) and dataset_meta.get("enabled"))
    upload_has_tables = bool(upload_record.tables.exists())

    intent = _coerce_str(arguments.get("intent") or "auto").strip().lower() or "auto"
    raw_text_args = arguments.get("text") if isinstance(arguments.get("text"), Mapping) else {}
    raw_table_args = arguments.get("table") if isinstance(arguments.get("table"), Mapping) else {}
    text_args: dict[str, object] = dict(raw_text_args) if isinstance(raw_text_args, Mapping) else {}
    table_args: dict[str, object] = dict(raw_table_args) if isinstance(raw_table_args, Mapping) else {}

    # Backwards-compatible: accept legacy flat args when models omit nested objects.
    for key in ("page", "offset", "mode", "token_budget", "chunk_neighbor"):
        if key in arguments and key not in text_args:
            text_args[key] = arguments.get(key)
    for key in (
        "sheet_name",
        "sheet_index",
        "table_order_index",
        "match_column",
        "match_value",
        "match_values",
        "query",
        "filters",
        "select_columns",
        "columns",
        "sort_by",
        "sort_direction",
        "limit",
        "offset",
        "aggregate",
        "mode",
        "value_column",
        "max_rows",
    ):
        if key in arguments and key not in table_args:
            table_args[key] = arguments.get(key)

    is_table_chunk = bool(chunk_meta.get("is_table_chunk"))
    is_dataset_card = bool(
        chunk_meta.get("is_dataset_card")
        or chunk_meta.get("strategy") == "dataset_card"
        or chunk_meta.get("dataset_mode")
    )

    def _has_table_signal(payload: Mapping[str, object]) -> bool:
        for key in (
            "match_column",
            "match_value",
            "match_values",
            "filters",
            "query",
            "select_columns",
            "columns",
            "sort_by",
            "aggregate",
            "sheet_name",
            "sheet_index",
            "table_order_index",
            "value_column",
        ):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set, dict)) and not value:
                continue
            return True
        return False

    def _has_strong_table_signal(payload: Mapping[str, object]) -> bool:
        """Strong signals that indicate a real table query (not just sheet/index hints)."""
        for key in ("query", "match_column", "match_value", "match_values", "filters"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
            if isinstance(value, (list, dict)) and value:
                return True
        return False

    wants_table = False
    wants_text = False
    if intent == "table":
        wants_table = True
    elif intent == "text":
        wants_text = True

    if is_dataset_card:
        wants_table = True

    if is_document_format:
        # Deterministic document routing: PDFs/DOCX/TXT are read as text even if they contain visual tables.
        wants_text = True
        wants_table = False
    elif _has_table_signal(table_args):
        wants_table = True

    if not wants_text and not wants_table:
        # Auto routing: prefer tabular engines only for native tabular formats or datasets.
        wants_table = dataset_enabled or native_tabular
        wants_text = not wants_table

    def _log_read_knowledge_performance(envelope: Mapping[str, object]) -> None:
        duration_ms = int((time.perf_counter() - start) * 1000.0)
        warn_ms = int(getattr(settings, "MCP_SLO_READ_KNOWLEDGE_WARN_MS", 1500) or 0)
        status_value = str(envelope.get("status") or "").strip().lower() or "ok"
        slow = bool(warn_ms and duration_ms >= warn_ms)
        resolved_upload_id = str(envelope.get("document_id") or upload_record.id)

        diagnostics = envelope.get("diagnostics")
        if isinstance(diagnostics, dict):
            diagnostics["read_knowledge_duration_ms"] = duration_ms

        detail: dict[str, object] = {
            "status": envelope.get("status"),
            "engine": envelope.get("engine"),
            "engine_tool": (
                diagnostics.get("engine_tool") if isinstance(diagnostics, Mapping) else None
            ),
            "duration_ms": duration_ms,
            "total_matches": envelope.get("total_matches"),
            "truncated": envelope.get("truncated"),
            "intent": intent,
            "wants_table": wants_table,
            "wants_text": wants_text,
            "dataset_enabled": dataset_enabled,
            "upload_has_tables": upload_has_tables,
            "is_table_chunk": is_table_chunk,
            "is_dataset_card": is_dataset_card,
        }
        # Include auto_fallback if present
        auto_fallback = diagnostics.get("auto_fallback") if isinstance(diagnostics, Mapping) else None
        if auto_fallback:
            detail["auto_fallback"] = auto_fallback
            detail["prior_engine"] = diagnostics.get("prior_engine") if isinstance(diagnostics, Mapping) else None
            detail["prior_status"] = diagnostics.get("prior_status") if isinstance(diagnostics, Mapping) else None
        error_code = envelope.get("error_code")
        if error_code not in (None, ""):
            detail["error_code"] = error_code
        if slow:
            detail["slo"] = "slow"
            detail["slo_warn_ms"] = warn_ms

        structured_log(
            "mcp",
            "read_knowledge.performance",
            detail,
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
                "document_id": resolved_upload_id,
            },
            logger_obj=logger,
            level=logging.WARNING if slow or status_value in {"error", "constraint_error"} else logging.INFO,
        )

    def _envelope(
        *,
        engine: str,
        engine_tool: str,
        result: Mapping[str, object],
        resolved_upload_id: str,
        requested_document_id: str,
        resolved_chunk_id: str | None,
        resolved_document_id_used: str,
        identifier_diagnostics: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        status_value = str(result.get("status") or "").strip() if isinstance(result, Mapping) else ""
        status_value = status_value or "ok"

        throttle_notice = result.get("throttle_notice") if isinstance(result.get("throttle_notice"), Mapping) else None
        truncated_flag = bool(result.get("truncated")) if isinstance(result.get("truncated"), bool) else False
        truncated_flag = bool(truncated_flag or throttle_notice)

        evidence: dict[str, object] = {"snippets": [], "rows": []}
        total_matches: int | None = None

        diagnostics: dict[str, object] = {
            "requested_document_id": requested_document_id,
            "resolved_upload_id": resolved_upload_id,
            "resolved_chunk_id": resolved_chunk_id,
            "resolved_document_id_used": resolved_document_id_used,
            "engine_tool": engine_tool,
        }
        if identifier_diagnostics:
            try:
                diagnostics.update(dict(identifier_diagnostics))
            except Exception:
                pass

        identifier_gate = result.get("identifier_gate") if isinstance(result.get("identifier_gate"), Mapping) else None
        if identifier_gate:
            diagnostics["identifier_gate"] = identifier_gate
        required_identifiers = result.get("required_identifiers") if isinstance(result.get("required_identifiers"), list) else None
        if required_identifiers:
            diagnostics["required_identifiers"] = [str(item) for item in required_identifiers[:12] if str(item).strip()]
        provided_identifiers = result.get("provided_identifiers") if isinstance(result.get("provided_identifiers"), list) else None
        if provided_identifiers:
            diagnostics["provided_identifiers"] = [str(item) for item in provided_identifiers[:12] if str(item).strip()]

        if engine == "text_page":
            snippets = result.get("snippets") if isinstance(result.get("snippets"), list) else []
            evidence["snippets"] = snippets
            total_matches = len(snippets)
            for key in ("document_id", "page", "mode", "mode_downgraded", "token_budget", "chunk_neighbor"):
                value = result.get(key)
                if value not in (None, "", []):
                    diagnostics[key] = value
            reads = result.get("knowledge_reads") if isinstance(result.get("knowledge_reads"), list) else None
            if reads:
                diagnostics["knowledge_reads"] = reads
            warnings = result.get("ingestion_warnings") if isinstance(result.get("ingestion_warnings"), list) else None
            if warnings:
                diagnostics["ingestion_warnings"] = warnings
        elif engine in {"table_preview", "db_preview"}:
            rows = result.get("rows") if isinstance(result.get("rows"), list) else []
            evidence["rows"] = rows
            original_count = result.get("original_match_count")
            if isinstance(original_count, int):
                total_matches = original_count
            elif isinstance(result.get("total_matches"), int):
                total_matches = int(result.get("total_matches"))
            elif isinstance(result.get("match_count"), int):
                total_matches = int(result.get("match_count"))
            else:
                total_matches = len(rows)
            for key in (
                "mode",
                "query",
                "match_column",
                "match_value",
                "match_values",
                "value_column",
                "sheet_name",
                "columns",
                "evaluated_rows",
                "row_limit",
                "match_count",
                "total",
                "display_total",
            ):
                value = result.get(key)
                if value not in (None, "", []):
                    diagnostics[key] = value
            if "total" in result and result.get("total") is not None:
                evidence["total"] = result.get("total")
            if "display_total" in result and result.get("display_total") not in (None, ""):
                evidence["display_total"] = result.get("display_total")
        elif engine == "file_dataset":
            rows = result.get("rows") if isinstance(result.get("rows"), list) else []
            evidence["rows"] = rows
            if isinstance(result.get("total_matches"), int):
                total_matches = int(result.get("total_matches"))
            elif isinstance(result.get("match_count"), int):
                total_matches = int(result.get("match_count"))
            else:
                total_matches = len(rows)
            aggregate_result = result.get("aggregate_result") if isinstance(result.get("aggregate_result"), Mapping) else None
            if aggregate_result:
                evidence["aggregate_result"] = dict(aggregate_result)
            for key in (
                "sheet_name",
                "sheet_index",
                "query",
                "filters",
                "select_columns",
                "sort_by",
                "sort_direction",
                "offset",
                "limit",
                "match_count",
                "total_matches",
                "scanned_rows",
                "duration_ms",
                "dataset",
            ):
                value = result.get(key)
                if value not in (None, "", []):
                    diagnostics[key] = value

        if engine in {"table_preview", "db_preview", "file_dataset"} and _tabular_privacy_enabled():
            verified_policy = _verified_lookup_policy(conversation)
            strict_pii = bool(verified_policy.get("enabled")) and bool(verified_policy.get("require_for_pii"))
            verified_lookup, verified_source = _conversation_is_verified_for_lookup(
                conversation,
                allow_customer_match=bool(verified_policy.get("allow_customer_match")),
            )
            diagnostics["verified_lookup"] = {
                "verified": bool(verified_lookup),
                "source": verified_source,
                "pii_requires_verification": bool(strict_pii),
            }
            rows_in = evidence.get("rows") if isinstance(evidence.get("rows"), list) else []
            evidence["rows"] = _sanitize_tabular_rows_for_prompt(
                [row for row in rows_in if isinstance(row, Mapping)],
                upload=upload_record,
                verified=bool(verified_lookup),
                strict_pii=bool(strict_pii),
            )
            if engine == "file_dataset":
                aggregate_in = evidence.get("aggregate_result") if isinstance(evidence.get("aggregate_result"), Mapping) else None
                if aggregate_in:
                    evidence["aggregate_result"] = _sanitize_dataset_aggregate_for_prompt(
                        aggregate_in,
                        upload=upload_record,
                        verified=bool(verified_lookup),
                        strict_pii=bool(strict_pii),
                    )
            diagnostics["privacy_applied"] = True

        envelope: dict[str, object] = {
            "tool": "read_knowledge",
            "status": status_value,
            "engine": engine,
            "document_id": resolved_upload_id,
            "evidence": evidence,
            "total_matches": total_matches,
            "truncated": truncated_flag,
            "throttle_notice": throttle_notice,
            "diagnostics": diagnostics,
        }

        for key in ("error", "error_code", "hint"):
            value = result.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set, dict)) and not value:
                continue
            envelope[key] = value

        return envelope

    def _row_cell_value(row: Mapping[str, object], *, column_norm: str) -> str | None:
        if not column_norm:
            return None
        cells = row.get("cells") if isinstance(row.get("cells"), list) else []
        for cell in cells:
            if not isinstance(cell, Mapping):
                continue
            label = _normalize_column_name(cell.get("column"))
            if not label:
                continue
            if label != column_norm:
                continue
            value = cell.get("value")
            text = str(value).strip() if value is not None else ""
            return text or None
        return None

    def _collect_identifier_values(rows: Sequence[Mapping[str, object]], *, column: str) -> list[str]:
        column_norm = _normalize_column_name(column)
        if not column_norm:
            return []
        seen: set[str] = set()
        values_out: list[str] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            value = _row_cell_value(row, column_norm=column_norm)
            if not value:
                continue
            norm_val = _normalize_identifier_value(value)
            if not norm_val or norm_val in seen:
                continue
            seen.add(norm_val)
            values_out.append(value.strip())
            if len(values_out) >= 12:
                break
        return values_out

    def _filter_rows_by_identifier(
        rows: Sequence[Mapping[str, object]],
        *,
        column: str,
        allowed_values: Sequence[str],
    ) -> list[dict[str, object]]:
        column_norm = _normalize_column_name(column)
        allowed_norm = {_normalize_identifier_value(v) for v in allowed_values if _normalize_identifier_value(v)}
        if not column_norm or not allowed_norm:
            return [dict(row) for row in rows if isinstance(row, Mapping)]
        filtered: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            cell_value = _row_cell_value(row, column_norm=column_norm)
            if not cell_value:
                continue
            if _normalize_identifier_value(cell_value) in allowed_norm:
                filtered.append(dict(row))
        return filtered

    if wants_table and not wants_text:
        verified_policy = _verified_lookup_policy(conversation)
        verified_lookup, verified_source = _conversation_is_verified_for_lookup(
            conversation,
            allow_customer_match=bool(verified_policy.get("allow_customer_match")),
        )
        requested_columns = _extract_requested_columns(table_args)
        requested_sensitive_columns = [col for col in requested_columns if _column_is_sensitive(col)]

        if (
            bool(verified_policy.get("enabled"))
            and bool(verified_policy.get("require_for_pii"))
            and requested_sensitive_columns
            and not verified_lookup
        ):
            engine = "file_dataset" if (dataset_enabled or is_dataset_card) else "table_preview"
            engine_tool = "dataset_query" if engine == "file_dataset" else "table_aggregate"
            diag = {
                "verified_lookup": {"verified": False, "source": verified_source, "required": True},
                "requested_sensitive_columns": requested_sensitive_columns[:12],
            }
            envelope = _envelope(
                engine=engine,
                engine_tool=engine_tool,
                result={
                    "status": "verification_required",
                    "error": "verification_required",
                    "error_code": "verification_required",
                    "rows": [],
                    "match_count": 0,
                    "total_matches": 0,
                    "hint": (
                        "This request includes sensitive fields (PII) and requires identity verification before I can share them. "
                        "Verify the customer (OTP/email verification or an authenticated customer match) and retry."
                    ),
                },
                resolved_upload_id=str(upload_record.id),
                requested_document_id=raw_id,
                resolved_chunk_id=str(chunk_record.id) if chunk_record else None,
                resolved_document_id_used=str(upload_record.id),
                identifier_diagnostics=diag,
            )
            _log_read_knowledge_performance(envelope)
            return envelope

        # Dataset mode can answer lookups/filters/sorts precisely across all rows.
        if dataset_enabled or is_dataset_card:
            dataset_args: dict[str, object] = {"document_id": str(upload_record.id)}
            identifier_diag: dict[str, object] = {}
            requested_identifier_column = ""
            requested_identifier_values: list[str] = []
            requested_identifier_policy = ""

            sheet_name = _coerce_str(table_args.get("sheet_name")).strip()
            if sheet_name:
                dataset_args["sheet_name"] = sheet_name
            try:
                sheet_index = int(table_args.get("sheet_index")) if table_args.get("sheet_index") is not None else None
            except (TypeError, ValueError):
                sheet_index = None
            if sheet_index:
                dataset_args["sheet_index"] = sheet_index

            match_column = _coerce_str(table_args.get("match_column")).strip()
            match_value = _coerce_str(table_args.get("match_value")).strip()
            match_values = table_args.get("match_values") if isinstance(table_args.get("match_values"), list) else []
            match_values_clean = [_coerce_str(v).strip() for v in match_values if _coerce_str(v).strip()]
            if match_column and match_value:
                requested_identifier_column = match_column
                requested_identifier_values = [match_value]
                requested_identifier_policy = "eq"
            elif match_column and match_values_clean:
                requested_identifier_column = match_column
                requested_identifier_values = match_values_clean
                requested_identifier_policy = "in"

            filters_input = table_args.get("filters")
            filters_out: list[dict[str, object]] = []
            if isinstance(filters_input, list) and filters_input:
                for entry in filters_input:
                    if not isinstance(entry, Mapping):
                        continue
                    column = _coerce_str(entry.get("column")).strip()
                    op = _coerce_str(entry.get("op")).strip().lower()
                    if not column or not op:
                        continue
                    value = _coerce_str(entry.get("value")).strip() if entry.get("value") is not None else ""
                    values = entry.get("values") if isinstance(entry.get("values"), list) else []
                    values_clean = [_coerce_str(v).strip() for v in values if _coerce_str(v).strip()]
                    case_sensitive = bool(entry.get("case_sensitive") or False)

                    if _column_suggests_identifier(column) and op not in {"eq", "in"}:
                        if value and _should_force_exact_identifier_match(column, value):
                            op = "eq"
                        else:
                            identifier_diag = {
                                "requested_identifier": {"column": column, "values": [value] if value else [], "policy": "eq"},
                                "match_policy": "eq_required",
                            }
                            envelope = _envelope(
                                engine="file_dataset",
                                engine_tool="dataset_query",
                                result={
                                    "status": "disambiguation_required",
                                    "error": "identifier_exact_match_required",
                                    "error_code": "identifier_exact_match_required",
                                    "rows": [],
                                    "match_count": 0,
                                    "total_matches": 0,
                                    "hint": (
                                        f"Provide the exact {column} value (full identifier) to look up a single record. "
                                        "Partial/contains matching is not allowed for identifiers."
                                    ),
                                },
                                resolved_upload_id=str(upload_record.id),
                                requested_document_id=raw_id,
                                resolved_chunk_id=str(chunk_record.id) if chunk_record else None,
                                resolved_document_id_used=str(upload_record.id),
                                identifier_diagnostics=identifier_diag,
                            )
                            _log_read_knowledge_performance(envelope)
                            return envelope

                    filter_payload: dict[str, object] = {"column": column, "op": op}
                    if op == "in":
                        if values_clean:
                            filter_payload["values"] = values_clean
                        elif value:
                            filter_payload["values"] = [value]
                    else:
                        if value:
                            filter_payload["value"] = value
                        filter_payload["case_sensitive"] = case_sensitive

                    if filter_payload.get("values") or filter_payload.get("value"):
                        filters_out.append(filter_payload)

                    if not requested_identifier_column and _column_suggests_identifier(column):
                        if op == "eq" and value:
                            requested_identifier_column = column
                            requested_identifier_values = [value]
                            requested_identifier_policy = "eq"
                        elif op == "in" and values_clean:
                            requested_identifier_column = column
                            requested_identifier_values = values_clean
                            requested_identifier_policy = "in"

                if filters_out:
                    dataset_args["filters"] = filters_out
            elif requested_identifier_column and requested_identifier_values:
                if requested_identifier_policy == "in" and len(requested_identifier_values) > 1:
                    dataset_args["filters"] = [
                        {"column": requested_identifier_column, "op": "in", "values": requested_identifier_values}
                    ]
                elif requested_identifier_policy == "in":
                    dataset_args["filters"] = [
                        {"column": requested_identifier_column, "op": "eq", "value": requested_identifier_values[0], "case_sensitive": False}
                    ]
                    requested_identifier_policy = "eq"
                else:
                    dataset_args["filters"] = [
                        {"column": requested_identifier_column, "op": "eq", "value": requested_identifier_values[0], "case_sensitive": False}
                    ]

            query = _coerce_str(table_args.get("query")).strip()
            if query and not dataset_args.get("filters") and not requested_identifier_column:
                identifier_candidate = _extract_identifier_candidate(query)
                if identifier_candidate and _should_force_exact_identifier_match("id", identifier_candidate):
                    column_schema: list[str] = []
                    available_sheets = dataset_meta.get("sheets") if isinstance(dataset_meta, Mapping) else None
                    sheet_meta: Mapping[str, object] | None = None
                    normalized_request = _normalize_column_name(sheet_name) if sheet_name else ""
                    if isinstance(available_sheets, list) and available_sheets:
                        if sheet_index:
                            for entry in available_sheets:
                                if not isinstance(entry, Mapping):
                                    continue
                                if int(entry.get("sheet_index") or 0) == sheet_index:
                                    sheet_meta = entry
                                    break
                        if sheet_meta is None and normalized_request:
                            for entry in available_sheets:
                                if not isinstance(entry, Mapping):
                                    continue
                                candidate = _normalize_column_name(entry.get("sheet_name"))
                                if candidate and candidate == normalized_request:
                                    sheet_meta = entry
                                    break
                        if sheet_meta is None:
                            sheet_meta = next((entry for entry in available_sheets if isinstance(entry, Mapping)), None)
                        if sheet_meta and isinstance(sheet_meta.get("column_schema"), list):
                            column_schema = [
                                str(col or "").strip()
                                for col in sheet_meta.get("column_schema")  # type: ignore[arg-type]
                                if str(col or "").strip()
                            ]
                    if not column_schema and isinstance(dataset_meta, Mapping) and isinstance(dataset_meta.get("column_schema"), list):
                        column_schema = [
                            str(col or "").strip()
                            for col in dataset_meta.get("column_schema")  # type: ignore[arg-type]
                            if str(col or "").strip()
                        ]
                    chosen_identifier_col = _pick_best_identifier_column(
                        column_schema,
                        query_text=query,
                        identifier_value=identifier_candidate,
                    )
                    if chosen_identifier_col:
                        requested_identifier_column = chosen_identifier_col
                        requested_identifier_values = [identifier_candidate]
                        requested_identifier_policy = "eq"
                        dataset_args["filters"] = [
                            {
                                "column": chosen_identifier_col,
                                "op": "eq",
                                "value": identifier_candidate,
                                "case_sensitive": False,
                            }
                        ]
                        query = ""

            if query:
                dataset_args["query"] = query

            selected_from_schema = False
            if requested_identifier_column and requested_identifier_values and requested_identifier_policy == "eq":
                column_schema: list[str] = []
                available_sheets = dataset_meta.get("sheets") if isinstance(dataset_meta, Mapping) else None
                sheet_meta: Mapping[str, object] | None = None
                if isinstance(available_sheets, list) and available_sheets:
                    normalized_request = _normalize_column_name(sheet_name) if sheet_name else ""
                    if not sheet_index and not normalized_request:
                        hits = match_upload_for_identifier(
                            upload=upload_record,
                            identifier_value=requested_identifier_values[0],
                        )
                        unique_sheets = {(hit.sheet_index, hit.sheet_name) for hit in hits if hit.sheet_index or hit.sheet_name}
                        if len(unique_sheets) == 1:
                            target_index, target_name = next(iter(unique_sheets))
                            if target_index is not None:
                                for entry in available_sheets:
                                    if not isinstance(entry, Mapping):
                                        continue
                                    if int(entry.get("sheet_index") or 0) == int(target_index):
                                        sheet_meta = entry
                                        break
                            if sheet_meta is None and target_name:
                                target_norm = _normalize_column_name(target_name)
                                for entry in available_sheets:
                                    if not isinstance(entry, Mapping):
                                        continue
                                    if _normalize_column_name(entry.get("sheet_name")) == target_norm:
                                        sheet_meta = entry
                                        break
                        elif len(unique_sheets) > 1:
                            options = sorted(
                                unique_sheets,
                                key=lambda pair: (
                                    int(pair[0] or 0),
                                    str(pair[1] or ""),
                                ),
                            )[:8]
                            envelope = _envelope(
                                engine="file_dataset",
                                engine_tool="dataset_query",
                                result={
                                    "status": "disambiguation_required",
                                    "error": "sheet_disambiguation_required",
                                    "error_code": "sheet_disambiguation_required",
                                    "rows": [],
                                    "match_count": 0,
                                    "total_matches": 0,
                                    "hint": (
                                        "This identifier may exist in multiple dataset sheets. Specify sheet_name or "
                                        "sheet_index before querying."
                                    ),
                                    "sheet_options": [
                                        {"sheet_index": idx, "sheet_name": name}
                                        for idx, name in options
                                    ],
                                },
                                resolved_upload_id=str(upload_record.id),
                                requested_document_id=raw_id,
                                resolved_chunk_id=str(chunk_record.id) if chunk_record else None,
                                resolved_document_id_used=str(upload_record.id),
                                identifier_diagnostics={
                                    "requested_identifier": {
                                        "column": requested_identifier_column,
                                        "values": list(requested_identifier_values),
                                        "policy": requested_identifier_policy,
                                    },
                                    "match_policy": "sheet_disambiguation_required",
                                },
                            )
                            _log_read_knowledge_performance(envelope)
                            return envelope
                        if sheet_index:
                            for entry in available_sheets:
                                if not isinstance(entry, Mapping):
                                    continue
                                if int(entry.get("sheet_index") or 0) == sheet_index:
                                    sheet_meta = entry
                                    break
                    if sheet_meta is None and normalized_request:
                        for entry in available_sheets:
                            if not isinstance(entry, Mapping):
                                continue
                            candidate = _normalize_column_name(entry.get("sheet_name"))
                            if candidate and candidate == normalized_request:
                                sheet_meta = entry
                                break
                    if sheet_meta is None:
                        sheet_meta = next((entry for entry in available_sheets if isinstance(entry, Mapping)), None)
                    if sheet_meta and isinstance(sheet_meta.get("column_schema"), list):
                        column_schema = [
                            str(col or "").strip()
                            for col in sheet_meta.get("column_schema")  # type: ignore[arg-type]
                            if str(col or "").strip()
                        ]
                if not column_schema and isinstance(dataset_meta, Mapping) and isinstance(dataset_meta.get("column_schema"), list):
                    column_schema = [
                        str(col or "").strip()
                        for col in dataset_meta.get("column_schema")  # type: ignore[arg-type]
                        if str(col or "").strip()
                    ]
                if column_schema:
                    max_columns_exact = int(getattr(settings, "DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT", 50) or 50)
                    max_columns_exact = max(3, min(50, max_columns_exact))
                    if len(column_schema) > max_columns_exact:
                        column_schema = column_schema[:max_columns_exact]
                    dataset_args["select_columns"] = column_schema
                    selected_from_schema = True

            if not selected_from_schema:
                select_columns = table_args.get("select_columns")
                if not isinstance(select_columns, list) or not select_columns:
                    select_columns = table_args.get("columns")
                if isinstance(select_columns, list) and select_columns:
                    chosen = [_coerce_str(c).strip() for c in select_columns if _coerce_str(c).strip()]
                    if requested_identifier_column and requested_identifier_column not in chosen:
                        chosen.insert(0, requested_identifier_column)
                    dataset_args["select_columns"] = chosen

            sort_by = _coerce_str(table_args.get("sort_by")).strip()
            if sort_by:
                dataset_args["sort_by"] = sort_by
            sort_direction = _coerce_str(table_args.get("sort_direction")).strip().lower()
            if sort_direction in {"asc", "desc"}:
                dataset_args["sort_direction"] = sort_direction
            try:
                limit = int(table_args.get("limit")) if table_args.get("limit") is not None else None
            except (TypeError, ValueError):
                limit = None
            if limit:
                dataset_args["limit"] = limit
            try:
                offset = int(table_args.get("offset")) if table_args.get("offset") is not None else None
            except (TypeError, ValueError):
                offset = None
            if offset is not None:
                dataset_args["offset"] = max(0, offset)

            aggregate = table_args.get("aggregate") if isinstance(table_args.get("aggregate"), Mapping) else None
            if aggregate:
                dataset_args["aggregate"] = dict(aggregate)
            else:
                mode = _coerce_str(table_args.get("mode")).strip().lower()
                value_column = _coerce_str(table_args.get("value_column")).strip()
                if mode == "column_sum" and value_column:
                    dataset_args["aggregate"] = {"operation": "sum", "column": value_column}

            result = _dataset_query_handler(dataset_args, conversation=conversation, context=context)
            result_out = dict(result) if isinstance(result, Mapping) else {"status": "error", "error": "invalid_result"}

            if requested_identifier_column and requested_identifier_values:
                identifier_diag["requested_identifier"] = {
                    "column": requested_identifier_column,
                    "values": requested_identifier_values[:12],
                    "policy": requested_identifier_policy or "eq",
                }
                rows = result_out.get("rows") if isinstance(result_out.get("rows"), list) else []
                status_value = str(result_out.get("status") or "").strip().lower()
                filtered_rows = (
                    _filter_rows_by_identifier(
                        rows,
                        column=requested_identifier_column,
                        allowed_values=requested_identifier_values,
                    )
                    if status_value == "ok"
                    else [dict(row) for row in rows if isinstance(row, Mapping)]
                )
                identifier_diag["matched_identifiers"] = _collect_identifier_values(
                    filtered_rows,
                    column=requested_identifier_column,
                )

                if status_value == "ok":
                    if not filtered_rows:
                        result_out["status"] = "not_found"
                        result_out["error"] = "identifier_not_found"
                        result_out["error_code"] = "identifier_not_found"
                        result_out["rows"] = []
                        result_out["match_count"] = 0
                        result_out["total_matches"] = 0
                        result_out["hint"] = (
                            f"No rows found for {requested_identifier_column}={requested_identifier_values[0]!r}. "
                            "Double-check the exact identifier and the column name."
                        )
                    else:
                        matched_values = identifier_diag.get("matched_identifiers")
                        if (
                            len(requested_identifier_values) == 1
                            and isinstance(matched_values, list)
                            and len(matched_values) > 1
                        ):
                            result_out["status"] = "disambiguation_required"
                            result_out["error"] = "identifier_ambiguous"
                            result_out["error_code"] = "identifier_ambiguous"
                            result_out["rows"] = []
                            result_out["match_count"] = 0
                            result_out["total_matches"] = len(matched_values)
                            result_out["hint"] = (
                                f"Multiple {requested_identifier_column} values matched {requested_identifier_values[0]!r}. "
                                f"Choose one exact identifier: {', '.join(str(v) for v in matched_values[:8])}."
                            )
                        else:
                            result_out["rows"] = filtered_rows
                            result_out["match_count"] = len(filtered_rows)

            envelope = _envelope(
                engine="file_dataset",
                engine_tool="dataset_query",
                result=result_out,
                resolved_upload_id=str(upload_record.id),
                requested_document_id=raw_id,
                resolved_chunk_id=str(chunk_record.id) if chunk_record else None,
                resolved_document_id_used=str(upload_record.id),
                identifier_diagnostics=identifier_diag or None,
            )
            _log_read_knowledge_performance(envelope)
            if envelope.get("status") == "ok" and _has_prompt_evidence(envelope):
                _record_knowledge_audit_event_once(
                    context=context,
                    conversation=conversation,
                    upload=upload_record,
                    action=KnowledgeAuditAction.READ,
                    engine=_coerce_str(envelope.get("engine")).strip(),
                    status=_coerce_str(envelope.get("status")).strip(),
                    metadata={"engine_tool": "dataset_query"},
                )
            return envelope

        # Non-dataset tables fall back to the preview/aggregate engine.
        table_agg_args: dict[str, object] = {"document_id": str(upload_record.id)}
        identifier_diag: dict[str, object] = {}
        requested_identifier_column = _coerce_str(table_args.get("match_column")).strip()
        requested_identifier_values: list[str] = []
        requested_identifier_policy = ""
        match_value = _coerce_str(table_args.get("match_value")).strip()
        match_values = table_args.get("match_values") if isinstance(table_args.get("match_values"), list) else []
        match_values_clean = [_coerce_str(v).strip() for v in match_values if _coerce_str(v).strip()]
        if requested_identifier_column and match_value:
            requested_identifier_values = [match_value]
            requested_identifier_policy = "eq"
        elif requested_identifier_column and match_values_clean:
            requested_identifier_values = match_values_clean
            requested_identifier_policy = "in"
        for key in ("query", "match_column", "match_value", "match_values", "sheet_name", "table_order_index", "value_column"):
            value = table_args.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set, dict)) and not value:
                continue
            table_agg_args[key] = value

        columns = table_args.get("select_columns")
        if not isinstance(columns, list) or not columns:
            columns = table_args.get("columns")
        if isinstance(columns, list) and columns:
            chosen = [_coerce_str(c).strip() for c in columns if _coerce_str(c).strip()]
            if requested_identifier_column and requested_identifier_column not in chosen:
                chosen.insert(0, requested_identifier_column)
            table_agg_args["columns"] = chosen

        aggregate = table_args.get("aggregate") if isinstance(table_args.get("aggregate"), Mapping) else None
        if aggregate and _coerce_str(aggregate.get("operation")).strip().lower() == "sum":
            col = _coerce_str(aggregate.get("column")).strip()
            if col:
                table_agg_args["mode"] = "column_sum"
                table_agg_args["value_column"] = col
        else:
            mode = _coerce_str(table_args.get("mode")).strip().lower()
            if mode in {"row_total", "column_sum"}:
                table_agg_args["mode"] = mode

        try:
            max_rows = int(table_args.get("max_rows")) if table_args.get("max_rows") is not None else None
        except (TypeError, ValueError):
            max_rows = None
        if not max_rows:
            try:
                max_rows = int(table_args.get("limit")) if table_args.get("limit") is not None else None
            except (TypeError, ValueError):
                max_rows = None
        if max_rows:
            table_agg_args["max_rows"] = max_rows

        result = _table_aggregate_handler(table_agg_args, conversation=conversation, context=context)
        result_out = dict(result) if isinstance(result, Mapping) else {"status": "error", "error": "invalid_result"}

        # Smart fallback: if table engine returned not_found with 0 rows for a document-type upload
        # without strong table signals, retry with text mode instead.
        table_status = str(result_out.get("status") or "").strip().lower()
        table_evaluated_rows = int(result_out.get("evaluated_rows") or 0)
        is_document_upload = bool(upload_record.pages.exists())
        has_strong_signal = _has_strong_table_signal(table_args)

        if (
            table_status == "not_found"
            and table_evaluated_rows == 0
            and is_document_upload
            and not has_strong_signal
            and not is_table_chunk
            and not is_dataset_card
        ):
            # Fallback to text mode using upload_record.id (not chunk id)
            fallback_read_args: dict[str, object] = {"document_id": str(upload_record.id)}
            for key in ("page", "offset", "mode", "token_budget", "chunk_neighbor"):
                value = text_args.get(key)
                if value is not None and not (isinstance(value, str) and not value.strip()):
                    fallback_read_args[key] = value

            fallback_result = _read_document_handler(fallback_read_args, conversation=conversation, context=context)
            fallback_envelope = _envelope(
                engine="text_page",
                engine_tool="read_document",
                result=fallback_result,
                resolved_upload_id=str(upload_record.id),
                requested_document_id=raw_id,
                resolved_chunk_id=str(chunk_record.id) if chunk_record else None,
                resolved_document_id_used=str(upload_record.id),
            )
            # Add provenance about the fallback
            fallback_diagnostics = fallback_envelope.get("diagnostics")
            if isinstance(fallback_diagnostics, dict):
                fallback_diagnostics["auto_fallback"] = "table_empty_to_text"
                fallback_diagnostics["prior_engine"] = "table_preview"
                fallback_diagnostics["prior_status"] = table_status
                fallback_diagnostics["prior_evaluated_rows"] = table_evaluated_rows

            _log_read_knowledge_performance(fallback_envelope)
            if fallback_envelope.get("status") == "ok" and _has_prompt_evidence(fallback_envelope):
                _record_knowledge_audit_event_once(
                    context=context,
                    conversation=conversation,
                    upload=upload_record,
                    action=KnowledgeAuditAction.READ,
                    engine=_coerce_str(fallback_envelope.get("engine")).strip(),
                    status=_coerce_str(fallback_envelope.get("status")).strip(),
                    metadata={"engine_tool": "read_document", "auto_fallback": True},
                )
            return fallback_envelope

        if requested_identifier_column and requested_identifier_values and _column_suggests_identifier(requested_identifier_column):
            identifier_diag["requested_identifier"] = {
                "column": requested_identifier_column,
                "values": requested_identifier_values[:12],
                "policy": requested_identifier_policy or "eq",
            }
            rows = result_out.get("rows") if isinstance(result_out.get("rows"), list) else []
            status_value = str(result_out.get("status") or "").strip().lower()
            filtered_rows = (
                _filter_rows_by_identifier(
                    rows,
                    column=requested_identifier_column,
                    allowed_values=requested_identifier_values,
                )
                if status_value == "ok"
                else [dict(row) for row in rows if isinstance(row, Mapping)]
            )
            identifier_diag["matched_identifiers"] = _collect_identifier_values(
                filtered_rows,
                column=requested_identifier_column,
            )

            if status_value == "ok":
                if not filtered_rows:
                    result_out["status"] = "not_found"
                    result_out["error"] = "identifier_not_found"
                    result_out["error_code"] = "identifier_not_found"
                    result_out["rows"] = []
                    result_out["match_count"] = 0
                    result_out["original_match_count"] = 0
                    result_out["hint"] = (
                        f"No rows found for {requested_identifier_column}={requested_identifier_values[0]!r}. "
                        "Double-check the exact identifier and the column name."
                    )
                else:
                    matched_values = identifier_diag.get("matched_identifiers")
                    if (
                        len(requested_identifier_values) == 1
                        and isinstance(matched_values, list)
                        and len(matched_values) > 1
                    ):
                        result_out["status"] = "disambiguation_required"
                        result_out["error"] = "identifier_ambiguous"
                        result_out["error_code"] = "identifier_ambiguous"
                        result_out["rows"] = []
                        result_out["match_count"] = 0
                        result_out["original_match_count"] = 0
                        result_out["hint"] = (
                            f"Multiple {requested_identifier_column} values matched {requested_identifier_values[0]!r}. "
                            f"Choose one exact identifier: {', '.join(str(v) for v in matched_values[:8])}."
                        )
                    else:
                        result_out["rows"] = filtered_rows
                        result_out["match_count"] = len(filtered_rows)
                        result_out["original_match_count"] = len(filtered_rows)

        envelope = _envelope(
            engine="table_preview",
            engine_tool="table_aggregate",
            result=result_out,
            resolved_upload_id=str(upload_record.id),
            requested_document_id=raw_id,
            resolved_chunk_id=str(chunk_record.id) if chunk_record else None,
            resolved_document_id_used=str(upload_record.id),
            identifier_diagnostics=identifier_diag or None,
        )
        _log_read_knowledge_performance(envelope)
        if envelope.get("status") == "ok" and _has_prompt_evidence(envelope):
            _record_knowledge_audit_event_once(
                context=context,
                conversation=conversation,
                upload=upload_record,
                action=KnowledgeAuditAction.READ,
                engine=_coerce_str(envelope.get("engine")).strip(),
                status=_coerce_str(envelope.get("status")).strip(),
                metadata={"engine_tool": "table_aggregate"},
            )
        return envelope

    # Text excerpt path (default).
    read_id = str(chunk_record.id if chunk_record else upload_record.id)
    read_args: dict[str, object] = {"document_id": read_id}
    for key in ("page", "offset", "mode", "token_budget", "chunk_neighbor"):
        value = text_args.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        read_args[key] = value
    result = _read_document_handler(read_args, conversation=conversation, context=context)
    envelope = _envelope(
        engine="text_page",
        engine_tool="read_document",
        result=result,
        resolved_upload_id=str(upload_record.id),
        requested_document_id=raw_id,
        resolved_chunk_id=str(chunk_record.id) if chunk_record else None,
        resolved_document_id_used=read_id,
    )
    _log_read_knowledge_performance(envelope)
    if envelope.get("status") == "ok" and _has_prompt_evidence(envelope):
        _record_knowledge_audit_event_once(
            context=context,
            conversation=conversation,
            upload=upload_record,
            action=KnowledgeAuditAction.READ,
            engine=_coerce_str(envelope.get("engine")).strip(),
            status=_coerce_str(envelope.get("status")).strip(),
            metadata={"engine_tool": "read_document"},
        )
    return envelope


def _action_tool_result(action: ActionType, payload: Mapping[str, object]) -> Mapping[str, object]:
    """
    Structure a tool result as a planned action without side effects.

    The legacy ActionDispatcher will still execute these actions based on the
    AiOrchestratorPlan, so MCP tooling focuses on planning, not persistence.
    """

    return {
        "action": action.value,
        "payload": dict(payload),
    }


def _create_case_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only; no side effects
    title = _coerce_str(arguments.get("title")).strip() or "Customer request"
    description = _coerce_str(arguments.get("description")).strip()
    priority = _normalize_priority(arguments.get("priority")) or "medium"
    ai_diagnosis = _coerce_str(arguments.get("ai_diagnosis")).strip()
    ai_actions_taken = _coerce_str(arguments.get("ai_actions_taken")).strip()
    raw_suggestions = arguments.get("ai_suggested_actions") or []
    suggestions: list[str] = [
        str(item)
        for item in raw_suggestions
        if isinstance(item, (str, int, float))
    ]
    payload = {
        "title": title,
        "description": description,
        "priority": priority,
        "ai_diagnosis": ai_diagnosis,
        "ai_actions_taken": ai_actions_taken,
        "ai_suggested_actions": suggestions,
        "metadata": {"source": "mcp_orchestrator"},
    }
    return _action_tool_result(ActionType.CREATE_CASE, payload)


def _update_case_status_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation  # planning only
    status_raw = _coerce_str(arguments.get("status")).strip().lower()
    if status_raw in {"resolve", "resolved", "close", "closed"}:
        status = "closed"
    elif status_raw in {"open", "reopen", "re-open"}:
        status = "open"
    else:
        status = status_raw or "open"
    payload = {
        "case_id": _coerce_str(arguments.get("case_id")).strip(),
        "status": status,
        "note": _coerce_str(arguments.get("note")).strip() or None,
    }
    return _action_tool_result(ActionType.UPDATE_CASE_STATUS, payload)


def _update_case_details_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    payload: dict[str, object] = {
        "case_id": _coerce_str(arguments.get("case_id")).strip(),
        "allow_description_overwrite": bool(arguments.get("allow_description_overwrite") or False),
    }
    for key in ("title", "description", "priority"):
        value = arguments.get(key)
        if value is None:
            continue
        if key == "priority":
            normalized = _normalize_priority(value)
            if normalized:
                payload[key] = normalized
        else:
            text = _coerce_str(value).strip()
            if text:
                payload[key] = text
    return _action_tool_result(ActionType.UPDATE_CASE_DETAILS, payload)


def _add_case_history_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    payload = {
        "case_id": _coerce_str(arguments.get("case_id")).strip(),
        "summary": _coerce_str(arguments.get("summary")).strip(),
    }
    return _action_tool_result(ActionType.ADD_CASE_HISTORY, payload)


def _flag_escalation_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    reason = _coerce_str(arguments.get("reason")).strip() or "Escalated by MCP orchestrator"
    details = _coerce_str(arguments.get("details")).strip()
    payload: dict[str, object] = {"reason": reason}
    if details:
        payload["metadata"] = {"details": details}
    return _action_tool_result(ActionType.FLAG_ESCALATION, payload)


def _create_customer_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only
    full_name = _coerce_str(arguments.get("full_name")).strip() or "Web Visitor"
    email = _coerce_str(arguments.get("email")).strip()
    phone = _coerce_str(arguments.get("phone")).strip()
    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), Mapping) else {}
    payload = {
        "display_name": full_name,
        "primary_email": email,
        "primary_phone": phone,
        "metadata": metadata,
        # record_origin is filled by the legacy handler if omitted
    }
    return _action_tool_result(ActionType.CREATE_CUSTOMER, payload)


def _update_customer_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context, conversation
    full_name = _coerce_str(arguments.get("full_name")).strip()
    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), Mapping) else {}
    payload: dict[str, object] = {
        "customer_id": _coerce_str(arguments.get("customer_id")).strip(),
    }
    if full_name:
        payload["display_name"] = full_name
    if metadata:
        payload["metadata"] = metadata
    return _action_tool_result(ActionType.UPDATE_CUSTOMER, payload)


def _create_lead_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only
    payload = {
        "title": _coerce_str(arguments.get("title")).strip(),
        "description": _coerce_str(arguments.get("description")).strip(),
        "source": _coerce_str(arguments.get("source")).strip() or "mcp_orchestrator",
        "conversation_id": str(conversation.id),
    }
    return _action_tool_result(ActionType.CREATE_LEAD, payload)


def _create_appointment_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # planning only
    payload = {
        "topic": _coerce_str(arguments.get("topic")).strip(),
        "preferred_time": _coerce_str(arguments.get("preferred_time")).strip(),
        "notes": _coerce_str(arguments.get("notes")).strip(),
        "conversation_id": str(conversation.id),
    }
    return _action_tool_result(ActionType.CREATE_APPOINTMENT, payload)


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


def _read_document_agentic_wrapper(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    Wrapper for read_document that routes to agentic batch handler when enabled.
    
    In agentic mode:
    - Accepts `ids` parameter for batch reading
    - Accepts `max_chars` parameter for token control
    - Returns simplified content-focused response
    
    In legacy mode:
    - Falls through to standard _read_document_handler
    """
    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
    
    if feature_state.rag_agentic_mode:
        # Use batch handler (handles both single and multiple IDs)
        raw_ids = arguments.get("ids")
        if isinstance(raw_ids, (list, tuple)):
            return _agentic_batch_read_handler(arguments, conversation, context)
        document_id = _coerce_str(arguments.get("document_id")).strip()
        pages = arguments.get("pages")
        page = arguments.get("page")
        offset = arguments.get("offset")
        if document_id and (isinstance(pages, list) and pages or page is not None or offset is not None):
            # Page-specific read in agentic mode; convert response to agentic format.
            result = _read_document_handler(arguments, conversation, context)
            return _convert_to_agentic_read_response(result, conversation=conversation)
        if document_id:
            return {
                "tool": "read_document",
                "status": "error",
                "error": "missing_ids_or_pages",
                "contents": [],
                "hint": "Provide ids from search_knowledge results (read_id/id), or specify pages/page/offset with document_id.",
            }
        return {
            "tool": "read_document",
            "status": "error",
            "error": "missing_ids_or_pages",
            "contents": [],
            "hint": "Provide ids from search_knowledge results (read_id/id).",
        }
    
    # Legacy mode - use standard handler
    return _read_document_handler(arguments, conversation, context)


_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "search_knowledge": _search_knowledge_handler,
    "read_knowledge": _read_knowledge_handler,
    "read_document": _read_document_agentic_wrapper,  # Uses agentic handler when flag enabled
    "list_tables": _list_tables_handler,
    "get_document_structure": _get_document_structure_handler,
    "table_aggregate": _table_aggregate_handler,
    "list_tables": _list_tables_handler,
    "table_aggregate": _table_aggregate_handler,
    "query_dataset": _dataset_query_handler,
    "dataset_query": _dataset_query_handler,
    "create_case": _create_case_handler,
    "update_case_status": _update_case_status_handler,
    "update_case_details": _update_case_details_handler,
    "add_case_history": _add_case_history_handler,
    "flag_escalation": _flag_escalation_handler,
    "create_customer": _create_customer_handler,
    "update_customer": _update_customer_handler,
    "create_lead": _create_lead_handler,
    "create_appointment": _create_appointment_handler,
}
