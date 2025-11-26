"""
Tool registry and dispatcher for MCP orchestration.

This module will eventually expose two public artifacts:
1. TOOL_DEFINITIONS – JSON schemas advertised to the LLM provider.
2. execute_tool(...) – server-side implementation of each tool call.

For phase one we only anchor the structure so future phases can iterate without
touching unrelated parts of the codebase.
"""

from __future__ import annotations

import uuid
import re
from collections import Counter
from functools import lru_cache
import logging
import math
from typing import Any, Callable, Mapping, Sequence

from django.db import models

from apps.accounts.models import KnowledgeStatus, KnowledgeUpload, KnowledgeUploadChunk, KnowledgeUploadTableRow
from apps.conversations.models import Conversation
from apps.services.ai_orchestrator import (
    ActionType,
    AiOrchestratorService,
    KnowledgeSearchService,
)
from apps.services.rag_logging import structured_log
from apps.services.rag_logging import structured_log
from core.metrics import latency_monitor
from .identifier_registry import IdentifierGuardrail, IdentifierRegistryService
from .types import ToolExecutionContext


logger = logging.getLogger(__name__)


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


TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    _function_schema(
        name="search_knowledge",
        description="Search the knowledge base using a natural-language query.",
        properties={
            "query": {
                "type": "string",
                "description": "Visitor question or keywords to search for.",
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
        preview_items.append(
            {
                "label": payload.get("public_label") or payload.get("title") or payload.get("label"),
                "read_state": payload.get("read_state"),
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
    query = _coerce_str(arguments.get("query")).strip()
    if not query:
        return {
            "tool": "search_knowledge",
            "status": "error",
            "error": "query is required",
            "snippets": [],
        }

    intent_info = _query_intent(query)
    intent = intent_info.get("intent")
    normalized_query = query.lower()
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
    raw_limit = arguments.get("limit")
    limit: int | None
    try:
        limit = int(raw_limit) if raw_limit is not None else None
    except (TypeError, ValueError):
        limit = None

    # Tune limits based on intent to keep results targeted.
    if intent == "identifier":
        limit = min(limit or 5, 4)
    elif intent == "table":
        limit = min(8, max(limit or 5, 6))

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
    if guard and guard.provided_identifiers:
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

    result = service.search(
        business_profile=conversation.business_profile,
        query=query,
        limit=limit,
        identifier_filter=identifier_filter,
    )
    snippet_payloads = _serialize_snippets(result.snippets)
    # If locked identifier exists, drop snippets whose identifier hash/value does not match locked value.
    if locked_key and locked_value:
        locked_val_norm = str(locked_value).strip()
        filtered_snippets = []
        for p in snippet_payloads:
            identifiers = p.get("identifiers") if isinstance(p, Mapping) else None
            if identifiers and isinstance(identifiers, Mapping):
                candidate = identifiers.get(locked_key)
                if candidate and str(candidate).strip().lower() != locked_val_norm.lower():
                    continue
            filtered_snippets.append(p)
        snippet_payloads = filtered_snippets
    decision = None
    if guard:
        decision = guard.evaluate_snippets(snippet_payloads)
        _record_identifier_check(context, decision)
        if decision.status == "identifier_conflict":
            # Treat conflicts as missing/required identifiers; do not poison the session.
            return {
                "tool": "search_knowledge",
                "query": query,
                "limit": limit,
                "intent": intent,
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
            return {
                "tool": "search_knowledge",
                "query": query,
                "limit": limit,
                "intent": intent,
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
    if allowed_uploads is not None:
        snippet_payloads = [p for p in snippet_payloads if str(p.get("upload_id") or "") in allowed_uploads]
        if not snippet_payloads:
            _log_snippet_payloads(
                tool="search_knowledge",
                conversation=conversation,
                snippet_payloads=snippet_payloads,
                meta={"query": query, "intent": intent, "read_required": read_required, "filtered": True},
            )
            return {
                "tool": "search_knowledge",
                "query": query,
                "limit": limit,
                "intent": intent,
                "status": "ok",
                "snippets": [],
                "identifier_gate": decision.as_dict() if decision else None,
                "hint": "No records found for this identifier.",
            }
    read_required = False
    if intent in {"table", "identifier"}:
        if len(snippet_payloads) <= 2:
            read_required = True
        elif all((p.get("read_state") or "summary") in {"summary", "preview"} for p in snippet_payloads):
            read_required = True
    if intent == "table" and aggregation_query:
        read_required = True
    # Attach read hints for table/identifier paths so the model can issue a precise read.
    for payload in snippet_payloads:
        chunk_id = payload.get("chunk_id") or payload.get("id")
        upload_id = payload.get("upload_id")
        chunk_index = payload.get("chunk_index")
        hinted_page = (int(chunk_index) + 1) if isinstance(chunk_index, int) else None
        mode_hint = "full_page" if intent in {"table", "identifier"} else "excerpt"
        payload["read_hint"] = {
            "document_id": str(chunk_id or upload_id or ""),
            "page": hinted_page,
            "mode": mode_hint,
        }
        if read_required:
            payload["read_required"] = True
    for payload in snippet_payloads:
        context.add_knowledge_result(payload)
    if guard and decision and decision.status == "ok":
        applied_filter = decision.as_dict()
        applied_filter["tool"] = "search_knowledge"
        context.identifier_filters.append(applied_filter)
        IdentifierRegistryService.record_event(
            business_profile=conversation.business_profile,
            decision=decision,
            tool="search_knowledge",
            conversation=conversation,
            upload_ids=[str(p.get("upload_id") or "") for p in snippet_payloads if p.get("upload_id")],
        )
    elif guard and decision:
        IdentifierRegistryService.record_event(
            business_profile=conversation.business_profile,
            decision=decision,
            tool="search_knowledge",
            conversation=conversation,
            upload_ids=list(decision.blocked_uploads or ()),
        )

    metrics = _log_tool_metrics(
        tool="search_knowledge",
        conversation=conversation,
        snippets=snippet_payloads,
        extra={
            "status": result.status,
            "limit": limit,
            "query_length": len(query),
        },
    )
    context.reserve_characters(int(metrics.get("char_count", 0)))

    return {
        "tool": "search_knowledge",
        "query": query,
        "limit": limit,
        "intent": intent,
        "intent_signal": intent_info,
        "status": result.status,
        "diagnostics": dict(result.diagnostics or {}),
        "snippets": snippet_payloads,
        "hint": _search_hint(result.status, intent, snippet_payloads, result.diagnostics),
    }


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
            IdentifierRegistryService.record_event(
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

    # Enforce per-turn chunk budget. Each read_document call counts as one unit.
    context.reserve_chunk_reads(1)
    context.reserve_chunk_pages(1)

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
        IdentifierRegistryService.record_event(
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

    return {
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


def _table_aggregate_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context  # aggregation is read-only
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
    match_column = _normalize_column_name(match_column_input)
    match_value = _normalize_column_name(match_value_input)
    query_input = _coerce_str(arguments.get("query")).strip()
    query = _normalize_column_name(query_input)
    if not query and not match_column:
        query = ""
    try:
        table_index = int(arguments.get("table_order_index"))
    except (TypeError, ValueError):
        table_index = None
    sheet_name = _normalize_column_name(arguments.get("sheet_name"))
    sheet_name_input = _coerce_str(arguments.get("sheet_name")).strip()
    sheet_name = sheet_name_input.lower()
    try:
        row_limit = int(arguments.get("max_rows") or 50)
    except (TypeError, ValueError):
        row_limit = 50
    row_limit = max(1, min(200, row_limit))

    rows_qs = KnowledgeUploadTableRow.objects.filter(
        table__upload=upload,
        table__upload__business_profile=conversation.business_profile,
    ).select_related("table").prefetch_related("cells").order_by("table__order_index", "row_index")
    if table_index is not None:
        rows_qs = rows_qs.filter(table__order_index=table_index)
    elif sheet_name:
        rows_qs = rows_qs.filter(
            models.Q(table__title__iexact=sheet_name_input)
            | models.Q(table__section_heading__iexact=sheet_name_input)
            | models.Q(table__metadata__sheet_name__iexact=sheet_name_input)
        )

    total_value = 0.0
    matched_rows: list[dict[str, object]] = []
    evaluated_rows = 0

    for row in rows_qs:
        evaluated_rows += 1
        table = row.table
        if not table:
            continue
        cells = sorted(row.cells.all(), key=lambda c: c.column_index)
        column_map = {
            _normalize_column_name(cell.column_key or f"column_{cell.column_index + 1}"): cell
            for cell in cells
        }
        row_matches = True
        if match_column and match_value:
            candidate = column_map.get(match_column)
            candidate_value = _normalize_column_name(getattr(candidate, "raw_text", ""))
            if not candidate_value or match_value not in candidate_value:
                row_matches = False
        elif query:
            haystack_parts = [
                (row.raw_text or "").lower(),
                " ".join((cell.raw_text or "").lower() for cell in cells),
            ]
            if query not in " ".join(haystack_parts):
                row_matches = False
        if not row_matches:
            continue

        preview_cells: list[dict[str, object]] = []
        contributions: list[dict[str, object]] = []
        total_column_value: float | None = None
        total_column_display: str | None = None
        total_column_priority = -1
        for cell in cells:
            column_label = cell.column_key or f"column_{cell.column_index + 1}"
            cell_value = cell.raw_text or ""
            if len(preview_cells) < 12:
                preview_cells.append({"column": column_label, "value": cell_value})
            normalized_label = _normalize_column_name(column_label)
            numeric_candidate = _parse_numeric_value(cell_value)
            total_priority = _total_column_priority(column_label)
            is_total_col = total_priority > 0
            if numeric_candidate is not None:
                contributions.append(
                    {
                        "column": column_label,
                        "value": numeric_candidate,
                        "display": cell_value.strip() or _format_numeric_display(numeric_candidate),
                        "is_total_column": is_total_col,
                    }
                )
                if is_total_col:
                    if total_priority > total_column_priority:
                        total_column_value = numeric_candidate
                        total_column_display = cell_value.strip() or _format_numeric_display(numeric_candidate)
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
            candidate_text = candidate.raw_text if candidate else ""
            numeric_value = _parse_numeric_value(candidate_text)
            if numeric_value is None:
                continue
            display_value = _format_numeric_display(numeric_value, candidate_text)
        total_value += numeric_value or 0.0
        contributions_sorted = sorted(contributions, key=lambda entry: abs(entry["value"]), reverse=True)
        matched_rows.append(
            {
                "row_index": row.row_index,
                "table_order_index": table.order_index,
                "sheet_name": (table.metadata or {}).get("sheet_name") if isinstance(table.metadata, Mapping) else None,
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
            )
            for row in matched_rows
        ]
    structured_log(
        "mcp",
        "table.aggregate",
        {
            "document_id": str(upload.id),
            "mode": mode,
            "match_count": len(matched_rows),
            "evaluated_rows": evaluated_rows,
            "contribution_rows": sum(row.get("contribution_count", 0) for row in matched_rows),
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
        "value_column": value_column_input or None,
        "sheet_name": sheet_name_input or None,
        "match_count": len(matched_rows),
        "total": total_value if matched_rows else None,
        "display_total": _format_numeric_display(total_value) if matched_rows else None,
        "rows": matched_rows,
        "snippets": snippet_payloads,
        "hint": "No matching rows found." if not matched_rows else None,
    }


def _build_table_aggregate_snippet(
    *,
    upload: KnowledgeUpload,
    row: Mapping[str, object],
    query: str | None,
    match_column: str | None,
    match_value: str | None,
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
