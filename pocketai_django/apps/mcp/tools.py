"""
Tool registry and dispatcher for MCP orchestration.

This module will eventually expose two public artifacts:
1. TOOL_DEFINITIONS – JSON schemas advertised to the LLM provider.
2. execute_tool(...) – server-side implementation of each tool call.

For phase one we only anchor the structure so future phases can iterate without
touching unrelated parts of the codebase.
"""

from __future__ import annotations

import logging
import uuid
from typing import Callable, Mapping

from django.conf import settings

from apps.accounts.feature_flags import FeatureFlagService
from apps.conversations.models import Conversation
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
    get_tool_definitions,
)
from .tool_handlers.gateway import _mcp_call_tool_handler, _mcp_search_tools_handler
from .tool_handlers.files import (
    _pdf_extract_pages_handler,
    _pdf_extract_text_handler,
    _pdf_generate_handler,
    _pdf_merge_handler,
    _read_conversation_file_handler,
    _search_conversation_files_handler,
)
from .tool_handlers.email import (
    _email_create_draft_handler,
    _email_get_message_handler,
    _email_get_thread_handler,
    _email_search_handler,
    _email_send_draft_handler,
)
from .tool_handlers.native_integration import (
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
from .tool_handlers.integration_catalog import (
    EMAIL_INTEGRATION_TOOL_REGISTRY,
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
from .tool_handlers.agent_run import (
    _continue_agent_run_handler,
    _get_agent_run_handler,
    _list_agent_runs_handler,
    _request_user_input_handler,
    _start_agent_run_handler,
)
from .tool_handlers.tasks import (
    _draft_agentic_task_handler,
    _list_tasks_handler,
    _pause_agentic_task_handler,
    _request_agentic_task_activation_handler,
    _update_agentic_task_handler,
)
from .tool_handlers.memory import (
    _forget_memory_handler,
    _save_memory_handler,
    _search_memory_handler,
)
from .tool_handlers.context_retrieval import _retrieve_earlier_context_handler
from .knowledge_search_tool import _knowledge_service, _portal_file_embedding_service, _search_knowledge_handler
from .knowledge_read_tool import _read_knowledge_handler
from .knowledge_read.engine import _agentic_read_v2_handler
from .knowledge_support.agentic_response import _convert_to_agentic_search_response
from .knowledge_support.search_fusion import _fuse_batched_search_runs
from .runtime.agentic_read_cursor import _verify_agentic_read_cursor_v2
from .runtime.budget_guidance import build_repeat_search_guidance, search_budget_exceeded_payload
from .runtime.search_cursor import _encode_search_cursor, _search_cursor_handle_cache_key


logger = logging.getLogger(__name__)


TOOL_DEFINITIONS = get_tool_definitions()



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


ToolHandler = Callable[[Mapping[str, object], Conversation, ToolExecutionContext], Mapping[str, object]]


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
    from apps.voice.tools.mcp import initiate_phone_call_tool as _initiate_phone_call_handler
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
    "draft_agentic_task": _draft_agentic_task_handler,
    "update_agentic_task": _update_agentic_task_handler,
    "request_agentic_task_activation": _request_agentic_task_activation_handler,
    "pause_agentic_task": _pause_agentic_task_handler,
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
