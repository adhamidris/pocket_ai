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
from functools import lru_cache
from typing import Any, Callable, Mapping, Sequence

from django.db import models

from apps.accounts.models import KnowledgeStatus, KnowledgeUpload, KnowledgeUploadChunk
from apps.conversations.models import Conversation
from apps.services.ai_orchestrator import (
    ActionType,
    AiOrchestratorService,
    KnowledgeSearchService,
)
from .types import ToolExecutionContext


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
    return payloads


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

    raw_limit = arguments.get("limit")
    limit: int | None
    try:
        limit = int(raw_limit) if raw_limit is not None else None
    except (TypeError, ValueError):
        limit = None

    service = _knowledge_service()
    result = service.search(
        business_profile=conversation.business_profile,
        query=query,
        limit=limit,
    )
    snippet_payloads = _serialize_snippets(result.snippets)
    for payload in snippet_payloads:
        context.add_knowledge_result(payload)

    return {
        "tool": "search_knowledge",
        "query": query,
        "limit": limit,
        "status": result.status,
        "diagnostics": dict(result.diagnostics or {}),
        "snippets": snippet_payloads,
    }


def _read_document_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    # Enforce per-turn chunk budget. Each read_document call counts as one unit.
    context.reserve_chunk_reads(1)

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
    service = _knowledge_service()

    # Decide whether this identifier refers to a chunk or an upload.
    # We prefer chunk-focused reads when possible.
    chunk_exists = KnowledgeUploadChunk.objects.filter(
        id=identifier,
        business_profile=business,
        upload__status=KnowledgeStatus.ACTIVE,
    ).exists()

    snippets: list[Any] = []
    if chunk_exists:
        snippets.extend(
            service.load_chunk_contents(
                business_profile=business,
                chunk_ids=[str(identifier)],
                neighbor=1,
            )
        )
    else:
        upload_exists = KnowledgeUpload.objects.filter(
            id=identifier,
            business_profile=business,
            status=KnowledgeStatus.ACTIVE,
        ).exists()
        if not upload_exists:
            return {
                "tool": "read_document",
                "status": "not_found",
                "error": "document not found for this business",
                "snippets": [],
            }
        snippets.extend(
            service.load_contents(
                business_profile=business,
                knowledge_ids=[str(identifier)],
            )
        )

    snippet_payloads = _serialize_snippets(snippets)
    knowledge_reads: list[dict[str, object]] = []
    for payload in snippet_payloads:
        context.add_knowledge_result(payload)
        read_entry = {
            "id": payload.get("id"),
            "label": payload.get("public_label") or payload.get("title") or "Knowledge",
        }
        knowledge_reads.append(read_entry)
        context.add_knowledge_read(read_entry)

    ingestion_warnings = _build_ingestion_warnings(snippet_payloads, knowledge_reads)
    for warning in ingestion_warnings:
        context.add_ingestion_warning(warning)

    return {
        "tool": "read_document",
        "document_id": document_id,
        "status": "ok",
        "snippets": snippet_payloads,
        "knowledge_reads": knowledge_reads,
        "ingestion_warnings": ingestion_warnings,
    }


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
