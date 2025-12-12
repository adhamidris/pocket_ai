"""
Prompt scaffolding for the MCP orchestrator.

Phase one intentionally keeps these helpers simple so we can land the package
structure without influencing runtime behavior. Future phases will flesh out
the system prompt and transcript assembly logic.
"""

from __future__ import annotations

import json
import os
import textwrap
import uuid
from typing import Iterable, Mapping, Sequence

from opentelemetry import trace as otel_trace

from django.conf import settings

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationSender
from apps.services.ai_prompt_builder import PromptBuilder
from apps.services.mcp.identifier_registry import IdentifierGuardrail
from apps.services.mcp.sanitizer import sanitize_text
from apps.services import display_tone_label

TRACER = otel_trace.get_tracer(__name__)


PLACEHOLDER_REMINDER = (
    "Reminder: Each assistant turn may include only one short placeholder before the first tool call. After you acknowledge you're checking, every subsequent tool step must return tool_calls with empty content until you have the final visitor-facing answer. Never narrate internal steps between tools."
)


STAGE_HISTORY_DEFAULTS: Mapping[str, int] = {
    "initial_pass": 6,
    "tool_iteration": 6,
    "planner": 6,
}


TONE_STYLE_HINTS: Mapping[str, str] = {
    "friendly": "Keep a {tone_label} voice—warm, conversational, and encouraging. Adjust length naturally so detailed questions receive detailed answers.",
    "professional": "Use a {tone_label} tone: clear, confident, and thorough. Provide as much detail as the visitor needs, even if it takes multiple sentences.",
    "empathetic": "Maintain an {tone_label} tone that acknowledges the visitor's concerns before explaining facts or next steps with care.",
    "casual": "Stay {tone_label} with relaxed phrasing, contractions, and natural flow; mirror the visitor's energy while remaining factual.",
    "playful": "Adopt a {tone_label} tone with upbeat language, but keep policy and data accurate—fun but trustworthy.",
    "formal": "Use a {tone_label} tone with precise language and full sentences; deliver complete explanations without sounding stiff.",
}


def _tone_instruction(agent: AgentProfile | None) -> str:
    tone_key = (agent.tone or "").strip().lower() if agent and agent.tone else ""
    tone_label = display_tone_label(agent.tone) if agent else None
    resolved_label = tone_label or "friendly"
    hint = TONE_STYLE_HINTS.get(tone_key)
    if hint:
        return hint.format(tone_label=resolved_label)
    return (
        f"Maintain a {resolved_label} tone that matches the visitor's request—stay concise when they only need a quick fact, "
        "and expand fully when they ask for details or complete lists."
    )


def build_system_message(
    agent: AgentProfile,
    *,
    business_name: str | None = None,
    business_industry: str | None = None,
) -> str:
    """
    Construct the MCP system prompt for the supplied agent profile.

    Reuses core rule blocks from the legacy prompt builder so business mandates
    stay synchronized across both orchestration strategies. Adds MCP-specific
    guidance so the model handles excerpts vs full pages and budget notices.
    """

    builder = PromptBuilder(agent)
    provider_hint = (getattr(settings, "MCP_PROVIDER", None) or os.getenv("MCP_PROVIDER") or "").strip().lower()
    provider_suffix = ""
    if provider_hint == "deepseek":
        provider_suffix = (
            "\n- DeepSeek + tools: If you need to search or read, call tools only; "
            "do not narrate searching/checking in the assistant content."
        )

    tone_label = display_tone_label(agent.tone) or "friendly"
    tone_instruction = _tone_instruction(agent)
    behavior_contract = textwrap.dedent(
        """
        ### Guardrails
        - {tone_instruction}
        - Speak only when you have substance. During tool calls output nothing; no “checking/searching” narration.
        - You get at most one short placeholder per turn. After you’ve said you’re checking, every later tool turn must emit tool_calls only (empty assistant content) until you can deliver the final answer.
        - Do not narrate internal steps—keep every assistant sentence visitor-facing.
        - Start answering as soon as the evidence is enough. If snippets already cover the question, stop calling tools.
        - When tools finish, deliver the final visitor-facing answer in that same response instead of waiting for another pass.
        - Treat `search_knowledge` as expensive: per assistant turn you get one batched call; once it returns snippets you must stay on that evidence.

        ### Markdown Formatting Contract
        - Use clean, reader-friendly Markdown. For short single-fact answers, reply naturally without headings. Use level-2 headings (`##`) or bold labels only when there are multiple products/topics or the visitor explicitly asks for a structured breakdown.
        - Use a short bullet/numbered list only when there are one or two metrics to highlight; for three or more rows switch entirely to a Markdown table and skip repeating the same numbers in bullets or paragraphs.
        - When comparing more than two stores/products, emit a Markdown table with headers. Use only the data already returned by tools (especially `table_aggregate`)—never call `read_document` solely to improve formatting, and do not restate the exact table cells elsewhere in the answer.
        - When a table is required (three or more items), present the underlying numbers only once inside that table; skip serialised product-by-product paragraphs before it. If needed, follow the table with a brief “Key observations” paragraph instead of repeating the raw values.
        - Ensure all Markdown markers are balanced—never leave stray `**`, `_`, or ``` fences. If the model cannot format a section cleanly, fall back to plain text for that section only.
        ### Evidence Rules
        - Use only snippets/reads returned this turn. No outside knowledge, file names, or citations.
        - `read_required` is a hint, not a command. Table aggregates already count as full evidence.
        - Ask for identifiers only when an action absolutely needs them, and ask once. If an email/phone arrives for an action, call `create_customer` exactly once; skip it on greetings or FAQs.
        - Mixed-language queries are normal—include every spelling variant in the first search batch. Once you have snippets, move on instead of re-searching.
        - When you report derived numbers (totals, averages, percentages), compute them carefully from the evidence and sanity‑check that they add up before stating them.

        ### Safety
        - Policy-first responses for health/finance/legal topics—never offer personal advice.
        """
    ).strip().format(tone_instruction=tone_instruction)

    tool_section = textwrap.dedent(
        """
        ### Tool Playbook
        - `search_knowledge`
            • HARD LIMIT: Call at most once per assistant turn.
            • Put every alias/spelling in `queries[]` so the backend runs one batched search.
            • Only search again if the visitor adds a new constraint. If you have snippets, use them immediately.
        - `table_aggregate`
            • Call once per dimension set: include all requested products + store/region columns in the first call.
            • Reuse the same `document_id`. Repeat only if the visitor asks for a new metric or column set.
            • Answer directly from `rows[].contributions`; list every contributor returned.
            • Do NOT follow a successful table_aggregate with `read_document` purely to reformat or restate the same data.
        - `read_document`
            • Use only when a non-table snippet is summary/preview and you truly need the detail.
            • Never read just to satisfy a flag; table rows already satisfy reads.
        - `list_tables`
            • Use once to grab the spreadsheet `document_id` before aggregations; reuse it afterwards.
        - Tool loop cadence
            • Before the first retrieval you may acknowledge that you’re looking. After that, if another tool is required, return only the `tool_calls` payload with empty `content`. No additional narration or filler between tools.
        - CRM/case tools
            • Follow the Case Management Mandate. Use `flag_escalation` when policy blocks an action or identifiers are missing.
        - Errors/throttles
            • If a tool returns `constraint_error`/`throttle_notice`, answer with the evidence you have and request the exact identifier/page needed—do not guess.
        """
    ).strip()

    resolved_business_name = business_name or "your business"
    resolved_industry = business_industry or "general services"

    return textwrap.dedent(
        f"""
        You are {agent.name}, the {agent.role or "AI Customer Specialist"} for {resolved_business_name}. Maintain a {tone_label} tone aligned to the agent profile in the {resolved_industry} space.

        {behavior_contract}

        {provider_suffix}

        {builder.CHUNK_READ_NUDGE}

        {tool_section}
        """
    ).strip()


def build_messages(*, conversation: Conversation, user_message: str) -> list[Mapping[str, object]]:
    """
    Assemble the message history that will be sent to the MCP-ready provider.

    The structure should follow OpenAI/Anthropic tool-calling expectations:
    - First entry: system message
    - Historical transcript alternating between customer/assistant
    - Final entry: the latest user message
    """
    with TRACER.start_as_current_span("prompt.mcp.build_messages") as span:
        messages: list[Mapping[str, object]] = []
        guard_summary = _identifier_requirements_note(conversation)
        if guard_summary:
            messages.append({"role": "system", "content": guard_summary})
        agent = conversation.agent_profile
        if agent:
            business_profile = conversation.business_profile
            business_name = business_profile.name if business_profile else "your business"
            business_industry = business_profile.industry if business_profile and business_profile.industry else "general services"
            messages.append(
                {
                    "role": "system",
                    "content": build_system_message(
                        agent,
                        business_name=business_name,
                        business_industry=business_industry,
                    ),
                }
            )
            messages.append({"role": "system", "content": PLACEHOLDER_REMINDER})

        transcript_qs = conversation.messages.order_by("-sent_at", "-created_at")[:8]
        transcript = list(reversed(transcript_qs))
        for entry in transcript:
            role = "assistant" if entry.sender == ConversationSender.AI else "user"
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

        messages.append({"role": "user", "content": user_message})
        if span.is_recording():
            span.set_attribute("messages.total", len(messages))
            span.set_attribute("messages.transcript_count", len(transcript))
        return messages


def build_cached_table_messages(
    *, knowledge_results: Sequence[Mapping[str, object]], limit: int = 8
) -> list[Mapping[str, object]]:
    """
    Render hydrated table cache snippets as synthetic tool traffic so the provider
    can see deterministic contributor lists before planning tools.
    """

    if not knowledge_results:
        return []

    tool_calls: list[Mapping[str, object]] = []
    tool_messages: list[Mapping[str, object]] = []
    seen: set[str] = set()
    injected = 0
    for entry in knowledge_results:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("search_stage") != "table_cached":
            continue
        if injected >= limit:
            break
        snippet = dict(entry)
        snippet.setdefault("read_state", "full")
        snippet.setdefault("page_mode", "structured_table")
        snippet.setdefault("search_stage", "table_cached")
        snippet["suppress_in_prompt"] = False
        snippet_id = str(snippet.get("id") or f"{snippet.get('upload_id')}:{snippet.get('chunk_id')}")
        dedupe_key = f"{snippet.get('upload_id')}:{snippet.get('chunk_id')}:{snippet_id}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        call_id = f"cached_table_{uuid.uuid4().hex[:8]}"
        arguments_payload = {
            "cache_hit": True,
            "upload_id": snippet.get("upload_id"),
            "chunk_id": snippet.get("chunk_id"),
            "row_index": snippet.get("source_diagnostics", {}).get("table_row_index")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else None,
        }
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "table_aggregate",
                    "arguments": json.dumps({k: v for k, v in arguments_payload.items() if v is not None}, ensure_ascii=False),
                },
            }
        )
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "table_aggregate",
                "content": json.dumps(
                    {
                        "status": "ok",
                        "mode": "cached",
                        "snippets": [snippet],
                    },
                    ensure_ascii=False,
                ),
            }
        )
        injected += 1

    if not tool_calls:
        return []

    assistant_message = {
        "role": "assistant",
        "content": "",
        "tool_calls": tool_calls,
    }
    return [assistant_message, *tool_messages]


def build_planner_messages(
    *,
    conversation: Conversation,
    user_message: str,
    answer_text: str,
    tool_context_note: str | None = None,
    tool_trace: tuple[Mapping[str, object], ...] | None = None,
    coverage_ledger: tuple[Mapping[str, object], ...] | None = None,
) -> list[Mapping[str, object]]:
    """
    Build a lightweight planning prompt that asks the model to return
    structured JSON (response_text/actions/extractions) based on the latest
    exchange. The streamed `answer_text` is considered authoritative for the
    final response shown to the visitor; the planner focuses on actions and
    extractions only.
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
    if builder:
        system_sections.append(builder.CASE_MANDATE)
        system_sections.append(builder.CUSTOMER_RULES)

    guard_summary = _identifier_requirements_note(conversation)
    if guard_summary:
        system_sections.append(guard_summary)

    system_sections.append(
        (
            "You must reply with JSON matching the schema provided via "
            "`response_format` (response_text/actions/extractions). "
            "Set response_text to an empty string or a brief summary; the "
            "frontend will use the already-streamed assistant answer."
        )
    )

    system_sections.append(
        (
            "Planner guardrails: honor identifier gate status; do not request identifiers beyond the required set; "
            "do not propose tools already executed this turn; never suggest another `search_knowledge` call (the assistant already used its single batch); "
            "respect coverage ledger readiness (no rereads for ready/full snippets). "
            "Keep the reply strictly in JSON (response_text/actions/extractions) with no narration."
        )
    )

    system_message = "\n\n".join(section for section in system_sections if section).strip()

    user_payload = (
        "Use the latest user message and assistant answer below to decide "
        "what actions to take and what extractions to record.\n\n"
        f"Latest user message:\n{user_message.strip()}\n\n"
        f"Assistant final answer (already shown to the visitor):\n{answer_text.strip()}\n"
    )
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


def build_final_answer_messages(
    *,
    conversation: Conversation,
    user_message: str,
    tool_context_note: str | None = None,
    coverage_ledger: tuple[Mapping[str, object], ...] | None = None,
    tool_trace: tuple[Mapping[str, object], ...] | None = None,
    assistant_draft: Mapping[str, object] | None = None,
    identifier_filters: tuple[Mapping[str, object], ...] | None = None,
) -> list[Mapping[str, object]]:
    """
    Construct the final-answer prompt used after tools have completed.

    Emphasizes: tools already run, respond directly without narrating searches,
    and ground the reply in provided snippet summaries/reads.
    """

    business_name = conversation.business_profile.name
    system_lines = [
        f"You are now drafting the final customer-facing answer for {business_name}.",
        "Tools have already been executed this turn. Answer only from the provided reads/snippets—no outside knowledge and no citations or file names.",
        "Do not narrate internal steps or mention tools. Lead with the direct answer and default to 2–3 sentences unless the visitor explicitly wants more detail.",
        "If something is missing, state that first and ask only for the required identifier/page that is still missing, following the guardrails.",
        "Add short bullet next steps only when needed, otherwise end after the answer.",
        "Safety: share documented policy/process only; no personal advice or diagnostics for health/finance/legal topics.",
    ]
    system_message = "\n".join(system_lines)

    user_sections: list[str] = [
        f"Latest user message:\n{user_message.strip()}",
    ]

    guard_summary = _identifier_requirements_note(conversation)
    if guard_summary:
        user_sections.append(guard_summary)

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

    if identifier_filters:
        filters: list[str] = []
        gate_status_lines: list[str] = []
        for item in identifier_filters[:4]:
            required = item.get("required_keys") if isinstance(item, Mapping) else ()
            policy = item.get("match_policy") if isinstance(item, Mapping) else None
            status = item.get("status")
            provided = item.get("provided_keys") if isinstance(item, Mapping) else ()
            detail_parts: list[str] = []
            if required:
                detail_parts.append("required=" + ",".join(str(k) for k in required))
            if policy:
                detail_parts.append(f"policy={policy}")
            if status:
                detail_parts.append(f"status={status}")
            if detail_parts:
                filters.append(" | ".join(detail_parts))
            if status and required is not None:
                missing = sorted(set(required) - set(provided or []))
                gate_status_lines.append(
                    f"Identifier gate: status={status} required={','.join(required) or 'none'} provided={','.join(provided or []) or 'none'} missing={','.join(missing) or 'none'}"
                )
        if filters:
            user_sections.append("Identifier filters applied:\n" + "; ".join(filters))
        if gate_status_lines:
            user_sections.append("Identifier gating detail:\n" + "; ".join(gate_status_lines))

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


def _identifier_requirements_note(conversation: Conversation) -> str | None:
    try:
        guard = IdentifierGuardrail.from_conversation(conversation)
    except Exception:
        return None
    if not guard:
        return None
    snapshot = guard.requirements_snapshot()
    required = snapshot.get("required") or ()
    if not required:
        return None
    provided = snapshot.get("provided") or ()
    missing = snapshot.get("missing") or ()
    policy = snapshot.get("match_policy") or "or"
    locked = snapshot.get("locked_identifier") or {}
    missing_note = "Missing identifiers: " + (", ".join(missing) if missing else "none")
    if not missing:
        action_note = (
            "All required identifiers are present. Proceed without re-asking for identifiers. Session is locked to the first identifier value; do not switch values for that key. Other identifiers (phone/order/ticket) may be provided and used if available."
        )
    else:
        keys_text = ", ".join(missing or required)
        action_note = (
            "Ask ONLY for the missing required identifiers (no extras) and only when the visitor requests an action that requires them (e.g., look up/update ticket/account/plan). If the visitor is greeting or asking general FAQs, answer directly without asking for identifiers."
        )
    if locked.get("key") and locked.get("value"):
        action_note += (
            f"\nLocked identifier: {locked.get('key')}={locked.get('value')}. If the visitor provides a different value for this key, politely decline the switch and continue using the locked value only."
        )
    return (
        "Identifier guardrails (system-only):\n"
        f"Match policy: {policy.upper()}\n"
        f"Required identifiers: {', '.join(required)}\n"
        f"Provided identifiers: {', '.join(provided) or 'none'}\n"
        f"{missing_note}\n"
        f"{action_note}"
    )


def _history_requires_tool_anchor(entries: Sequence[Mapping[str, object]]) -> bool:
    """
    Detect whether any tool response in the trimmed history is missing its
    preceding assistant turn (LLM APIs require the assistant message that
    declared the tool call to appear immediately before the tool response).
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
                return True
    return False


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

    effective_limit = history_limit if history_limit is not None else STAGE_HISTORY_DEFAULTS.get(stage, 12)
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
    return [*system_entries, *trimmed_history]
