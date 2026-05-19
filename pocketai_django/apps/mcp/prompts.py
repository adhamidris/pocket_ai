"""
Prompt scaffolding for the MCP orchestrator.

Phase one intentionally keeps these helpers simple so we can land the package
structure without influencing runtime behavior. Future phases will flesh out
the system prompt and transcript assembly logic.
"""

from __future__ import annotations

import re
import textwrap
import uuid
from typing import Iterable, Mapping, Sequence

from core.otel import otel_trace

from django.conf import settings

from pocketai.language import normalize_language_code

from apps.accounts.models import AgentProfile
from apps.agent_runs.models import AgentRun, AgentRunStatus
from apps.conversations.models import Conversation, ConversationSender
from apps.conversations.instruction_contracts import (
    active_custom_assistant_name,
    build_custom_assistant_instruction_note,
)
from apps.llm.ai_prompt_builder import PromptBuilder
from apps.mcp.sanitizer import sanitize_text
from apps.accounts.feature_flags import FeatureFlagService
from apps.mcp.schemas.agentic_prompts import (
    build_model_specific_prompt,
    get_tone_instruction,
)
from apps.mcp.prompt_context_notes import (
    _automation_resource_refs_note,
    _build_run_memory_context,
    _compacted_history_note,
    _conversation_files_note,
    _conversation_memory_note,
    _recent_search_refs_note,
)

TRACER = otel_trace.get_tracer(__name__)

PLACEHOLDER_REMINDER = (
    "After acknowledging you are checking, keep tool steps silent until the final answer."
)

# Portal UX: the model can provide a user-friendly spinner label for each tool call
# without adding extra tool calls or leaking narration into the chat.
PORTAL_SPINNER_HINT_INSTRUCTIONS = (
    "When calling tools, include `__ui.spinner_text` (short label) in tool arguments for portal display."
)

AGENT_WORKFORCE_BACKGROUND_RUN_INSTRUCTIONS = textwrap.dedent(
    """
    ---

    ## Background Runs (Agent Workforce)

    When the visitor asks for a long-running, multi-step, or operational task (multiple tools, multiple deliverables,
    or likely >1 minute), you MAY proactively delegate it to a background run so the chat stays responsive.

    **Creating runs:**
    - Use `start_agent_run(goal=..., title=..., success_criteria=[...], constraints={...}, plan={...})`.
    - Prefer spawning at most ONE background run per user turn, unless the visitor explicitly asks for multiple.
    - Do not delegate simple Q&A or small single-step tasks.
    - If key details are missing, ask the visitor first instead of starting the run.
    - After creating the run, tell the visitor what you started and that progress/results will appear in the Activity panel.

    **Checking run status:**
    - Use `list_agent_runs(status_filter="all"|"active"|"waiting"|"completed")` to see runs for this conversation.
    - Use `get_agent_run(run_id=..., include_events=true)` to get detailed status and results of a specific run.
    - When a visitor asks about task progress, check the runs instead of guessing.
    - If a run completed, you can summarize its results for the visitor.
    - If a run is waiting for approval or user input, let the visitor know what's needed.
    - If the system prompt already includes an "Active Background Runs" snapshot, treat it as current for this turn and do NOT call `list_agent_runs` unless the visitor explicitly asks for a refresh or you need more runs than shown.
    - If the visitor explicitly asks for a refresh, call `list_agent_runs(..., refresh=true)` to bypass caching.

    **Continuing existing runs:**
    - Use `continue_agent_run(run_id=..., message=...)` to send follow-up instructions to an existing run.
    - The background run will resume with its full execution conversation history.
    - Use this when: "now email that", "also do X", "send that to Y", "add more details".
    - Do NOT create a new run when you can continue an existing one.
    - Runs automatically have access to available tools. Starting further background runs from inside a run is blocked.

    **When to use each tool:**
    - `start_agent_run` → Brand new multi-step task with no prior context needed
    - `continue_agent_run` → Follow-up work on an existing task
    - `draft_task` → Persistent manual/scheduled/webhook/email-inbox task that should be saved for future runs
    - `request_task_activation` → Activate a drafted task only after the visitor explicitly approves it
    - `list_tasks` / `update_task` / `pause_task` → Manage saved tasks owned by agents
    - Direct tools (email_create_draft, etc.) → Simple one-shot actions you can do yourself

    **Saved automations:**
    - If the visitor asks to create a recurring, scheduled, webhook, or email-monitoring task, create a draft first.
    - Do not save a vague one-line task. A saved automation must contain a reusable `wake_up_prompt` that can run well in isolation later.
    - Build the draft from the current conversation context. If the visitor says "turn what we just did into an automation", extract the steps followed, tools used, decisions made, quality criteria, reporting style, stop/pause conditions, and what the automation must remember.
    - Infer safe/basic defaults when they are obvious. For monitors, default toward new/unread items, avoiding already-inspected items, using metadata/snippets before full reads, and notifying only on relevant findings.
    - Ask the visitor only for decisions that materially change execution, such as scope, notification behavior, risk/approval policy, or what counts as relevant. Do not ask trivia before drafting.
    - Before activation, show a plain-language draft preview with: automation name, when it runs, what it will do, how it will behave, and what it will remember.
    - When calling `draft_task`, include `wake_up_prompt`, `memory_instructions`, `draft_summary`, `workflow_type`, and `memory_shape`; include `clarification_questions` when important execution decisions remain unresolved.
    - Do not activate a persistent task silently. Summarize the owning assistant, trigger, draft behavior, memory behavior, and approval impact, then ask for explicit approval.
    - Custom Assistants own specialization. If the task needs a specialized assistant that is not already active in this conversation, ask the visitor which Custom Assistant should own it before drafting.

    **Example flow:**
    1. Visitor: "Research competitor pricing" → `start_agent_run(goal="Research...")`
    2. Run completes with research data
    3. Visitor: "Now email that to my boss" → `continue_agent_run(run_id=..., message="Email the research to boss@...")`
    4. Same background run continues, already has the research, just needs to email it
    """
).strip()

_ARABIC_CHAR_PATTERN = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def _selected_ui_language(conversation: Conversation) -> str:
    metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    if not isinstance(metadata, Mapping):
        return ""
    candidates = (
        metadata.get("ui_language"),
        metadata.get("uiLanguage"),
        metadata.get("selected_language"),
        metadata.get("selectedLanguage"),
    )
    for candidate in candidates:
        normalized = normalize_language_code(candidate)
        if normalized:
            return normalized
    return ""


def _build_runs_context_summary(conversation: Conversation, *, limit: int = 8) -> str | None:
    """
    Build a brief summary of active/recent runs for system context.

    Returns None if no relevant runs exist.
    """
    from core.tenancy import tenant_context

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        return None

    with tenant_context(business_id):
        runs = list(
            AgentRun.objects.filter(conversation_id=conversation.id)
            .exclude(status__in=[AgentRunStatus.CANCELLED])
            .order_by("-created_at")[:limit]
        )

    if not runs:
        return None

    # Group by status
    active_runs = [r for r in runs if r.status in {AgentRunStatus.QUEUED, AgentRunStatus.RUNNING}]
    waiting_runs = [r for r in runs if r.status in {AgentRunStatus.WAITING_USER, AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_EXTERNAL, AgentRunStatus.PAUSED}]
    completed_runs = [r for r in runs if r.status in {AgentRunStatus.COMPLETED, AgentRunStatus.FAILED}]

    lines: list[str] = ["## Active Background Runs"]

    if active_runs:
        lines.append(f"\n**Running ({len(active_runs)}):**")
        for run in active_runs[:3]:
            lines.append(f"- [{run.status}] {run.title or 'Untitled'} (id: {run.id})")

    if waiting_runs:
        lines.append(f"\n**Waiting ({len(waiting_runs)}):**")
        for run in waiting_runs[:3]:
            meta = run.metadata if isinstance(getattr(run, "metadata", None), dict) else {}
            wait_reason = ""
            if meta.get("pending_approval_id"):
                wait_reason = " - needs approval"
            elif meta.get("pending_user_input"):
                wait_reason = " - needs user input"
            lines.append(f"- [{run.status}] {run.title or 'Untitled'}{wait_reason} (id: {run.id})")

    if completed_runs:
        lines.append(f"\n**Recently Completed ({len(completed_runs)}):**")
        for run in completed_runs[:3]:
            result_payload = run.result if isinstance(getattr(run, "result", None), dict) else {}
            response_preview = str(result_payload.get("response_text") or "").strip()[:100]
            if response_preview:
                response_preview = f' - "{response_preview}..."' if len(response_preview) >= 100 else f' - "{response_preview}"'
            lines.append(f"- [{run.status}] {run.title or 'Untitled'}{response_preview} (id: {run.id})")

    if not active_runs and not waiting_runs and not completed_runs:
        return None

    lines.append("\nUse `list_agent_runs()` or `get_agent_run(run_id=...)` for details.")

    return "\n".join(lines)


STAGE_HISTORY_DEFAULTS: Mapping[str, int] = {
    "initial_pass": 6,
    "tool_iteration": 6,
    "planner": 6,
    "postflight": 6,
}

PREPLAN_OUTPUT_HINT = textwrap.dedent(
    """
    Return JSON inside response_text with this shape:
    {
      "route": "search" | "read" | "answer",
      "search_query": "short query if search is needed",
      "tools": ["search_knowledge", "read_knowledge"],
      "clarifying_question": "optional question if key info is missing",
      "notes": "short reasoning"
    }
    Use empty strings/arrays when not applicable.
    """
).strip()

VERIFICATION_OUTPUT_HINT = textwrap.dedent(
    """
    Return JSON inside response_text with this shape:
    {
      "verdict": "supported" | "needs_clarification" | "unsupported",
      "missing_points": ["short item", "..."],
      "final_response": "If not supported, provide ONE concise clarification question. Otherwise empty.",
      "notes": "short reasoning"
    }
    """
).strip()

PLANNER_CRM_RULES = textwrap.dedent(
    """
    ### CRM Capture Rules (MCP)
    - Legacy support-era CRM actions are retired from the active MCP tool surface.
    - Do not invent or request retired case/lead/customer tools.
    """
).strip()

MCP_GATEWAY_AGENTIC_RULES = textwrap.dedent(
    """
    External MCP tools:
    - Never invent tool names.
    - Use `mcp_search_tools(query=...)` to get a `tool_id`, then `mcp_call_tool(tool_id=..., arguments={...})`.
    """
).strip()


def build_system_message(
    agent: AgentProfile,
    *,
    business_name: str | None = None,
    business_industry: str | None = None,
    business_niches: str | None = None,
    provider_name: str | None = None,
    business_profile=None,  # Optional: for agentic mode feature flag check
    model_id: str | None = None,
    has_mcp_connections: bool = False,
    agent_name_override: str | None = None,
) -> str:
    """
    Construct the active MCP system prompt.

    The prompt stack is agentic-only: model-specific templates from
    `agentic_prompts.py` plus runtime-injected rules (tone + optional MCP gateway
    guidance). Legacy non-agentic fallback prompts have been removed.
    """

    del provider_name

    if business_profile is None:
        raise RuntimeError("build_system_message requires a business_profile for agentic prompt routing.")

    resolved_business_name = business_name or "your business"
    new_contract_enabled = bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
    feature_state = FeatureFlagService.snapshot(business_profile)
    rag_agentic_enabled = bool(getattr(feature_state, "rag_agentic_mode", False)) and new_contract_enabled
    if not rag_agentic_enabled:
        raise RuntimeError(
            "Legacy non-agentic MCP prompt path has been removed. "
            "Enable rag_agentic_mode and MCP_NEW_CONTRACT_ENABLED for this conversation."
        )

    rules: list[str] = []
    tone = get_tone_instruction(agent.tone) if hasattr(agent, "tone") and agent.tone else ""
    if tone:
        rules.append(tone.strip())
    if has_mcp_connections:
        rules.append(MCP_GATEWAY_AGENTIC_RULES)
    additional_rules_str = "\n\n".join(rule for rule in rules if rule)
    agentic_read_v2_enabled = bool(getattr(settings, "MCP_AGENTIC_READ_V2_ENABLED", False))
    if not agentic_read_v2_enabled:
        raise RuntimeError(
            "Agentic prompt routing requires MCP_AGENTIC_READ_V2_ENABLED=true."
        )
    return build_model_specific_prompt(
        agent,
        model_id=model_id,
        business_name=resolved_business_name,
        business_industry=business_industry,
        business_niches=business_niches,
        additional_rules=additional_rules_str,
        agent_name_override=agent_name_override,
    )




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

        automation_resource_refs_note = _automation_resource_refs_note(conversation)
        if automation_resource_refs_note:
            system_sections.append(automation_resource_refs_note.strip())

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

        # Determine if this is an execution conversation for a background run.
        # Use is_agent_run which is already computed above, or check metadata directly
        convo_meta_for_history = getattr(conversation, "metadata", None)
        convo_meta_for_history_map = convo_meta_for_history if isinstance(convo_meta_for_history, Mapping) else {}
        is_execution_conversation = bool(
            convo_meta_for_history_map.get("source") == "agent_run"
            or convo_meta_for_history_map.get("agent_run_id")
            or convo_meta_for_history_map.get("agentRunId")
        )

        # Execution conversations (agent workforce) get larger history to maintain context
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



def build_planner_messages(
    *,
    conversation: Conversation,
    user_message: str,
    answer_text: str,
    tool_context_note: str | None = None,
    tool_trace: tuple[Mapping[str, object], ...] | None = None,
    coverage_ledger: tuple[Mapping[str, object], ...] | None = None,
    evidence_note: str | None = None,
) -> list[Mapping[str, object]]:
    """
    Build a lightweight postflight prompt that asks the model to return
    structured JSON (response_text/actions/extractions) based on the latest
    exchange. The streamed `answer_text` is considered authoritative for the
    final response shown to the visitor; this pass focuses on:
      1) backend actions + structured extractions, and
      2) verification of whether the final answer is supported by evidence.

    The verification payload is returned as JSON inside `response_text`.
    """

    agent = conversation.agent_profile
    builder = PromptBuilder(agent) if agent else None
    business_name = conversation.business_profile.name

    system_sections: list[str] = []
    system_sections.append(
        f"You are an orchestration planner for {business_name}. "
        "Your job is to propose backend actions and structured extractions "
        "based on a customer conversation and the AI assistant's final reply."
    )
    # This project is now focused on knowledge-RAG (no customer record linking / CRM actions).
    # Keep postflight limited to verification + structured extraction only.

    system_sections.append(VERIFICATION_OUTPUT_HINT)
    system_sections.append(
        (
            "You must reply with ONLY valid JSON (no markdown, no extra text) in this exact format:\n"
            '{"response_text": "<your verification JSON as a string>", "actions": [...], "extractions": [...]}\n'
            "Put the verification JSON (verdict/missing_points/final_response/notes) inside response_text as a string value."
        )
    )

    system_sections.append(
        (
            "Postflight rules: do not propose tools already executed this turn; "
            "never suggest another `search_knowledge` call (this user turn already used its single batch search); "
            "respect coverage ledger readiness (no rereads for ready/full snippets). "
            "If no backend action is needed, return actions/extractions as empty arrays. Keep reply strictly JSON."
        )
    )

    system_message = "\n\n".join(section for section in system_sections if section).strip()

    user_payload = (
        "Use the latest user message and assistant answer below to decide "
        "what actions to take, what extractions to record, and whether the answer is supported.\n\n"
        f"Latest user message:\n{user_message.strip()}\n\n"
        f"Assistant final answer (already shown to the visitor):\n{answer_text.strip()}\n"
    )
    evidence_block = evidence_note.strip() if isinstance(evidence_note, str) and evidence_note.strip() else "None"
    user_payload = f"{user_payload}\nEvidence summary:\n{evidence_block}\n"
    if tool_context_note:
        user_payload = f"{user_payload}\nTool diagnostics this turn:\n{tool_context_note.strip()}\n"

    trace_block = ""
    if tool_trace:
        parts: list[str] = []
        for entry in tool_trace[:10]:
            name = entry.get("tool")
            status = entry.get("status")
            mode = entry.get("mode")
            page = entry.get("page")
            err = entry.get("error_code")
            if not name:
                continue
            detail: list[str] = [str(name)]
            if mode:
                detail.append(f"mode={mode}")
            if page:
                detail.append(f"page={page}")
            if status:
                detail.append(f"status={status}")
            if err:
                detail.append(f"error={err}")
            parts.append(", ".join(detail))
        if parts:
            trace_block = "Recent tools: " + " | ".join(parts)

    coverage_block = ""
    if coverage_ledger:
        items: list[str] = []
        for entry in coverage_ledger[:8]:
            if entry.get("suppress_in_prompt"):
                continue
            label = entry.get("title") or entry.get("label") or "Knowledge"
            state = entry.get("read_state") or "summary"
            topics = entry.get("coverage") if isinstance(entry.get("coverage"), (list, tuple)) else ()
            topic_text = ", ".join(topics[:2]) if topics else ""
            item_parts = [str(label), f"state={state}"]
            if topic_text:
                item_parts.append(f"topics={topic_text}")
            items.append(" ".join(item_parts))
        if items:
            coverage_block = "Knowledge ledger: " + " | ".join(items)

    context_lines = "\n".join([line for line in (tool_context_note, trace_block, coverage_block) if line])
    if context_lines:
        user_payload = f"{user_payload}\nTooling context:\n{context_lines}\n"

    messages: list[Mapping[str, object]] = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_payload},
    ]
    return messages


def build_preplan_messages(
    *,
    conversation: Conversation,
    user_message: str,
    recent_history: Sequence[Mapping[str, object]] | None = None,
) -> list[Mapping[str, object]]:
    """
    Build a lightweight routing prompt that proposes a minimal tool plan.

    The model returns JSON (inside response_text) describing the recommended
    first-step tools and a suggested search query, if needed.
    """

    business_name = conversation.business_profile.name
    system_sections = [
        (
            f"You are a routing planner for {business_name}. "
            "Decide the minimal first-step tool strategy for the latest user request."
        ),
        (
            "Never invent document IDs or data. If a specific document is required and no ID is known, "
            "route to search_knowledge first."
        ),
        (
            "If the request is about products, pricing, policies, eligibility, limits, or fees, route to search_knowledge."
        ),
        (
            "If the request is a follow-up that clearly refers to an already-known document or table, "
            "you may route to read_knowledge, but only if IDs are already available."
        ),
        PREPLAN_OUTPUT_HINT,
        (
            "You must reply with ONLY valid JSON (no markdown, no extra text) in this exact format:\n"
            '{"response_text": "<your JSON blob as a string>", "actions": [], "extractions": []}\n'
            "Put the preplan JSON inside response_text as a string value."
        ),
    ]
    system_message = "\n\n".join(section for section in system_sections if section).strip()

    history_lines: list[str] = []
    if recent_history:
        for entry in recent_history:
            role = str(entry.get("role") or "").strip()
            content = str(entry.get("content") or "").strip()
            if not role or not content:
                continue
            history_lines.append(f"{role}: {content}")
    history_block = "\n".join(history_lines[:6]).strip()

    user_payload = f"Latest user message:\n{user_message.strip()}\n"
    if history_block:
        user_payload = f"{user_payload}\nRecent context:\n{history_block}\n"

    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_payload.strip()},
    ]


def build_verification_messages(
    *,
    conversation: Conversation,
    user_message: str,
    draft_answer: str,
    evidence_note: str | None = None,
) -> list[Mapping[str, object]]:
    """
    Build a verification prompt that checks if the draft answer is supported
    by the available evidence.
    """

    business_name = conversation.business_profile.name
    system_sections = [
        (
            f"You are a verification agent for {business_name}. "
            "Check whether the draft answer is fully supported by the evidence."
        ),
        (
            "Only use the evidence provided. If support is insufficient, ask ONE concise clarifying question "
            "instead of guessing."
        ),
        VERIFICATION_OUTPUT_HINT,
        (
            "You must reply with ONLY valid JSON (no markdown, no extra text) in this exact format:\n"
            '{"response_text": "<your verdict JSON as a string>", "actions": [], "extractions": []}\n'
            "Put the verdict/missing_points/final_response/notes JSON inside response_text as a string value."
        ),
    ]
    system_message = "\n\n".join(section for section in system_sections if section).strip()

    evidence_block = evidence_note.strip() if isinstance(evidence_note, str) and evidence_note.strip() else "None"
    user_payload = textwrap.dedent(
        f"""
        User question:
        {user_message.strip()}

        Draft answer:
        {draft_answer.strip()}

        Evidence summary:
        {evidence_block}
        """
    ).strip()

    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_payload},
    ]


def build_final_answer_messages(
    *,
    conversation: Conversation,
    user_message: str,
    tool_context_note: str | None = None,
    coverage_ledger: tuple[Mapping[str, object], ...] | None = None,
    tool_trace: tuple[Mapping[str, object], ...] | None = None,
    assistant_draft: Mapping[str, object] | None = None,
) -> list[Mapping[str, object]]:
    """
    Construct the final-answer prompt used after tools have completed.

    Emphasizes: tools already run, respond directly without narrating searches,
    and ground the reply in provided snippet summaries/reads.
    """

    business_name = conversation.business_profile.name
    selected_ui_language = _selected_ui_language(conversation)
    if selected_ui_language.startswith("ar"):
        language_instruction = (
            "Language: The visitor selected Arabic in the UI. Reply ONLY in Modern Standard Arabic (MSA). "
            "Do not include English translations unless the visitor asks."
        )
    elif selected_ui_language.startswith("en"):
        language_instruction = (
            "Language: The visitor selected English in the UI. Reply ONLY in English. "
            "Do not include Arabic translations unless the visitor asks."
        )
    else:
        language_instruction = (
            "Language: Reply in the visitor's language. If the visitor writes in Arabic, respond in Modern Standard Arabic (MSA)."
        )

    system_lines = [
        f"You are now drafting the final customer-facing answer for {business_name}.",
        "Tools have already been executed this turn. Answer only from the provided reads/snippets—no outside knowledge and no document titles, IDs, or citations.",
        "Do not narrate internal steps or mention tools. Lead with the direct answer and keep replies concise. For multi-row structured output, prefer response_blocks (type=table/kv) instead of markdown tables.",
        "If something is missing, state that first and request only the missing information/page for the action. Do not ask clarifying questions for broad requests.",
        "Do not mention cases or leads. Offer human follow-up only if the visitor explicitly asks or has repeated/insisted, and request consent before stating that a follow-up will happen.",
        "Add short bullet next steps only when needed, otherwise end after the answer.",
        "Safety: share documented policy/process only; no personal advice or diagnostics for health/finance/legal topics.",
        language_instruction,
    ]
    system_message = "\n".join(system_lines)

    user_sections: list[str] = [
        f"Latest user message:\n{user_message.strip()}",
    ]

    history = conversation.messages.order_by("-sent_at", "-created_at")[:4]
    history_lines: list[str] = []
    for entry in reversed(history):
        prefix = "Customer" if entry.sender == ConversationSender.CUSTOMER else "Assistant"
        body = entry.body or ""
        if entry.sender == ConversationSender.AI:
            body = sanitize_text(body)
        history_lines.append(f"{prefix}: {body}")
    if history_lines:
        user_sections.append("Recent conversation:\n" + "\n".join(history_lines))

    if tool_context_note:
        user_sections.append(f"Tooling summary:\n{tool_context_note.strip()}")

    if coverage_ledger:
        items: list[str] = []
        for entry in coverage_ledger[:5]:
            if entry.get("suppress_in_prompt"):
                continue
            label = entry.get("title") or entry.get("label") or "Knowledge"
            state = entry.get("read_state") or "summary"
            topics = entry.get("coverage") if isinstance(entry.get("coverage"), (list, tuple)) else ()
            topic_text = ", ".join(topics[:2]) if topics else ""
            parts = [str(label), f"state={state}"]
            if topic_text:
                parts.append(f"topics={topic_text}")
            items.append(" ".join(parts))
        if items:
            user_sections.append("Coverage ledger:\n" + " | ".join(items))

    if tool_trace:
        tools_run: list[str] = []
        for entry in tool_trace[:10]:
            name = entry.get("tool")
            if not name:
                continue
            mode = entry.get("mode")
            page = entry.get("page")
            parts = [str(name)]
            if mode:
                parts.append(f"mode={mode}")
            if page:
                parts.append(f"page={page}")
            tools_run.append(" ".join(parts))
        if tools_run:
            user_sections.append("Tools executed this turn:\n" + "; ".join(tools_run))

    if assistant_draft:
        raw_draft = assistant_draft.get("content")
        draft_text = raw_draft if isinstance(raw_draft, str) else str(raw_draft or "")
        draft_text = draft_text.strip()
        if draft_text:
            user_sections.append(f"Assistant draft (internal, refine as needed):\n{draft_text}")

    payload = "\n\n".join(user_sections)
    with TRACER.start_as_current_span("prompt.mcp.build_final_answer") as span:
        if span.is_recording():
            span.set_attribute("prompt.length", len(payload))
            span.set_attribute("tool_trace.count", len(tool_trace or ()))
            span.set_attribute("coverage.count", len(coverage_ledger or ()))
        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": payload},
        ]


def _strip_incomplete_tool_chains(entries: list[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """
    Strip assistant messages with tool_calls that are missing their corresponding
    tool responses from the end of the message list.

    This handles the case where limit_messages_for_stage can't fix broken
    tool_call/response chains by going backwards (e.g., when tool responses
    simply don't exist in the message list).
    """
    if not entries:
        return entries

    # Work backwards from the end, tracking which tool_call_ids need responses
    # and removing incomplete chains
    result = list(entries)

    while result:
        # Check if the current messages have incomplete tool chains
        pending_ids: set[str] = set()
        for entry in result:
            role = entry.get("role")
            if role == "assistant":
                tool_calls = entry.get("tool_calls")
                if isinstance(tool_calls, Sequence):
                    for tool_call in tool_calls:
                        if isinstance(tool_call, Mapping):
                            tool_id = str(tool_call.get("id") or "").strip()
                            if tool_id:
                                pending_ids.add(tool_id)
            elif role == "tool":
                tool_call_id = str(entry.get("tool_call_id") or "").strip()
                pending_ids.discard(tool_call_id)

        if not pending_ids:
            # All tool_calls have responses, we're done
            break

        # Find and remove the last assistant message with incomplete tool_calls
        # and any trailing tool responses that belong to it
        removed_any = False
        for i in range(len(result) - 1, -1, -1):
            entry = result[i]
            if entry.get("role") == "assistant" and entry.get("tool_calls"):
                tool_calls = entry.get("tool_calls")
                if isinstance(tool_calls, Sequence):
                    has_incomplete = False
                    for tool_call in tool_calls:
                        if isinstance(tool_call, Mapping):
                            tool_id = str(tool_call.get("id") or "").strip()
                            if tool_id in pending_ids:
                                has_incomplete = True
                                break
                    if has_incomplete:
                        # Remove this assistant message and any following tool messages
                        result = result[:i]
                        removed_any = True
                        break

        if not removed_any:
            # Safety: avoid infinite loop if we can't make progress
            break

    return result


def _history_requires_tool_anchor(entries: Sequence[Mapping[str, object]]) -> bool:
    """
    Detect whether any tool response in the trimmed history is missing its
    preceding assistant message (LLM APIs require the assistant message that
    declared the tool call to appear immediately before the tool response).

    Also detect if any assistant message with tool_calls is missing its
    corresponding tool responses (OpenAI API requires all tool_calls to have
    matching tool response messages).
    """

    pending_ids: set[str] = set()
    for entry in entries:
        role = entry.get("role")
        if role == "assistant":
            tool_calls = entry.get("tool_calls")
            if isinstance(tool_calls, Sequence):
                for tool_call in tool_calls:
                    if not isinstance(tool_call, Mapping):
                        continue
                    tool_id = str(tool_call.get("id") or "").strip()
                    if tool_id:
                        pending_ids.add(tool_id)
        elif role == "tool":
            tool_call_id = str(entry.get("tool_call_id") or "").strip()
            if tool_call_id and tool_call_id not in pending_ids:
                # Tool response without its assistant message
                return True
            # Remove the tool_call_id from pending since we found its response
            pending_ids.discard(tool_call_id)

    # If there are still pending tool_call_ids, it means there are assistant
    # messages with tool_calls but their responses were cut off
    return len(pending_ids) > 0


def limit_messages_for_stage(
    messages: Sequence[Mapping[str, object]],
    *,
    stage: str,
    history_limit: int | None = None,
) -> list[Mapping[str, object]]:
    """
    Trim transcripts so each LLM call only receives the recent context it needs.

    System messages are preserved while non-system entries are windowed based on
    the provided history_limit (or a stage-specific default).
    """

    stage_key = (stage or "").strip().lower()
    default_limit = STAGE_HISTORY_DEFAULTS.get(stage_key, 12)
    override_setting = {
        "initial_pass": "MCP_STAGE_HISTORY_INITIAL_PASS",
        "tool_iteration": "MCP_STAGE_HISTORY_TOOL_ITERATION",
        "planner": "MCP_STAGE_HISTORY_PLANNER",
        "postflight": "MCP_STAGE_HISTORY_POSTFLIGHT",
    }.get(stage_key)
    if history_limit is not None:
        effective_limit = history_limit
    else:
        effective_limit = default_limit
        if override_setting:
            override_value = getattr(settings, override_setting, None)
            if override_value is not None:
                try:
                    effective_limit = int(override_value)
                except (TypeError, ValueError):
                    effective_limit = default_limit
    system_entries: list[Mapping[str, object]] = []
    other_entries: list[Mapping[str, object]] = []
    for entry in messages:
        role = entry.get("role")
        if role == "system":
            system_entries.append(entry)
        else:
            other_entries.append(entry)
    if not effective_limit or effective_limit <= 0:
        return [*system_entries, *other_entries]

    start_index = max(0, len(other_entries) - effective_limit)
    trimmed_history = other_entries[start_index:]
    while start_index > 0 and _history_requires_tool_anchor(trimmed_history):
        start_index -= 1
        trimmed_history = other_entries[start_index:]

    # If we've included all messages but still have broken tool_call/response
    # chains (e.g., assistant message with tool_calls but no responses),
    # strip incomplete tool call chains from the end.
    trimmed_history = _strip_incomplete_tool_chains(list(trimmed_history))

    last_user_entry = None
    for entry in reversed(other_entries):
        if entry.get("role") == "user" and isinstance(entry.get("content"), str) and entry.get("content", "").strip():
            last_user_entry = entry
            break
    if last_user_entry is not None and not any(entry is last_user_entry for entry in trimmed_history):
        trimmed_history = [last_user_entry, *trimmed_history]
    return [*system_entries, *trimmed_history]
