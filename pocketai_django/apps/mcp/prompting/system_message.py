from __future__ import annotations

import re
import textwrap
from typing import Mapping

from django.conf import settings

from pocketai.language import normalize_language_code

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import AgentProfile
from apps.agent_runs.models import AgentRun, AgentRunStatus
from apps.conversations.models import Conversation
from apps.mcp.prompting.agentic_prompts import (
    build_model_specific_prompt,
    get_tone_instruction,
)

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
    business_profile=None,
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
