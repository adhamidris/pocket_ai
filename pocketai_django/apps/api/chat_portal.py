from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import re
import secrets
import select
import time
import uuid
from datetime import datetime, timedelta
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
    BusinessProfile,
    McpConnectionApprovalMode,
    McpConnectionAuditAction,
    McpToolOperationType,
)
from apps.mcp.models import (
    AgentMcpToolSetting,
    McpConnectionAuditEvent,
)
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
from apps.rag.rag_logging import structured_log
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
from apps.conversations.portal_turn_events import (
    PORTAL_TURN_EVENTS_NOTIFY_CHANNEL,
    append_turn_event,
    get_portal_redis_client,
    list_turn_events,
    portal_turn_redis_stream_key,
)
from apps.conversations.portal_stream_trace import PortalStreamTrace
from apps.conversations.portal_session_event_bus import (
    portal_session_agent_requests_stream_key,
    portal_session_conversation_stream_key,
)
from apps.conversations.portal_turn_runner import run_turn_background
from apps.conversations.content_blocks import (
    extract_text_from_content_blocks,
)
from core.tenancy import tenant_context

logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)

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


def _json_debug_exact(value: object, *, depth: int = 10) -> object:
    """
    Preserve exact debug payload values without clipping/redaction.

    Used only for explicit portal debug I/O mirrors (developer-facing),
    where we need to inspect the exact tool-call request/response boundary
    as seen by the model.
    """

    if value is None:
        return None
    if depth <= 0:
        return value if isinstance(value, (str, int, float, bool)) else str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in value.items():
            out[str(key)] = _json_debug_exact(item, depth=depth - 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_json_debug_exact(item, depth=depth - 1) for item in value]
    return str(value)


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

    llm_request = entry.get("llm_request")
    if isinstance(llm_request, Mapping) and llm_request:
        out["llm_request"] = _json_debug_exact(llm_request, depth=10)  # type: ignore[arg-type]

    llm_response = entry.get("llm_response")
    if isinstance(llm_response, Mapping) and llm_response:
        llm_response_out = _json_debug_exact(llm_response, depth=6)
        if isinstance(llm_response_out, dict):
            content_value = llm_response_out.get("content")
            if isinstance(content_value, str):
                try:
                    llm_response_out["content_json"] = json.loads(content_value)
                except Exception:
                    pass
        out["llm_response"] = llm_response_out  # type: ignore[assignment]

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


def _canonicalize_portal_message_blocks(
    *,
    body: str,
    blocks: object | None,
) -> list[dict[str, object]]:
    del body  # Body is fallback text; canonical source-of-truth is persisted content_blocks.
    if not isinstance(blocks, list):
        return []
    canonical: list[dict[str, object]] = []
    for entry in blocks:
        if isinstance(entry, Mapping):
            canonical.append(dict(entry))
    return canonical


def _canonicalize_turn_event_payload(
    event_type: str,
    payload_obj: object | None,
) -> dict[str, object]:
    payload = payload_obj if isinstance(payload_obj, dict) else {}
    if str(event_type or "").strip().lower() != "turn_persisted":
        return payload

    # Preserve turn_persisted payload as streamed/persisted source-of-truth.
    # Only coerce camelCase alias when present for compatibility.
    blocks = payload.get("content_blocks")
    if blocks is None and "contentBlocks" in payload:
        payload["content_blocks"] = _canonicalize_portal_message_blocks(body="", blocks=payload.get("contentBlocks"))
        payload.pop("contentBlocks", None)
    elif blocks is not None:
        payload["content_blocks"] = _canonicalize_portal_message_blocks(body="", blocks=blocks)
    return payload


def _safe_canonicalize_blocks(
    *,
    body: str,
    blocks: object | None,
) -> list[dict[str, object]]:
    """Guarded wrapper: returns raw blocks on failure instead of crashing."""
    try:
        return _canonicalize_portal_message_blocks(body=body, blocks=blocks)
    except Exception:
        logger.exception("_canonicalize_portal_message_blocks failed; using raw blocks")
        return list(blocks) if isinstance(blocks, list) else []


def _safe_canonicalize_turn_event(
    event_type: str,
    payload_obj: object | None,
) -> dict[str, object]:
    """Guarded wrapper: returns original payload on failure instead of crashing the SSE generator."""
    try:
        return _canonicalize_turn_event_payload(event_type, payload_obj)
    except Exception:
        logger.exception("_canonicalize_turn_event_payload failed; passing through raw payload")
        return payload_obj if isinstance(payload_obj, dict) else {}


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
    body = message.body if isinstance(message.body, str) else str(message.body or "")
    content_blocks = _safe_canonicalize_blocks(body=body, blocks=message.content_blocks)
    if not body and content_blocks:
        body = extract_text_from_content_blocks(content_blocks)
    return {
        "id": str(message.id),
        "sender": message.sender,
        "body": body,
        "sent_at": message.sent_at.isoformat(),
        "metadata": message.metadata,
        "content_blocks": content_blocks,
    }


def _bootstrap_to_dict(result: PortalSessionBootstrap) -> dict:
    payload = {
        "business": _business_to_dict(result.business),
        "agent": _agent_to_dict(result.agent),
        "session": _session_to_dict(result.session),
        "messages": [_message_to_dict(msg) for msg in result.messages],
    }
    try:
        # If a portal turn is currently streaming (e.g., waiting for tool approval),
        # expose it so the frontend can resume by replaying the turn event log.
        with tenant_context(result.business.id):
            active_turn = (
                PortalTurn.objects.filter(conversation_id=result.session.conversation_id)
                .filter(status__in={PortalTurnStatus.STREAMING, PortalTurnStatus.WAITING_APPROVAL})
                .order_by("-started_at")
                .first()
            )
        payload["active_turn"] = _portal_turn_to_dict(active_turn) if active_turn else None
    except Exception:  # pragma: no cover - best effort only
        payload["active_turn"] = None
    try:
        from apps.accounts.feature_flags import FeatureFlagService

        feature_state = FeatureFlagService.snapshot(result.business)
        payload["capabilities"] = {
            "subAgentsEnabled": bool(getattr(feature_state, "sub_agents_v1", False)),
        }
    except Exception:  # pragma: no cover - best effort only
        payload["capabilities"] = {"subAgentsEnabled": False}
    return payload



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

    # Patch any persisted in-flight portal message blocks so refresh reflects the latest decision.
    # This mirrors `portal_tool_approval` behavior for chat-thread approval cards created by runs.
    if approval is not None and business_id:
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
                    ConversationMessage.objects.filter(id=msg.id).update(
                        content_blocks=updated_blocks,
                        metadata=meta_out,
                    )
        except Exception:  # pragma: no cover - best effort only
            logger.exception("portal run approval message patch failed approval=%s", getattr(approval, "id", None))

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
        metrics_enabled = bool(getattr(settings, "PORTAL_STREAM_METRICS", False))
        started_at = time.perf_counter()
        first_event_at: float | None = None
        events_sent = 0
        keepalives_sent = 0
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
        session_bus = str(getattr(settings, "PORTAL_SESSION_EVENT_BUS", "postgres") or "postgres").strip().lower()
        redis_conn = None
        redis_stream_positions: dict[str, str] = {}
        if session_bus == "redis":
            redis_conn = get_portal_redis_client(socket_timeout_seconds=20.0)
            if redis_conn is not None and conversation_id:
                start_id = _parse_session_since_id(request)
                if not start_id:
                    start_ms = max(0, int(time.time() * 1000) - 1)
                    start_id = f"{start_ms}-0"
                redis_stream_positions[portal_session_conversation_stream_key(conversation_id=conversation_id)] = start_id
                if subagents_enabled and agent_profile_id:
                    redis_stream_positions[portal_session_agent_requests_stream_key(agent_profile_id=agent_profile_id)] = start_id
            else:
                redis_conn = None
                session_bus = "postgres"

        try:
            while True:
                close_old_connections()
                if redis_conn is not None and redis_stream_positions:
                    try:
                        entries = redis_conn.xread(redis_stream_positions, count=250, block=15_000)
                    except Exception:
                        redis_conn = None
                        session_bus = "postgres"
                        continue

                    if entries:
                        for raw_stream, raw_entries in entries:
                            stream_key = (
                                raw_stream.decode("utf-8", errors="replace")
                                if isinstance(raw_stream, (bytes, bytearray))
                                else str(raw_stream)
                            )
                            for raw_id, fields in raw_entries:
                                entry_id = (
                                    raw_id.decode("utf-8", errors="replace")
                                    if isinstance(raw_id, (bytes, bytearray))
                                    else str(raw_id)
                                )
                                redis_stream_positions[stream_key] = entry_id

                                raw_event = fields.get(b"event") if isinstance(fields, dict) else None
                                if raw_event is None and isinstance(fields, dict):
                                    raw_event = fields.get("event")  # type: ignore[index]
                                event_name = (
                                    raw_event.decode("utf-8", errors="replace")
                                    if isinstance(raw_event, (bytes, bytearray))
                                    else str(raw_event or "")
                                ).strip()
                                if not event_name:
                                    continue
                                if not subagents_enabled and event_name in {"agentRunEvent", "agentRequestEvent", "conversationMessage"}:
                                    # Keep behavior compatible with legacy polling mode:
                                    # when sub-agents are disabled, don't surface Tasks/Inbox messages.
                                    continue

                                raw_payload = fields.get(b"payload") if isinstance(fields, dict) else None
                                if raw_payload is None and isinstance(fields, dict):
                                    raw_payload = fields.get("payload")  # type: ignore[index]
                                payload_text = (
                                    raw_payload.decode("utf-8", errors="replace")
                                    if isinstance(raw_payload, (bytes, bytearray))
                                    else str(raw_payload or "")
                                )
                                try:
                                    payload = json.loads(payload_text) if payload_text else {}
                                except Exception:
                                    payload = {}

                                if event_name == "agentRunEvent" and isinstance(payload, dict):
                                    event_obj = payload.get("event") if isinstance(payload.get("event"), dict) else {}
                                    key = (str(event_obj.get("runId") or ""), int(event_obj.get("sequenceIndex") or 0))
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    seen_order.append(key)
                                    if len(seen_order) > seen_limit:
                                        old = seen_order.pop(0)
                                        seen.discard(old)
                                elif event_name == "agentRequestEvent" and isinstance(payload, dict):
                                    req_obj = payload.get("request") if isinstance(payload.get("request"), dict) else {}
                                    key = (str(req_obj.get("id") or ""), str(req_obj.get("updatedAt") or ""))
                                    if key in seen_requests:
                                        continue
                                    seen_requests.add(key)
                                    seen_requests_order.append(key)
                                    if len(seen_requests_order) > seen_limit:
                                        old = seen_requests_order.pop(0)
                                        seen_requests.discard(old)
                                elif event_name == "conversationMessage" and isinstance(payload, dict):
                                    msg_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
                                    msg_id = str(msg_obj.get("id") or "")
                                    if msg_id and msg_id in seen_messages:
                                        continue
                                    if msg_id:
                                        seen_messages.add(msg_id)
                                        seen_messages_order.append(msg_id)
                                        if len(seen_messages_order) > seen_limit:
                                            old = seen_messages_order.pop(0)
                                            seen_messages.discard(old)

                                if first_event_at is None:
                                    first_event_at = time.perf_counter()
                                events_sent += 1
                                yield f"id: {entry_id}\n"
                                yield f"event: {event_name}\n"
                                yield f"data: {json.dumps(payload)}\n\n"
                        continue

                if session_bus != "postgres":
                    # Waiting for more Redis stream events.
                    now = time.monotonic()
                    if now - last_heartbeat >= 15.0:
                        keepalives_sent += 1
                        yield "event: heartbeat\n"
                        yield "data: {}\n\n"
                        last_heartbeat = now
                    continue

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
                                "run": _serialize_agent_run_for_portal(run_obj)
                                if run_obj
                                else {"id": str(event.run_id)},
                                "event": _serialize_agent_run_event_for_portal(event),
                            }
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
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
                            "metadata": msg.metadata
                            if isinstance(getattr(msg, "metadata", None), dict)
                            else {},
                            "content_blocks": msg.content_blocks
                            if isinstance(getattr(msg, "content_blocks", None), list)
                            else [],
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
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
                            yield "event: conversationMessage\n"
                            yield f"data: {json.dumps(payload)}\n\n"
                        message_since = latest_created_at

                if business_id and agent_profile_id and subagents_enabled:
                    with tenant_context(business_id):
                        requests_batch = list(
                            AgentRequest.objects.select_related("from_agent_profile", "to_agent_profile")
                            .filter(business_profile_id=business_id)
                            .filter(
                                Q(to_agent_profile_id=agent_profile_id)
                                | Q(from_agent_profile_id=agent_profile_id)
                            )
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
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
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
                                if first_event_at is None:
                                    first_event_at = time.perf_counter()
                                events_sent += 1
                                yield "event: voiceCallTranscript\n"
                                yield f"data: {json.dumps(evt)}\n\n"
                        # Shorter sleep when actively streaming transcripts
                        time.sleep(0.15)
                        continue

                now = time.monotonic()
                if now - last_heartbeat >= 15.0:
                    keepalives_sent += 1
                    yield "event: heartbeat\n"
                    yield "data: {}\n\n"
                    last_heartbeat = now
                time.sleep(0.5)  # Reduced from 1.0s for better responsiveness
        finally:
            if metrics_enabled:
                structured_log(
                    "portal",
                    "stream.session_sse",
                    {
                        "conversation_id": str(conversation_id or ""),
                        "business_id": str(business_id or ""),
                        "agent_id": str(agent_profile_id or ""),
                        "elapsed_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
                        "first_event_ms": int(max(0.0, (first_event_at - started_at) * 1000.0)) if first_event_at else None,
                        "events_sent": int(events_sent),
                        "heartbeats_sent": int(keepalives_sent),
                        "subagents_enabled": bool(subagents_enabled),
                        "event_bus": str(session_bus or "postgres"),
                    },
                )

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    response["X-Portal-Stream-Protocol-Version"] = str(getattr(settings, "PORTAL_STREAM_PROTOCOL_VERSION", 1))
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

    execution_mode = str(getattr(settings, "PORTAL_TURN_EXECUTION_MODE", "thread") or "thread").strip().lower()
    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.create(
            conversation=conversation,
            agent_profile=agent,
            status=PortalTurnStatus.STREAMING,
            run_after=timezone.now(),
            user_message=body,
            metadata={"source": "portal", "origin": "turn_create", "execution_mode": execution_mode},
        )
    if execution_mode == "worker":
        # Phase 2: turn execution is handled by a dedicated DB-leased worker pool.
        # The portal streams events from Postgres (LISTEN/NOTIFY + event log), so the UX remains identical.
        pass
    else:
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


def _parse_session_since_id(request: HttpRequest) -> str | None:
    raw = request.GET.get("since") or request.GET.get("since_id") or ""
    if not raw:
        raw = request.META.get("HTTP_LAST_EVENT_ID", "")
    raw = str(raw or "").strip()
    return raw or None


def _open_portal_turn_listen_connection():
    """
    Open a dedicated Postgres connection for LISTEN/NOTIFY.

    Why:
    - The portal turn runner appends events in the background and persists them to Postgres.
    - Streaming should be push-based (LISTEN/NOTIFY), not DB-polling, to feel like modern token streaming.
    - We intentionally do not reuse Django's ORM connection for LISTEN to avoid interfering with request queries.
    """
    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        from django.db import connections

        db = connections["default"].settings_dict
        options = db.get("OPTIONS") if isinstance(db.get("OPTIONS"), dict) else {}
        connect_kwargs: dict[str, object] = {
            "dbname": db.get("NAME") or "",
            "user": db.get("USER") or "",
            "password": db.get("PASSWORD") or "",
            "host": db.get("HOST") or "",
            "port": db.get("PORT") or "",
        }
        for key in (
            "sslmode",
            "sslrootcert",
            "sslcert",
            "sslkey",
            "sslcrl",
            "application_name",
        ):
            value = options.get(key)
            if value:
                connect_kwargs[key] = value
        conn = psycopg2.connect(**connect_kwargs)  # type: ignore[arg-type]
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cursor:
            cursor.execute(f"LISTEN {PORTAL_TURN_EVENTS_NOTIFY_CHANNEL}")
        return conn
    except Exception:  # pragma: no cover - best effort only
        return None


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
        metrics_enabled = bool(getattr(settings, "PORTAL_STREAM_METRICS", False))
        trace = PortalStreamTrace(turn_id=turn.id, component="sse")
        conn_id = uuid.uuid4().hex[:8]
        started_at = time.perf_counter()
        first_event_at: float | None = None
        events_sent = 0
        keepalives_sent = 0
        yield ": stream_open\n\n"
        last_seq = int(since or 0)
        keepalive_seconds = 15.0
        last_keepalive = time.monotonic()
        event_bus = str(getattr(settings, "PORTAL_TURN_EVENT_BUS", "postgres") or "postgres").strip().lower()
        turn_log_mode = str(getattr(settings, "PORTAL_TURN_EVENT_LOG_MODE", "db") or "db").strip().lower()
        if event_bus == "postgres":
            # Postgres-backed SSE requires the DB event log. This clamp is intentionally
            # runtime (not import-time only) so tests using `override_settings()` can't
            # accidentally create an invalid combination.
            turn_log_mode = "db"
        redis_conn = None
        redis_stream_key = None
        redis_last_id = f"{last_seq}-0"
        sent_turn_persisted = False
        if event_bus == "redis":
            redis_conn = get_portal_redis_client(socket_timeout_seconds=keepalive_seconds + 5.0)
            if redis_conn is not None:
                redis_stream_key = portal_turn_redis_stream_key(turn_id=turn.id)
            else:
                event_bus = "postgres"

        listen_conn = _open_portal_turn_listen_connection() if event_bus == "postgres" else None
        listen_enabled = listen_conn is not None
        trace.record(
            "sse.open",
            {
                "conn": conn_id,
                "since_seq": int(since or 0),
                "event_bus": str(event_bus or "postgres"),
                "turn_log_mode": str(turn_log_mode or "db"),
                "listen_enabled": bool(listen_enabled),
            },
        )

        try:
            while True:
                if redis_conn is not None and redis_stream_key:
                    # Avoid "block forever" so we can emit keepalives and survive slow first-token turns.
                    # After we deliver `turn_persisted`, prefer a short block window so the
                    # SSE stream can observe `FINALIZED` and close promptly.
                    block_ms = 200 if sent_turn_persisted else int(max(200, keepalive_seconds * 1000))
                    from_id = str(redis_last_id)
                    t0 = time.perf_counter()
                    try:
                        entries = redis_conn.xread({redis_stream_key: redis_last_id}, count=250, block=block_ms)
                    except Exception:
                        # Degrade to Postgres if Redis is unavailable.
                        redis_conn = None
                        redis_stream_key = None
                        if listen_conn is None:
                            listen_conn = _open_portal_turn_listen_connection()
                            listen_enabled = bool(listen_conn is not None)
                        event_bus = "postgres"
                        continue

                    if entries:
                        trace.record(
                            "sse.redis.xread",
                            {
                                "conn": conn_id,
                                "from_id": from_id,
                                "dt_ms": int(max(0.0, (time.perf_counter() - t0) * 1000.0)),
                                "block_ms": int(block_ms),
                            },
                        )
                        entry_count = 0
                        batch_types: dict[str, int] = {}
                        batch_text_chars = 0
                        for _stream_key, stream_entries in entries:
                            for entry_id, fields in stream_entries:
                                entry_count += 1
                                entry_id_str = (
                                    entry_id.decode("utf-8", errors="replace") if isinstance(entry_id, (bytes, bytearray)) else str(entry_id)
                                )
                                redis_last_id = entry_id_str
                                seq_value = None
                                try:
                                    seq_value = int(entry_id_str.split("-", 1)[0])
                                except Exception:
                                    seq_value = None

                                raw_type = fields.get(b"type") if isinstance(fields, dict) else None
                                if raw_type is None and isinstance(fields, dict):
                                    raw_type = fields.get("type")  # type: ignore[index]
                                event_type = (
                                    raw_type.decode("utf-8", errors="replace") if isinstance(raw_type, (bytes, bytearray)) else str(raw_type or "")
                                ).strip() or "event"

                                raw_payload = fields.get(b"payload") if isinstance(fields, dict) else None
                                if raw_payload is None and isinstance(fields, dict):
                                    raw_payload = fields.get("payload")  # type: ignore[index]
                                payload_text = raw_payload.decode("utf-8", errors="replace") if isinstance(raw_payload, (bytes, bytearray)) else str(raw_payload or "")
                                try:
                                    payload_obj = json.loads(payload_text) if payload_text else {}
                                except Exception:
                                    payload_obj = {}
                                # Events from Redis are already canonicalized by _finalize_turn;
                                # re-canonicalizing here is a lossy round-trip.  Skip it.

                                if seq_value is None:
                                    # Fallback: keep Last-Event-ID monotonic even if the Redis stream ID is unexpected.
                                    seq_value = int(payload_obj.get("seq") or 0) if isinstance(payload_obj, dict) else 0
                                last_seq = max(last_seq, int(seq_value or 0))

                                payload = {
                                    "turn_id": str(turn.id),
                                    "seq": int(seq_value or 0),
                                    "type": event_type,
                                    "payload": payload_obj or {},
                                }
                                if event_type.strip().lower() == "text_delta":
                                    # Block-only portal stream contract: ignore legacy raw text events.
                                    continue
                                batch_types[event_type] = batch_types.get(event_type, 0) + 1
                                if event_type.strip().lower() == "turn_persisted":
                                    sent_turn_persisted = True
                                if first_event_at is None:
                                    first_event_at = time.perf_counter()
                                events_sent += 1
                                yield f"id: {payload['seq']}\n"
                                yield "event: turnEvent\n"
                                yield f"data: {json.dumps(payload)}\n\n"
                        trace.record(
                            "sse.batch",
                            {
                                "conn": conn_id,
                                "source": "redis",
                                "events": int(entry_count),
                                "types": batch_types,
                                "text_chars": int(batch_text_chars),
                                "last_seq": int(last_seq),
                            },
                        )
                        continue

                if redis_conn is None or not redis_stream_key:
                    # Postgres-backed streaming (Phase 1/2 behavior).
                    t0 = time.perf_counter()
                    with tenant_context(business_id):
                        events = list(list_turn_events(turn_id=turn.id, since_seq=last_seq, limit=250))
                    trace.record(
                        "sse.db.poll",
                        {
                            "conn": conn_id,
                            "dt_ms": int(max(0.0, (time.perf_counter() - t0) * 1000.0)),
                            "events": int(len(events)),
                            "since_seq": int(last_seq),
                        },
                    )
                    if events:
                        batch_types: dict[str, int] = {}
                        batch_text_chars = 0
                        for evt in events:
                            last_seq = int(evt.seq or 0)
                            payload = {
                                "turn_id": str(turn.id),
                                "seq": last_seq,
                                "type": evt.type,
                                "payload": _safe_canonicalize_turn_event(evt.type, evt.payload or {}),
                            }
                            event_type = str(evt.type or "").strip() or "event"
                            if event_type.strip().lower() == "text_delta":
                                # Block-only portal stream contract: ignore legacy raw text events.
                                continue
                            batch_types[event_type] = batch_types.get(event_type, 0) + 1
                            if str(evt.type or "").strip().lower() == "turn_persisted":
                                sent_turn_persisted = True
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
                            yield f"id: {last_seq}\n"
                            yield "event: turnEvent\n"
                            yield f"data: {json.dumps(payload)}\n\n"
                        trace.record(
                            "sse.batch",
                            {
                                "conn": conn_id,
                                "source": "db",
                                "events": int(len(events)),
                                "types": batch_types,
                                "text_chars": int(batch_text_chars),
                                "last_seq": int(last_seq),
                            },
                        )
                        continue

                with tenant_context(business_id):
                    latest = (
                        PortalTurn.objects.filter(id=turn.id)
                        .values_list("status", "last_event_seq")
                        .first()
                    )
                if latest:
                    latest_status, latest_seq = latest
                    if redis_conn is not None and redis_stream_key:
                        # Redis is a live bus; Postgres remains source-of-truth for replay.
                        # If Redis missed/trimmed events, backfill from Postgres.
                        if turn_log_mode == "db" and int(latest_seq or 0) > last_seq:
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
                                    if first_event_at is None:
                                        first_event_at = time.perf_counter()
                                    events_sent += 1
                                    yield f"id: {last_seq}\n"
                                    yield "event: turnEvent\n"
                                    yield f"data: {json.dumps(payload)}\n\n"
                                redis_last_id = f"{last_seq}-0"
                                continue
                    if latest_status in {PortalTurnStatus.FINALIZED, PortalTurnStatus.FAILED, PortalTurnStatus.CANCELLED}:
                        if redis_conn is not None and redis_stream_key:
                            drained = False
                            # Drain any remaining Redis entries (non-blocking) before closing.
                            drained_total = 0
                            while True:
                                try:
                                    # Redis Streams: BLOCK 0 means "block forever". Omit BLOCK for a truly non-blocking drain.
                                    drain_entries = redis_conn.xread({redis_stream_key: redis_last_id}, count=250)
                                except Exception:
                                    drain_entries = []
                                if not drain_entries:
                                    break
                                drained = True
                                drain_batch = 0
                                for _stream_key, stream_entries in drain_entries:
                                    for entry_id, fields in stream_entries:
                                        drain_batch += 1
                                        entry_id_str = (
                                            entry_id.decode("utf-8", errors="replace")
                                            if isinstance(entry_id, (bytes, bytearray))
                                            else str(entry_id)
                                        )
                                        redis_last_id = entry_id_str
                                        seq_value = None
                                        try:
                                            seq_value = int(entry_id_str.split("-", 1)[0])
                                        except Exception:
                                            seq_value = None

                                        raw_type = fields.get(b"type") if isinstance(fields, dict) else None
                                        if raw_type is None and isinstance(fields, dict):
                                            raw_type = fields.get("type")  # type: ignore[index]
                                        event_type = (
                                            raw_type.decode("utf-8", errors="replace")
                                            if isinstance(raw_type, (bytes, bytearray))
                                            else str(raw_type or "")
                                        ).strip() or "event"

                                        raw_payload = fields.get(b"payload") if isinstance(fields, dict) else None
                                        if raw_payload is None and isinstance(fields, dict):
                                            raw_payload = fields.get("payload")  # type: ignore[index]
                                        payload_text = (
                                            raw_payload.decode("utf-8", errors="replace")
                                            if isinstance(raw_payload, (bytes, bytearray))
                                            else str(raw_payload or "")
                                        )
                                        try:
                                            payload_obj = json.loads(payload_text) if payload_text else {}
                                        except Exception:
                                            payload_obj = {}
                                        # Events from Redis are already canonicalized by _finalize_turn.

                                        if seq_value is None:
                                            seq_value = int(payload_obj.get("seq") or 0) if isinstance(payload_obj, dict) else 0
                                        last_seq = max(last_seq, int(seq_value or 0))

                                        payload = {
                                            "turn_id": str(turn.id),
                                            "seq": int(seq_value or 0),
                                            "type": event_type,
                                            "payload": payload_obj or {},
                                        }
                                        if event_type.strip().lower() == "text_delta":
                                            # Block-only portal stream contract: ignore legacy raw text events.
                                            continue
                                        if event_type.strip().lower() == "turn_persisted":
                                            sent_turn_persisted = True
                                        if first_event_at is None:
                                            first_event_at = time.perf_counter()
                                        events_sent += 1
                                        yield f"id: {payload['seq']}\n"
                                        yield "event: turnEvent\n"
                                        yield f"data: {json.dumps(payload)}\n\n"
                                drained_total += drain_batch
                                trace.record(
                                    "sse.drain_batch",
                                    {
                                        "conn": conn_id,
                                        "events": int(drain_batch),
                                        "drained_total": int(drained_total),
                                        "last_seq": int(last_seq),
                                    },
                                )
                            # If we drained at least once, loop back to status check to avoid a tight close/open race.
                            if drained:
                                continue
                            break
                        else:
                            # Degraded mode: when turn events aren't persisted to Postgres (Phase 5+),
                            # still deliver the final persisted assistant message once it's available.
                            if not sent_turn_persisted and turn_log_mode in {"minimal", "off"} and latest_status == PortalTurnStatus.FINALIZED:
                                with tenant_context(business_id):
                                    message_id = (
                                        PortalTurn.objects.filter(id=turn.id)
                                        .values_list("message_id", flat=True)
                                        .first()
                                    )
                                    msg = ConversationMessage.objects.filter(id=message_id).first() if message_id else None
                                if msg is not None:
                                    last_seq += 1
                                    payload = {
                                        "turn_id": str(turn.id),
                                        "seq": int(last_seq),
                                        "type": "turn_persisted",
                                        "payload": {
                                            "text": msg.body or "",
                                            "message_id": str(msg.id),
                                            "session_status": None,
                                            "metadata_version": 1,
                                            "content_blocks": _safe_canonicalize_blocks(
                                                body=msg.body or "",
                                                blocks=msg.content_blocks or [],
                                            ),
                                        },
                                    }
                                    sent_turn_persisted = True
                                    if first_event_at is None:
                                        first_event_at = time.perf_counter()
                                    events_sent += 1
                                    yield f"id: {payload['seq']}\n"
                                    yield "event: turnEvent\n"
                                    yield f"data: {json.dumps(payload)}\n\n"
                                    break
                            if (
                                latest_status == PortalTurnStatus.FINALIZED
                                and not sent_turn_persisted
                                and turn_log_mode in {"minimal", "off"}
                            ):
                                # Final message not visible yet; keep the stream alive and retry.
                                continue
                            if int(latest_seq or 0) <= last_seq:
                                break

                if listen_conn is not None:
                    now = time.monotonic()
                    timeout = max(0.0, keepalive_seconds - (now - last_keepalive))
                    try:
                        readable, _, _ = select.select([listen_conn], [], [], timeout)
                    except Exception:
                        readable = []
                    if not readable:
                        # Keep the SSE connection warm.
                        keepalives_sent += 1
                        yield ": keepalive\n\n"
                        last_keepalive = time.monotonic()
                        continue

                    try:
                        listen_conn.poll()
                    except Exception:
                        # On any LISTEN connection issue, fall back to keepalive pacing.
                        keepalives_sent += 1
                        yield ": keepalive\n\n"
                        last_keepalive = time.monotonic()
                        continue

                    matched = False
                    try:
                        while getattr(listen_conn, "notifies", None):
                            notify = listen_conn.notifies.pop(0)
                            raw = getattr(notify, "payload", "") or ""
                            try:
                                note = json.loads(raw) if raw else {}
                            except Exception:
                                note = {}
                            if str(note.get("turn_id") or "") == str(turn.id):
                                matched = True
                                break
                    except Exception:
                        matched = True
                    if matched:
                        continue
                    # Notification was for a different turn; keep waiting.
                    continue

                # Fallback: if LISTEN isn't available, yield periodic keepalives and re-check.
                now = time.monotonic()
                if now - last_keepalive >= keepalive_seconds:
                    keepalives_sent += 1
                    yield ": keepalive\n\n"
                    last_keepalive = now
                time.sleep(0.15)
        finally:
            trace.record(
                "sse.close",
                {
                    "conn": conn_id,
                    "elapsed_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
                    "events_sent": int(events_sent),
                    "keepalives_sent": int(keepalives_sent),
                    "event_bus": str(event_bus or "postgres"),
                    "final_seq": int(last_seq),
                },
            )
            trace.close()
            if listen_conn is not None:
                try:
                    listen_conn.close()
                except Exception:
                    pass
            if metrics_enabled:
                structured_log(
                    "portal",
                    "stream.turn_sse",
                    {
                        "turn_id": str(turn.id),
                        "conversation_id": str(getattr(conversation, "id", "") or ""),
                        "business_id": str(business_id or ""),
                        "elapsed_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
                        "first_event_ms": int(max(0.0, (first_event_at - started_at) * 1000.0)) if first_event_at else None,
                        "events_sent": int(events_sent),
                        "keepalives_sent": int(keepalives_sent),
                        "listen_enabled": bool(listen_enabled),
                        "event_bus": str(event_bus or "postgres"),
                        "since_seq": int(since or 0),
                        "final_seq": int(last_seq),
                    },
                )

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    response["X-Portal-Stream-Protocol-Version"] = str(getattr(settings, "PORTAL_STREAM_PROTOCOL_VERSION", 1))
    return response


@csrf_exempt
@require_POST
def portal_turn_cancel(request: HttpRequest, turn_id: uuid.UUID) -> JsonResponse:
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
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)

    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.filter(id=turn_id, conversation_id=conversation.id).first()
        if not turn:
            return _json_error("not_found", "Turn not found.", status=404)
        if turn.status in {PortalTurnStatus.FINALIZED, PortalTurnStatus.FAILED, PortalTurnStatus.CANCELLED}:
            return JsonResponse(
                {
                    "turn": _portal_turn_to_dict(turn),
                    "cancelled": False,
                }
            )
        PortalTurn.objects.filter(id=turn.id).update(
            status=PortalTurnStatus.CANCELLED,
            updated_at=timezone.now(),
        )
        try:
            append_turn_event(
                turn_id=turn.id,
                event_type="turn_cancelled",
                payload={"turn_id": str(turn.id)},
            )
        except Exception:  # pragma: no cover - best effort only
            logger.exception("portal turn cancel event failed turn=%s", turn.id)

    return JsonResponse(
        {
            "turn": _portal_turn_to_dict(turn),
            "cancelled": True,
        }
    )
