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
import hashlib
import json
import uuid
import re
import time
from collections import Counter
from functools import lru_cache
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping, Sequence

from django.db import models
from django.db.models import Prefetch
from django.core.cache import cache

from apps.accounts.models import (
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    KnowledgeUploadTableCell,
)
from apps.conversations.models import Conversation
from apps.services.ai_orchestrator import (
    ActionType,
    AiOrchestratorService,
    KnowledgeSearchService,
)
from apps.services.rag_logging import structured_log
from core.metrics import latency_monitor
from .identifier_registry import IdentifierGuardrail, IdentifierRegistryService
from .types import ToolExecutionContext


logger = logging.getLogger(__name__)
IDENTIFIER_MAPPING_CACHE_TTL = 300


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
        name="list_tables",
        description="List uploads that contain structured tables so you can grab their document IDs before aggregations.",
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
    _function_schema(
        name="read_document",
        description="Load full document content or a chunk by ID so you can cite exact details.",
        properties={
            "document_id": {
                "type": "string",
                "description": "UUID of the upload or chunk returned by search_knowledge.",
            },
            "page": {
                "type": "integer",
                "minimum": 1,
                "description": "Page window to load (1 = first chunk).",
                "default": 1,
            },
            "offset": {
                "type": "integer",
                "description": "Optional zero-based chunk index override when requesting specific spans.",
            },
            "mode": {
                "type": "string",
                "enum": ["excerpt", "full_page"],
                "description": "excerpt keeps responses small; full_page returns the entire inline limit.",
                "default": "excerpt",
            },
            "token_budget": {
                "type": "integer",
                "description": "Approximate token budget for this page window (used to lower the char cap).",
            },
            "chunk_neighbor": {
                "type": "integer",
                "minimum": 0,
                "maximum": 3,
                "description": "Number of neighbor chunks to stitch around the requested page.",
                "default": 1,
            },
        },
        required=("document_id",),
    ),
    _function_schema(
        name="table_aggregate",
        description=(
            "Aggregate numeric values from a structured table (totals + per-column contributions). "
            "Use it to sum wide sheets and to retrieve contributor lists via rows[].contributions."
        ),
        properties={
            "document_id": {
                "type": "string",
                "description": "UUID of the upload returned by search_knowledge/read_document.",
            },
            "query": {
                "type": "string",
                "description": "Optional text snippet to match rows (case-insensitive substring).",
            },
            "match_column": {
                "type": "string",
                "description": "Column name to check when filtering rows (normalized, case-insensitive).",
            },
            "match_value": {
                "type": "string",
                "description": "Expected value for match_column (substring match).",
            },
            "match_values": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Batch version of match_value; pass multiple row identifiers to retrieve them in one call.",
                "minItems": 1,
            },
            "value_column": {
                "type": "string",
                "description": "Column to sum when mode=column_sum. Defaults to row totals.",
            },
            "mode": {
                "type": "string",
                "enum": ["row_total", "column_sum"],
                "description": "row_total sums every numeric cell in the row; column_sum sums a single column.",
                "default": "row_total",
            },
            "table_order_index": {
                "type": "integer",
                "description": "Optional table index within the upload (1-based).",
            },
            "sheet_name": {
                "type": "string",
                "description": "Optional sheet name/section heading to scope the aggregation.",
            },
            "max_rows": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Maximum number of matching rows to include in the response.",
                "default": 50,
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of column/store names to include in the response.",
            },
        },
        required=("document_id",),
    ),
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

    handler = _TOOL_HANDLERS.get(name)
    if not handler:
        raise ValueError(f"Unsupported MCP tool: {name}")
    ctx = context or ToolExecutionContext()
    return handler(arguments, conversation=conversation, context=ctx)


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
) -> str | None:
    if not cache_key:
        return None
    try:
        fingerprint = json.dumps(cache_key, sort_keys=True, default=str)
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
        table_stats = metadata.get("table_stats") if isinstance(metadata, Mapping) else {}
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
    seen: set[tuple[str | None, str | None]] = set()
    deduped: list[dict[str, object]] = []
    for entry in payloads:
        chunk_id = str(entry.get("chunk_id") or "") or None
        upload_id = str(entry.get("upload_id") or "") or None
        key = (chunk_id, upload_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped[:8]


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
    for payload in snippet_payloads[:5]:
        upload_id = payload.get("upload_id")
        chunk_id = payload.get("chunk_id") or payload.get("id")
        preview_items.append(
            {
                "label": payload.get("public_label") or payload.get("title") or payload.get("label"),
                "upload_id": str(upload_id) if upload_id else None,
                "chunk_id": str(chunk_id) if chunk_id else None,
                "read_state": payload.get("read_state"),
                "read_required": bool(payload.get("read_required")),
                "is_table_chunk": bool(payload.get("is_table_chunk")),
                "score": payload.get("score"),
                "preview": _snippet_preview_text(payload),
            }
        )
    detail = dict(meta or {})
    detail["snippet_count"] = len(snippet_payloads)
    if preview_items:
        detail["snippets"] = preview_items
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
    if intent == "table" and len(snippets) <= 2:
        return "If you still need exact rows/columns, request the specific page via read_document full_page."
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

    if not queries:
        return {
            "tool": "search_knowledge",
            "status": "error",
            "error": "query is required",
            "snippets": [],
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
    cache_token = _identifier_mapping_cache_token(cache_key)
    cached_mapping = None
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
        if cache_key:
            context.identifier_mapping_cache[cache_key] = cache_payload
        if cache_token:
            cache.set(cache_token, cache_payload, IDENTIFIER_MAPPING_CACHE_TTL)

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
        detail = {
            "status": status,
            "intent": intent,
            "path": diag.get("path"),
            "snippet_count": snippet_count,
            "limit": limit_value,
            "total_ms": diag.get("total_duration_ms"),
            "alias_ms": diag.get("alias_duration_ms"),
            "vector_ms": diag.get("vector_duration_ms"),
            "lexical_ms": diag.get("fts_duration_ms"),
            "rerank_ms": diag.get("rerank_duration_ms"),
            "table_ms": diag.get("table_duration_ms"),
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
        structured_log(
            "mcp",
            "search.performance",
            detail,
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
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
            cached_result.setdefault("intent", intent)
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
            )
        snippet_payloads = _serialize_snippets(result.snippets)
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
                    "intent": intent,
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
                    "intent": intent,
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
                    "intent": intent,
                    "intent_signal": intent_info,
                    "status": "ok",
                    "snippets": [],
                    "identifier_gate": decision.as_dict() if decision else None,
                    "hint": "No records found for this identifier.",
                }
        if intent == "identifier":
            if len(snippet_payloads) <= 2:
                read_required = True
            elif all((payload.get("read_state") or "summary") in {"summary", "preview"} for payload in snippet_payloads):
                read_required = True
        for payload in snippet_payloads:
            chunk_id = payload.get("chunk_id") or payload.get("id")
            upload_id = payload.get("upload_id")
            chunk_index = payload.get("chunk_index")
            hinted_page = (int(chunk_index) + 1) if isinstance(chunk_index, int) else None
            mode_hint = "full_page" if intent == "identifier" else "excerpt"
            payload["read_hint"] = {
                "document_id": str(chunk_id or upload_id or ""),
                "page": hinted_page,
                "mode": mode_hint,
            }
            if read_required:
                payload["read_required"] = True
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
        _log_snippet_payloads(
            tool="search_knowledge",
            conversation=conversation,
            snippet_payloads=snippet_payloads,
            meta={"query": query_text, "intent": intent, "read_required": read_required},
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
            "intent": intent,
            "intent_signal": intent_info,
            "status": result.status,
            "diagnostics": dict(result.diagnostics or {}),
            "snippets": snippet_payloads,
            "hint": _search_hint(result.status, intent, snippet_payloads, result.diagnostics),
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
            cached_result.setdefault("intent", intent)
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
    if non_cached_queries > 1:
        executor = ThreadPoolExecutor(max_workers=min(non_cached_queries, 4), thread_name_prefix="mcp_search")
    try:
        for idx, query_text, intent_info, intent, limit_for_run in pending_specs:
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
    seen_snippets: set[str] = set()
    for run in runs:
        for snippet in run.get("snippets", []):
            identifier = snippet.get("chunk_id") or snippet.get("id") or snippet.get("upload_id")
            dedup_key = str(identifier) if identifier else json.dumps(snippet, sort_keys=True, default=str)
            if dedup_key in seen_snippets:
                continue
            seen_snippets.add(dedup_key)
            deduped_snippets.append(snippet)
            if clip_limit and len(deduped_snippets) >= clip_limit:
                break
        if clip_limit and len(deduped_snippets) >= clip_limit:
            break

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

    payload = {
        "tool": "search_knowledge",
        "query": primary_run.get("query"),
        "limit": limit_cap,
        "intent": primary_run.get("intent"),
        "intent_signal": primary_run.get("intent_signal"),
        "status": final_status,
        "diagnostics": diag,
        "snippets": deduped_snippets,
        "hint": _search_hint(final_status, primary_run.get("intent"), deduped_snippets, diag),
    }
    if len(queries) > 1:
        payload["batched_queries"] = tuple(queries)
    return payload


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

    chunk_record = KnowledgeUploadChunk.objects.filter(
        id=identifier,
        business_profile=business,
        upload__status=KnowledgeStatus.ACTIVE,
    ).select_related("upload").first()
    upload_record = None
    gating_upload_id = None
    if chunk_record:
        gating_upload_id = chunk_record.upload_id
    else:
        upload_record = KnowledgeUpload.objects.filter(
            id=identifier,
            business_profile=business,
            status=KnowledgeStatus.ACTIVE,
        ).first()
        if not upload_record:
            return {
                "tool": "read_document",
                "status": "not_found",
                "error": "document not found for this business",
                "snippets": [],
            }
        gating_upload_id = upload_record.id

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

    def _coerce_page(value: object) -> int:
        try:
            page_value = int(value)
        except (TypeError, ValueError):
            page_value = 1
        return max(1, page_value)

    page_index = _coerce_page(arguments.get("page"))
    offset_value = arguments.get("offset")
    if offset_value is not None:
        try:
            offset_int = int(offset_value)
            page_index = max(1, offset_int + 1)
        except (TypeError, ValueError):
            pass

    raw_mode = _coerce_str(arguments.get("mode")).strip().lower()
    mode = raw_mode if raw_mode in {"excerpt", "full_page"} else None

    token_budget: int | None = None
    raw_budget = arguments.get("token_budget")
    if raw_budget is not None:
        try:
            token_budget = max(0, int(raw_budget))
        except (TypeError, ValueError):
            token_budget = None

    neighbor = arguments.get("chunk_neighbor")
    try:
        neighbor_window = int(neighbor)
    except (TypeError, ValueError):
        neighbor_window = 1
    neighbor_window = max(0, min(3, neighbor_window))

    service = _knowledge_service()
    throttle_notice: dict[str, object] | None = None

    upload_source = chunk_record.upload if chunk_record else upload_record
    knowledge_entry = _match_knowledge_entry(context, [str(identifier), str(gating_upload_id)])
    if mode is None:
        prefer_full_page = _detect_full_page_intent(
            conversation,
            None,
            document_entry=knowledge_entry,
            upload=upload_source,
        )
        mode = "full_page" if (prefer_full_page and _budget_allows_full_page(context, business_profile=business, service=service)) else "excerpt"

    downgraded = False
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

    cache_key = _read_cache_key(document_id, page_index, mode or "excerpt", neighbor_window, token_budget)
    cached_payload = None
    if context.read_cache and cache_key in context.read_cache:
        cached_payload = copy.deepcopy(context.read_cache[cache_key])
    if cached_payload:
        structured_log(
            "mcp",
            "read_document.cache_hit",
            {
                "document_id": document_id,
                "page": page_index,
                "mode": mode,
            },
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
        )
        return cached_payload

    # Enforce per-turn chunk budget only when actually loading the window.
    context.reserve_chunk_reads(1)
    context.reserve_chunk_pages(1)

    snippets: list[Any] = []
    if chunk_record:
        snippets.extend(
            service.load_page_window(
                business_profile=business,
                chunk_id=identifier,
                page_index=page_index,
                neighbor=neighbor_window,
                mode=mode,
                token_budget=token_budget,
            )
        )
    else:
        snippets.extend(
            service.load_page_window(
                business_profile=business,
                upload_id=identifier,
                page_index=page_index,
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
            "page_index": page_index,
            "token_budget": token_budget,
            "snippet_count": len(snippet_payloads),
        },
    )
    context.reserve_characters(int(metrics.get("char_count", 0)))
    _log_snippet_payloads(
        tool="read_document",
        conversation=conversation,
        snippet_payloads=snippet_payloads,
        meta={
            "document_id": document_id,
            "mode": mode,
            "page_index": page_index,
            "neighbor": neighbor_window,
            "token_budget": token_budget,
        },
    )

    payload = {
        "tool": "read_document",
        "document_id": document_id,
        "page": page_index,
        "mode": mode,
        "mode_downgraded": downgraded,
        "token_budget": token_budget,
        "status": "ok",
        "snippets": snippet_payloads,
        "knowledge_reads": knowledge_reads,
        "ingestion_warnings": ingestion_warnings,
        "throttle_notice": throttle_notice,
    }
    _bounded_cache_store(context.read_cache, cache_key, payload)
    return payload


def _list_tables_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    query_input = _coerce_str(arguments.get("query")).strip()
    raw_limit = arguments.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else 5
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(10, limit))

    uploads_qs = (
        KnowledgeUpload.objects.filter(
            business_profile=conversation.business_profile,
            status=KnowledgeStatus.ACTIVE,
            tables__isnull=False,
        )
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
            "query": query_input or None,
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
    upload = KnowledgeUpload.objects.filter(
        id=identifier,
        business_profile=conversation.business_profile,
        status=KnowledgeStatus.ACTIVE,
    ).first()
    if not upload:
        chunk = KnowledgeUploadChunk.objects.filter(
            id=identifier,
            business_profile=conversation.business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
        ).select_related("upload").first()
        upload = chunk.upload if chunk else None
    if not upload:
        return {
            "tool": "table_aggregate",
            "status": "not_found",
            "error": "document not found for this business",
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
    if isinstance(raw_match_values, (list, tuple)):
        for candidate in raw_match_values:
            normalized = _normalize_column_name(candidate)
            if normalized:
                normalized_match_values.append(normalized)
    match_value = _normalize_column_name(match_value_input)
    if match_value and match_value not in normalized_match_values:
        normalized_match_values.append(match_value)
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
    raw_columns = arguments.get("columns")
    column_filters: list[str] = []
    if isinstance(raw_columns, (list, tuple)):
        for entry in raw_columns:
            candidate = _coerce_str(entry).strip()
            if candidate:
                column_filters.append(candidate)
    normalized_column_filters = { _normalize_column_name(value) for value in column_filters if _normalize_column_name(value) }

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
        contributions: list[dict[str, object]] = []
        total_column_value: float | None = None
        total_column_display: str | None = None
        total_column_priority = -1
        for cell in cells:
            column_label = cell.get("column") or f"column_{(cell.get('column_index') or 0) + 1}"
            cell_value = cell.get("raw_text") or ""
            normalized_label = cell.get("normalized") or _normalize_column_name(column_label)
            include_in_preview = True
            if normalized_column_filters and normalized_label not in normalized_column_filters:
                include_in_preview = False
            if include_in_preview and len(preview_cells) < 12:
                preview_cells.append({"column": column_label, "value": cell_value})
            numeric_candidate = cell.get("numeric")
            total_priority = int(cell.get("total_priority") or 0)
            is_total_col = bool(cell.get("is_total_column"))
            if numeric_candidate is not None:
                numeric_float = float(numeric_candidate)
                if not normalized_column_filters or normalized_label in normalized_column_filters or is_total_col:
                    contributions.append(
                        {
                            "column": column_label,
                            "value": numeric_float,
                            "display": cell_value.strip() or _format_numeric_display(numeric_float),
                            "is_total_column": is_total_col,
                        }
                    )
                if is_total_col:
                    if total_priority > total_column_priority:
                        total_column_value = numeric_float
                        total_column_display = cell_value.strip() or _format_numeric_display(numeric_float)
                        total_column_priority = total_priority

        numeric_value: float | None = None
        display_value: str | None = None
        if mode == "row_total":
            if total_column_value is not None:
                numeric_value = float(total_column_value)
                display_value = total_column_display or _format_numeric_display(total_column_value)
            else:
                non_total_values = [entry["value"] for entry in contributions if not entry["is_total_column"]]
                if non_total_values:
                    numeric_value = float(sum(non_total_values))
                    display_value = _format_numeric_display(numeric_value)
                else:
                    continue
        else:
            if not value_column_raw:
                continue
            candidate = column_map.get(value_column_raw)
            candidate_text = candidate.get("raw_text") if isinstance(candidate, Mapping) else ""
            numeric_value = _parse_numeric_value(candidate_text)
            if numeric_value is None:
                continue
            display_value = _format_numeric_display(numeric_value, candidate_text)
        total_value += numeric_value or 0.0
        contributions_sorted = sorted(contributions, key=lambda entry: abs(entry["value"]), reverse=True)
        matched_rows.append(
            {
                "row_index": row_payload.get("row_index"),
                "table_order_index": row_payload.get("table_order_index"),
                "sheet_name": row_payload.get("sheet_name"),
                "row_total": numeric_value,
                "row_total_display": display_value,
                "cells": preview_cells,
                "contributions": contributions_sorted[:200],
                "contribution_count": len(contributions_sorted),
                "total_column_value": total_column_display,
            }
        )
        if len(matched_rows) >= row_limit:
            break

    status = "ok" if matched_rows else "not_found"
    snippet_payloads: list[dict[str, object]] = []
    if matched_rows:
        snippet_payloads = [
            _build_table_aggregate_snippet(
                upload=upload,
                row=row,
                query=query_input,
                match_column=match_column_input,
                match_value=match_value_input,
                columns=column_filters,
            )
            for row in matched_rows
        ]
    duration_ms = int((time.perf_counter() - start) * 1000)
    structured_log(
        "mcp",
        "table.aggregate",
        {
            "document_id": str(upload.id),
            "mode": mode,
            "match_count": len(matched_rows),
            "evaluated_rows": evaluated_rows,
            "contribution_rows": sum(row.get("contribution_count", 0) for row in matched_rows),
            "requested_columns": column_filters,
            "duration_ms": duration_ms,
            "row_limit": row_limit,
            "match_column": match_column_input or None,
            "match_value": match_value_input or None,
            "query": query_input or None,
            "sheet_name": sheet_name_input or None,
            "cache_hit": cache_hit,
        },
        context={"business": conversation.business_profile_id},
        logger_obj=logger,
    )
    if matched_rows:
        structured_log(
            "mcp",
            "table.aggregate.payload",
            {
                "document_id": str(upload.id),
                "mode": mode,
                "match_count": len(matched_rows),
                "total": total_value,
                "rows": matched_rows,
                "requested_columns": column_filters,
                "duration_ms": duration_ms,
                "row_limit": row_limit,
                "match_column": match_column_input or None,
                "match_value": match_value_input or None,
                "match_values": normalized_match_values or None,
                "query": query_input or None,
                "sheet_name": sheet_name_input or None,
                "cache_hit": cache_hit,
            },
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            logger_obj=logger,
        )

    return {
        "tool": "table_aggregate",
        "status": status,
        "document_id": str(upload.id),
        "mode": mode,
        "query": query_input or None,
        "match_column": match_column_input or None,
        "match_value": match_value_input or None,
        "match_values": normalized_match_values or None,
        "value_column": value_column_input or None,
        "sheet_name": sheet_name_input or None,
        "columns": column_filters or None,
        "match_count": len(matched_rows),
        "total": total_value if matched_rows else None,
        "display_total": _format_numeric_display(total_value) if matched_rows else None,
        "rows": matched_rows,
        "snippets": snippet_payloads,
        "duration_ms": duration_ms,
        "evaluated_rows": evaluated_rows,
        "row_limit": row_limit,
        "cache_hit": cache_hit,
        "hint": "No matching rows found." if not matched_rows else None,
    }


def _build_table_aggregate_snippet(
    *,
    upload: KnowledgeUpload,
    row: Mapping[str, object],
    query: str | None,
    match_column: str | None,
    match_value: str | None,
    columns: Sequence[str] | None = None,
) -> dict[str, object]:
    row_index = row.get("row_index")
    table_idx = row.get("table_order_index")
    sheet_name = row.get("sheet_name")
    row_total = row.get("row_total")
    row_total_display = row.get("row_total_display") or _format_numeric_display(row_total if isinstance(row_total, (int, float)) else None)
    cells = row.get("cells") if isinstance(row.get("cells"), list) else []
    contributions = row.get("contributions") if isinstance(row.get("contributions"), list) else []
    primary_label = _table_row_label(cells, query)
    snippet_id = f"table-aggregate:{upload.id}:{table_idx}:{row_index}"
    summary = f"{primary_label or 'Table row'} – total {row_total_display or 'unknown'}"
    contribution_lines: list[str] = []
    total_lines: list[str] = []
    total_entries = [entry for entry in contributions if entry.get("is_total_column")]
    for entry in total_entries[:10]:
        column = entry.get("column") or "Total"
        display = entry.get("display") or _format_numeric_display(entry.get("value"))
        total_lines.append(f"- {column}: {display or '—'}")
    if len(total_entries) > 10:
        total_lines.append(f"...+{len(total_entries) - 10} more totals")
    for entry in contributions[:25]:
        column = entry.get("column") or "Column"
        display = entry.get("display") or _format_numeric_display(entry.get("value"))
        contribution_lines.append(f"- {column}: {display or '—'}")
    if len(contributions) > 25:
        contribution_lines.append(f"...+{len(contributions) - 25} more columns")
    structured_table = {
        "row_index": row_index,
        "table_order_index": table_idx,
        "sheet_name": sheet_name,
        "row_total": row_total,
        "row_total_display": row_total_display,
        "columns": [dict(entry) for entry in contributions],
    }
    diagnostics = {
        "table_aggregate": True,
        "table_row_index": row_index,
        "table_order_index": table_idx,
        "table_sheet_name": sheet_name,
        "table_contribution_count": len(contributions),
        "table_aggregate_query": query or None,
        "table_match_column": match_column or None,
        "table_match_value": match_value or None,
        "table_row_total": row_total,
        "table_row_total_display": row_total_display,
        "table_requested_columns": list(columns or ()),
    }
    if total_entries:
        diagnostics["table_total_columns"] = [
            {
                "column": entry.get("column"),
                "value": entry.get("value"),
                "display": entry.get("display"),
            }
            for entry in total_entries
        ]
    label_suffix = f" (sheet {sheet_name})" if sheet_name else ""
    content_parts: list[str] = [summary]
    if total_lines:
        content_parts.extend(["", "Totals:", *total_lines])
    if contribution_lines:
        content_parts.extend(["", "Contributors:", *contribution_lines])
    return {
        "id": snippet_id,
        "title": f"Table aggregate{label_suffix}".strip(),
        "public_label": primary_label or f"Row {row_index}",
        "summary": summary,
        "content": "\n".join(content_parts),
        "content_mode": "structured_table",
        "read_state": "full",
        "page_mode": "structured_table",
        "structured_table_count": 1,
        "is_table_chunk": True,
        "structured_tables": [structured_table],
        "source_diagnostics": diagnostics,
        "upload_id": str(upload.id),
        "chunk_id": None,
        "status": "table_aggregate",
        "entities": (),
        "issues": (),
        "topic_hints": (),
        "coverage": (),
        "row_index": row_index,
    }


def _table_row_label(cells: Sequence[Mapping[str, object]] | object, fallback: str | None) -> str | None:
    if isinstance(cells, Sequence):
        for cell in cells:
            if not isinstance(cell, Mapping):
                continue
            value = cell.get("value")
            if isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    return trimmed
    return (fallback or "").strip() or None


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


_TOOL_HANDLERS: dict[str, ToolHandler] = {
    "search_knowledge": _search_knowledge_handler,
    "read_document": _read_document_handler,
    "list_tables": _list_tables_handler,
    "table_aggregate": _table_aggregate_handler,
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
