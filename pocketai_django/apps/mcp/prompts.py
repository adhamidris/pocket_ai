from __future__ import annotations

from apps.accounts.feature_flags import FeatureFlagService
from apps.mcp.prompting import messages as _messages
from apps.mcp.prompting import system_message as _system_message
from apps.mcp.prompting.context_notes import (
    _agentic_task_resource_refs_note,
    _build_run_memory_context,
    _compacted_history_note,
    _conversation_files_note,
    _conversation_memory_note,
    _recent_search_refs_note,
)
from apps.mcp.prompting.messages import (
    AGENT_WORKFORCE_BACKGROUND_RUN_INSTRUCTIONS,
    MCP_GATEWAY_AGENTIC_RULES,
    PLACEHOLDER_REMINDER,
    PLANNER_CRM_RULES,
    PORTAL_SPINNER_HINT_INSTRUCTIONS,
    PREPLAN_OUTPUT_HINT,
    TRACER,
    VERIFICATION_OUTPUT_HINT,
    _ARABIC_CHAR_PATTERN,
    _build_runs_context_summary,
    _history_requires_tool_anchor,
    _selected_ui_language,
    _strip_incomplete_tool_chains,
    build_final_answer_messages,
    build_planner_messages,
    build_preplan_messages,
    build_verification_messages,
    limit_messages_for_stage,
)


def _with_bridge_feature_flags(func, *args, **kwargs):
    original = _system_message.FeatureFlagService
    _system_message.FeatureFlagService = FeatureFlagService
    try:
        return func(*args, **kwargs)
    finally:
        _system_message.FeatureFlagService = original


def build_system_message(*args, **kwargs):
    return _with_bridge_feature_flags(_messages.build_system_message, *args, **kwargs)


def build_messages(*args, **kwargs):
    return _with_bridge_feature_flags(_messages.build_messages, *args, **kwargs)
