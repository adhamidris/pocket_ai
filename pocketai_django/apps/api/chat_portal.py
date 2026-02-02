from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from queue import Empty, Queue
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Max, Q
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from core.otel import otel_context, otel_trace

from apps.accounts.models import (
    AgentMcpToolSetting,
    BusinessProfile,
    McpConnectionApprovalMode,
    McpConnectionAuditAction,
    McpConnectionAuditEvent,
    McpToolOperationType,
)
from apps.conversations.content_blocks import (
    content_blocks_from_response_blocks,
    extract_text_from_content_blocks,
    new_block_id,
)
from apps.conversations.rich_blocks import RichBlockStreamBuilder, apply_block_ops, coerce_block_event, rich_blocks_from_text
from apps.conversations.models import (
    AgentRequest,
    AgentRequestStatus,
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunMemoryItem,
    AgentRunMemoryKind,
    AgentRunStatus,
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
    PortalTurn,
    PortalTurnStatus,
)
from apps.core.logging_utils import LogEmoji
from apps.rag.ai_orchestrator import (
    ActionDispatcher,
    AiOrchestratorService,
    StreamingTurnContext,
)
from apps.rag.rag_logging import structured_log
from apps.mcp.sanitizer import sanitize_placeholder_thinking, sanitize_text, sanitize_with_diagnostics
from apps.mcp.tool_artifacts import store_remote_tool_output_artifact
from apps.llm.llm_provider import load_default_provider
from apps.conversations.portal import (
    ChatPortalService,
    PortalAgentSummary,
    PortalBusinessSummary,
    PortalMessage,
    PortalNotFoundError,
    PortalSessionBootstrap,
    PortalSessionState,
    PortalValidationError,
)
from apps.conversations.portal_turn_events import list_turn_events
from apps.conversations.portal_turn_runner import run_turn_background
from core.tenancy import tenant_context

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

# Active streaming turn cancellation registry.
#
# The portal UI supports an explicit "Stop" action. A parallel HTTP request can
# set the cancellation event for the active streaming turn so the backend stops
# generating and persists the partial transcript (including reasoning blocks).
_ACTIVE_STREAM_CANCEL_EVENTS: dict[str, threading.Event] = {}
_ACTIVE_STREAM_CANCEL_LOCK = threading.Lock()

CONTEXT_STATUS_CODES = {
    "searching_knowledge",
    "searching_start",
    "searching_complete",
    "reading_document",
    "reading_start",
    "reading_complete",
    "planning_actions",
    "responding",
    "answer_started",
    "answer_finalized",
    "clarifying",
}

TOOL_EVENT_PHASES = {"started", "finished", "approval_requested", "approval_resolved"}
EMAIL_PENDING_DRAFT_META_KEY = "email_pending_draft"


def _pending_email_account_id_for_draft(conversation: object, *, draft_id: str) -> str:
    if not draft_id:
        return ""
    meta = getattr(conversation, "metadata", None)
    if not isinstance(meta, Mapping):
        return ""
    pending = meta.get(EMAIL_PENDING_DRAFT_META_KEY)
    if not isinstance(pending, Mapping):
        return ""
    pending_draft_id = str(pending.get("draft_id") or "").strip()
    if not pending_draft_id or pending_draft_id != draft_id:
        return ""
    return str(pending.get("email_account_id") or "").strip()


def _clear_pending_email_draft_meta(
    conversation: object,
    *,
    draft_id: str,
    email_account_id: str | None = None,
) -> bool:
    if not draft_id:
        return False
    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        existing_meta = getattr(conversation, "metadata", None)
        meta = dict(existing_meta) if isinstance(existing_meta, Mapping) else {}
        pending = meta.get(EMAIL_PENDING_DRAFT_META_KEY)
        if not isinstance(pending, Mapping):
            return False
        pending_draft_id = str(pending.get("draft_id") or "").strip()
        if pending_draft_id and pending_draft_id != draft_id:
            return False
        pending_email_account_id = str(pending.get("email_account_id") or "").strip()
        if email_account_id and pending_email_account_id and pending_email_account_id != email_account_id:
            return False
        meta.pop(EMAIL_PENDING_DRAFT_META_KEY, None)
        setattr(conversation, "metadata", meta)
        save = getattr(conversation, "save", None)
        if callable(save):
            conversation.save(update_fields=["metadata", "last_activity_at"])
        return True


def _queue_put(queue, item):
    put = getattr(queue, "put", None)
    if callable(put):
        put(item)
    else:
        queue.append(item)


def _enqueue_status_events(queue, *, code: str, label: str | None = None, meta: dict | None = None) -> None:
    code_value = (code or "").strip()
    if not code_value:
        return
    label_value = label or code_value.replace("_", " ").title()
    payload: dict[str, object] = {"type": "status", "state": code_value, "label": label_value}
    if meta:
        payload["meta"] = meta
    if code_value in CONTEXT_STATUS_CODES:
        ctx_payload = {"type": "context_progress", "state": code_value, "label": label_value}
        if meta:
            ctx_payload["meta"] = meta
        _queue_put(queue, ctx_payload)
    _queue_put(queue, payload)


def _portal_debug_tool_trace_enabled(request: HttpRequest, payload: Mapping[str, object], metadata: Mapping[str, object]) -> bool:
    """
    Gate portal tool-trace/search debug payloads behind:
    - a server-side enable setting (or DEBUG), and
    - optional shared-token verification (if configured).
    """

    enabled = bool(getattr(settings, "PORTAL_DEBUG_TOOL_TRACE", False) or getattr(settings, "DEBUG", False))
    if not enabled:
        return False
    return True


def _clip_debug_text(value: object, *, limit: int = 480) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return f"{text[: max(0, limit - 1)].rstrip()}…"
    return text


def _json_safe_debug(value: object, *, depth: int = 3, string_limit: int = 240, list_limit: int = 12) -> object:
    def _is_numeric_metric(v: object) -> bool:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            candidate = v.strip()
            if not candidate:
                return False
            try:
                float(candidate)
            except ValueError:
                return False
            return True
        return False

    if value is None:
        return None
    if depth <= 0:
        return _clip_debug_text(value, limit=string_limit)
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return _clip_debug_text(value, limit=string_limit)
        return value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= list_limit:
                out["…"] = f"+{max(0, len(value) - list_limit)} more keys"
                break
            key_str = str(key or "").strip() or f"key_{idx}"
            lowered = key_str.lower()
            if any(token in lowered for token in ("password", "secret", "api_key", "apikey")):
                out[key_str] = "[REDACTED]"
                continue
            if "token" in lowered:
                safe_token_metrics = {
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "max_tokens",
                    "max_context_tokens",
                    "max_input_tokens",
                    "response_token_reserve",
                    "tokens_est",
                    "tokens_est_before",
                    "tokens_est_after",
                    "token_budget",
                }
                if lowered not in safe_token_metrics or not _is_numeric_metric(item):
                    out[key_str] = "[REDACTED]"
                    continue
            out[key_str] = _json_safe_debug(item, depth=depth - 1, string_limit=string_limit, list_limit=list_limit)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out_list: list[object] = []
        for item in items[:list_limit]:
            out_list.append(_json_safe_debug(item, depth=depth - 1, string_limit=string_limit, list_limit=list_limit))
        if len(items) > list_limit:
            out_list.append(f"…(+{len(items) - list_limit} more)")
        return out_list
    return _clip_debug_text(value, limit=string_limit)


def _serialize_tool_trace_entry(entry: Mapping[str, object]) -> dict[str, object]:
    tool = str(entry.get("tool") or "").strip()
    arguments = entry.get("arguments")
    args_out: dict[str, object] | None = None
    if isinstance(arguments, Mapping) and arguments:
        allowed_keys = {
            "query",
            "queries",
            "limit",
            "document_id",
            "ids",
            "items",
            "page",
            "pages",
            "offset",
            "mode",
            "neighbor_window",
            "chunk_neighbor",
            "token_budget",
            "max_chars",
            "columns",
            "filters",
            "operation",
        }
        filtered: dict[str, object] = {}
        for key, value in arguments.items():
            key_str = str(key)
            if key_str not in allowed_keys:
                continue
            if key_str == "items" and isinstance(value, list):
                # Avoid dumping raw signed cursors into the portal debug panel.
                safe_items: list[dict[str, object]] = []
                for item in value[:12]:
                    if not isinstance(item, Mapping):
                        continue
                    item_id = str(item.get("id") or "").strip()
                    cursor = item.get("cursor")
                    cursor_fp = None
                    if isinstance(cursor, str) and cursor.strip():
                        digest = hashlib.sha256(cursor.strip().encode("utf-8")).hexdigest()
                        cursor_fp = {"len": len(cursor.strip()), "sha256_10": digest[:10]}
                    safe_entry: dict[str, object] = {}
                    if item_id:
                        safe_entry["id"] = item_id
                    if cursor_fp:
                        safe_entry["cursor"] = cursor_fp
                    if safe_entry:
                        safe_items.append(safe_entry)
                filtered[key_str] = safe_items
            else:
                filtered[key_str] = value
        if not filtered:
            # Fall back to a bounded view of whatever was provided (still redacts tokens).
            filtered = dict(list(arguments.items())[:12])
        args_out = _json_safe_debug(filtered, depth=3, string_limit=240, list_limit=12)  # type: ignore[assignment]

    out: dict[str, object] = {
        "tool": tool or None,
        "status": entry.get("status"),
        "error_code": entry.get("error_code"),
        "duration_ms": entry.get("duration_ms"),
        "cache_hit": entry.get("cache_hit"),
        "origin": entry.get("origin"),
    }
    if args_out:
        out["arguments"] = args_out
    output_summary = entry.get("output_summary")
    if isinstance(output_summary, Mapping) and output_summary:
        out["output_summary"] = _json_safe_debug(output_summary, depth=4, string_limit=240, list_limit=16)  # type: ignore[arg-type]
    prompt_compaction = entry.get("prompt_compaction")
    if isinstance(prompt_compaction, Mapping) and prompt_compaction:
        out["prompt_compaction"] = _json_safe_debug(prompt_compaction, depth=3, string_limit=180, list_limit=12)  # type: ignore[arg-type]
    hint = entry.get("hint")
    if hint:
        out["hint"] = _clip_debug_text(hint, limit=240)
    engine = entry.get("engine")
    if engine:
        out["engine"] = _clip_debug_text(engine, limit=80)
    return {k: v for k, v in out.items() if v is not None and v != ""}


def _serialize_llm_usage(usage: Mapping[str, object] | None) -> dict[str, object] | None:
    if not usage:
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    try:
        prompt_val = int(prompt) if prompt is not None else 0
    except (TypeError, ValueError):
        prompt_val = 0
    try:
        completion_val = int(completion) if completion is not None else 0
    except (TypeError, ValueError):
        completion_val = 0
    try:
        total_val = int(total) if total is not None else 0
    except (TypeError, ValueError):
        total_val = 0
    if not total_val and (prompt_val or completion_val):
        total_val = prompt_val + completion_val
    out: dict[str, object] = {
        "prompt_tokens": prompt_val,
        "completion_tokens": completion_val,
        "total_tokens": total_val,
    }
    calls_raw = usage.get("calls")
    if isinstance(calls_raw, (list, tuple)):
        calls: list[dict[str, object]] = []
        for entry in calls_raw:
            if not isinstance(entry, Mapping):
                continue
            call_prompt = entry.get("prompt_tokens")
            call_completion = entry.get("completion_tokens")
            call_total = entry.get("total_tokens")
            try:
                call_prompt_val = int(call_prompt) if call_prompt is not None else 0
            except (TypeError, ValueError):
                call_prompt_val = 0
            try:
                call_completion_val = int(call_completion) if call_completion is not None else 0
            except (TypeError, ValueError):
                call_completion_val = 0
            try:
                call_total_val = int(call_total) if call_total is not None else 0
            except (TypeError, ValueError):
                call_total_val = 0
            if not call_total_val and (call_prompt_val or call_completion_val):
                call_total_val = call_prompt_val + call_completion_val
            call_entry: dict[str, object] = {
                "prompt_tokens": call_prompt_val,
                "completion_tokens": call_completion_val,
                "total_tokens": call_total_val,
            }
            stage = entry.get("stage")
            if stage:
                call_entry["stage"] = stage
            model = entry.get("model")
            if model:
                call_entry["model"] = model
            provider = entry.get("provider")
            if provider:
                call_entry["provider"] = provider
            calls.append(call_entry)
        if calls:
            out["calls"] = calls
    provider = usage.get("provider")
    if provider:
        out["provider"] = provider
    model = usage.get("model")
    if model:
        out["model"] = model
    return out


def _serialize_context_budget() -> dict[str, object] | None:
    max_context = getattr(settings, "MCP_MAX_CONTEXT_TOKENS", None)
    max_input = getattr(settings, "MCP_MAX_INPUT_TOKENS", None)
    reserve = getattr(settings, "MCP_RESPONSE_TOKEN_RESERVE", None)
    try:
        max_context_val = int(max_context) if max_context is not None else 0
    except (TypeError, ValueError):
        max_context_val = 0
    try:
        max_input_val = int(max_input) if max_input is not None else 0
    except (TypeError, ValueError):
        max_input_val = 0
    try:
        reserve_val = int(reserve) if reserve is not None else 0
    except (TypeError, ValueError):
        reserve_val = 0

    max_context_val = max(0, max_context_val)
    max_input_val = max(0, max_input_val)
    reserve_val = max(0, reserve_val)
    if not max_context_val and not max_input_val and not reserve_val:
        return None
    return {
        "max_context_tokens": max_context_val,
        "max_input_tokens": max_input_val,
        "response_token_reserve": reserve_val,
    }


def _serialize_knowledge_result(entry: Mapping[str, object]) -> dict[str, object]:
    title = entry.get("title") or entry.get("public_label") or entry.get("label") or "Knowledge"
    preview_source = (
        entry.get("summary")
        or entry.get("preview")
        or entry.get("content")
        or entry.get("text")
        or ""
    )
    out: dict[str, object] = {
        "id": entry.get("id") or entry.get("chunk_id") or entry.get("upload_id"),
        "title": _clip_debug_text(title, limit=140),
        "search_stage": entry.get("search_stage"),
        "read_state": entry.get("read_state") or entry.get("readState"),
        "document_id": entry.get("upload_id") or entry.get("document_id"),
        "chunk_id": entry.get("chunk_id"),
        "source": entry.get("source_file") or entry.get("source"),
        "is_table_chunk": entry.get("is_table_chunk"),
    }
    if preview_source:
        out["preview"] = _clip_debug_text(preview_source, limit=420)
    read_hint = entry.get("read_hint") or entry.get("readHint")
    if isinstance(read_hint, Mapping) and read_hint:
        out["read_hint"] = _json_safe_debug(read_hint, depth=2, string_limit=160, list_limit=8)
    return {k: v for k, v in out.items() if v is not None and v != "" and v != []}


def _serialize_debug_tools_payload(stream_context: StreamingTurnContext) -> dict[str, object] | None:
    tool_context = getattr(stream_context, "tool_context", None)
    tool_trace_raw = None
    knowledge_results_raw = None
    knowledge_reads_raw = None
    search_history_raw = None
    coverage_ledger_raw = None
    table_rows_raw = None
    llm_usage_raw = getattr(stream_context, "llm_usage", None)
    prompt_budget_raw = None
    if tool_context is not None:
        tool_trace_raw = getattr(tool_context, "tool_trace", None)
        knowledge_results_raw = getattr(tool_context, "knowledge_results", None)
        knowledge_reads_raw = getattr(tool_context, "knowledge_reads", None)
        search_history_raw = getattr(tool_context, "search_history", None)
        coverage_ledger_raw = getattr(tool_context, "coverage_ledger", None)
        table_rows_raw = getattr(tool_context, "table_aggregate_rows", None)
        prompt_budget_raw = getattr(tool_context, "prompt_budget_entries", None)
    if tool_trace_raw is None:
        tool_trace_raw = getattr(stream_context, "tool_trace", None)
    if knowledge_results_raw is None:
        knowledge_results_raw = getattr(stream_context, "knowledge_payload", None)
    if knowledge_reads_raw is None:
        knowledge_reads_raw = getattr(stream_context, "knowledge_reads", None)

    tool_trace: list[dict[str, object]] = []
    if isinstance(tool_trace_raw, (list, tuple)):
        for entry in tool_trace_raw[-30:]:
            if isinstance(entry, Mapping):
                tool_trace.append(_serialize_tool_trace_entry(entry))

    knowledge_results: list[dict[str, object]] = []
    if isinstance(knowledge_results_raw, (list, tuple)):
        for entry in knowledge_results_raw[:20]:
            if isinstance(entry, Mapping):
                knowledge_results.append(_serialize_knowledge_result(entry))

    knowledge_reads: list[dict[str, object]] = []
    if isinstance(knowledge_reads_raw, (list, tuple)):
        for entry in knowledge_reads_raw[:20]:
            if isinstance(entry, Mapping):
                knowledge_reads.append(_json_safe_debug(entry, depth=2, string_limit=180, list_limit=10))  # type: ignore[arg-type]

    search_history: list[object] = []
    if isinstance(search_history_raw, (list, tuple)):
        for entry in search_history_raw[-12:]:
            if isinstance(entry, Mapping):
                search_history.append(_json_safe_debug(entry, depth=3, string_limit=220, list_limit=12))

    coverage_ledger: list[object] = []
    if isinstance(coverage_ledger_raw, (list, tuple)):
        for entry in coverage_ledger_raw[:30]:
            if isinstance(entry, Mapping):
                coverage_ledger.append(_json_safe_debug(entry, depth=2, string_limit=200, list_limit=12))

    table_aggregate_rows: list[object] = []
    if isinstance(table_rows_raw, (list, tuple)):
        for entry in table_rows_raw[:12]:
            if isinstance(entry, Mapping):
                table_aggregate_rows.append(_json_safe_debug(entry, depth=3, string_limit=200, list_limit=12))

    llm_usage = _serialize_llm_usage(llm_usage_raw if isinstance(llm_usage_raw, Mapping) else None)
    context_budget = _serialize_context_budget()

    prompt_budget: list[object] = []
    if isinstance(prompt_budget_raw, (list, tuple)):
        for entry in prompt_budget_raw[-12:]:
            if isinstance(entry, Mapping):
                prompt_budget.append(_json_safe_debug(entry, depth=4, string_limit=180, list_limit=20))

    if (
        not tool_trace
        and not knowledge_results
        and not knowledge_reads
        and not search_history
        and not coverage_ledger
        and not table_aggregate_rows
        and not prompt_budget
        and not llm_usage
        and not context_budget
    ):
        return None
    return {
        "tool_trace": tool_trace,
        "search_history": search_history,
        "knowledge_results": knowledge_results,
        "knowledge_reads": knowledge_reads,
        "coverage_ledger": coverage_ledger,
        "table_aggregate_rows": table_aggregate_rows,
        "prompt_budget": prompt_budget,
        "context_budget": context_budget,
        "usage": llm_usage,
    }


LOW_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(hi|hello|hey|hola|hallo|مرحبا|السلام عليكم|as-salamu alaykum)\b", re.IGNORECASE),
    re.compile(r"^(good\s+(morning|evening|afternoon|day|night))\b", re.IGNORECASE),
    re.compile(r"^(thanks|thank you|gracias|gracias|shukran|شكرا)\b", re.IGNORECASE),
    re.compile(r"^(test|testing)\b", re.IGNORECASE),
)
LOW_INTENT_SIMPLE = {
    "hi",
    "hello",
    "hey",
    "hola",
    "مرحبا",
    "salam",
    "salaam",
    "as-salamu alaykum",
    "thanks",
    "thank you",
    "gracias",
    "شكرا",
    "test",
    "testing",
}
STRUCTURED_KEYWORDS = {
    "account",
    "action",
    "appointment",
    "apply",
    "balance",
    "book",
    "case",
    "cancel",
    "card",
    "complaint",
    "contact",
    "contract",
    "escalate",
    "fee",
    "help",
    "issue",
    "lead",
    "meeting",
    "order",
    "payment",
    "phone",
    "price",
    "problem",
    "refund",
    "schedule",
    "status",
    "support",
    "ticket",
    "update",
}


def _is_low_intent_message(text: str) -> bool:
    """Detect short greetings/acks that do not require planner metadata."""

    normalized = (text or "").strip()
    if not normalized:
        return False
    if len(normalized) > 80:
        return False
    lowered = normalized.lower()
    if any(keyword in lowered for keyword in STRUCTURED_KEYWORDS):
        return False
    if any(ch.isdigit() for ch in lowered):
        return False
    letters = sum(ch.isalpha() for ch in lowered)
    if letters == 0:
        return True
    for pattern in LOW_INTENT_PATTERNS:
        if pattern.match(lowered):
            return True
    if lowered in LOW_INTENT_SIMPLE:
        return True
    return False


def _tool_activity_present(stream_context: StreamingTurnContext | None) -> bool:
    if not stream_context:
        return False
    tool_context = getattr(stream_context, "tool_context", None)
    if not tool_context:
        return False
    signal_attrs = (
        "tool_trace",
        "knowledge_results",
        "knowledge_reads",
        "identifier_filters",
        "coverage_ledger",
        "table_aggregate_rows",
    )
    for attr in signal_attrs:
        values = getattr(tool_context, attr, None)
        if values:
            return True
    if getattr(stream_context, "knowledge_payload", None):
        return True
    return False


# Tools that purely retrieve knowledge (no CRM side effects)
_KNOWLEDGE_ONLY_TOOLS = frozenset({
    "search_knowledge",
    "read_document",
    "read_knowledge",
    "get_document_structure",
    "table_aggregate",
    "dataset_query",
    "query_dataset",
    "list_tables",
})

# CRM tools that need planner for action extraction
_CRM_TOOLS = frozenset({
    "create_case",
    "update_case_details",
    "add_case_history",
    "create_lead",
    "create_customer",
    "update_customer",
    "get_customer",
})


def _has_crm_signals(stream_context: StreamingTurnContext | None, user_message: str) -> bool:
    """
    Detect if this turn has CRM-related signals that warrant running the planner.

    Returns True if:
    - CRM tools were called (create_case, create_lead, etc.)
    - Non-knowledge tools were called
    - User message contains identifiers (email, phone, digits)
    - User message contains complaint/escalation signals
    """
    # Check tool trace for CRM or non-knowledge tools
    if stream_context:
        tool_context = getattr(stream_context, "tool_context", None)
        if tool_context:
            tool_trace = getattr(tool_context, "tool_trace", [])
            if isinstance(tool_trace, list):
                for entry in tool_trace:
                    if not isinstance(entry, dict):
                        continue
                    tool_name = entry.get("tool", "")
                    # If any CRM tool was called, definitely need planner
                    if tool_name in _CRM_TOOLS:
                        return True
                    # If tool is not knowledge-only, might have side effects
                    if tool_name and tool_name not in _KNOWLEDGE_ONLY_TOOLS:
                        return True
            # Check for identifier filters (indicates customer data was involved)
            identifier_filters = getattr(tool_context, "identifier_filters", None)
            if identifier_filters:
                return True

    # Check message for CRM signals
    lowered = (user_message or "").lower()
    # Identifiers (email, phone, digits) suggest action requests
    if any(ch.isdigit() for ch in lowered):
        return True
    if "@" in lowered:  # Email pattern
        return True
    # Complaint/escalation keywords
    crm_keywords = {
        "complaint", "complain", "angry", "frustrated", "escalate",
        "manager", "supervisor", "refund", "cancel", "urgent",
        "problem", "issue", "broken", "not working", "help me",
    }
    if any(kw in lowered for kw in crm_keywords):
        return True

    return False


def _business_planner_override(business: BusinessProfile | None) -> bool | None:
    if not business:
        return None
    metadata = business.metadata if isinstance(business.metadata, dict) else {}
    if not metadata:
        return None
    enabled = metadata.get("portal_planner_enabled")
    if isinstance(enabled, bool):
        return enabled
    disabled = metadata.get("portal_disable_planner")
    if isinstance(disabled, bool):
        return not disabled
    return None


def _planner_decision(
    *,
    conversation,
    user_message: str,
    stream_context: StreamingTurnContext | None,
) -> tuple[bool, str | None]:
    if getattr(settings, "PORTAL_FORCE_PLANNER", False):
        return True, None
    if getattr(settings, "PORTAL_DISABLE_PLANNER", False):
        return False, "env_disabled"
    override = _business_planner_override(getattr(conversation, "business_profile", None))
    if override is not None:
        return override, "business_override" if not override else None
    # Skip planner for low intent messages (greetings, etc.)
    if _is_low_intent_message(user_message):
        return False, "low_intent"
    # Only run planner if CRM signals are present (not just any tool activity)
    # This saves ~9s latency for pure knowledge Q&A turns
    if _has_crm_signals(stream_context, user_message):
        return True, None
    # Knowledge-only turns don't need planner
    if _tool_activity_present(stream_context):
        return False, "knowledge_only"
    # No tool activity and no CRM signals - default to running planner
    # (might be a complex intent that didn't trigger tools)
    return True, None


class PortalTraceLogger:
    """
    Structured trace logger for portal LLM turns.
    
    Clean hierarchical output - uses tree characters (├, └) only for
    showing actual nested structure, not decorative frames.
    """
    
    def __init__(
        self,
        *,
        conversation,
        agent,
        session_token: str,
        orchestrator_mode: str,
    ) -> None:
        from apps.core.console_logger import Verbosity, get_verbosity
        
        self.conversation_id = getattr(conversation, "id", None)
        self.business_id = getattr(getattr(conversation, "business_profile", None), "id", None)
        self.business_slug = getattr(getattr(conversation, "business_profile", None), "slug", None)
        self.agent_slug = getattr(agent, "slug", None)
        self.session_token = session_token
        self.orchestrator_mode = orchestrator_mode
        self._logged_header = False
        
        tz_name = getattr(settings, "PORTAL_TRACE_TIMEZONE", "Africa/Cairo")
        try:
            self._timezone = ZoneInfo(tz_name)
        except Exception:
            self._timezone = ZoneInfo("UTC")
        
        self._console_verbosity = get_verbosity(for_console=True)

    def _timestamp(self) -> str:
        return datetime.now(self._timezone).strftime("%H:%M:%S")

    def _stringify(self, value: Any, max_len: int | None = None) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            result = value
        else:
            try:
                result = json.dumps(value, ensure_ascii=False)
            except Exception:
                result = str(value)
        
        if max_len and len(result) > max_len:
            return result[:max_len - 3] + "..."
        return result

    def format_data(self, value: Any) -> str:
        return self._stringify(value)
    
    def _log_header(self) -> None:
        """Log the portal request header - clean format."""
        if self._logged_header:
            return
        
        self._logged_header = True
        ts = self._timestamp()
        
        # Clean separator and header
        logger.info("─" * 60)
        logger.info(f"[{ts}] PORTAL.REQUEST conv={str(self.conversation_id)[:8]}... agent={self.agent_slug}")

    def log(self, title: str, detail: str | dict | None = None, *, indent: int = 0, extra: Any | None = None) -> None:
        """Log an event with clean hierarchical formatting."""
        from apps.core.console_logger import Verbosity
        
        # Log header on first event
        self._log_header()
        
        indent_prefix = "  " * max(indent, 0)
        
        # Format based on verbosity
        if self._console_verbosity == Verbosity.MINIMAL:
            logger.info(f"{indent_prefix}├─ {title}")
        elif self._console_verbosity == Verbosity.STANDARD:
            if detail:
                detail_text = self._stringify(detail, max_len=80)
                logger.info(f"{indent_prefix}├─ {title}: {detail_text}")
            else:
                logger.info(f"{indent_prefix}├─ {title}")
        else:
            # Verbose: full details
            logger.info(f"{indent_prefix}├─ {title}")
            if detail:
                detail_text = self._stringify(detail)
                if len(detail_text) > 100:
                    for i in range(0, len(detail_text), 100):
                        logger.info(f"{indent_prefix}│   {detail_text[i:i+100]}")
                else:
                    logger.info(f"{indent_prefix}│   {detail_text}")

    def log_status(self, code: str, *, label: str | None = None, meta: dict | None = None, indent: int = 1) -> None:
        """Log status events - respects verbosity."""
        from apps.core.console_logger import Verbosity
        
        if self._console_verbosity == Verbosity.MINIMAL:
            return
        
        # Standard: only important status codes
        important_codes = {"searching_complete", "reading_complete", "answer_finalized", "stream_complete"}
        if self._console_verbosity == Verbosity.STANDARD and code not in important_codes:
            return
        
        detail = {"code": code}
        if label:
            detail["label"] = label
        if meta:
            detail.update(meta)
        
        self.log(f"status.{code}", detail, indent=indent)

    def log_spinner(
        self,
        text: str,
        *,
        pending: bool,
        prev_text: str | None = None,
        reason: str | None = None,
        indent: int = 1,
    ) -> None:
        """Log spinner status updates (portal state-machine only)."""
        from apps.core.console_logger import Verbosity

        if self._console_verbosity == Verbosity.MINIMAL:
            return

        detail: dict[str, object] = {"text": text, "pending": pending}
        if prev_text is not None:
            detail["prev_text"] = prev_text
        if reason:
            detail["reason"] = reason

        self.log("spinner.update", detail, indent=indent)

    def log_error(self, title: str, error: Exception | str, *, indent: int = 1) -> None:
        """Log an error."""
        self._log_header()
        indent_prefix = "  " * indent
        logger.error(f"{indent_prefix}├─ ERROR: {title}")
        logger.error(f"{indent_prefix}│   {str(error)}")




def _service() -> ChatPortalService:
    return ChatPortalService()


def _json_error(code: str, message: str, *, status: int = 400, extra: dict | None = None) -> JsonResponse:
    payload: dict[str, object] = {"error": {"code": code, "message": message}}
    if extra:
        payload["error"].update(extra)
    return JsonResponse(payload, status=status)


def _parse_json_body(request: HttpRequest) -> dict:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PortalValidationError("Invalid JSON payload") from exc


def _business_prefers_mcp(business: BusinessProfile | None, *, conversation=None) -> bool:
    """
    Evaluate whether a business should use the MCP orchestrator.

    Business metadata can override the global setting via the key
    `mcp_orchestrator_enabled`. When unset, the global
    RAG_USE_MCP_ORCHESTRATOR flag is used.
    """

    global_default = getattr(settings, "RAG_USE_MCP_ORCHESTRATOR", False)
    # Allow per-conversation escalation to the MCP orchestrator when a portal
    # feature requires tool calling (e.g., uploaded files).
    convo_meta = getattr(conversation, "metadata", None)
    if isinstance(convo_meta, dict) and convo_meta.get("mcp_required"):
        return True
    if business is None:
        return global_default
    metadata = business.metadata if isinstance(business.metadata, dict) else {}
    override = metadata.get("mcp_orchestrator_enabled")
    if override is None:
        return global_default
    return bool(override)


def _business_to_dict(summary: PortalBusinessSummary) -> dict:
    return {"id": str(summary.id), "name": summary.name, "slug": summary.slug}


def _agent_to_dict(summary: PortalAgentSummary) -> dict:
    return {
        "id": str(summary.id),
        "name": summary.name,
        "role": summary.role,
        "slug": summary.slug,
        "shareable_path": summary.shareable_path,
    }


def _session_to_dict(session: PortalSessionState) -> dict:
    return {
        "conversation_id": str(session.conversation_id),
        "session_token": session.session_token,
        "status": session.status,
        "started_at": session.started_at.isoformat(),
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


def _portal_turn_to_dict(turn: PortalTurn) -> dict[str, object]:
    return {
        "id": str(turn.id),
        "status": turn.status,
        "last_event_seq": int(turn.last_event_seq or 0),
        "message_id": str(turn.message_id) if getattr(turn, "message_id", None) else None,
        "started_at": turn.started_at.isoformat() if getattr(turn, "started_at", None) else None,
        "finalized_at": turn.finalized_at.isoformat() if getattr(turn, "finalized_at", None) else None,
    }


def _serialize_tool_approval(approval: ConversationToolApproval) -> dict[str, object]:
    return {
        "id": str(approval.id),
        "status": approval.status,
        "tool_name": approval.tool_name,
        "remote_tool_name": approval.remote_tool_name,
        "tool_call_id": approval.tool_call_id,
        "event_id": approval.event_id,
        "requested_at": approval.requested_at.isoformat() if approval.requested_at else None,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        "metadata": approval.metadata or {},
    }


def _normalize_portal_content_blocks(blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    """
    Normalize portal `content_blocks` ordering for consistent UX on refresh.

    The portal streams tool cards as they arrive. When the assistant text is persisted
    after a tool approval request, we want the content blocks to render in the same
    order after a refresh (e.g. lead-in text followed by an approval card).

    Current normalization:
    - Move "initiate_phone_call" tool cards to the end of the message (stable),
      unless they have child blocks.
    """

    if not isinstance(blocks, list) or not blocks:
        return blocks

    tool_names = {"initiate_phone_call", "phone_call"}
    call_block_ids: list[str] = []
    child_parent_ids: set[str] = set()

    for entry in blocks:
        if not isinstance(entry, Mapping):
            continue
        parent_id = str(entry.get("parent_block_id") or entry.get("parentBlockId") or "").strip()
        if parent_id:
            child_parent_ids.add(parent_id)

        if str(entry.get("type") or "").strip().lower() != "tool_use":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").strip().lower()
        if tool_name in tool_names:
            block_id = str(entry.get("block_id") or entry.get("blockId") or "").strip()
            if block_id:
                call_block_ids.append(block_id)

    if not call_block_ids:
        return blocks

    movable_ids = {block_id for block_id in call_block_ids if block_id and block_id not in child_parent_ids}
    if not movable_ids:
        return blocks

    head: list[dict[str, object]] = []
    tail: list[dict[str, object]] = []
    for entry in blocks:
        block_id = str(entry.get("block_id") or entry.get("blockId") or "").strip() if isinstance(entry, Mapping) else ""
        if block_id and block_id in movable_ids:
            tail.append(entry)
        else:
            head.append(entry)
    return [*head, *tail]


def _apply_portal_tool_approval_state(
    blocks: list[dict[str, object]],
    *,
    approval: ConversationToolApproval,
    phase: str = "approval_resolved",
) -> tuple[list[dict[str, object]], bool]:
    """
    Update persisted portal content blocks when a tool approval resolves.

    Without this, the UI can regress on refresh (e.g., denied approvals show CTAs again)
    because the last persisted message still contains `approval_requested` payloads.
    """

    if not isinstance(blocks, list) or not blocks:
        return blocks, False

    approval_id = str(getattr(approval, "id", "") or "").strip()
    if not approval_id:
        return blocks, False
    approval_event_id = str(getattr(approval, "event_id", "") or "").strip()
    approval_tool_call_id = str(getattr(approval, "tool_call_id", "") or "").strip()

    status = str(getattr(approval, "status", "") or "").strip().lower()
    if not status:
        return blocks, False

    mutated = False
    resolved_at = approval.resolved_at.isoformat() if approval.resolved_at else None
    expires_at = approval.expires_at.isoformat() if approval.expires_at else None

    for entry in blocks:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "").strip().lower() != "tool_use":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue

        match_id = str(payload.get("approval_id") or payload.get("approvalId") or "").strip()
        nested = payload.get("approval")
        nested_id = str(nested.get("id") or "").strip() if isinstance(nested, Mapping) else ""
        payload_event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
        payload_tool_call_id = str(payload.get("tool_call_id") or payload.get("toolCallId") or "").strip()
        matches = match_id == approval_id or nested_id == approval_id
        if not matches and approval_event_id and payload_event_id and payload_event_id == approval_event_id:
            matches = True
        if not matches and approval_tool_call_id and payload_tool_call_id and payload_tool_call_id == approval_tool_call_id:
            matches = True
        if not matches:
            continue

        payload["approval_id"] = approval_id
        payload["phase"] = phase
        payload["status"] = status

        approval_payload: dict[str, object] = dict(nested) if isinstance(nested, Mapping) else {}
        approval_payload["id"] = approval_id
        approval_payload["status"] = status
        if resolved_at:
            approval_payload["resolved_at"] = resolved_at
        if expires_at:
            approval_payload["expires_at"] = expires_at
        payload["approval"] = approval_payload
        entry["payload"] = payload
        mutated = True

    if mutated:
        blocks = _normalize_portal_content_blocks(blocks)
    return blocks, mutated


def _clip_portal_text(value: str, limit: int) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _serialize_agent_run_for_portal(run: AgentRun) -> dict[str, object]:
    plan_payload = run.plan if isinstance(getattr(run, "plan", None), dict) else {}
    result_payload = run.result if isinstance(getattr(run, "result", None), dict) else {}
    response_text = ""
    if isinstance(result_payload, dict):
        response_text = str(result_payload.get("response_text") or result_payload.get("responseText") or "").strip()
    error_detail = str(getattr(run, "error_detail", "") or "").strip()
    return {
        "id": str(run.id),
        "title": run.title or "",
        "source": run.source,
        "status": run.status,
        "attemptCount": int(run.attempt_count or 0),
        "maxAttempts": int(run.max_attempts or 0),
        "runAfter": run.run_after.isoformat() if run.run_after else None,
        "leaseExpiresAt": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "createdAt": run.created_at.isoformat() if run.created_at else None,
        "updatedAt": run.updated_at.isoformat() if run.updated_at else None,
        "errorDetail": _clip_portal_text(error_detail, 800) if error_detail else "",
        "plan": plan_payload,
        "result": {"responseText": _clip_portal_text(response_text, 6000)} if response_text else {},
    }


def _serialize_agent_run_event_for_portal(event: AgentRunEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "runId": str(event.run_id),
        "sequenceIndex": int(event.sequence_index),
        "stream": event.stream,
        "type": event.event_type,
        "label": event.label or "",
        "payload": event.payload if isinstance(getattr(event, "payload", None), dict) else {},
        "createdAt": event.created_at.isoformat() if event.created_at else None,
    }


def _append_agent_run_event(
    run: AgentRun,
    *,
    stream: str,
    event_type: str,
    label: str = "",
    payload: dict[str, object] | None = None,
) -> AgentRunEvent:
    with transaction.atomic():
        locked_run = AgentRun.objects.select_for_update().get(id=run.id)
        next_index = (
            AgentRunEvent.objects.filter(run=locked_run).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
        )
        return AgentRunEvent.objects.create(
            run=locked_run,
            sequence_index=int(next_index) + 1,
            stream=stream,
            event_type=event_type,
            label=(label or "")[:240],
            payload=payload or {},
        )


def _build_portal_agent_runs_snapshot(
    *,
    conversation_id: uuid.UUID,
    business_id: uuid.UUID | None,
    runs_limit: int = 15,
    events_limit_per_run: int = 20,
) -> dict[str, object]:
    runs_limit = max(1, min(int(runs_limit), 50))
    events_limit_per_run = max(0, min(int(events_limit_per_run), 50))

    with tenant_context(business_id):
        runs = list(
            AgentRun.objects.filter(conversation_id=conversation_id)
            .order_by("-created_at")[:runs_limit]
        )
        run_ids = [run.id for run in runs]

        events_by_run: dict[str, list[dict[str, object]]] = {}
        max_created_at: datetime | None = None

        if run_ids and events_limit_per_run:
            counts: dict[str, int] = {}
            grouped: dict[str, list[AgentRunEvent]] = {}
            qs = (
                AgentRunEvent.objects.filter(run_id__in=run_ids)
                .order_by("run_id", "-sequence_index")
            )
            for event in qs:
                run_id_str = str(event.run_id)
                current = counts.get(run_id_str, 0)
                if current >= events_limit_per_run:
                    continue
                counts[run_id_str] = current + 1
                grouped.setdefault(run_id_str, []).append(event)
                if event.created_at and (max_created_at is None or event.created_at > max_created_at):
                    max_created_at = event.created_at
            for run_id_str, event_list in grouped.items():
                events_by_run[run_id_str] = [
                    _serialize_agent_run_event_for_portal(item) for item in reversed(event_list)
                ]

        cursor_value = (max_created_at or timezone.now()).isoformat()
        return {
            "conversationId": str(conversation_id),
            "runs": [_serialize_agent_run_for_portal(run) for run in runs],
            "eventsByRun": events_by_run,
            "cursor": {"since": cursor_value},
        }


def _serialize_agent_request_for_portal(request: AgentRequest) -> dict[str, object]:
    context_refs = request.context_refs if isinstance(getattr(request, "context_refs", None), list) else []
    from_agent = getattr(request, "from_agent_profile", None)
    to_agent = getattr(request, "to_agent_profile", None)
    return {
        "id": str(request.id),
        "status": request.status,
        "subject": request.subject or "",
        "question": _clip_portal_text(str(request.question or ""), 6000),
        "contextRefs": context_refs,
        "resolution": _clip_portal_text(str(request.resolution or ""), 6000),
        "fromAgent": {
            "id": str(getattr(from_agent, "id", "") or ""),
            "name": str(getattr(from_agent, "name", "") or ""),
            "slug": str(getattr(from_agent, "slug", "") or ""),
        }
        if from_agent
        else {},
        "toAgent": {
            "id": str(getattr(to_agent, "id", "") or ""),
            "name": str(getattr(to_agent, "name", "") or ""),
            "slug": str(getattr(to_agent, "slug", "") or ""),
        }
        if to_agent
        else {},
        "conversationId": str(request.conversation_id) if request.conversation_id else None,
        "agentRunId": str(request.agent_run_id) if request.agent_run_id else None,
        "createdAt": request.created_at.isoformat() if request.created_at else None,
        "updatedAt": request.updated_at.isoformat() if request.updated_at else None,
        "resolvedAt": request.resolved_at.isoformat() if request.resolved_at else None,
    }


def _build_portal_agent_requests_snapshot(
    *,
    business_id: uuid.UUID | None,
    agent_profile_id: uuid.UUID | None,
    limit: int = 25,
) -> dict[str, object]:
    limit = max(1, min(int(limit), 100))
    if not business_id or not agent_profile_id:
        return {
            "agentProfileId": str(agent_profile_id) if agent_profile_id else None,
            "requests": [],
            "cursor": {"since": timezone.now().isoformat()},
        }

    with tenant_context(business_id):
        qs = (
            AgentRequest.objects.select_related("from_agent_profile", "to_agent_profile")
            .filter(business_profile_id=business_id)
            .filter(Q(to_agent_profile_id=agent_profile_id) | Q(from_agent_profile_id=agent_profile_id))
            .order_by("-updated_at")[:limit]
        )
        requests = list(qs)
        max_updated = None
        for req in requests:
            if req.updated_at and (max_updated is None or req.updated_at > max_updated):
                max_updated = req.updated_at
        cursor_value = (max_updated or timezone.now()).isoformat()
        return {
            "agentProfileId": str(agent_profile_id),
            "requests": [_serialize_agent_request_for_portal(req) for req in requests],
            "cursor": {"since": cursor_value},
        }


def _message_to_dict(message: PortalMessage) -> dict:
    return {
        "id": str(message.id),
        "sender": message.sender,
        "body": message.body,
        "sent_at": message.sent_at.isoformat(),
        "metadata": message.metadata,
        "content_blocks": message.content_blocks,
    }


def _bootstrap_to_dict(result: PortalSessionBootstrap) -> dict:
    payload = {
        "business": _business_to_dict(result.business),
        "agent": _agent_to_dict(result.agent),
        "session": _session_to_dict(result.session),
        "messages": [_message_to_dict(msg) for msg in result.messages],
    }
    try:
        # Inject pending tool approvals into existing messages to preserve ordering.
        # This keeps approval cards in the same message as the text that preceded them.
        _inject_pending_tool_approvals_into_messages(
            messages=payload["messages"],
            conversation_id=result.session.conversation_id,
            business_id=result.business.id,
        )
    except Exception:  # pragma: no cover - best effort only
        pass
    try:
        from apps.accounts.feature_flags import FeatureFlagService

        feature_state = FeatureFlagService.snapshot(result.business)
        payload["capabilities"] = {
            "subAgentsEnabled": bool(getattr(feature_state, "sub_agents_v1", False)),
        }
    except Exception:  # pragma: no cover - best effort only
        payload["capabilities"] = {"subAgentsEnabled": False}
    return payload


def _inject_pending_tool_approvals_into_messages(
    *,
    messages: list[dict],
    conversation_id: uuid.UUID,
    business_id: uuid.UUID,
    limit: int = 20,
) -> None:
    """
    Inject pending approval blocks into existing AI messages to preserve ordering.

    Instead of creating a synthetic message (which causes ordering issues on refresh),
    this function finds the last AI message and appends any pending approval blocks
    to its content_blocks array. This keeps the approval card in the same position
    relative to the text that preceded it during streaming.
    """
    if not conversation_id or not business_id or not isinstance(messages, list):
        return
    limit = max(1, min(int(limit or 0), 50))

    # Find approval IDs already rendered in existing messages
    already_rendered: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        blocks = msg.get("content_blocks") or msg.get("contentBlocks") or []
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() not in {"tool_use", "tool_result"}:
                continue
            payload = block.get("payload")
            if not isinstance(payload, dict):
                continue
            approval = payload.get("approval")
            approval_id = ""
            if isinstance(approval, dict):
                approval_id = str(approval.get("id") or "").strip()
            if not approval_id:
                approval_id = str(payload.get("approval_id") or payload.get("approvalId") or "").strip()
            if approval_id:
                already_rendered.add(approval_id)

    # Fetch pending approvals from database
    now = timezone.now()
    with tenant_context(business_id):
        approvals = list(
            ConversationToolApproval.objects.select_related("connection")
            .filter(conversation_id=conversation_id, status=ConversationToolApprovalStatus.PENDING)
            .order_by("requested_at", "id")[:limit]
        )

    if not approvals:
        return

    # Build approval blocks
    approval_blocks: list[dict[str, object]] = []
    for approval in approvals:
        approval_id = str(getattr(approval, "id", "") or "").strip()
        if not approval_id or approval_id in already_rendered:
            continue

        approval_meta = approval.metadata if isinstance(getattr(approval, "metadata", None), dict) else {}
        operation_type = approval_meta.get("operation_type") or approval_meta.get("operationType")
        reason = approval_meta.get("reason")
        mode = approval_meta.get("approval_mode") or approval_meta.get("approvalMode") or approval_meta.get("mode")
        preview = approval_meta.get("preview") if isinstance(approval_meta.get("preview"), dict) else None

        is_expired = bool(approval.expires_at and approval.expires_at <= now)
        approval_payload: dict[str, object] = {
            "id": approval_id,
            "status": ConversationToolApprovalStatus.EXPIRED if is_expired else ConversationToolApprovalStatus.PENDING,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        }
        if operation_type:
            approval_payload["operation_type"] = operation_type
        if reason:
            approval_payload["reason"] = reason
        if mode:
            approval_payload["mode"] = mode
        if preview:
            approval_payload["preview"] = preview

        tool_name = str(approval.tool_name or "").strip()
        remote_tool_name = str(approval.remote_tool_name or "").strip()
        tool_call_id = str(approval.tool_call_id or "").strip()
        event_id = str(approval.event_id or "").strip() or f"approval_{approval_id}"

        input_payload = approval.input_payload if isinstance(getattr(approval, "input_payload", None), dict) else {}

        kind = "tool"
        remote: dict[str, object] | None = None
        if approval.connection_id and remote_tool_name:
            kind = "mcp_remote"
            connection = getattr(approval, "connection", None)
            remote = {
                "connection_name": str(getattr(connection, "name", "") or "").strip(),
                "remote_tool": remote_tool_name,
            }
        elif tool_name == "initiate_phone_call":
            kind = "phone"
        elif tool_name.startswith("email_"):
            kind = "email"

        phase_value = "approval_resolved" if is_expired else "approval_requested"
        status_value = ConversationToolApprovalStatus.EXPIRED if is_expired else "pending_approval"

        tool_event_payload: dict[str, object] = {
            "event_id": event_id,
            "phase": phase_value,
            "status": status_value,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": kind,
            "approval": approval_payload,
            "input": input_payload,
        }
        if remote and (remote.get("connection_name") or remote.get("remote_tool")):
            tool_event_payload["remote"] = remote

        approval_blocks.append(
            {
                "block_id": f"tool_approval_{approval_id}",
                "type": "tool_use",
                "created_at": approval.requested_at.isoformat() if approval.requested_at else now.isoformat(),
                "payload": tool_event_payload,
            }
        )

    if not approval_blocks:
        return

    # Find the last AI message to inject blocks into
    last_ai_message: dict | None = None
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender") or "").strip().lower()
        if sender in {"ai", "assistant"}:
            last_ai_message = msg
            break

    if last_ai_message is not None:
        # Inject approval blocks into the last AI message's content_blocks
        existing_blocks = last_ai_message.get("content_blocks")
        if not isinstance(existing_blocks, list):
            existing_blocks = []
            last_ai_message["content_blocks"] = existing_blocks
        # Append approval blocks at the end (preserving text → approval order)
        existing_blocks.extend(approval_blocks)
    else:
        # Fallback: create a synthetic message if no AI message exists
        message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pending_tool_approvals:{conversation_id}")
        messages.append({
            "id": str(message_id),
            "sender": "ai",
            "body": "",
            "sent_at": now.isoformat(),
            "metadata": {"type": "pending_tool_approvals"},
            "content_blocks": approval_blocks,
        })


def _pending_tool_approvals_message(
    *,
    conversation_id: uuid.UUID,
    business_id: uuid.UUID,
    existing_messages: list[dict] | None,
    limit: int = 20,
) -> dict | None:
    """
    Surface pending approvals in the portal transcript on refresh.

    Why:
    - During an interactive tool approval, the portal turn can be "in-flight" while waiting.
      Tool cards exist only in the live stream until the turn finalizes and persists a message.
    - If the visitor refreshes mid-approval, those cards disappear even though the approval
      still exists in the database.

    This helper rebuilds minimal tool_use blocks from ConversationToolApproval rows so the
    UI stays in sync after a refresh.
    """

    if not conversation_id or not business_id:
        return None
    limit = max(1, min(int(limit or 0), 50))

    already_rendered: set[str] = set()
    if isinstance(existing_messages, list):
        for msg in existing_messages:
            if not isinstance(msg, dict):
                continue
            blocks = msg.get("content_blocks") or msg.get("contentBlocks") or []
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if str(block.get("type") or "").strip().lower() not in {"tool_use", "tool_result"}:
                    continue
                payload = block.get("payload")
                if not isinstance(payload, dict):
                    continue
                approval = payload.get("approval")
                approval_id = ""
                if isinstance(approval, dict):
                    approval_id = str(approval.get("id") or "").strip()
                if not approval_id:
                    approval_id = str(payload.get("approval_id") or payload.get("approvalId") or "").strip()
                if approval_id:
                    already_rendered.add(approval_id)

    now = timezone.now()
    with tenant_context(business_id):
        approvals = list(
            ConversationToolApproval.objects.select_related("connection")
            .filter(conversation_id=conversation_id, status=ConversationToolApprovalStatus.PENDING)
            .order_by("requested_at", "id")[:limit]
        )

    blocks: list[dict[str, object]] = []
    for approval in approvals:
        approval_id = str(getattr(approval, "id", "") or "").strip()
        if not approval_id or approval_id in already_rendered:
            continue

        approval_meta = approval.metadata if isinstance(getattr(approval, "metadata", None), dict) else {}
        operation_type = approval_meta.get("operation_type") or approval_meta.get("operationType")
        reason = approval_meta.get("reason")
        mode = approval_meta.get("approval_mode") or approval_meta.get("approvalMode") or approval_meta.get("mode")
        preview = approval_meta.get("preview") if isinstance(approval_meta.get("preview"), dict) else None

        is_expired = bool(approval.expires_at and approval.expires_at <= now)
        approval_payload: dict[str, object] = {
            "id": approval_id,
            "status": ConversationToolApprovalStatus.EXPIRED if is_expired else ConversationToolApprovalStatus.PENDING,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
        }
        if operation_type:
            approval_payload["operation_type"] = operation_type
        if reason:
            approval_payload["reason"] = reason
        if mode:
            approval_payload["mode"] = mode
        if preview:
            approval_payload["preview"] = preview

        tool_name = str(approval.tool_name or "").strip()
        remote_tool_name = str(approval.remote_tool_name or "").strip()
        tool_call_id = str(approval.tool_call_id or "").strip()
        event_id = str(approval.event_id or "").strip() or f"approval_{approval_id}"

        input_payload = approval.input_payload if isinstance(getattr(approval, "input_payload", None), dict) else {}

        kind = "tool"
        remote: dict[str, object] | None = None
        if approval.connection_id and remote_tool_name:
            kind = "mcp_remote"
            connection = getattr(approval, "connection", None)
            remote = {
                "connection_name": str(getattr(connection, "name", "") or "").strip(),
                "remote_tool": remote_tool_name,
            }
        elif tool_name == "initiate_phone_call":
            kind = "phone"
        elif tool_name.startswith("email_"):
            kind = "email"

        phase_value = "approval_resolved" if is_expired else "approval_requested"
        status_value = ConversationToolApprovalStatus.EXPIRED if is_expired else "pending_approval"

        tool_event_payload: dict[str, object] = {
            "event_id": event_id,
            "phase": phase_value,
            "status": status_value,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": kind,
            "approval": approval_payload,
            "input": input_payload,
        }
        if remote and (remote.get("connection_name") or remote.get("remote_tool")):
            tool_event_payload["remote"] = remote

        blocks.append(
            {
                "block_id": f"tool_approval_{approval_id}",
                "type": "tool_use",
                "created_at": approval.requested_at.isoformat() if approval.requested_at else now.isoformat(),
                "payload": tool_event_payload,
            }
        )

    if not blocks:
        return None

    message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"pending_tool_approvals:{conversation_id}")
    return {
        "id": str(message_id),
        "sender": "ai",
        "body": "",
        "sent_at": now.isoformat(),
        "metadata": {"type": "pending_tool_approvals"},
        "content_blocks": blocks,
    }


@require_GET
def resolve_portal_handle(request: HttpRequest, business_slug: str, agent_slug: str) -> JsonResponse:
    service = _service()
    try:
        business, agent = service.resolve_handle(business_slug, agent_slug)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    return JsonResponse(
        {
            "business": {"id": str(business.id), "name": business.name, "slug": business.slug},
            "agent": {
                "id": str(agent.id),
                "name": agent.name,
                "role": agent.role or "AI Assistant",
                "slug": agent.slug,
                "shareable_path": agent.shareable_path,
            },
        }
    )


@csrf_exempt
@require_POST
def bootstrap_session(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    business_slug = (payload.get("business_slug") or payload.get("businessSlug") or "").strip()
    agent_slug = (payload.get("agent_slug") or payload.get("agentSlug") or "").strip()
    existing_session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip() or None
    metadata_raw = payload.get("metadata") or {}
    metadata: dict[str, object] = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}

    if not business_slug or not agent_slug:
        return _json_error("validation_error", "business_slug and agent_slug are required.")

    # If an authenticated tenant user is bootstrapping the portal, attach their id
    # to the session metadata so internal tools can resolve per-user integrations.
    try:
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            business, _agent = service.resolve_handle(business_slug, agent_slug)
            allowed = bool(user.is_staff or user.business_profiles.filter(id=business.id).exists())
            if allowed and "actor_user_id" not in metadata and "actorUserId" not in metadata:
                metadata["actor_user_id"] = str(user.id)
    except Exception:  # pragma: no cover - best effort only
        pass

    try:
        result = service.bootstrap_session(
            business_slug=business_slug,
            agent_slug=agent_slug,
            existing_session_token=existing_session_token,
            metadata=metadata,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    response = JsonResponse(_bootstrap_to_dict(result), status=200)
    response.set_cookie(
        f"chat_session_{result.business.slug}_{result.agent.slug}",
        result.session.session_token,
        max_age=3600 * 24 * 365,
        httponly=False,
        secure=False,
        samesite="Lax",
    )
    return response


@csrf_exempt
@require_http_methods(["GET", "POST"])
def messages_endpoint(request: HttpRequest) -> JsonResponse:
    service = _service()
    if request.method == "GET":
        session_token = request.GET.get("session_token") or request.GET.get("sessionToken")
        if not session_token:
            return _json_error("validation_error", "session_token is required")
        limit_param = request.GET.get("limit")
        limit = None
        if limit_param:
            try:
                limit = max(1, min(200, int(limit_param)))
            except ValueError:
                return _json_error("validation_error", "limit must be an integer between 1 and 200")
        try:
            messages = service.list_messages(session_token=session_token, limit=limit)
            session = service.get_session_state(session_token=session_token)
        except PortalNotFoundError as exc:
            return _json_error("not_found", str(exc), status=404)
        return JsonResponse(
            {"session": _session_to_dict(session), "messages": [_message_to_dict(msg) for msg in messages]}
        )

    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}

    try:
        message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse({"message": _message_to_dict(message)}, status=201)


@csrf_exempt
@require_POST
def submit_csat(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    try:
        score = int(payload.get("score"))
    except (TypeError, ValueError):
        return _json_error("validation_error", "score must be an integer between 1 and 5")
    comment = (payload.get("comment") or "").strip() or None

    try:
        session = service.record_csat(session_token=session_token, score=score, comment=comment)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse({"session": _session_to_dict(session)}, status=200)


@csrf_exempt
@require_POST
def submit_feedback(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    feedback_type = (payload.get("feedback_type") or payload.get("feedbackType") or "").strip()
    if not session_token or not feedback_type:
        return _json_error("validation_error", "session_token and feedback_type are required.")
    message_id_value = payload.get("message_id") or payload.get("messageId")
    message_id: uuid.UUID | None = None
    if message_id_value:
        try:
            message_id = uuid.UUID(str(message_id_value))
        except (TypeError, ValueError):
            return _json_error("validation_error", "message_id must be a valid UUID.")
    feedback_payload = {
        "query_text": payload.get("query_text"),
        "expected_behavior": payload.get("expected_behavior"),
        "expected_entities": payload.get("expected_entities") or [],
        "expected_aliases": payload.get("expected_aliases") or [],
        "notes": payload.get("notes"),
        "auto_promote": payload.get("auto_promote", True),
    }
    try:
        feedback = service.record_feedback(
            session_token=session_token,
            feedback_type=feedback_type,
            message_id=message_id,
            payload=feedback_payload,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse(
        {
            "feedback": {
                "id": str(feedback.id),
                "feedback_type": feedback.feedback_type,
                "created_at": feedback.created_at.isoformat(),
            }
        },
        status=201,
    )


def _verification_hash(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mask_verification_destination(method: str, destination: str) -> str:
    text = (destination or "").strip()
    if not text:
        return ""
    if method == "email":
        if "@" not in text:
            return "[EMAIL]"
        local, _, domain = text.partition("@")
        local = local.strip()
        domain = domain.strip()
        if not domain:
            return "[EMAIL]"
        return f"{local[:1] or '*'}***@{domain}"
    if method == "phone":
        digits = re.sub(r"\D", "", text)
        if len(digits) < 4:
            return "***"
        return f"***{digits[-2:]}"
    return "***"


def _portal_verification_policy(business: BusinessProfile | None) -> dict[str, object]:
    enabled = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_ENABLED", True))
    require_for_pii = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_REQUIRE_FOR_PII", True))
    allow_customer_match = bool(getattr(settings, "MCP_VERIFIED_LOOKUP_ALLOW_CUSTOMER_MATCH", True))

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


def _conversation_is_verified_for_lookup(conversation) -> bool:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    marker = metadata.get("verified_lookup") if isinstance(metadata, Mapping) else None
    if marker is True:
        return True
    if isinstance(marker, Mapping):
        status = str(marker.get("status") or marker.get("state") or "").strip().lower()
        if status in {"verified", "ok", "passed"}:
            return True
        if marker.get("verified") is True:
            return True
    return False


@csrf_exempt
@require_POST
def portal_verification_status(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    if not session_token:
        return _json_error("validation_error", "session_token is required.")
    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    policy = _portal_verification_policy(getattr(conversation, "business_profile", None))
    verified = _conversation_is_verified_for_lookup(conversation)
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "verified_lookup": {
                "enabled": bool(policy.get("enabled")),
                "verified": bool(verified),
            },
        }
    )


@csrf_exempt
@require_POST
def portal_verification_start(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    method = str(payload.get("method") or "").strip().lower()
    destination = str(payload.get("destination") or payload.get("value") or "").strip()
    if not session_token or not method or not destination:
        return _json_error("validation_error", "session_token, method, and destination are required.")
    if method not in {"email", "phone"}:
        return _json_error("validation_error", "method must be 'email' or 'phone'.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    policy = _portal_verification_policy(getattr(conversation, "business_profile", None))
    if not bool(policy.get("enabled")):
        return _json_error("verification_disabled", "Verification is disabled for this business.", status=403)
    if _conversation_is_verified_for_lookup(conversation):
        return JsonResponse(
            {
                "session": _session_to_dict(session),
                "verified_lookup": {"verified": True},
            },
            status=200,
        )

    ttl_seconds = int(getattr(settings, "PORTAL_VERIFICATION_OTP_TTL_SECONDS", 600) or 600)
    ttl_seconds = max(60, min(ttl_seconds, 3600))
    cooldown_seconds = int(getattr(settings, "PORTAL_VERIFICATION_RESEND_COOLDOWN_SECONDS", 30) or 30)
    cooldown_seconds = max(5, min(cooldown_seconds, 300))
    max_attempts = int(getattr(settings, "PORTAL_VERIFICATION_MAX_ATTEMPTS", 5) or 5)
    max_attempts = max(3, min(max_attempts, 10))

    cooldown_key = f"portal:verify:cooldown:{conversation.id}:{method}"
    if cache.get(cooldown_key):
        return _json_error("rate_limited", "Please wait a moment before requesting another code.", status=429)
    cache.set(cooldown_key, True, timeout=cooldown_seconds)

    challenge_id = uuid.uuid4()
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = timezone.now() + timedelta(seconds=ttl_seconds)
    record = {
        "method": method,
        "destination_sha256": _verification_hash(destination),
        "code_sha256": _verification_hash(code),
        "attempts": 0,
        "max_attempts": max_attempts,
        "expires_at": expires_at.isoformat(),
    }
    cache_key = f"portal:verify:challenge:{conversation.id}:{challenge_id}"
    cache.set(cache_key, record, timeout=ttl_seconds)

    # NOTE: For local/dev we can return the code to simplify end-to-end testing.
    return_code = bool(getattr(settings, "PORTAL_VERIFICATION_DEBUG_RETURN_CODE", False) or getattr(settings, "DEBUG", False))
    response: dict[str, object] = {
        "session": _session_to_dict(session),
        "challenge_id": str(challenge_id),
        "method": method,
        "destination": _mask_verification_destination(method, destination),
        "expires_in": ttl_seconds,
    }
    if return_code:
        response["debug_code"] = code
    return JsonResponse(response, status=200)


@csrf_exempt
@require_POST
def portal_verification_confirm(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    challenge_id_raw = str(payload.get("challenge_id") or payload.get("challengeId") or "").strip()
    code = str(payload.get("code") or "").strip()
    if not session_token or not challenge_id_raw or not code:
        return _json_error("validation_error", "session_token, challenge_id, and code are required.")
    try:
        challenge_id = uuid.UUID(challenge_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "challenge_id must be a valid UUID.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    cache_key = f"portal:verify:challenge:{conversation.id}:{challenge_id}"
    record = cache.get(cache_key)
    if not isinstance(record, Mapping):
        return _json_error("verification_expired", "Verification challenge expired. Request a new code.", status=410)

    expires_at_raw = record.get("expires_at")
    expires_at: datetime | None = None
    if isinstance(expires_at_raw, str) and expires_at_raw:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            expires_at = None
    if expires_at and timezone.is_naive(expires_at):
        expires_at = timezone.make_aware(expires_at, timezone.get_current_timezone())
    if expires_at and expires_at < timezone.now():
        cache.delete(cache_key)
        return _json_error("verification_expired", "Verification challenge expired. Request a new code.", status=410)

    attempts = int(record.get("attempts") or 0)
    max_attempts = int(record.get("max_attempts") or 5)
    if attempts >= max_attempts:
        cache.delete(cache_key)
        return _json_error("too_many_attempts", "Too many attempts. Request a new code.", status=429)

    expected = str(record.get("code_sha256") or "")
    provided = _verification_hash(code)
    if not expected or not hmac.compare_digest(expected, provided):
        attempts += 1
        record_out = dict(record)
        record_out["attempts"] = attempts
        ttl_remaining = 60
        if expires_at:
            ttl_remaining = max(1, int((expires_at - timezone.now()).total_seconds()))
        cache.set(cache_key, record_out, timeout=ttl_remaining)
        return _json_error(
            "invalid_code",
            "Invalid code. Please try again.",
            status=400,
            extra={"attempts_left": max(0, max_attempts - attempts)},
        )

    convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
    meta = dict(convo_meta)
    meta["verified_lookup"] = {
        "status": "verified",
        "method": record.get("method"),
        "verified_at": timezone.now().isoformat(),
    }
    conversation.metadata = meta
    conversation.save(update_fields=["metadata", "last_activity_at"])
    cache.delete(cache_key)

    session = service.get_session_state(session_token=session_token, conversation=conversation)
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "verified_lookup": {"verified": True},
        },
        status=200,
    )


@csrf_exempt
@require_POST
def portal_tool_approval(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    approval_id = (payload.get("approval_id") or payload.get("approvalId") or "").strip()
    decision_raw = payload.get("decision") or payload.get("action") or payload.get("status") or ""
    decision = str(decision_raw).strip().lower()
    remember_raw = payload.get("remember") or payload.get("always_allow") or payload.get("alwaysAllow") or False
    remember = False
    if isinstance(remember_raw, str):
        remember = remember_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        remember = bool(remember_raw)
    if not session_token or not approval_id or not decision:
        return _json_error("validation_error", "session_token, approval_id, and decision are required.")

    if decision in {"approve", "approved", "allow"}:
        next_status = ConversationToolApprovalStatus.APPROVED
    elif decision in {"deny", "denied", "reject"}:
        next_status = ConversationToolApprovalStatus.DENIED
    else:
        return _json_error("validation_error", "decision must be approve or deny.")

    try:
        approval_uuid = uuid.UUID(approval_id)
    except (TypeError, ValueError):
        return _json_error("validation_error", "approval_id is invalid.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    approval: ConversationToolApproval | None = None
    now = timezone.now()
    business_id = getattr(conversation, "business_profile_id", None)
    preference_saved = False
    with transaction.atomic():
        with tenant_context(business_id):
            approval = ConversationToolApproval.objects.select_for_update().filter(
                id=approval_uuid,
                conversation=conversation,
            ).first()
            if not approval:
                return _json_error("not_found", "Approval not found.", status=404)
            if approval.status != ConversationToolApprovalStatus.PENDING:
                try:
                    age_ms = None
                    if approval.requested_at:
                        age_ms = int((now - approval.requested_at).total_seconds() * 1000)
                    structured_log(
                        "portal",
                        "approval.tool.decision",
                        {
                            "status": approval.status,
                            "decision": decision,
                            "already_resolved": True,
                            "tool_name": approval.tool_name,
                            "remote_tool_name": approval.remote_tool_name,
                            "connection_id": str(approval.connection_id or ""),
                            "age_ms": age_ms,
                        },
                        context={
                            "business": business_id,
                            "conversation": conversation.id,
                            "approval": str(approval.id),
                        },
                        level=logging.INFO,
                    )
                except Exception:
                    pass
                return JsonResponse({"session": _session_to_dict(session), "approval": _serialize_tool_approval(approval)})
            if approval.expires_at and approval.expires_at <= now:
                approval.status = ConversationToolApprovalStatus.EXPIRED
                approval.resolved_at = now
                approval.save(update_fields=["status", "resolved_at", "updated_at"])
                try:
                    age_ms = None
                    if approval.requested_at:
                        age_ms = int((now - approval.requested_at).total_seconds() * 1000)
                    structured_log(
                        "portal",
                        "approval.tool.decision",
                        {
                            "status": approval.status,
                            "decision": decision,
                            "expired": True,
                            "tool_name": approval.tool_name,
                            "remote_tool_name": approval.remote_tool_name,
                            "connection_id": str(approval.connection_id or ""),
                            "age_ms": age_ms,
                        },
                        context={
                            "business": business_id,
                            "conversation": conversation.id,
                            "approval": str(approval.id),
                        },
                        level=logging.INFO,
                    )
                except Exception:
                    pass
                return JsonResponse({"session": _session_to_dict(session), "approval": _serialize_tool_approval(approval)})
            approval.status = next_status
            approval.resolved_at = now
            approval.save(update_fields=["status", "resolved_at", "updated_at"])

    if approval and approval.connection_id:
        try:
            with tenant_context(business_id):
                McpConnectionAuditEvent.objects.create(
                    business_profile=conversation.business_profile,
                    connection=approval.connection,
                    connection_id_snapshot=approval.connection_id,
                    actor_user=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
                    action=McpConnectionAuditAction.TOOL_APPROVED
                    if approval.status == ConversationToolApprovalStatus.APPROVED
                    else McpConnectionAuditAction.TOOL_DENIED,
                    description=f"Tool {approval.tool_name} {approval.status} via portal.",
                    metadata={
                        "approval_id": str(approval.id),
                        "conversation_id": str(conversation.id),
                        "tool_name": approval.tool_name,
                        "remote_tool_name": approval.remote_tool_name,
                        "tool_call_id": approval.tool_call_id,
                    },
                )
        except Exception:  # pragma: no cover - audit should never block
            logger.exception("mcp_tool_approval_audit_failed approval=%s", approval.id)

    if (
        approval
        and approval.status == ConversationToolApprovalStatus.APPROVED
        and remember
        and approval.connection_id
        and approval.remote_tool_name
    ):
        allow_persist = False
        if getattr(settings, "PORTAL_ALLOW_MCP_TOOL_PREFERENCES", False):
            allow_persist = True
        elif getattr(request, "user", None) and request.user.is_authenticated:
            allow_persist = bool(
                request.user.is_staff
                or request.user.business_profiles.filter(id=conversation.business_profile_id).exists()
            )
        if allow_persist and conversation.agent_profile_id:
            try:
                operation_value = str((approval.metadata or {}).get("operation_type") or "").strip().lower()
                operation_type = (
                    operation_value
                    if operation_value in {McpToolOperationType.READ, McpToolOperationType.WRITE, McpToolOperationType.UNKNOWN}
                    else McpToolOperationType.UNKNOWN
                )
                with tenant_context(business_id):
                    AgentMcpToolSetting.objects.update_or_create(
                        agent_profile_id=conversation.agent_profile_id,
                        connection_id=approval.connection_id,
                        tool_name=approval.remote_tool_name,
                        defaults={
                            "approval_mode": McpConnectionApprovalMode.AUTO,
                            "operation_type": operation_type,
                        },
                    )
                preference_saved = True
            except Exception:  # pragma: no cover - best effort only
                logger.exception("portal_tool_preference_save_failed approval=%s", approval.id)

    # If this approval unblocks a background AgentRun (Tasks panel), resume/cancel it.
    if approval:
        actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        if actor_user:
            actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
        else:
            actor_snapshot = {
                "type": "portal_session",
                "session_hash": hashlib.sha256(session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
            }
        with tenant_context(business_id):
            waiting_runs = list(
                AgentRun.objects.filter(conversation_id=conversation.id, status=AgentRunStatus.WAITING_APPROVAL)
                .order_by("-updated_at")[:15]
            )
            for run in waiting_runs:
                meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
                pending_id = str(meta.get("pending_approval_id") or "").strip()
                if pending_id and pending_id != str(approval.id):
                    continue

                decision_value = "approve" if approval.status == ConversationToolApprovalStatus.APPROVED else "deny"
                _append_agent_run_event(
                    run,
                    stream=AgentRunEventStream.EXECUTED,
                    event_type=AgentRunEventType.PROGRESS,
                    label="Approved" if decision_value == "approve" else "Denied",
                    payload={
                        "decision": decision_value,
                        "approval_id": str(approval.id),
                        "tool_name": approval.tool_name,
                        "remote_tool_name": approval.remote_tool_name,
                    },
                )
                AgentRunMemoryItem.objects.create(
                    run=run,
                    kind=AgentRunMemoryKind.DECISION,
                    key="tool_approval",
                    content=decision_value,
                    payload={
                        "decision": decision_value,
                        "approval_id": str(approval.id),
                        "tool_name": approval.tool_name,
                        "remote_tool_name": approval.remote_tool_name,
                        "actor": actor_snapshot,
                    },
                    created_by=actor_user,
                )

                next_meta = dict(meta)
                next_meta.pop("pending_approval_id", None)

                if approval.status == ConversationToolApprovalStatus.APPROVED:
                    AgentRun.objects.filter(id=run.id).update(
                        status=AgentRunStatus.QUEUED,
                        run_after=timezone.now(),
                        lease_expires_at=None,
                        error_detail="",
                        metadata=next_meta,
                        updated_at=timezone.now(),
                    )
                else:
                    AgentRun.objects.filter(id=run.id).update(
                        status=AgentRunStatus.CANCELLED,
                        finished_at=timezone.now(),
                        lease_expires_at=None,
                        run_after=None,
                        error_detail="denied",
                        metadata=next_meta,
                        updated_at=timezone.now(),
                    )

    if approval:
        try:
            latency_ms = None
            if approval.requested_at and approval.resolved_at:
                latency_ms = int((approval.resolved_at - approval.requested_at).total_seconds() * 1000)
            structured_log(
                "portal",
                "approval.tool.decision",
                {
                    "status": approval.status,
                    "decision": decision,
                    "remember": remember,
                    "preference_saved": preference_saved,
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                    "connection_id": str(approval.connection_id or ""),
                    "operation_type": str((approval.metadata or {}).get("operation_type") or ""),
                    "latency_ms": latency_ms,
                },
                context={
                    "business": business_id,
                    "conversation": conversation.id,
                    "approval": str(approval.id),
                },
                level=logging.INFO,
            )
        except Exception:  # pragma: no cover - observability must not block portal responses
            pass

    # Patch any persisted in-flight assistant message blocks so refresh reflects the latest decision.
    # Without this, visitors can see stale "pending" CTAs after a deny/expire.
    if approval and business_id:
        try:
            approval_id_str = str(approval.id)
            with tenant_context(business_id):
                base_qs = (
                    ConversationMessage.objects.filter(conversation_id=conversation.id, sender=ConversationSender.AI)
                    .order_by("-sent_at", "-created_at")
                )
                candidates = list(base_qs.filter(metadata__pending_approval_id=approval_id_str)[:6])
                if not candidates:
                    candidates = list(base_qs[:30])
                for msg in candidates:
                    blocks_raw = msg.content_blocks if isinstance(getattr(msg, "content_blocks", None), list) else []
                    updated_blocks, mutated = _apply_portal_tool_approval_state(blocks_raw, approval=approval)
                    if not mutated:
                        continue
                    meta_in = msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {}
                    meta_out = dict(meta_in)
                    if str(meta_out.get("pending_approval_id") or "").strip() == approval_id_str:
                        meta_out.pop("pending_approval_id", None)
                    if meta_out.get("portal_turn_state") == "waiting_approval":
                        meta_out["portal_turn_state"] = "approval_resolved"
                    ConversationMessage.objects.filter(id=msg.id).update(
                        content_blocks=updated_blocks,
                        metadata=meta_out,
                    )
        except Exception:  # pragma: no cover - best effort only
            logger.exception("portal tool approval message patch failed approval=%s", getattr(approval, "id", None))

    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "approval": _serialize_tool_approval(approval),
            "preferenceSaved": preference_saved,
        }
    )


@csrf_exempt
@require_POST
def portal_agent_run_user_input(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    run_id_raw = (payload.get("run_id") or payload.get("runId") or "").strip()
    message = str(payload.get("message") or "").strip()
    extra = payload.get("payload")
    extra_payload = dict(extra) if isinstance(extra, dict) else {}

    if not session_token or not run_id_raw:
        return _json_error("validation_error", "session_token and run_id are required.")
    if not message and not extra_payload:
        return _json_error("validation_error", "message or payload is required.")

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "run_id is invalid.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    business_id = getattr(conversation, "business_profile_id", None)
    enabled = False
    try:
        from apps.accounts.feature_flags import FeatureFlagService
        from apps.accounts.models import BusinessProfile

        if business_id:
            with tenant_context(business_id):
                business = BusinessProfile.objects.filter(id=business_id).only("id", "metadata").first()
            enabled = bool(getattr(FeatureFlagService.snapshot(business), "sub_agents_v1", False)) if business else False
    except Exception:  # pragma: no cover - best effort only
        enabled = False

    if not enabled:
        return _json_error("feature_disabled", "Sub-agents are not enabled for this business.", status=403)
    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    if actor_user:
        actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
    else:
        actor_snapshot = {
            "type": "portal_session",
            "session_hash": hashlib.sha256(session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
        }

    with tenant_context(business_id):
        run = AgentRun.objects.filter(id=run_uuid, conversation_id=conversation.id).first()
        if run is None:
            return _json_error("not_found", "Run not found.", status=404)

        if run.status not in {AgentRunStatus.WAITING_USER, AgentRunStatus.PAUSED, AgentRunStatus.WAITING_EXTERNAL}:
            return _json_error("run_not_waiting_user", "Run is not waiting for user input.", status=409)

        _append_agent_run_event(
            run,
            stream=AgentRunEventStream.EXECUTED,
            event_type=AgentRunEventType.PROGRESS,
            label="User input received",
            payload={"message": message, "payload": extra_payload} if extra_payload else {"message": message},
        )
        AgentRunMemoryItem.objects.create(
            run=run,
            kind=AgentRunMemoryKind.NOTE,
            key="user_input",
            content=message[:4000],
            payload={"actor": actor_snapshot, "payload": extra_payload},
            created_by=actor_user,
        )

        next_meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
        next_meta = dict(next_meta)
        next_meta.pop("pending_user_input", None)

        AgentRun.objects.filter(id=run.id).update(
            status=AgentRunStatus.QUEUED,
            run_after=timezone.now(),
            lease_expires_at=None,
            error_detail="",
            metadata=next_meta,
            updated_at=timezone.now(),
        )
        run.refresh_from_db()

    try:
        if message:
            service.append_message(
                session_token=session_token,
                sender=ConversationSender.CUSTOMER,
                body=message,
                metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "user_input"},
                conversation=conversation,
            )
    except Exception:  # pragma: no cover - chat transcript should not block execution
        logger.exception("portal_agent_run_user_input_message_failed run=%s", run_uuid)

    # Also append into the run's isolated execution conversation so the run can continue
    # with a true session transcript (no restart / resume hacks).
    try:
        execution_conversation = None
        if run and run.execution_conversation_id and business_id:
            with tenant_context(business_id):
                execution_conversation = Conversation.objects.filter(
                    id=run.execution_conversation_id,
                    business_profile_id=business_id,
                ).first()
        if execution_conversation:
            body = message
            if not body and extra_payload:
                body = "User input payload:\n" + json.dumps(extra_payload, ensure_ascii=False)
            if body:
                service.append_message(
                    session_token=execution_conversation.session_token,
                    sender=ConversationSender.CUSTOMER,
                    body=body,
                    metadata={"source": "agent_run", "agent_run_id": str(run.id), "type": "user_input"},
                    conversation=execution_conversation,
                )
    except Exception:  # pragma: no cover - best effort only
        logger.exception("portal_agent_run_user_input_execution_message_failed run=%s", run_uuid)

    return JsonResponse({"session": _session_to_dict(session), "run": _serialize_agent_run_for_portal(run)}, status=200)


@csrf_exempt
@require_POST
def portal_agent_run_approval(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    run_id_raw = (payload.get("run_id") or payload.get("runId") or "").strip()
    approval_id_raw = (payload.get("approval_id") or payload.get("approvalId") or "").strip()
    decision_raw = payload.get("decision") or payload.get("action") or payload.get("status") or ""
    decision = str(decision_raw).strip().lower()

    if not session_token or not run_id_raw or not decision:
        return _json_error("validation_error", "session_token, run_id, and decision are required.")

    if decision in {"approve", "approved", "allow"}:
        next_status = ConversationToolApprovalStatus.APPROVED
        decision_value = "approve"
    elif decision in {"deny", "denied", "reject"}:
        next_status = ConversationToolApprovalStatus.DENIED
        decision_value = "deny"
    else:
        return _json_error("validation_error", "decision must be approve or deny.")

    try:
        run_uuid = uuid.UUID(run_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "run_id is invalid.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    business_id = getattr(conversation, "business_profile_id", None)
    enabled = False
    try:
        from apps.accounts.feature_flags import FeatureFlagService

        if business_id:
            with tenant_context(business_id):
                business = BusinessProfile.objects.filter(id=business_id).only("id", "metadata").first()
            enabled = bool(getattr(FeatureFlagService.snapshot(business), "sub_agents_v1", False)) if business else False
    except Exception:  # pragma: no cover - best effort only
        enabled = False

    if not enabled:
        return _json_error("feature_disabled", "Sub-agents are not enabled for this business.", status=403)

    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    if actor_user:
        actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
    else:
        actor_snapshot = {
            "type": "portal_session",
            "session_hash": hashlib.sha256(session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
        }

    run: AgentRun | None = None
    approval: ConversationToolApproval | None = None
    with transaction.atomic():
        with tenant_context(business_id):
            run = AgentRun.objects.select_for_update().filter(id=run_uuid, conversation_id=conversation.id).first()
            if run is None:
                return _json_error("not_found", "Run not found.", status=404)

            if run.status not in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.PAUSED}:
                return _json_error("run_not_waiting_approval", "Run is not waiting for approval.", status=409)

            meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
            pending_id = str(meta.get("pending_approval_id") or "").strip()
            if not pending_id:
                return _json_error("missing_pending_approval", "Run has no pending approval to resolve.", status=409)
            if approval_id_raw and approval_id_raw != pending_id:
                return _json_error("approval_mismatch", "approval_id does not match the run's pending approval.", status=409)

            try:
                approval_uuid = uuid.UUID(pending_id)
            except (TypeError, ValueError):
                return _json_error("approval_invalid", "Run pending approval ID is invalid.", status=409)

            approval = ConversationToolApproval.objects.select_for_update().filter(
                id=approval_uuid,
                conversation__business_profile_id=business_id,
            ).first()
            if not approval:
                return _json_error("not_found", "Approval not found.", status=404)

            execution_uuid = run.execution_conversation_id
            if execution_uuid and approval.conversation_id != execution_uuid:
                return _json_error("approval_mismatch", "Approval does not belong to this run.", status=409)

            now = timezone.now()
            if approval.status == ConversationToolApprovalStatus.PENDING:
                approval.status = next_status
                approval.resolved_at = now
                approval.save(update_fields=["status", "resolved_at", "updated_at"])

            _append_agent_run_event(
                run,
                stream=AgentRunEventStream.EXECUTED,
                event_type=AgentRunEventType.PROGRESS,
                label="Approved" if decision_value == "approve" else "Denied",
                payload={
                    "decision": decision_value,
                    "approval_id": str(approval.id),
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                },
            )
            AgentRunMemoryItem.objects.create(
                run=run,
                kind=AgentRunMemoryKind.DECISION,
                key="tool_approval",
                content=decision_value,
                payload={
                    "decision": decision_value,
                    "approval_id": str(approval.id),
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                    "actor": actor_snapshot,
                },
                created_by=actor_user,
            )

            next_meta = dict(meta)
            next_meta.pop("pending_approval_id", None)
            # NOTE: pending_tool_call is preserved - worker will execute it directly and clear it

            if decision_value == "approve":
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.QUEUED,
                    run_after=now,
                    lease_expires_at=None,
                    error_detail="",
                    metadata=next_meta,
                    updated_at=now,
                )
            else:
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.CANCELLED,
                    finished_at=now,
                    lease_expires_at=None,
                    run_after=None,
                    error_detail="denied",
                    metadata=next_meta,
                    updated_at=now,
                )
            run.refresh_from_db()

    assert run is not None
    if approval is not None:
        try:
            latency_ms = None
            if approval.requested_at and approval.resolved_at:
                latency_ms = int((approval.resolved_at - approval.requested_at).total_seconds() * 1000)
            structured_log(
                "portal",
                "approval.run.decision",
                {
                    "decision": decision_value,
                    "approval_status": approval.status,
                    "tool_name": approval.tool_name,
                    "remote_tool_name": approval.remote_tool_name,
                    "latency_ms": latency_ms,
                    "actor_type": str(actor_snapshot.get("type") or ""),
                },
                context={
                    "business": business_id,
                    "conversation": conversation.id,
                    "run": run.id,
                    "approval": str(approval.id),
                },
                level=logging.INFO,
            )
        except Exception:  # pragma: no cover - observability must not block portal responses
            pass
    return JsonResponse({"session": _session_to_dict(session), "run": _serialize_agent_run_for_portal(run)}, status=200)


@csrf_exempt
@require_POST
def portal_agent_request_update(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    request_id_raw = (payload.get("request_id") or payload.get("requestId") or "").strip()
    status_raw = payload.get("status") or payload.get("state") or payload.get("action") or ""
    status_value = str(status_raw).strip().lower().replace("-", "_").replace(" ", "_")
    resolution = str(payload.get("resolution") or payload.get("message") or payload.get("reply") or "").strip()

    if not session_token or not request_id_raw:
        return _json_error("validation_error", "session_token and request_id are required.")

    try:
        request_uuid = uuid.UUID(request_id_raw)
    except (TypeError, ValueError):
        return _json_error("validation_error", "request_id is invalid.")

    status_map = {
        "open": AgentRequestStatus.OPEN,
        "in_progress": AgentRequestStatus.IN_PROGRESS,
        "inprogress": AgentRequestStatus.IN_PROGRESS,
        "start": AgentRequestStatus.IN_PROGRESS,
        "started": AgentRequestStatus.IN_PROGRESS,
        "resolve": AgentRequestStatus.RESOLVED,
        "resolved": AgentRequestStatus.RESOLVED,
    }
    next_status = status_map.get(status_value)
    if not next_status:
        return _json_error("validation_error", "status must be open, in_progress, or resolved.")
    if next_status == AgentRequestStatus.RESOLVED and not resolution:
        return _json_error("validation_error", "resolution is required when resolving a request.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    business_id = getattr(conversation, "business_profile_id", None)
    enabled = False
    try:
        from apps.accounts.feature_flags import FeatureFlagService
        from apps.accounts.models import BusinessProfile

        if business_id:
            with tenant_context(business_id):
                business = BusinessProfile.objects.filter(id=business_id).only("id", "metadata").first()
            enabled = bool(getattr(FeatureFlagService.snapshot(business), "sub_agents_v1", False)) if business else False
    except Exception:  # pragma: no cover - best effort only
        enabled = False

    if not enabled:
        return _json_error("feature_disabled", "Sub-agents are not enabled for this business.", status=403)
    agent_profile_id = getattr(conversation, "agent_profile_id", None)
    actor_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    if actor_user:
        actor_snapshot: dict[str, object] = {"type": "user", "user_id": str(getattr(actor_user, "id", "") or "")}
    else:
        actor_snapshot = {
            "type": "portal_session",
            "session_hash": hashlib.sha256(session_token.encode("utf-8", errors="ignore")).hexdigest()[:16],
        }

    run_payload: dict[str, object] | None = None
    with transaction.atomic():
        with tenant_context(business_id):
            qs = AgentRequest.objects.select_for_update().select_related("from_agent_profile", "to_agent_profile").filter(
                id=request_uuid,
                business_profile_id=business_id,
            )
            if agent_profile_id:
                qs = qs.filter(Q(to_agent_profile_id=agent_profile_id) | Q(from_agent_profile_id=agent_profile_id))
            agent_request = qs.first()
            if agent_request is None:
                return _json_error("not_found", "Request not found.", status=404)

            now = timezone.now()
            update_fields: list[str] = ["status", "updated_at"]
            agent_request.status = next_status
            if next_status == AgentRequestStatus.RESOLVED:
                agent_request.resolution = resolution[:8000]
                agent_request.resolved_at = now
                update_fields.extend(["resolution", "resolved_at"])
            agent_request.save(update_fields=update_fields)

            if next_status == AgentRequestStatus.RESOLVED and agent_request.agent_run_id:
                run = AgentRun.objects.select_for_update().filter(id=agent_request.agent_run_id).first()
                if run and run.status in {AgentRunStatus.WAITING_EXTERNAL, AgentRunStatus.PAUSED}:
                    _append_agent_run_event(
                        run,
                        stream=AgentRunEventStream.EXECUTED,
                        event_type=AgentRunEventType.PROGRESS,
                        label="Agent response received",
                        payload={
                            "agent_request_id": str(agent_request.id),
                            "subject": agent_request.subject,
                        },
                    )
                    AgentRunMemoryItem.objects.create(
                        run=run,
                        kind=AgentRunMemoryKind.NOTE,
                        key="agent_request",
                        content=resolution[:4000],
                        payload={
                            "agent_request_id": str(agent_request.id),
                            "subject": agent_request.subject,
                            "actor": actor_snapshot,
                        },
                        created_by=actor_user,
                    )

                    next_meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
                    next_meta = dict(next_meta)
                    inputs = next_meta.get("external_inputs")
                    if not isinstance(inputs, list):
                        inputs = []
                    inputs.append(
                        {
                            "type": "agent_request",
                            "id": str(agent_request.id),
                            "subject": agent_request.subject,
                            "resolution": resolution[:4000],
                            "at": now.isoformat(),
                        }
                    )
                    next_meta["external_inputs"] = inputs[-10:]
                    next_meta.pop("pending_agent_request_id", None)
                    next_meta.pop("pending_agent_request", None)

                    AgentRun.objects.filter(id=run.id).update(
                        status=AgentRunStatus.QUEUED,
                        run_after=now,
                        lease_expires_at=None,
                        error_detail="",
                        metadata=next_meta,
                        updated_at=now,
                    )
                    run.refresh_from_db()
                    run_payload = _serialize_agent_run_for_portal(run)

    response_payload: dict[str, object] = {
        "session": _session_to_dict(session),
        "request": _serialize_agent_request_for_portal(agent_request),
    }
    if run_payload:
        response_payload["run"] = run_payload
    return JsonResponse(response_payload, status=200)


@csrf_exempt
@require_POST
def portal_tool_history(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    if not session_token:
        return _json_error("validation_error", "session_token is required.")

    raw_limit = payload.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else 100
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 250))

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=True)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    business_id = getattr(conversation, "business_profile_id", None)
    approvals: list[dict[str, object]] = []
    tool_events: list[dict[str, object]] = []

    with tenant_context(business_id):
        approvals_qs = (
            ConversationToolApproval.objects.select_related("connection")
            .filter(conversation=conversation)
            .order_by("-requested_at")[:limit]
        )
        approvals = [
            {
                **_serialize_tool_approval(item),
                "connection_name": getattr(getattr(item, "connection", None), "name", "") or "",
            }
            for item in approvals_qs
        ]

        seen: set[tuple[str, str]] = set()
        remote_by_event_id: dict[str, dict[str, str]] = {}

        for message in getattr(conversation, "messages", ()).all():
            blocks = message.content_blocks if isinstance(getattr(message, "content_blocks", None), list) else []
            if not blocks:
                continue
            message_id_value = str(message.id)
            message_sent_at = message.sent_at.isoformat() if getattr(message, "sent_at", None) else None

            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "").strip().lower()
                payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}

                if block_type == "tool_use":
                    event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
                    phase = str(payload.get("phase") or "").strip().lower() or "started"
                    if not event_id:
                        continue
                    key = (event_id, phase)
                    if key in seen:
                        continue
                    seen.add(key)

                    remote = payload.get("remote") if isinstance(payload.get("remote"), dict) else {}
                    connection_name = str(remote.get("connection_name") or "").strip()
                    remote_tool_name = str(remote.get("remote_tool") or "").strip()
                    if connection_name or remote_tool_name:
                        remote_by_event_id[event_id] = {
                            "connection_name": connection_name,
                            "remote_tool_name": remote_tool_name,
                        }

                    summary: dict[str, object] = {
                        "event_id": event_id,
                        "phase": phase,
                        "status": str(payload.get("status") or "").strip(),
                        "tool_name": str(payload.get("tool_name") or payload.get("toolName") or "").strip(),
                        "connection_name": connection_name,
                        "remote_tool_name": remote_tool_name,
                        "duration_ms": payload.get("duration_ms") if payload.get("duration_ms") is not None else None,
                        "message_id": message_id_value,
                        "message_sent_at": message_sent_at,
                    }
                    tool_events.append(summary)
                    continue

                if block_type == "tool_result":
                    event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
                    if not event_id:
                        continue
                    phase = "finished"
                    key = (event_id, phase)
                    if key in seen:
                        continue
                    seen.add(key)

                    remote_hint = remote_by_event_id.get(event_id) or {}
                    summary = {
                        "event_id": event_id,
                        "phase": phase,
                        "status": str(payload.get("status") or "").strip(),
                        "tool_name": str(payload.get("tool_name") or payload.get("toolName") or "").strip(),
                        "connection_name": remote_hint.get("connection_name", ""),
                        "remote_tool_name": remote_hint.get("remote_tool_name", ""),
                        "duration_ms": payload.get("duration_ms") if payload.get("duration_ms") is not None else None,
                        "message_id": message_id_value,
                        "message_sent_at": message_sent_at,
                    }
                    tool_events.append(summary)

    tool_events.sort(key=lambda item: (item.get("message_sent_at") or "", item.get("event_id") or "", item.get("phase") or ""))
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "history": {
                "approvals": approvals,
                "toolEvents": tool_events[-limit:],
            },
        }
    )


@csrf_exempt
@require_POST
def portal_email_send_draft(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    draft_id = (payload.get("draft_id") or payload.get("draftId") or "").strip()
    email_account_id = (payload.get("email_account_id") or payload.get("emailAccountId") or "").strip()
    if not session_token or not draft_id:
        return _json_error("validation_error", "session_token and draft_id are required.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    if not email_account_id:
        email_account_id = _pending_email_account_id_for_draft(conversation, draft_id=draft_id)

    arguments: dict[str, object] = {"draft_id": draft_id}
    if email_account_id:
        arguments["email_account_id"] = email_account_id

    from apps.mcp.tools import execute_tool
    from apps.mcp.types import ToolExecutionContext

    result = execute_tool(
        "email_send_draft",
        arguments,
        conversation=conversation,
        context=ToolExecutionContext(),
    )

    status_value = str(result.get("status") or "").strip().lower()
    if status_value != "ok":
        hint = str(result.get("hint") or result.get("error") or "Email send failed.").strip()
        return JsonResponse(
            {
                "session": _session_to_dict(session),
                "result": result,
                "error": {"code": "email_send_failed", "message": hint or "Email send failed."},
            },
            status=400,
        )

    _clear_pending_email_draft_meta(conversation, draft_id=draft_id, email_account_id=email_account_id or None)
    return JsonResponse({"session": _session_to_dict(session), "result": result})


@csrf_exempt
@require_POST
def portal_email_discard_draft(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    draft_id = (payload.get("draft_id") or payload.get("draftId") or "").strip()
    email_account_id = (payload.get("email_account_id") or payload.get("emailAccountId") or "").strip()
    if not session_token or not draft_id:
        return _json_error("validation_error", "session_token and draft_id are required.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    if not email_account_id:
        email_account_id = _pending_email_account_id_for_draft(conversation, draft_id=draft_id)

    cleared = _clear_pending_email_draft_meta(conversation, draft_id=draft_id, email_account_id=email_account_id or None)
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "discarded": True,
            "cleared_pending": cleared,
        }
    )


@csrf_exempt
@require_POST
def stream_stop(request: HttpRequest) -> JsonResponse:
    """
    Request cancellation of the currently active streaming turn for a session.

    This is best-effort: if no active stream is registered the call succeeds
    with `cancelled=false`.
    """

    try:
        payload = _parse_json_body(request)
    except PortalValidationError:
        return _json_error("invalid_payload", "Invalid JSON payload")

    session_token = str(payload.get("session_token") or payload.get("sessionToken") or "").strip()
    if not session_token:
        return _json_error("missing_session_token", "session_token is required")

    cancel_event = None
    with _ACTIVE_STREAM_CANCEL_LOCK:
        cancel_event = _ACTIVE_STREAM_CANCEL_EVENTS.get(session_token)

    if cancel_event is not None:
        cancel_event.set()
        return JsonResponse({"ok": True, "cancelled": True})
    return JsonResponse({"ok": True, "cancelled": False})


@csrf_exempt
@require_POST
def stream_send(request: HttpRequest) -> StreamingHttpResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError:
        return StreamingHttpResponse(status=400)

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata = payload.get("metadata") or {}
    debug_tool_trace_enabled = _portal_debug_tool_trace_enabled(request, payload, metadata if isinstance(metadata, Mapping) else {})

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    customer_message: PortalMessage | None = None
    try:
        customer_message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
            conversation=conversation,
        )
    except PortalValidationError:
        return StreamingHttpResponse(status=400)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    agent = conversation.agent_profile
    if not agent:
        return StreamingHttpResponse(status=500)

    use_mcp = _business_prefers_mcp(conversation.business_profile, conversation=conversation)
    trace_logger = PortalTraceLogger(
        conversation=conversation,
        agent=agent,
        session_token=session_token,
        orchestrator_mode="mcp" if use_mcp else "legacy",
    )
    trace_logger.log("request.received", detail=f"body={body}")
    if metadata:
        trace_logger.log("request.metadata", detail=trace_logger.format_data(metadata), indent=1)
    if customer_message:
        trace_logger.log("customer.message_recorded", detail=f"id={customer_message.id}", indent=1)
    def _provider_label(provider_obj: Any) -> str:
        if provider_obj is None:
            return "unknown"
        for attr in ("name", "label", "model_name"):
            value = getattr(provider_obj, attr, None)
            if isinstance(value, str) and value:
                return value
        return provider_obj.__class__.__name__

    if use_mcp:
        from apps.llm.llm_provider import load_mcp_provider
        from apps.mcp.orchestrator import McpOrchestratorService

        provider = load_mcp_provider()
        orchestrator = McpOrchestratorService(agent=agent, provider=provider)
        trace_logger.log(
            "orchestrator.selected",
            detail=f"mode=mcp provider={_provider_label(provider)}",
            indent=1,
        )
    else:
        provider = load_default_provider()
        orchestrator = AiOrchestratorService(agent=agent, provider=provider)
        trace_logger.log(
            "orchestrator.selected",
            detail=f"mode=legacy provider={_provider_label(provider)}",
            indent=1,
        )
    dispatcher = ActionDispatcher(agent=agent)

    def serialize_action_results(results):
        payloads = []
        for result in results:
            payloads.append(
                {
                    "action": result.action.value,
                    "status": result.status,
                    "metadata": result.metadata,
                    "error": result.error,
                }
            )
        return payloads

    def serialize_planned_actions(planned):
        payloads = []
        for action in planned:
            payloads.append(
                {
                    "action": action.action.value,
                    "status": "queued",
                    "metadata": action.payload,
                    "error": None,
                }
            )
        return payloads

    stream_queue: Queue = Queue()
    stream_sentinel = object()
    finalize_queue: Queue = Queue()
    finalize_sentinel = object()
    actions_queue: Queue = Queue()
    actions_sentinel = object()
    stream_complete = threading.Event()
    stream_stopped = threading.Event()
    cancel_requested = threading.Event()
    plan_holder: dict[str, Any] = {}
    state_machine_enabled = getattr(settings, "PORTAL_STREAM_STATE_MACHINE", False)
    plan_holder["metadata_version"] = 1
    plan_holder["session_status"] = conversation.status
    plan_holder["spinner_text"] = None
    reserved_message_id = uuid.uuid4()
    plan_holder["pending_message_id"] = reserved_message_id

    # Register cancel handle so a parallel HTTP request can stop this turn.
    with _ACTIVE_STREAM_CANCEL_LOCK:
        _ACTIVE_STREAM_CANCEL_EVENTS[session_token] = cancel_requested

    def _unregister_cancel_handle() -> None:
        with _ACTIVE_STREAM_CANCEL_LOCK:
            current = _ACTIVE_STREAM_CANCEL_EVENTS.get(session_token)
            if current is cancel_requested:
                _ACTIVE_STREAM_CANCEL_EVENTS.pop(session_token, None)

    blocks_lock = threading.Lock()
    content_blocks: list[dict[str, object]] = []
    content_blocks_by_id: dict[str, dict[str, object]] = {}
    rich_builder = RichBlockStreamBuilder()
    block_ops_active = False
    tool_use_block_id_by_event_id: dict[str, str] = {}
    reasoning_block_id_by_call_id: dict[str, str] = {}
    spinner_state = {
        "text": None,
        "pending": True,
        "tool_inflight": 0,
    }
    spinner_phase_state = {
        "searching": {"active": False, "index": 0, "label": None, "meta": None, "timer": None},
        "reading": {"active": False, "index": 0, "label": None, "meta": None, "timer": None},
    }
    spinner_phase_interval_setting = getattr(settings, "PORTAL_SPINNER_PHASE_INTERVAL", 5.5)
    try:
        spinner_phase_interval = float(spinner_phase_interval_setting)
    except (TypeError, ValueError):
        spinner_phase_interval = 5.5
    if spinner_phase_interval < 0:
        spinner_phase_interval = 0.0

    def _search_base_text(label: str | None, meta: dict | None) -> str:
        return label or "Searching knowledge…"

    def _search_variant_text(label: str | None, meta: dict | None) -> str:
        query_text = ""
        if isinstance(meta, dict):
            raw_query = meta.get("query")
            if isinstance(raw_query, str):
                query_text = raw_query.strip()
        if query_text:
            return f"Exploring deeper insights for {query_text[:60]}…"
        return "Exploring deeper insights…"

    def _reading_base_text(label: str | None, meta: dict | None) -> str:
        return label or "Reading knowledge…"

    def _reading_variant_text(label: str | None, meta: dict | None) -> str:
        doc_hint = ""
        if isinstance(meta, dict):
            doc_hint = str(meta.get("document_id") or "").strip()
        if not doc_hint and isinstance(label, str):
            parts = label.split(":", 1)
            if len(parts) == 2:
                doc_hint = parts[1].strip()
        hint_suffix = f" ({doc_hint[:40]})" if doc_hint else ""
        return f"Gathering more context…{hint_suffix}"

    SPINNER_PHASE_VARIANTS: dict[str, tuple[Callable[[str | None, dict | None], str], ...]] = {
        "searching": (_search_base_text, _search_variant_text),
        "reading": (_reading_base_text, _reading_variant_text),
    }

    def _current_message_id() -> str:
        raw_id = plan_holder.get("ai_message_id") or plan_holder.get("pending_message_id")
        if raw_id:
            return str(raw_id)
        return ""

    def _current_session_status() -> str | None:
        status = plan_holder.get("session_status")
        if status:
            return status
        return conversation.status

    def _next_metadata_version() -> int:
        version = int(plan_holder.get("metadata_version") or 1) + 1
        plan_holder["metadata_version"] = version
        return version

    def _emit_spinner_status(
        raw_text: str | None,
        *,
        pending: bool = True,
        fallback: str | None = "Working...",
        allow_empty: bool = False,
        reason: str | None = None,
        force: bool = False,
    ) -> None:
        if not state_machine_enabled and not force:
            return
        # Do not trim spinner labels (keep full text and let the UI wrap naturally).
        text_value = sanitize_placeholder_thinking(raw_text, fallback=fallback, limit=0)
        if text_value:
            lowered = text_value.strip().lower()
            if lowered.startswith("drafting") or lowered.startswith("responding"):
                text_value = ""
        if text_value is None and allow_empty:
            text_value = ""
        if text_value is None:
            return
        if spinner_state["text"] == text_value and spinner_state["pending"] == pending:
            return
        prev_text = spinner_state.get("text")
        spinner_state["text"] = text_value
        spinner_state["pending"] = pending
        plan_holder["spinner_text"] = text_value or None
        try:
            trace_logger.log_spinner(
                text_value,
                pending=pending,
                prev_text=str(prev_text) if prev_text is not None else None,
                reason=reason,
            )
        except Exception:  # pragma: no cover - logging must never break streaming
            logger.exception("portal spinner trace log failed")
        payload = {
            "type": "spinnerStatus",
            "message_id": _current_message_id(),
            "spinner_text": text_value,
            "pending": pending,
        }
        stream_queue.put(payload)

    def _phase_variant_text(phase: str, index: int, label: str | None, meta: dict | None) -> str:
        variants = SPINNER_PHASE_VARIANTS.get(phase)
        if not variants:
            return label or "Working..."
        variant_fn = variants[index % len(variants)]
        try:
            return variant_fn(label, meta)
        except Exception:
            return label or "Working..."

    def _cancel_phase_timer(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state:
            return
        timer = state.get("timer")
        if timer:
            timer.cancel()
            state["timer"] = None

    def _advance_phase_spinner(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state or not state.get("active"):
            return
        variants = SPINNER_PHASE_VARIANTS.get(phase)
        if not variants:
            return
        if state["index"] + 1 >= len(variants):
            state["active"] = False
            return
        state["index"] += 1
        text = _phase_variant_text(phase, state["index"], state.get("label"), state.get("meta"))
        _emit_spinner_status(text)
        _schedule_phase_rotation(phase)

    def _schedule_phase_rotation(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state or not state.get("active"):
            return
        variants = SPINNER_PHASE_VARIANTS.get(phase)
        if not variants or len(variants) <= 1 or spinner_phase_interval <= 0:
            return
        if state["index"] >= len(variants) - 1:
            return
        _cancel_phase_timer(phase)
        timer = threading.Timer(spinner_phase_interval, lambda: _advance_phase_spinner(phase))
        timer.daemon = True
        state["timer"] = timer
        timer.start()

    def _set_phase_spinner(phase: str, label: str | None, meta: dict | None, *, reset_index: bool) -> None:
        state = spinner_phase_state.setdefault(
            phase,
            {"active": False, "index": 0, "label": None, "meta": None, "timer": None},
        )
        if reset_index or not state["active"]:
            state["index"] = 0
        state["active"] = True
        state["label"] = label or state.get("label")
        if isinstance(meta, dict) and meta:
            state["meta"] = meta
        elif not state.get("meta"):
            state["meta"] = {}
        text = _phase_variant_text(phase, state["index"], state.get("label"), state.get("meta"))
        _emit_spinner_status(text)
        _schedule_phase_rotation(phase)

    def _reset_phase_spinner(phase: str) -> None:
        state = spinner_phase_state.get(phase)
        if not state:
            return
        _cancel_phase_timer(phase)
        state["active"] = False
        state["index"] = 0
        state["label"] = None
        state["meta"] = None

    def _progressive_spinner_update(code: str | None, label: str | None, meta: dict | None) -> bool:
        if not state_machine_enabled:
            return False
        phase_map = {
            "searching_start": ("searching", True),
            "searching_knowledge": ("searching", False),
            "reading_start": ("reading", True),
            "reading_document": ("reading", False),
        }
        reset_map = {
            "searching_complete": "searching",
            "reading_complete": "reading",
        }
        phase_entry = phase_map.get(code or "")
        if phase_entry:
            phase, reset_index = phase_entry
            _set_phase_spinner(phase, label, meta, reset_index=reset_index)
            return True
        reset_phase = reset_map.get(code or "")
        if reset_phase:
            _reset_phase_spinner(reset_phase)
        return False

    def _append_content_block(block: dict[str, object]) -> dict[str, object]:
        block_id = str(block.get("block_id") or "").strip()
        if not block_id:
            block_id = new_block_id()
            block["block_id"] = block_id
        with blocks_lock:
            content_blocks.append(block)
            content_blocks_by_id[block_id] = block
        return block

    def _get_content_block(block_id: str) -> dict[str, object] | None:
        key = (block_id or "").strip()
        if not key:
            return None
        with blocks_lock:
            return content_blocks_by_id.get(key)

    def _emit_block_events(events: list[dict[str, object]]) -> None:
        if not events:
            return
        message_id = _current_message_id()
        for event in events:
            if not event or not isinstance(event, Mapping):
                continue
            event_type = event.get("type")
            payload = dict(event.get("payload") or {})
            if event_type in {"block_start", "block_delta", "block_end"}:
                _apply_block_event({"type": event_type, "payload": payload})
            payload["message_id"] = message_id
            stream_queue.put({"type": event_type, "payload": payload})

    def _persist_inflight_message_snapshot(*, approval_id: str | None = None) -> None:
        """
        Persist the in-flight assistant message while waiting for tool approval.

        Why:
        - The portal streams assistant blocks over a POST SSE stream, but the DB message is
          normally persisted only after the turn finalizes.
        - Tool approvals can keep the stream idle long enough for clients/proxies to drop it.
        - If the visitor refreshes mid-approval, the transcript is reconstructed from DB
          messages only, causing the streamed assistant text to disappear.

        This helper upserts the assistant message (using the reserved message id) with the
        current block snapshot so refresh hydration stays consistent.
        """

        message_id = plan_holder.get("pending_message_id")
        if not message_id:
            return
        with blocks_lock:
            blocks_snapshot = copy.deepcopy(content_blocks)
        if not blocks_snapshot:
            return
        blocks_snapshot = _normalize_portal_content_blocks(blocks_snapshot)

        body_text = extract_text_from_content_blocks(blocks_snapshot)
        if not body_text:
            body_text = "Approval required."

        existing_metadata = plan_holder.get("message_metadata") if isinstance(plan_holder.get("message_metadata"), dict) else {}
        message_metadata = dict(existing_metadata or {})
        message_metadata["portal_turn_state"] = "waiting_approval"
        if approval_id:
            message_metadata["pending_approval_id"] = approval_id

        try:
            service.append_message(
                session_token=session_token,
                sender=ConversationSender.AI,
                body=body_text,
                metadata=message_metadata,
                content_blocks=blocks_snapshot,
                conversation=conversation,
                message_id=message_id,
            )
        except IntegrityError:
            try:
                service.update_message(
                    session_token=session_token,
                    message_id=message_id,
                    body=body_text,
                    metadata=message_metadata,
                    content_blocks=blocks_snapshot,
                    conversation=conversation,
                )
            except Exception:  # pragma: no cover - best effort only
                logger.exception("portal inflight message update failed")
        except Exception:  # pragma: no cover - best effort only
            logger.exception("portal inflight message persist failed")

    def on_reasoning_event(event: Mapping[str, object] | None) -> None:
        if not event or not isinstance(event, Mapping):
            return
        event_type = str(event.get("type") or "").strip().lower()
        if event_type not in {"reasoning_delta", "reasoning_end"}:
            return
        call_id = str(event.get("call_id") or "").strip()
        if not call_id:
            return
        stage = str(event.get("stage") or "").strip() or "llm"
        label = str(event.get("label") or "").strip() or stage.replace("_", " ").strip() or "LLM"
        block_id = reasoning_block_id_by_call_id.get(call_id)

        if event_type == "reasoning_delta":
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta:
                return
            if not block_id:
                block = {
                    "block_id": new_block_id(),
                    "type": "reasoning",
                    "created_at": timezone.now().isoformat(),
                    "payload": {
                        "title": label,
                        "stage": stage,
                        "collapsed": False,
                        "code": "",
                    },
                }
                _append_content_block(block)
                block_id = str(block.get("block_id") or "").strip()
                if not block_id:
                    return
                reasoning_block_id_by_call_id[call_id] = block_id
                stream_queue.put(
                    {
                        "type": "block_start",
                        "payload": {
                            "message_id": _current_message_id(),
                            "block": copy.deepcopy(block),
                        },
                    }
                )
            block = _get_content_block(block_id)
            if not block:
                return
            ops = [{"op": "append_code", "text": delta}]
            with blocks_lock:
                apply_block_ops(block, ops)
            stream_queue.put(
                {
                    "type": "block_delta",
                    "payload": {
                        "message_id": _current_message_id(),
                        "block_id": block_id,
                        "ops": ops,
                    },
                }
            )
            return

        if event_type == "reasoning_end":
            if not block_id:
                return
            block = _get_content_block(block_id)
            if block:
                payload_raw = block.get("payload")
                payload = payload_raw if isinstance(payload_raw, dict) else {}
                payload["collapsed"] = True
                payload["completed_at"] = timezone.now().isoformat()
                block["payload"] = payload
            stream_queue.put(
                {
                    "type": "block_end",
                    "payload": {
                        "message_id": _current_message_id(),
                        "block_id": block_id,
                    },
                }
            )
            return

    def _apply_block_event(event: Mapping[str, object]) -> bool:
        event_type = str(event.get("type") or "").strip()
        payload = event.get("payload") or {}
        if event_type == "block_start":
            block = payload.get("block")
            if not isinstance(block, Mapping):
                return False
            block_id = str(block.get("block_id") or "").strip()
            if not block_id:
                return False
            with blocks_lock:
                existing = content_blocks_by_id.get(block_id)
                if existing is not None:
                    existing.clear()
                    existing.update(block)
                else:
                    content_blocks.append(dict(block))
                    content_blocks_by_id[block_id] = content_blocks[-1]
            return True
        if event_type == "block_delta":
            block_id = str(payload.get("block_id") or "").strip()
            if not block_id:
                return False
            ops = payload.get("ops")
            if not isinstance(ops, list):
                return False
            with blocks_lock:
                block = content_blocks_by_id.get(block_id)
                if not block:
                    return False
                apply_block_ops(block, ops)
            return True
        if event_type == "block_end":
            block_id = str(payload.get("block_id") or "").strip()
            return bool(block_id)
        return False

    def on_block_event(event: Mapping[str, object] | None) -> None:
        nonlocal block_ops_active
        if not event:
            return
        normalized = coerce_block_event(event)
        if not normalized:
            return
        if not block_ops_active:
            block_ops_active = True
        _emit_block_events([normalized])

    def on_response_text_delta(chunk: str) -> None:
        if not chunk:
            return
        if block_ops_active:
            return
        events = rich_builder.feed_text(chunk)
        _emit_block_events(events)

    def on_status_change(state) -> None:
        if not state:
            return
        code: str | None = None
        label: str | None = None
        meta: dict | None = None
        if isinstance(state, str):
            code = state.strip()
        elif isinstance(state, dict):
            raw_code = state.get("code") or state.get("state")
            if isinstance(raw_code, str):
                code = raw_code.strip()
            raw_label = state.get("label")
            if isinstance(raw_label, str):
                label = raw_label.strip()
            raw_meta = state.get("meta")
            if isinstance(raw_meta, dict):
                meta = raw_meta
        if not code:
            return
        trace_logger.log_status(code, label=label, meta=meta)
        # Ensure any buffered tail text is flushed before the UI sees `stream_complete`.
        # Otherwise the final paragraph/list item (often missing a trailing newline) can
        # appear *after* the stream_complete status and spinner shutdown.
        if code == "stream_complete" and not block_ops_active:
            _emit_block_events(rich_builder.finalize())
        _enqueue_status_events(stream_queue, code=code, label=label, meta=meta)
        if state_machine_enabled:
            if int(spinner_state.get("tool_inflight") or 0) > 0 and code not in {"answer_started", "stream_complete", "complete"}:
                return
            if _progressive_spinner_update(code, label, meta):
                return
            if code == "thinking":
                _emit_spinner_status(
                    "",
                    pending=True,
                    fallback=None,
                    allow_empty=True,
                    reason="status:thinking",
                )
                return
            if code in {"searching_complete", "reading_complete"}:
                # Keep the last spinner label until the next concrete step replaces it.
                return
            if code in {"stream_complete", "complete"}:
                _emit_spinner_status("", pending=False, fallback=None, allow_empty=True, reason=f"status:{code}")

    def on_tool_event(event: Mapping[str, object] | None) -> None:
        """
        Stream external tool lifecycle events to the portal UI.

        Payloads must be JSON-safe and redacted; never emit secrets.
        """
        if not event or not isinstance(event, Mapping):
            return
        try:
            phase = str(event.get("phase") or "").strip().lower()
            if phase not in TOOL_EVENT_PHASES:
                return
            tool_name = str(event.get("tool_name") or "").strip()
            kind = str(event.get("kind") or "").strip() or "tool"
            status_value = str(event.get("status") or "").strip()
            if not status_value:
                if phase == "started":
                    status_value = "running"
                elif phase == "approval_requested":
                    status_value = "pending_approval"
            event_id_value = str(event.get("event_id") or "").strip()
            tool_call_id_value = str(event.get("tool_call_id") or "").strip()
            candidate_keys: list[str] = []
            if tool_call_id_value:
                candidate_keys.append(tool_call_id_value)
            if event_id_value and event_id_value not in candidate_keys:
                candidate_keys.append(event_id_value)
            if not candidate_keys:
                return
            block_key = candidate_keys[0]
            deferred_spinner_label: str | None = None
            deferred_spinner_reason: str | None = None
            deferred_bridge_thinking = False

            is_tool_discovery = tool_name.strip().lower() == "mcp_search_tools"
            # Ensure tool discovery spinner renders even if the assistant was mid-streaming
            # a text block (close the block first so the frontend doesn't hide the spinner).
            if is_tool_discovery and phase in {"started", "approval_requested"}:
                if not block_ops_active:
                    _emit_block_events(rich_builder.break_flow())

            if state_machine_enabled or is_tool_discovery:
                phase_lower = phase
                status_lower = status_value.lower()
                defer_spinner_update = phase_lower in {"finished", "approval_resolved"}
                if phase in {"started", "approval_requested"}:
                    spinner_state["tool_inflight"] = int(spinner_state.get("tool_inflight") or 0) + 1
                elif phase in {"finished", "approval_resolved"}:
                    previous_inflight = int(spinner_state.get("tool_inflight") or 0)
                    spinner_state["tool_inflight"] = max(0, previous_inflight - 1)

                spinner_label: str | None = None
                if phase_lower == "approval_requested" or status_lower in {"pending_approval", "pending"}:
                    # Approval cards render their own CTAs; extra "waiting" spinners are redundant/noisy.
                    spinner_label = None
                elif phase_lower == "started":
                    remote_meta = event.get("remote") if isinstance(event.get("remote"), Mapping) else None
                    if remote_meta:
                        raw_connection_name = str(remote_meta.get("connection_name") or remote_meta.get("connectionName") or "").strip()
                        connection_name = re.sub(r"\s*\(mcp\)\s*$", "", raw_connection_name, flags=re.IGNORECASE).strip()
                        raw_remote_tool = str(
                            remote_meta.get("remote_tool")
                            or remote_meta.get("remoteTool")
                            or remote_meta.get("tool")
                            or remote_meta.get("tool_name")
                            or remote_meta.get("toolName")
                            or ""
                        ).strip()
                        remote_tool_key = raw_remote_tool.lower().strip()

                        # Keep the spinner high-level and non-redundant with the tool chip.
                        # Tool chip shows the exact tool name; spinner should explain the general action.
                        if connection_name:
                            if remote_tool_key in {"get_me", "whoami"}:
                                spinner_label = f"Checking {connection_name}…"
                            elif remote_tool_key.startswith(("search_", "find_", "query_")) or "search" in remote_tool_key:
                                spinner_label = f"Searching {connection_name}…"
                            elif remote_tool_key.startswith(("list_", "get_", "read_", "fetch_", "retrieve_")):
                                spinner_label = f"Fetching from {connection_name}…"
                            elif remote_tool_key.startswith(
                                (
                                    "create_",
                                    "update_",
                                    "delete_",
                                    "add_",
                                    "remove_",
                                    "set_",
                                    "fork_",
                                    "merge_",
                                    "close_",
                                    "open_",
                                )
                            ):
                                spinner_label = f"Updating {connection_name}…"
                            else:
                                spinner_label = f"Working with {connection_name}…"
                    elif tool_name == "search_knowledge":
                        spinner_label = "Searching knowledge…"
                    elif tool_name == "read_document":
                        spinner_label = "Reading knowledge…"
                    elif tool_name == "mcp_search_tools":
                        spinner_label = "Searching tools…"
                    # Email operations
                    elif tool_name == "email_search":
                        spinner_label = "Searching emails…"
                    elif tool_name == "email_get_message":
                        spinner_label = "Retrieving email…"
                    elif tool_name == "email_get_thread":
                        spinner_label = "Retrieving email thread…"
                    elif tool_name == "email_create_draft":
                        spinner_label = "Creating email draft…"
                    elif tool_name == "email_send_draft":
                        spinner_label = "Sending email…"
                    # PDF/Document operations
                    elif tool_name == "pdf_generate":
                        spinner_label = "Generating PDF…"
                    elif tool_name == "pdf_merge":
                        spinner_label = "Merging PDFs…"
                    elif tool_name == "pdf_extract_pages":
                        spinner_label = "Extracting PDF pages…"
                    elif tool_name == "pdf_extract_text":
                        spinner_label = "Extracting text from PDF…"
                    else:
                        spinner_label = "Working…"
                elif phase_lower == "finished":
                    if status_lower in {"error", "failed", "tool_failed", "mcp_remote_error", "constraint_error"}:
                        spinner_label = "Trying another approach…"

                inflight_now = int(spinner_state.get("tool_inflight") or 0)
                terminal_error_statuses = {"error", "failed", "tool_failed", "mcp_remote_error", "constraint_error"}
                deferred_bridge_thinking = (
                    not stream_complete.is_set()
                    and inflight_now == 0
                    and not spinner_label
                    and (
                        (phase_lower == "finished" and status_lower not in terminal_error_statuses)
                        or (phase_lower == "approval_resolved" and status_lower not in {"approved"})
                    )
                )

                # Tool discovery is an internal gateway step; render it as spinner only (no tool block).
                # Emit spinner updates immediately because there is no follow-on block event.
                if spinner_label and (not defer_spinner_update or is_tool_discovery):
                    _emit_spinner_status(
                        spinner_label,
                        pending=True,
                        fallback="Working...",
                        reason=f"tool:{tool_name}:{phase_lower}:{status_lower}",
                        force=is_tool_discovery,
                    )
                elif defer_spinner_update:
                    deferred_spinner_label = spinner_label
                    deferred_spinner_reason = f"tool:{tool_name}:{phase_lower}:{status_lower}"

            # Tool discovery is an internal gateway step; render it as spinner only (no tool block).
            if is_tool_discovery:
                return

            payload: dict[str, object] = {
                "event_id": event_id_value or block_key,
                "phase": phase,
                "status": status_value,
                "tool_call_id": tool_call_id_value,
                "kind": kind,
                "tool_name": tool_name,
            }
            remote = event.get("remote") if isinstance(event.get("remote"), Mapping) else None
            if not remote:
                output_hint = event.get("output") if isinstance(event.get("output"), Mapping) else None
                remote_hint = output_hint.get("remote") if isinstance(output_hint, Mapping) else None
                if isinstance(remote_hint, Mapping):
                    remote = remote_hint
            if remote:
                # Never leak internal connection IDs/URLs to public portal visitors.
                safe_remote: dict[str, object] = {}
                connection_name = remote.get("connection_name") or remote.get("connectionName")
                remote_tool = (
                    remote.get("remote_tool")
                    or remote.get("remoteTool")
                    or remote.get("tool")
                    or remote.get("tool_name")
                    or remote.get("toolName")
                )
                if connection_name:
                    safe_remote["connection_name"] = _clip_debug_text(connection_name, limit=120)
                if remote_tool:
                    safe_remote["remote_tool"] = _clip_debug_text(remote_tool, limit=120)
                if safe_remote:
                    payload["remote"] = safe_remote
            approval_payload = event.get("approval") if isinstance(event.get("approval"), Mapping) else None
            approval_id = event.get("approval_id") or event.get("approvalId")
            if approval_payload:
                payload["approval"] = _json_safe_debug(approval_payload, depth=4, string_limit=480, list_limit=24)
                if not approval_id:
                    approval_id = approval_payload.get("id")
            if approval_id:
                payload["approval_id"] = str(approval_id)

            input_payload = event.get("input")
            if input_payload is not None and phase in {"started", "approval_requested", "finished", "approval_resolved"}:
                input_string_limit = 720
                input_list_limit = 32
                if tool_name.strip().lower() == "email_create_draft":
                    # Email drafts are user-facing; allow the portal to render the full draft body
                    # (still bounded by tool-side truncation at 12k chars).
                    input_string_limit = 12_000
                    input_list_limit = 96
                payload["input"] = _json_safe_debug(
                    input_payload,
                    depth=3,
                    string_limit=input_string_limit,
                    list_limit=input_list_limit,
                )

            tool_use_block_id: str | None = None
            for key in candidate_keys:
                tool_use_block_id = tool_use_block_id_by_event_id.get(key)
                if tool_use_block_id:
                    break
            tool_use_block = _get_content_block(tool_use_block_id) if tool_use_block_id else None
            if not tool_use_block:
                # If the assistant already started streaming text, close the active text block so the
                # new tool block is inserted in-order without offset-based reconstruction hacks.
                if not block_ops_active:
                    _emit_block_events(rich_builder.break_flow())
                tool_use_block = {
                    "block_id": new_block_id(),
                    "type": "tool_use",
                    "created_at": timezone.now().isoformat(),
                    "payload": {},
                }
                _append_content_block(tool_use_block)
                tool_use_block_id = str(tool_use_block.get("block_id") or "").strip()
                if tool_use_block_id:
                    for key in candidate_keys:
                        tool_use_block_id_by_event_id[key] = tool_use_block_id
            if tool_use_block_id:
                for key in candidate_keys:
                    tool_use_block_id_by_event_id[key] = tool_use_block_id
            # Merge updates into the existing tool payload so subsequent events that omit fields
            # (e.g. internal "finished" events without approval metadata) don't erase previously
            # captured context needed for UI hydration after a refresh.
            existing_payload = tool_use_block.get("payload")
            merged_payload: dict[str, object] = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
            merged_payload.update(payload)
            tool_use_block["payload"] = merged_payload
            if phase in {"finished", "approval_resolved"}:
                duration = event.get("duration_ms")
                try:
                    payload["duration_ms"] = int(duration) if duration is not None else 0
                except (TypeError, ValueError):
                    payload["duration_ms"] = 0
                output_payload = event.get("output")
                artifact_id: str | None = None
                output_preview: object | None = None
                if output_payload is not None:
                    scrubbed_output: object = output_payload
                    if isinstance(output_payload, Mapping):
                        # Never leak internal connection IDs/URLs to public portal visitors.
                        output_copy: dict[str, object] = dict(output_payload)
                        remote_out = output_copy.get("remote")
                        if isinstance(remote_out, Mapping):
                            safe_out_remote: dict[str, object] = {}
                            connection_name = remote_out.get("connection_name")
                            remote_tool = remote_out.get("tool") or remote_out.get("remote_tool")
                            if connection_name:
                                safe_out_remote["connection_name"] = _clip_debug_text(connection_name, limit=120)
                            if remote_tool:
                                safe_out_remote["remote_tool"] = _clip_debug_text(remote_tool, limit=120)
                            if safe_out_remote:
                                output_copy["remote"] = safe_out_remote
                            else:
                                output_copy.pop("remote", None)
                        scrubbed_output = output_copy

                    output_preview = _json_safe_debug(scrubbed_output, depth=3, string_limit=720, list_limit=24)
                    kind_lower = kind.lower().strip()
                    if isinstance(output_payload, Mapping):
                        artifact_raw = output_payload.get("artifact_id") or output_payload.get("artifactId")
                        if isinstance(artifact_raw, str) and artifact_raw.strip():
                            artifact_id = artifact_raw.strip()
                        prompt_view = output_payload.get("prompt_view") or output_payload.get("promptView")
                        if prompt_view is not None:
                            output_preview = _json_safe_debug(prompt_view, depth=3, string_limit=720, list_limit=24)

                    if artifact_id is None and kind_lower.startswith("mcp"):
                        try:
                            output_artifact = _json_safe_debug(scrubbed_output, depth=6, string_limit=4800, list_limit=96)
                            with tenant_context(getattr(conversation, "business_profile_id", None)):
                                artifact_id = store_remote_tool_output_artifact(
                                    conversation=conversation,
                                    tool_call_id=tool_call_id_value,
                                    tool_event_id=event_id_value or block_key,
                                    invoked_tool=tool_name,
                                    remote_event_payload=payload,
                                    tool_result=output_artifact
                                    if isinstance(output_artifact, Mapping)
                                    else {"output": output_artifact},
                                )
                        except Exception:  # pragma: no cover - best effort only
                            artifact_id = None

                if artifact_id:
                    payload["artifact_id"] = artifact_id
                if output_preview is not None:
                    payload["output_preview"] = output_preview

                existing_payload = tool_use_block.get("payload")
                merged_payload = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
                merged_payload.update(payload)
                tool_use_block["payload"] = merged_payload

                stream_queue.put(
                    {
                        "type": "block_tool_result",
                        "payload": {
                            "message_id": _current_message_id(),
                            "block": copy.deepcopy(tool_use_block),
                        },
                    }
                )

                # File-oriented internal tools should render user-facing attachment blocks
                # (download buttons, extracted text, etc.) separately from tool cards.
                try:
                    if isinstance(output_payload, Mapping) and str(payload.get("status") or "").strip().lower() in {"ok", "success"}:
                        created_blocks: list[dict[str, object]] = []
                        tool_lower = tool_name.strip().lower()

                        if tool_lower in {"pdf_generate", "pdf_merge", "pdf_extract_pages"}:
                            artifact = output_payload.get("artifact")
                            if isinstance(artifact, Mapping):
                                file_id_raw = artifact.get("file_id") or artifact.get("fileId") or artifact.get("id")
                                filename = str(artifact.get("filename") or "").strip()
                                try:
                                    file_uuid = uuid.UUID(str(file_id_raw))
                                except (TypeError, ValueError):
                                    file_uuid = None
                                if file_uuid:
                                    from apps.conversations.models import ConversationFile
                                    from apps.conversations.portal_files import portal_file_block

                                    with tenant_context(getattr(conversation, "business_profile_id", None)):
                                        file_obj = ConversationFile.objects.filter(
                                            id=file_uuid, conversation=conversation
                                        ).first()
                                    if file_obj is not None:
                                        label_map = {
                                            "pdf_generate": "Generated",
                                            "pdf_merge": "Merged",
                                            "pdf_extract_pages": "Extracted pages",
                                        }
                                        created_blocks.append(portal_file_block(file_obj, label=label_map.get(tool_lower, "Generated")))
                                    else:
                                        # Fallback if the artifact record isn't readable (should be rare).
                                        created_blocks.append(
                                            {
                                                "block_id": new_block_id(),
                                                "type": "file",
                                                "created_at": timezone.now().isoformat(),
                                                "payload": {
                                                    "file_id": str(file_uuid),
                                                    "filename": filename or "document.pdf",
                                                    "content_type": "application/pdf",
                                                    "size_bytes": 0,
                                                    "page_count": 0,
                                                    "kind": "artifact",
                                                    "status": "ready",
                                                    "label": "Generated",
                                                },
                                            }
                                        )

                        elif tool_lower == "pdf_extract_text":
                            file_meta = output_payload.get("file")
                            text_value = output_payload.get("text")
                            if isinstance(file_meta, Mapping) and isinstance(text_value, str) and text_value.strip():
                                file_id_raw = file_meta.get("id") or file_meta.get("file_id") or file_meta.get("fileId")
                                filename = str(file_meta.get("filename") or "").strip() or "document.pdf"
                                try:
                                    file_uuid = uuid.UUID(str(file_id_raw))
                                except (TypeError, ValueError):
                                    file_uuid = None
                                if file_uuid:
                                    from apps.conversations.portal_files import portal_file_text_block

                                    created_blocks.append(
                                        portal_file_text_block(
                                            file_id=file_uuid,
                                            filename=filename,
                                            page_count=int(file_meta.get("page_count") or 0),
                                            text=text_value.strip(),
                                            title=f"Extracted text from {filename}",
                                            collapsed=True,
                                        )
                                    )

                        for block in created_blocks:
                            _append_content_block(block)
                            stream_queue.put(
                                {
                                    "type": "block_start",
                                    "payload": {
                                        "message_id": _current_message_id(),
                                        "block": copy.deepcopy(block),
                                    },
                                }
                            )
                except Exception:  # pragma: no cover - best effort only
                    logger.exception("portal file block creation failed for tool=%s", tool_name)

                if state_machine_enabled and not stream_complete.is_set():
                    if deferred_spinner_label:
                        _emit_spinner_status(
                            deferred_spinner_label,
                            pending=True,
                            fallback="Working...",
                            reason=deferred_spinner_reason,
                        )
                    elif deferred_bridge_thinking and inflight_now == 0:
                        _emit_spinner_status(
                            "",
                            pending=True,
                            fallback=None,
                            allow_empty=True,
                            reason=f"tool:{tool_name}:{phase}:bridge_thinking",
                        )
            else:
                stream_queue.put(
                    {
                        "type": "block_tool_use",
                        "payload": {
                            "message_id": _current_message_id(),
                            "block": copy.deepcopy(tool_use_block),
                        },
                    }
                )
                if phase == "approval_requested":
                    _persist_inflight_message_snapshot(approval_id=str(approval_id).strip() if approval_id else None)
        except Exception:  # pragma: no cover - defensive
            logger.exception("portal tool event serialization failed")

    def signal_stream_complete() -> None:
        if stream_complete.is_set():
            return
        if not block_ops_active:
            _emit_block_events(rich_builder.finalize())
        stream_complete.set()
        trace_logger.log("stream.completed", indent=1)
        stream_queue.put({"type": "status", "state": "complete", "label": ""})
        logger.debug("Stream completion signaled for conversation %s", conversation.id)

    def signal_stream_stop() -> None:
        if stream_stopped.is_set():
            return
        stream_stopped.set()
        stream_queue.put(stream_sentinel)

    def on_placeholder_response(text: str) -> None:
        # Placeholder thinking is no longer surfaced via the spinner.
        return

    def on_spinner_update(text: str) -> None:
        # Portal spinner text is provided by the LLM via tool-call UI hints.
        if int(spinner_state.get("tool_inflight") or 0) > 0:
            return
        _emit_spinner_status(text, pending=True, fallback=None)

    def run_planner_async(
        stream_context: StreamingTurnContext,
        persisted_text: str,
        ai_message_id: uuid.UUID | None,
        parent_ctx,
    ) -> None:
        close_old_connections()
        token = None
        if parent_ctx is not None:
            token = otel_context.attach(parent_ctx)
        try:
            should_run_planner, skip_reason = _planner_decision(
                conversation=conversation,
                user_message=body,
                stream_context=stream_context,
            )
            if not should_run_planner:
                trace_logger.log(
                    "planner.skipped",
                    indent=1,
                    extra={"reason": skip_reason or "auto"},
                )
                plan_holder["planner_skipped"] = True
                return
            tool_context = getattr(stream_context, "tool_context", None)
            plan = orchestrator.run_planner_only(
                conversation=conversation,
                user_message=body,
                answer_text=persisted_text,
                tool_context=tool_context,
            )
            if plan is None:
                trace_logger.log("planner.skipped", indent=1)
                return
            plan_holder["plan"] = plan
            trace_logger.log(
                "planner.completed",
                detail=f"planned_actions={len(plan.planned_actions)} extractions={len(plan.extractions)}",
                indent=1,
            )
            trace_logger.log(
                "plan.ready",
                detail=f"actions={len(plan.planned_actions)} extractions={len(plan.extractions)}",
                indent=1,
            )

            pending_actions = serialize_planned_actions(plan.planned_actions)
            existing_metadata = plan_holder.get("message_metadata") if isinstance(plan_holder.get("message_metadata"), dict) else {}
            message_metadata = dict(existing_metadata or {})
            message_metadata.update(
                {
                    "citations": [snippet.title for snippet in plan.citations],
                    "actions": pending_actions,
                    "diagnostics": plan.diagnostics,
                }
            )
            verification_snapshot = None
            if tool_context is not None:
                verification = getattr(tool_context, "verification", None)
                if isinstance(verification, Mapping) and verification:
                    verification_snapshot = dict(verification)
            if verification_snapshot:
                missing_points = verification_snapshot.get("missing_points")
                missing_list: list[str] = []
                if isinstance(missing_points, list):
                    for entry in missing_points[:8]:
                        if isinstance(entry, str) and entry.strip():
                            missing_list.append(entry.strip())
                message_metadata["verification"] = {
                    "verdict": _clip_debug_text(str(verification_snapshot.get("verdict") or ""), limit=48),
                    "missing_points": missing_list,
                    "final_response": _clip_debug_text(str(verification_snapshot.get("final_response") or ""), limit=480),
                    "notes": _clip_debug_text(str(verification_snapshot.get("notes") or ""), limit=480),
                }
            answer_confidence = None
            if plan.diagnostics:
                answer_confidence = plan.diagnostics.get("answer_confidence")
            if answer_confidence is not None:
                message_metadata["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                message_metadata["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]

            if ai_message_id:
                service.update_message(
                    session_token=session_token,
                    message_id=ai_message_id,
                    metadata=message_metadata,
                    conversation=conversation,
                )
            plan_holder["message_metadata"] = message_metadata

            updated_payload = dict(plan_holder.get("final_payload") or {})
            if answer_confidence is not None:
                updated_payload["answer_confidence"] = answer_confidence
            if plan.ingestion_warnings:
                updated_payload["ingestion_warnings"] = [dict(item) for item in plan.ingestion_warnings]
            plan_holder["final_payload"] = updated_payload or None
            if updated_payload:
                version = _next_metadata_version()
                updated_payload["metadata_version"] = version
                actions_queue.put(
                    {
                        "type": "turnUpdated",
                        "payload": {
                            "message_id": str(updated_payload.get("message_id") or ai_message_id or ""),
                            "text": updated_payload.get("text"),
                            "session_status": updated_payload.get("session_status"),
                            "answer_confidence": updated_payload.get("answer_confidence"),
                            "ingestion_warnings": updated_payload.get("ingestion_warnings"),
                            "metadata_version": version,
                        },
                    }
                )

            action_results = []
            if plan.planned_actions:
                action_results = dispatcher.execute(conversation=conversation, planned_actions=plan.planned_actions)
                trace_logger.log(
                    "actions.executed",
                    detail=f"count={len(action_results)}",
                    indent=2,
                    extra=[
                        {
                            "action": result.action.value,
                            "status": result.status,
                            "error": result.error,
                        }
                        for result in action_results
                    ],
                )
            if plan.extractions:
                service.store_extractions(
                    session_token=session_token,
                    items=((extraction.extraction_type, extraction.payload) for extraction in plan.extractions),
                    conversation=conversation,
                )
                logger.info(
                    "portal extractions stored conversation=%s count=%s",
                    conversation.id,
                    len(plan.extractions),
                )
                trace_logger.log(
                    "extractions.stored",
                    detail=f"count={len(plan.extractions)}",
                    indent=2,
                )

            if plan.planned_actions:
                serialized_actions = serialize_action_results(action_results)
                updated_metadata = copy.deepcopy(message_metadata)
                updated_metadata["actions"] = serialized_actions
                if ai_message_id:
                    service.update_message(
                        session_token=session_token,
                        message_id=ai_message_id,
                        metadata=updated_metadata,
                        conversation=conversation,
                    )
                actions_queue.put(
                    {
                        "type": "actionsComplete",
                        "message_id": str(ai_message_id or ""),
                        "actions": serialized_actions,
                    }
                )
            elif plan.extractions:
                actions_queue.put(
                    {
                        "type": "actionsComplete",
                        "message_id": str(ai_message_id or ""),
                        "actions": [],
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("planner async failed: %s", exc)
            trace_logger.log_error("planner", exc, indent=1)
            actions_queue.put(
                {
                    "type": "actionsError",
                    "message_id": str(ai_message_id or ""),
                    "error": str(exc),
                }
            )
        finally:
            if token is not None:
                otel_context.detach(token)
            close_old_connections()
            actions_queue.put(actions_sentinel)

    def persist_initial_response(stream_context: StreamingTurnContext, parent_ctx) -> None:
        close_old_connections()
        token = None
        if parent_ctx is not None:
            token = otel_context.attach(parent_ctx)
        trace_logger.log("finalize.started", indent=1)
        try:
            with TRACER.start_as_current_span("portal.finalize_turn") as finalize_span:
                if finalize_span.is_recording():
                    finalize_span.set_attribute("conversation.id", str(conversation.id))
                    finalize_span.set_attribute("business.id", str(conversation.business_profile_id))
                base_plan = orchestrator.finalize_turn(stream_context)
                plan_holder["plan"] = base_plan
                persist_text = base_plan.response_text or ""
                if not persist_text:
                    streamed_text = "".join(stream_context.streamed_chunks).strip() if stream_context.streamed_chunks else ""
                    if streamed_text:
                        persist_text = streamed_text
                with blocks_lock:
                    blocks_snapshot = copy.deepcopy(content_blocks)
                if not persist_text:
                    derived_text = extract_text_from_content_blocks(blocks_snapshot)
                    if derived_text:
                        persist_text = derived_text
                if not persist_text:
                    persist_text = "(no content)"
                with TRACER.start_as_current_span("portal.finalize.sanitize") as sanitize_span:
                    response_text, _ = sanitize_with_diagnostics(
                        persist_text,
                        conversation=conversation,
                        stage="persisted_message",
                    )
                    if sanitize_span.is_recording():
                        sanitize_span.set_attribute("portal.sanitize_chars", len(persist_text))
                pending_actions = serialize_planned_actions(base_plan.planned_actions)
                message_metadata = {
                    "citations": [snippet.title for snippet in base_plan.citations],
                    "actions": pending_actions,
                    "diagnostics": base_plan.diagnostics,
                }
                if base_plan.diagnostics and base_plan.diagnostics.get("answer_confidence") is not None:
                    message_metadata["answer_confidence"] = base_plan.diagnostics.get("answer_confidence")
                if base_plan.ingestion_warnings:
                    message_metadata["ingestion_warnings"] = [dict(item) for item in base_plan.ingestion_warnings]

                # Ensure we persist a rich block representation of the answer.
                has_rich_text = any(
                    str(entry.get("type") or "").strip().lower()
                    in {"paragraph", "heading", "list", "list_item", "quote", "code_block"}
                    for entry in blocks_snapshot
                    if isinstance(entry, Mapping)
                )
                has_legacy_text = any(
                    str(entry.get("type") or "").strip().lower() == "text" for entry in blocks_snapshot if isinstance(entry, Mapping)
                )
                if not has_rich_text and not has_legacy_text and response_text:
                    # Ensure assistant text renders before tool cards on refresh.
                    blocks_snapshot = [*rich_blocks_from_text(response_text), *blocks_snapshot]

                # Structured outputs (tables/kv) should render as content blocks, not markdown.
                structured_blocks = content_blocks_from_response_blocks(list(base_plan.response_blocks))
                if structured_blocks:
                    blocks_snapshot.extend(structured_blocks)
                    for block in structured_blocks:
                        stream_queue.put(
                            {
                                "type": "block_start",
                                "payload": {
                                    "message_id": _current_message_id(),
                                    "block": copy.deepcopy(block),
                                },
                            }
                        )

                blocks_snapshot = _normalize_portal_content_blocks(blocks_snapshot)

                # Preserve pre-approval text when updating message body after approval resolution.
                # When a turn continues after tool approval, the new response_text only contains
                # the post-approval content. We need to prepend any existing pre-approval text
                # to maintain correct content ordering on page refresh.
                final_body = response_text
                pending_message_id = plan_holder.get("pending_message_id")
                if pending_message_id:
                    try:
                        existing_message = conversation.messages.filter(id=pending_message_id).first()
                        if existing_message and existing_message.body:
                            # Check if this message had pre-approval text by looking at content_blocks
                            existing_blocks = existing_message.content_blocks if isinstance(getattr(existing_message, "content_blocks", None), list) else []
                            has_tool_blocks = any(
                                isinstance(block, dict) and str(block.get("type") or "").strip().lower() in {"tool_use", "tool_result"}
                                for block in existing_blocks
                            )
                            # If there were tool blocks and existing body text, preserve the pre-approval text
                            if has_tool_blocks and existing_message.body.strip():
                                pre_approval_text = existing_message.body.strip()
                                # Only prepend if the new response doesn't already contain the pre-approval text
                                if pre_approval_text and pre_approval_text not in response_text:
                                    final_body = f"{pre_approval_text}\n\n{response_text}"
                    except Exception:  # pragma: no cover - best effort only
                        logger.exception("portal pre-approval text preservation failed")

                with TRACER.start_as_current_span("portal.finalize.persist") as persist_span:
                    try:
                        ai_message = service.append_message(
                            session_token=session_token,
                            sender=ConversationSender.AI,
                            body=final_body,
                            metadata=message_metadata,
                            content_blocks=blocks_snapshot,
                            conversation=conversation,
                            message_id=pending_message_id,
                        )
                    except IntegrityError:
                        ai_message = service.update_message(
                            session_token=session_token,
                            message_id=pending_message_id,
                            body=final_body,
                            metadata=message_metadata,
                            content_blocks=blocks_snapshot,
                            conversation=conversation,
                        )
                    if persist_span.is_recording():
                        persist_span.set_attribute("portal.actions.pending", len(pending_actions))
                try:
                    schedule_memory = getattr(orchestrator, "schedule_memory_update", None)
                    if callable(schedule_memory):
                        schedule_memory(
                            conversation=conversation,
                            user_message=body,
                            assistant_message=response_text,
                            expected_last_message_id=ai_message.id,
                        )
                except Exception:  # pragma: no cover - best effort background task
                    logger.exception("portal memory update scheduling failed")

                session_state = service.get_session_state(session_token=session_token, conversation=conversation)
                final_payload = {
                    "text": response_text,
                    "message_id": str(ai_message.id),
                    "session_status": session_state.status,
                    "metadata_version": plan_holder.get("metadata_version", 1),
                    "content_blocks": ai_message.content_blocks,
                }
                if base_plan.diagnostics:
                    answer_confidence = base_plan.diagnostics.get("answer_confidence")
                    if answer_confidence is not None:
                        final_payload["answer_confidence"] = answer_confidence
                if base_plan.ingestion_warnings:
                    final_payload["ingestion_warnings"] = [dict(item) for item in base_plan.ingestion_warnings]
                if debug_tool_trace_enabled:
                    debug_payload = _serialize_debug_tools_payload(stream_context)
                    final_payload["debug_tools"] = debug_payload or {
                        "tool_trace": [],
                        "search_history": [],
                        "knowledge_results": [],
                        "knowledge_reads": [],
                        "coverage_ledger": [],
                        "table_aggregate_rows": [],
                    }
                plan_holder["session_status"] = session_state.status
                extra_payload: dict[str, Any] = {
                    "citations": [snippet.title for snippet in base_plan.citations],
                    "pending_actions": len(base_plan.planned_actions),
                }
                trace_logger.log(
                    "response.persisted",
                    detail=f"message_id={ai_message.id}",
                    indent=1,
                    extra=extra_payload,
                )
                plan_holder["message_metadata"] = message_metadata
                plan_holder["final_payload"] = final_payload
                plan_holder["ai_message_id"] = ai_message.id
                plan_holder["pending_message_id"] = ai_message.id

                if not cancel_requested.is_set():
                    threading.Thread(
                        target=run_planner_async,
                        args=(
                            stream_context,
                            response_text,
                            ai_message.id,
                            otel_context.get_current(),
                        ),
                        daemon=True,
                    ).start()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator finalize failed: %s", exc)
            trace_logger.log_error("finalize", exc, indent=1)
            plan_holder["final_error"] = str(exc)
            actions_queue.put(actions_sentinel)
        finally:
            if token is not None:
                otel_context.detach(token)
            close_old_connections()
            signal_stream_complete()
            signal_stream_stop()
            finalize_queue.put(finalize_sentinel)

    def orchestrate(parent_ctx) -> None:
        close_old_connections()
        token = None
        if parent_ctx is not None:
            token = otel_context.attach(parent_ctx)
        try:
            trace_logger.log("orchestrator.turn.start", indent=1)
            with TRACER.start_as_current_span("portal.orchestrator.turn"):
                stream_context = orchestrator.stream_turn(
                    conversation=conversation,
                    user_message=body,
                    on_response_text_delta=on_response_text_delta,
                    on_status_change=on_status_change,
                    on_placeholder_response=on_placeholder_response,
                    on_stream_complete=signal_stream_complete,
                    on_spinner_update=on_spinner_update,
                    on_tool_event=on_tool_event,
                    on_block_event=on_block_event,
                    on_reasoning_event=on_reasoning_event,
                    should_cancel=cancel_requested.is_set,
                )
                plan_holder["context"] = stream_context
                threading.Thread(
                    target=persist_initial_response,
                    args=(stream_context, otel_context.get_current()),
                    daemon=True,
                ).start()
                signal_stream_complete()
                trace_logger.log("orchestrator.turn.complete", indent=1)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Orchestrator turn failed: %s", exc)
            plan_holder["error"] = str(exc)
            trace_logger.log_error("orchestrator.turn", exc, indent=1)
            signal_stream_complete()
            signal_stream_stop()
            finalize_queue.put(finalize_sentinel)
            actions_queue.put(actions_sentinel)
        finally:
            if token is not None:
                otel_context.detach(token)
            close_old_connections()

    request_context = otel_context.get_current()
    worker = threading.Thread(target=orchestrate, args=(request_context,), daemon=True)
    worker.start()

    def _block_event_stream() -> Iterable[str]:
        # Force an early flush so proxies (or WSGI servers) don't buffer the first real event.
        # This is a valid SSE "comment" line that the client safely ignores.
        yield ": stream_open\n\n"
        keepalive_setting = getattr(settings, "PORTAL_STREAM_KEEPALIVE_SECONDS", 15.0)
        try:
            keepalive_seconds = float(keepalive_setting)
        except (TypeError, ValueError):
            keepalive_seconds = 15.0
        if keepalive_seconds < 0:
            keepalive_seconds = 0.0
        last_keepalive = time.monotonic()
        while True:
            try:
                chunk = stream_queue.get(timeout=0.25)
            except Empty:
                if keepalive_seconds and time.monotonic() - last_keepalive >= keepalive_seconds:
                    last_keepalive = time.monotonic()
                    yield ": keepalive\n\n"
                continue
            if chunk is stream_sentinel:
                break
            if not isinstance(chunk, dict):
                continue

            chunk_type = str(chunk.get("type") or "").strip()
            last_keepalive = time.monotonic()
            if chunk_type in {"block_start", "block_delta", "block_end", "block_tool_use", "block_tool_result"}:
                payload = chunk.get("payload") or {}
                yield f"event: {chunk_type}\n"
                yield f"data: {json.dumps(payload)}\n\n"
                continue

            if chunk_type == "context_progress":
                state_value = chunk.get("state")
                label_value = chunk.get("label")
                data: dict[str, object] = {}
                if isinstance(state_value, str):
                    data["state"] = state_value
                if isinstance(label_value, str):
                    data["label"] = label_value
                meta_value = chunk.get("meta")
                if isinstance(meta_value, dict):
                    data["meta"] = meta_value
                yield "event: context_progress\n"
                yield f"data: {json.dumps(data)}\n\n"
                continue

            if chunk_type == "status":
                state_value = chunk.get("state")
                label_value = chunk.get("label")
                data: dict[str, object] = {}
                if isinstance(state_value, str):
                    data["state"] = state_value
                if isinstance(label_value, str):
                    data["label"] = label_value
                meta_value = chunk.get("meta")
                if isinstance(meta_value, dict):
                    data["meta"] = meta_value
                yield "event: status\n"
                yield f"data: {json.dumps(data)}\n\n"
                continue

            if chunk_type == "spinnerStatus":
                payload = {
                    "message_id": chunk.get("message_id"),
                    "text": chunk.get("spinner_text"),
                    "pending": chunk.get("pending"),
                }
                yield "event: spinnerStatus\n"
                yield f"data: {json.dumps(payload)}\n\n"
                continue

            # Unknown structured event: ignore rather than corrupting the transcript.

        worker.join()
        finalize_queue.get()
        final_payload = plan_holder.get("final_payload")
        if not final_payload:
            error_message = plan_holder.get("final_error") or plan_holder.get("error") or "AI finalization failed"
            yield "event: error\n"
            yield f"data: {json.dumps(str(error_message))}\n\n"
            return

        final_payload = dict(final_payload)
        final_payload["pending"] = False
        if "metadata_version" not in final_payload:
            final_payload["metadata_version"] = plan_holder.get("metadata_version", 1)
        trace_logger.log(
            "response.dispatched",
            detail=f"message_id={final_payload.get('message_id')}",
            indent=1,
        )
        _emit_spinner_status("", pending=False, fallback=None, allow_empty=True)
        yield "event: turnPersisted\n"
        yield f"data: {json.dumps(final_payload)}\n\n"

        while True:
            post_event = actions_queue.get()
            if post_event is actions_sentinel:
                break
            if post_event.get("type") == "turnUpdated":
                payload = post_event.get("payload") or {}
                yield "event: turnUpdated\n"
                yield f"data: {json.dumps(payload)}\n\n"
            elif post_event.get("type") == "actionsComplete":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "actions": post_event.get("actions", []),
                    "label": "Follow-up tasks completed.",
                }
                trace_logger.log(
                    "actions.completed",
                    detail=f"message_id={payload['message_id']} count={len(payload['actions'])}",
                    indent=2,
                )
                yield "event: actionsComplete\n"
                yield f"data: {json.dumps(payload)}\n\n"
            elif post_event.get("type") == "actionsError":
                payload = {
                    "message_id": post_event.get("message_id"),
                    "error": post_event.get("error", "Background workflow failed."),
                }
                trace_logger.log_error(
                    "actions",
                    payload.get("error") or "actions failed",
                    indent=2,
                )
                yield "event: actionsError\n"
                yield f"data: {json.dumps(payload)}\n\n"

    def event_stream() -> Iterable[str]:
        try:
            yield from _block_event_stream()
        finally:
            _unregister_cancel_handle()

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_GET
def events(request: HttpRequest) -> StreamingHttpResponse:
    session_token = request.GET.get("session_token") or request.GET.get("sessionToken")
    if not session_token:
        return StreamingHttpResponse(status=400)
    service = _service()
    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    conversation_id = getattr(conversation, "id", None)
    business_id = getattr(conversation, "business_profile_id", None)
    agent_profile_id = getattr(conversation, "agent_profile_id", None)
    subagents_enabled = False
    try:
        from apps.accounts.feature_flags import FeatureFlagService
        from apps.accounts.models import BusinessProfile

        if business_id:
            with tenant_context(business_id):
                business = BusinessProfile.objects.filter(id=business_id).first()
            subagents_enabled = bool(getattr(FeatureFlagService.snapshot(business), "sub_agents_v1", False)) if business else False
    except Exception:  # pragma: no cover - best effort only
        subagents_enabled = False

    def event_stream() -> Iterable[str]:
        yield "event: statusChanged\n"
        yield f"data: {json.dumps({'status': session.status})}\n\n"
        run_since = timezone.now()
        request_since = timezone.now()
        message_since = timezone.now()
        if conversation_id and subagents_enabled:
            try:
                snapshot = _build_portal_agent_runs_snapshot(
                    conversation_id=conversation_id,
                    business_id=business_id,
                )
                yield "event: agentRunsSnapshot\n"
                yield f"data: {json.dumps(snapshot)}\n\n"
                cursor = snapshot.get("cursor") if isinstance(snapshot, dict) else {}
                since_raw = cursor.get("since") if isinstance(cursor, dict) else None
                since = None
                if isinstance(since_raw, str) and since_raw.strip():
                    raw = since_raw.strip().replace("Z", "+00:00")
                    try:
                        since = datetime.fromisoformat(raw)
                    except ValueError:
                        since = None
                    if since is not None and since.tzinfo is None:
                        since = since.replace(tzinfo=timezone.utc)
                if since is None:
                    since = timezone.now()
                run_since = since
            except Exception:  # pragma: no cover - snapshot is best effort only
                snapshot = None
                run_since = timezone.now()

            try:
                request_snapshot = _build_portal_agent_requests_snapshot(
                    business_id=business_id,
                    agent_profile_id=agent_profile_id,
                )
                yield "event: agentRequestsSnapshot\n"
                yield f"data: {json.dumps(request_snapshot)}\n\n"
                cursor = request_snapshot.get("cursor") if isinstance(request_snapshot, dict) else {}
                since_raw = cursor.get("since") if isinstance(cursor, dict) else None
                since = None
                if isinstance(since_raw, str) and since_raw.strip():
                    raw = since_raw.strip().replace("Z", "+00:00")
                    try:
                        since = datetime.fromisoformat(raw)
                    except ValueError:
                        since = None
                    if since is not None and since.tzinfo is None:
                        since = since.replace(tzinfo=timezone.utc)
                if since is None:
                    since = timezone.now()
                request_since = since
            except Exception:  # pragma: no cover - snapshot is best effort only
                request_since = timezone.now()
            message_since = timezone.now()
        else:
            run_since = timezone.now()
            request_since = timezone.now()
            message_since = timezone.now()

        seen: set[tuple[str, int]] = set()
        seen_order: list[tuple[str, int]] = []
        seen_limit = 2000
        seen_requests: set[tuple[str, str]] = set()
        seen_requests_order: list[tuple[str, str]] = []
        seen_messages: set[str] = set()
        seen_messages_order: list[str] = []
        last_heartbeat = time.monotonic()

        while True:
            close_old_connections()
            if conversation_id and subagents_enabled:
                with tenant_context(business_id):
                    events_batch = list(
                        AgentRunEvent.objects.select_related("run")
                        .filter(run__conversation_id=conversation_id)
                        .filter(created_at__gte=run_since)
                        .order_by("created_at", "run_id", "sequence_index")[:250]
                    )
                if events_batch:
                    latest_created_at = run_since
                    for event in events_batch:
                        if event.created_at and event.created_at > latest_created_at:
                            latest_created_at = event.created_at
                        key = (str(event.run_id), int(event.sequence_index))
                        if key in seen:
                            continue
                        seen.add(key)
                        seen_order.append(key)
                        if len(seen_order) > seen_limit:
                            old = seen_order.pop(0)
                            seen.discard(old)
                        run_obj = getattr(event, "run", None)
                        payload = {
                            "run": _serialize_agent_run_for_portal(run_obj) if run_obj else {"id": str(event.run_id)},
                            "event": _serialize_agent_run_event_for_portal(event),
                        }
                        yield "event: agentRunEvent\n"
                        yield f"data: {json.dumps(payload)}\n\n"
                    run_since = latest_created_at

            if conversation_id and subagents_enabled:
                from apps.conversations.models import ConversationMessage

                def _serialize_message(msg: ConversationMessage) -> dict[str, object]:
                    return {
                        "id": str(msg.id),
                        "sender": msg.sender,
                        "body": msg.body or "",
                        "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
                        "metadata": msg.metadata if isinstance(getattr(msg, "metadata", None), dict) else {},
                        "content_blocks": msg.content_blocks if isinstance(getattr(msg, "content_blocks", None), list) else [],
                    }

                with tenant_context(business_id):
                    messages_batch = list(
                        ConversationMessage.objects.filter(conversation_id=conversation_id)
                        .filter(created_at__gte=message_since)
                        .filter(Q(metadata__source="agent_run") | Q(metadata__source="voice_call"))
                        .order_by("created_at", "id")[:250]
                    )
                if messages_batch:
                    latest_created_at = message_since
                    for msg in messages_batch:
                        if msg.created_at and msg.created_at > latest_created_at:
                            latest_created_at = msg.created_at
                        key = str(msg.id)
                        if key in seen_messages:
                            continue
                        seen_messages.add(key)
                        seen_messages_order.append(key)
                        if len(seen_messages_order) > seen_limit:
                            old = seen_messages_order.pop(0)
                            seen_messages.discard(old)
                        payload = {"message": _serialize_message(msg)}
                        yield "event: conversationMessage\n"
                        yield f"data: {json.dumps(payload)}\n\n"
                    message_since = latest_created_at

            if business_id and agent_profile_id and subagents_enabled:
                with tenant_context(business_id):
                    requests_batch = list(
                        AgentRequest.objects.select_related("from_agent_profile", "to_agent_profile")
                        .filter(business_profile_id=business_id)
                        .filter(Q(to_agent_profile_id=agent_profile_id) | Q(from_agent_profile_id=agent_profile_id))
                        .filter(updated_at__gte=request_since)
                        .order_by("updated_at", "id")[:250]
                    )
                if requests_batch:
                    latest_updated_at = request_since
                    for req in requests_batch:
                        if req.updated_at and req.updated_at > latest_updated_at:
                            latest_updated_at = req.updated_at
                        updated_key = req.updated_at.isoformat() if req.updated_at else ""
                        key = (str(req.id), updated_key)
                        if key in seen_requests:
                            continue
                        seen_requests.add(key)
                        seen_requests_order.append(key)
                        if len(seen_requests_order) > seen_limit:
                            old = seen_requests_order.pop(0)
                            seen_requests.discard(old)
                        payload = {"request": _serialize_agent_request_for_portal(req)}
                        yield "event: agentRequestEvent\n"
                        yield f"data: {json.dumps(payload)}\n\n"
                    request_since = latest_updated_at

            # Poll for voice call transcript events (faster polling for real-time feel)
            if conversation_id:
                cache_key = f"voice_transcript:{conversation_id}"
                transcript_events = cache.get(cache_key) or []
                if isinstance(transcript_events, list) and transcript_events:
                    # Use atomic pop pattern: get, process, then clear only what we processed
                    cache.delete(cache_key)
                    for evt in transcript_events:
                        if isinstance(evt, dict):
                            yield "event: voiceCallTranscript\n"
                            yield f"data: {json.dumps(evt)}\n\n"
                    # Shorter sleep when actively streaming transcripts
                    time.sleep(0.15)
                    continue

            now = time.monotonic()
            if now - last_heartbeat >= 15.0:
                yield "event: heartbeat\n"
                yield "data: {}\n\n"
                last_heartbeat = now
            time.sleep(0.5)  # Reduced from 1.0s for better responsiveness

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


# ------------------------------------------------------------------
# Session Management Endpoints
# ------------------------------------------------------------------


@csrf_exempt
@require_POST
def list_portal_sessions(request: HttpRequest) -> JsonResponse:
    """
    List session summaries for the given session tokens.
    
    Used by the frontend to populate the session history sidebar.
    Request body:
    {
        "business_slug": "acme",
        "agent_slug": "support",
        "session_tokens": ["token1", "token2", ...]
    }
    """
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    business_slug = (payload.get("business_slug") or payload.get("businessSlug") or "").strip()
    agent_slug = (payload.get("agent_slug") or payload.get("agentSlug") or "").strip()
    session_tokens = payload.get("session_tokens") or payload.get("sessionTokens") or []

    if not business_slug or not agent_slug:
        return _json_error("validation_error", "business_slug and agent_slug are required.")

    if not isinstance(session_tokens, list):
        return _json_error("validation_error", "session_tokens must be a list.")

    # Sanitize and limit tokens
    clean_tokens = [str(t).strip() for t in session_tokens if t][:100]

    try:
        from apps.conversations.portal import PortalSessionSummary
        sessions = service.list_sessions(
            business_slug=business_slug,
            agent_slug=agent_slug,
            session_tokens=clean_tokens,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    return JsonResponse({
        "sessions": [
            {
                "session_token": s.session_token,
                "title": s.title,
                "started_at": s.started_at.isoformat(),
                "last_activity_at": s.last_activity_at.isoformat(),
                "status": s.status,
                "message_count": s.message_count,
                "preview": s.preview,
            }
            for s in sessions
        ]
    })


@csrf_exempt
@require_POST
def create_portal_session(request: HttpRequest) -> JsonResponse:
    """
    Create a new chat session.
    
    Used when the user clicks "New Chat" to start a fresh conversation.
    Request body:
    {
        "business_slug": "acme",
        "agent_slug": "support",
        "metadata": {}  // optional
    }
    """
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    business_slug = (payload.get("business_slug") or payload.get("businessSlug") or "").strip()
    agent_slug = (payload.get("agent_slug") or payload.get("agentSlug") or "").strip()
    metadata_raw = payload.get("metadata") or {}
    metadata: dict[str, object] = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}

    if not business_slug or not agent_slug:
        return _json_error("validation_error", "business_slug and agent_slug are required.")

    # If the request is from an authenticated tenant user and they belong to the business,
    # attach actor_user_id so per-user integrations can be resolved safely.
    try:
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            business, _agent = service.resolve_handle(business_slug, agent_slug)
            allowed = bool(user.is_staff or user.business_profiles.filter(id=business.id).exists())
            if allowed and "actor_user_id" not in metadata and "actorUserId" not in metadata:
                metadata["actor_user_id"] = str(user.id)
    except Exception:  # pragma: no cover - best effort only
        pass

    try:
        result = service.create_new_session(
            business_slug=business_slug,
            agent_slug=agent_slug,
            metadata=metadata,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    response = JsonResponse(_bootstrap_to_dict(result), status=201)
    response.set_cookie(
        f"chat_session_{result.business.slug}_{result.agent.slug}",
        result.session.session_token,
        max_age=3600 * 24 * 365,
        httponly=False,
        secure=False,
        samesite="Lax",
    )
    return response


@csrf_exempt
@require_POST
def portal_turn_create(request: HttpRequest) -> JsonResponse:
    """
    Create a new portal turn (event-sourced streaming).

    Request body:
    {
        "session_token": "...",
        "body": "...",
        "metadata": {}
    }
    """
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    session_token = (payload.get("session_token") or payload.get("sessionToken") or "").strip()
    body = (payload.get("body") or "").strip()
    metadata_raw = payload.get("metadata") or {}
    metadata: dict[str, object] = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}

    if not session_token or not body:
        return _json_error("validation_error", "session_token and body are required.")

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
        session = service.get_session_state(session_token=session_token, conversation=conversation)
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    customer_message: PortalMessage | None = None
    try:
        customer_message = service.append_message(
            session_token=session_token,
            sender=ConversationSender.CUSTOMER,
            body=body,
            metadata=metadata,
            conversation=conversation,
        )
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    agent = conversation.agent_profile
    if not agent:
        return _json_error("validation_error", "Agent profile is missing.", status=500)

    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.create(
            conversation=conversation,
            agent_profile=agent,
            status=PortalTurnStatus.STREAMING,
            run_after=timezone.now(),
            user_message=body,
            metadata={"source": "portal", "origin": "turn_create"},
        )
    run_turn_background(turn_id=turn.id, business_id=business_id)

    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "turn": _portal_turn_to_dict(turn),
            "customer_message_id": str(customer_message.id) if customer_message else None,
        },
        status=201,
    )


def _parse_turn_since_seq(request: HttpRequest) -> int:
    raw = request.GET.get("since") or request.GET.get("since_seq") or ""
    if not raw:
        raw = request.META.get("HTTP_LAST_EVENT_ID", "")
    try:
        value = int(str(raw).strip())
        return value if value >= 0 else 0
    except (TypeError, ValueError):
        return 0


@require_GET
def portal_turn_events(request: HttpRequest, turn_id: uuid.UUID) -> StreamingHttpResponse:
    service = _service()
    session_token = (request.GET.get("session_token") or request.GET.get("sessionToken") or "").strip()
    if not session_token:
        return StreamingHttpResponse(status=400)

    try:
        conversation = service.get_conversation(session_token=session_token, include_messages=False)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.filter(id=turn_id, conversation_id=conversation.id).first()
    if not turn:
        return StreamingHttpResponse(status=404)

    since = _parse_turn_since_seq(request)

    def event_stream() -> Iterable[str]:
        yield ": stream_open\n\n"
        last_seq = int(since or 0)
        keepalive_seconds = 15.0
        last_keepalive = time.monotonic()

        while True:
            with tenant_context(business_id):
                events = list(list_turn_events(turn_id=turn.id, since_seq=last_seq, limit=250))
            if events:
                for evt in events:
                    last_seq = int(evt.seq or 0)
                    payload = {
                        "turn_id": str(turn.id),
                        "seq": last_seq,
                        "type": evt.type,
                        "payload": evt.payload or {},
                    }
                    yield f"id: {last_seq}\n"
                    yield "event: turnEvent\n"
                    yield f"data: {json.dumps(payload)}\n\n"
                continue

            with tenant_context(business_id):
                latest = (
                    PortalTurn.objects.filter(id=turn.id)
                    .values_list("status", "last_event_seq")
                    .first()
                )
            if latest:
                latest_status, latest_seq = latest
                if latest_status in {PortalTurnStatus.FINALIZED, PortalTurnStatus.FAILED, PortalTurnStatus.CANCELLED}:
                    if int(latest_seq or 0) <= last_seq:
                        break

            now = time.monotonic()
            if now - last_keepalive >= keepalive_seconds:
                yield ": keepalive\n\n"
                last_keepalive = now
            time.sleep(0.5)

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
