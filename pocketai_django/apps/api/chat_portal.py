from __future__ import annotations

from apps.conversations.portal_turn.events import get_portal_redis_client

from apps.api.portal_chat.activity_snapshots import (
    _append_agent_run_event,
    _build_portal_agent_requests_snapshot,
    _build_portal_agent_runs_snapshot,
    _create_portal_manual_automation_run,
    _is_runnable_automation,
    _run_snapshot_for_portal_run,
    _serialize_agent_request_for_portal,
    _serialize_agent_run_checkpoint_for_portal,
    _serialize_agent_run_event_for_portal,
    _serialize_agent_run_for_portal,
    _serialize_automation_for_portal,
)
from apps.api.portal_chat.activity_actions import (
    portal_agent_request_update,
    portal_agent_run_approval,
    portal_agent_run_checkpoint_resolve,
    portal_agent_run_user_input,
    portal_automation_manual_run,
)
from apps.api.portal_chat.serializers import (
    _agent_to_dict,
    _apply_portal_tool_approval_state,
    _bootstrap_to_dict,
    _business_to_dict,
    _canonicalize_portal_message_blocks,
    _canonicalize_turn_event_payload,
    _message_to_dict,
    _normalize_portal_content_blocks,
    _portal_turn_to_dict,
    _safe_canonicalize_blocks,
    _safe_canonicalize_turn_event,
    _serialize_tool_approval,
    _session_to_dict,
)
from apps.api.portal_chat.debug_tools import (
    _clip_debug_text,
    _json_debug_exact,
    _json_safe_debug,
    _portal_debug_tool_trace_enabled,
    _serialize_context_budget,
    _serialize_debug_tools_payload,
    _serialize_knowledge_result,
    _serialize_llm_usage,
    _serialize_tool_trace_entry,
)
from apps.api.portal_chat.email_drafts import (
    _clear_pending_email_draft_meta,
    _pending_email_account_id_for_draft,
)
from apps.api.portal_chat.email_endpoints import (
    portal_email_discard_draft,
    portal_email_send_draft,
)
from apps.api.portal_chat.conversation_endpoints import (
    conversation_messages,
    conversation_turns_create,
    conversations_collection,
    create_portal_session,
    list_portal_sessions,
    portal_turn_create,
)
from apps.api.portal_chat.planning import (
    LOW_INTENT_PATTERNS,
    LOW_INTENT_SIMPLE,
    STRUCTURED_KEYWORDS,
    _business_planner_override,
    _has_crm_signals,
    _is_low_intent_message,
    _planner_decision,
    _tool_activity_present,
)
from apps.api.portal_chat.request_context import (
    _attach_actor_user_id_if_authorized,
    _extract_conversation_id,
    _json_error,
    _normalize_portal_metadata,
    _parse_json_body,
    _request_ui_language,
    _require_authenticated_user,
    _resolve_request_conversation,
    _service,
    _session_summary_to_dict,
    _with_ui_language,
)
from apps.api.portal_chat.session_endpoints import (
    bootstrap_session,
    messages_endpoint,
    resolve_portal_handle,
    submit_csat,
    submit_feedback,
)
from apps.api.portal_chat.session_stream import events
from apps.api.portal_chat.streaming import (
    _open_portal_turn_listen_connection,
    _parse_session_since_id,
    _parse_turn_since_seq,
)
from apps.api.portal_chat.status_events import (
    CONTEXT_STATUS_CODES,
    TOOL_EVENT_PHASES,
    _enqueue_status_events,
    _queue_put,
)
from apps.api.portal_chat.tool_approvals import portal_tool_approval
from apps.api.portal_chat.tool_history import portal_tool_history
from apps.api.portal_chat import turn_stream as _turn_stream
from apps.api.portal_chat.turn_stream import (
    portal_turn_cancel,
)
from apps.api.portal_chat.tracing import PortalTraceLogger




# NOTE: Portal verification (OTP / verified lookup) was removed; this deployment runs as knowledge-RAG only.


def portal_turn_events(request, turn_id):
    _turn_stream._open_portal_turn_listen_connection = _open_portal_turn_listen_connection
    _turn_stream.get_portal_redis_client = get_portal_redis_client
    return _turn_stream.portal_turn_events(request, turn_id)
