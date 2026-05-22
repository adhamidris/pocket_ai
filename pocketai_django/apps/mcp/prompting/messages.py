"""
Prompt scaffolding for the MCP orchestrator.

Phase one intentionally keeps these helpers simple so we can land the package
structure without influencing runtime behavior. Future phases will flesh out
the system prompt and transcript assembly logic.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Mapping, Sequence

from core.otel import otel_trace

from django.conf import settings

from apps.conversations.models import Conversation, ConversationSender
from apps.conversations.instruction_contracts import (
    active_custom_assistant_name,
    build_custom_assistant_instruction_note,
)
from apps.mcp.text.sanitizer import sanitize_text
from apps.mcp.prompting.history_window import (
    STAGE_HISTORY_DEFAULTS,
    _history_requires_tool_anchor,
    _strip_incomplete_tool_chains,
    limit_messages_for_stage,
)
from apps.mcp.prompting.context_notes import (
    _agentic_task_resource_refs_note,
    _build_run_memory_context,
    _compacted_history_note,
    _conversation_files_note,
    _conversation_memory_note,
    _recent_search_refs_note,
)
from apps.mcp.prompting.stage_messages import (
    PLANNER_CRM_RULES,
    PREPLAN_OUTPUT_HINT,
    VERIFICATION_OUTPUT_HINT,
    build_final_answer_messages,
    build_planner_messages,
    build_preplan_messages,
    build_verification_messages,
)
from apps.mcp.prompting.system_message import (
    AGENT_WORKFORCE_BACKGROUND_RUN_INSTRUCTIONS,
    MCP_GATEWAY_AGENTIC_RULES,
    PLACEHOLDER_REMINDER,
    PORTAL_SPINNER_HINT_INSTRUCTIONS,
    _ARABIC_CHAR_PATTERN,
    _build_runs_context_summary,
    _selected_ui_language,
    build_system_message,
)

TRACER = otel_trace.get_tracer(__name__)


def build_messages(
    *,
    conversation: Conversation,
    user_message: str,
    model_id: str | None = None,
    has_mcp_connections: bool = False,
) -> list[Mapping[str, object]]:
    """
    Assemble the message history that will be sent to the MCP-ready provider.

    The structure should follow OpenAI/Anthropic tool-calling expectations:
    - First entry: system message
    - Historical transcript alternating between customer/assistant
    - Final entry: the latest user message
    """
    with TRACER.start_as_current_span("prompt.mcp.build_messages") as span:
        # Single-system-message contract:
        # Build one system message that contains the constitution prompt plus any
        # runtime context notes (memory/files notes, language).
        system_sections: list[str] = []

        agent = conversation.agent_profile
        business_profile = conversation.business_profile
        active_custom_assistant = active_custom_assistant_name(conversation)
        if agent:
            business_name = business_profile.name if business_profile else "your business"
            business_industry = (
                business_profile.industry if business_profile and business_profile.industry else "general services"
            )
            
            # Extract niches from line_of_business JSON list
            niches_list = []
            if business_profile:
                raw_lob = getattr(business_profile, "line_of_business", [])
                if isinstance(raw_lob, list):
                    niches_list.extend([str(n) for n in raw_lob if n])
                custom_lob = getattr(business_profile, "line_of_business_custom", [])
                if isinstance(custom_lob, list):
                    niches_list.extend([str(n) for n in custom_lob if n])
            
            business_niches = ", ".join(niches_list) if niches_list else None

            system_sections.append(
                build_system_message(
                    agent,
                    business_name=business_name,
                    business_industry=business_industry,
                    business_niches=business_niches,
                    business_profile=business_profile,
                    model_id=model_id,
                    has_mcp_connections=has_mcp_connections,
                    agent_name_override=active_custom_assistant,
                ).strip()
            )
        else:
            system_sections.append("You are a helpful assistant.".strip())

        custom_assistant_note = build_custom_assistant_instruction_note(conversation)
        if custom_assistant_note:
            system_sections.append(custom_assistant_note.strip())

        memory_note = _conversation_memory_note(conversation)
        if memory_note:
            system_sections.append(memory_note.strip())

        convo_meta_for_run = getattr(conversation, "metadata", None)
        convo_meta_for_run_map = convo_meta_for_run if isinstance(convo_meta_for_run, Mapping) else {}
        run_id_raw = str(
            convo_meta_for_run_map.get("agent_run_id") or convo_meta_for_run_map.get("agentRunId") or ""
        ).strip()
        run_uuid: uuid.UUID | None = None
        if run_id_raw:
            try:
                run_uuid = uuid.UUID(run_id_raw)
            except (TypeError, ValueError):
                run_uuid = None
        convo_source_raw = str(convo_meta_for_run_map.get("source") or "").strip().lower()
        if run_uuid and (convo_source_raw == "agent_run" or run_id_raw):
            run_memory_note = _build_run_memory_context(run_id=run_uuid, business_profile=business_profile)
            if run_memory_note:
                system_sections.append(run_memory_note.strip())

        compacted_note = _compacted_history_note(conversation)
        if compacted_note:
            system_sections.append(compacted_note.strip())

        recent_search_refs_note = _recent_search_refs_note(conversation)
        if recent_search_refs_note:
            system_sections.append(recent_search_refs_note.strip())

        agentic_task_resource_refs_note = _agentic_task_resource_refs_note(conversation)
        if agentic_task_resource_refs_note:
            system_sections.append(agentic_task_resource_refs_note.strip())

        files_note = _conversation_files_note(conversation)
        if files_note:
            system_sections.append(files_note.strip())

        if agent:
            system_sections.append(PLACEHOLDER_REMINDER.strip())
            system_sections.append(PORTAL_SPINNER_HINT_INSTRUCTIONS.strip())
            convo_meta = getattr(conversation, "metadata", None)
            convo_meta_map = convo_meta if isinstance(convo_meta, Mapping) else {}
            convo_source = str(convo_meta_map.get("source") or "").strip().lower()
            actor_raw = str(convo_meta_map.get("actor_user_id") or convo_meta_map.get("actorUserId") or "").strip()
            try:
                actor_uuid = uuid.UUID(actor_raw) if actor_raw else None
            except (TypeError, ValueError):
                actor_uuid = None

            is_agent_run = bool(convo_source == "agent_run" or convo_meta_map.get("agent_run_id") or convo_meta_map.get("agentRunId"))
            owner_id = getattr(business_profile, "user_id", None) if business_profile else None
            agent_user_id = getattr(agent, "user_id", None) if agent else None
            actor_allowed = bool(actor_uuid and actor_uuid in {owner_id, agent_user_id})

            if actor_allowed and not is_agent_run:
                system_sections.append(AGENT_WORKFORCE_BACKGROUND_RUN_INSTRUCTIONS.strip())
                # Inject active runs context so the LLM knows about existing tasks
                runs_context = _build_runs_context_summary(conversation)
                if runs_context:
                    system_sections.append(runs_context)

        normalized_user_message = (user_message or "").strip()
        selected_ui_language = _selected_ui_language(conversation)
        if selected_ui_language.startswith("ar"):
            system_sections.append(
                (
                    "Language enforcement: The visitor selected Arabic in the UI. Reply ONLY in Modern Standard Arabic (MSA). "
                    "Do not include English translations unless the visitor asks."
                ).strip()
            )
        elif selected_ui_language.startswith("en"):
            system_sections.append(
                (
                    "Language enforcement: The visitor selected English in the UI. Reply ONLY in English. "
                    "Do not include Arabic translations unless the visitor asks."
                ).strip()
            )
        elif normalized_user_message:
            if _ARABIC_CHAR_PATTERN.search(normalized_user_message):
                system_sections.append(
                    (
                        "Language enforcement: The visitor is writing in Arabic. Reply ONLY in Modern Standard Arabic (MSA). "
                        "Do not include English translations unless the visitor asks."
                    ).strip()
                )
            else:
                system_sections.append(
                    (
                        "Language enforcement: The visitor is writing in English. Reply ONLY in English. "
                        "Do not include Arabic translations unless the visitor asks."
                    ).strip()
                )

        system_message = "\n\n".join(section for section in system_sections if section).strip()

        messages: list[Mapping[str, object]] = [{"role": "system", "content": system_message}]

        # Determine if this is an execution conversation for a sub-agent run.
        # Use is_agent_run which is already computed above, or check metadata directly
        convo_meta_for_history = getattr(conversation, "metadata", None)
        convo_meta_for_history_map = convo_meta_for_history if isinstance(convo_meta_for_history, Mapping) else {}
        is_execution_conversation = bool(
            convo_meta_for_history_map.get("source") == "agent_run"
            or convo_meta_for_history_map.get("agent_run_id")
            or convo_meta_for_history_map.get("agentRunId")
        )

        # Execution conversations get larger history to maintain context
        # across multi-step tool executions and approval flows
        if is_execution_conversation:
            history_limit = int(getattr(settings, "MCP_EXECUTION_HISTORY_LIMIT", 30))
        elif getattr(settings, "MCP_LONG_CHAT_MEMORY_ENABLED", True) and memory_note:
            history_limit = max(1, int(getattr(settings, "MCP_MEMORY_RECENT_MESSAGES", 4) or 4))
        else:
            history_limit = 8

        transcript_qs = conversation.messages.order_by("-sent_at", "-created_at")[:history_limit]
        transcript = list(reversed(transcript_qs))

        # Import the content_blocks reconstruction function
        from apps.conversations.content_blocks import reconstruct_tool_messages_from_content_blocks

        for entry in transcript:
            role = "assistant" if entry.sender == ConversationSender.AI else "user"

            # For execution conversations, attempt to reconstruct tool messages from content_blocks
            # This preserves full tool call/result context that would otherwise be lost
            if is_execution_conversation and role == "assistant":
                entry_content_blocks = getattr(entry, "content_blocks", None)
                if entry_content_blocks:
                    tool_messages = reconstruct_tool_messages_from_content_blocks(
                        entry_content_blocks,
                        entry.body or "",
                    )
                    if tool_messages:
                        # Add reconstructed tool messages instead of just the body
                        messages.extend(tool_messages)
                        continue

            # Default behavior: use message body
            content = entry.body
            if role == "assistant":
                content = sanitize_text(content or "")
            messages.append(
                {
                    "role": role,
                    "content": content,
                    "name": entry.sender if entry.sender in {ConversationSender.AI, ConversationSender.CUSTOMER} else None,
                }
            )

        if normalized_user_message:
            last_entry = messages[-1] if messages else None
            if not (
                isinstance(last_entry, Mapping)
                and last_entry.get("role") == "user"
                and isinstance(last_entry.get("content"), str)
                and last_entry.get("content", "").strip() == normalized_user_message
            ):
                messages.append({"role": "user", "content": normalized_user_message})
        if span.is_recording():
            span.set_attribute("messages.total", len(messages))
            span.set_attribute("messages.transcript_count", len(transcript))
        return messages
