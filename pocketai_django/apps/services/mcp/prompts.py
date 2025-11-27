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

from django.conf import settings

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationSender
from apps.services.ai_prompt_builder import PromptBuilder
from apps.services.mcp.identifier_registry import IdentifierGuardrail
from apps.services.mcp.sanitizer import sanitize_text
from apps.services import display_tone_label


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


def build_system_message(agent: AgentProfile) -> str:
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
        ### Behavior Contract
        - {tone_instruction}
        - No tool narration or fillers (never start with “I’ll…/Let me…/Searching…/Reviewing…”); leave assistant content empty during tool calls.
        - Use ONLY provided snippets/reads; no outside knowledge; no citations/attribution/file names.
        - Safety: for sensitive domains (health/finance/legal), share policy/process only; no personal advice.
        - Only ask for identifiers when the visitor requests an action that requires them (e.g., look up/update ticket/account/plan/billing). Skip asking on greetings or general FAQs. Ask once, only for the required key(s), in one short sentence.
        - When an email or other required identifier is present and the visitor asks to check a ticket/case/order, call `search_knowledge` immediately using that identifier before asking for any other details. Ask for extra identifiers only if the search is empty or ambiguous.
        - During tool calls, keep assistant content empty (or minimal status) and avoid emitting placeholders. If multiple tool calls occur in sequence, do not repeat statuses or placeholder phrases.
        - Queries can mix Arabic and English; include every spelling/phrase variant in the **first** `search_knowledge` call and avoid reissuing a search with the same intent unless the visitor adds new details.
        """
    ).strip().format(tone_instruction=tone_instruction, tone_label=tone_label)

    tool_section = textwrap.dedent(
        """
        ### Tool Usage Guidance
        - If snippets are summary/preview or table hints, call `read_document` once with the provided hint (doc_id + page + mode) before citing details.
        - If snippets are ready/full, answer directly—do not reread unless the visitor asks for a different page/id.
        - If you hit a throttle_notice or constraint_error, answer with the evidence you have and ask for the precise identifier/page you need; do not guess.
        - Prefer the narrowest scope: page/chunk reads before whole-document reads.
        - Table aggregation: `table_aggregate` returns deterministic row totals plus `rows[].contributions` (every numeric column/vendor). Call it whenever the visitor needs totals or asks who/which customers/regions contributed so you cite the complete list instead of truncated previews.
        - Multi-product/store requests (e.g., “how many units for these products in these areas”) must use `table_aggregate` first so you fetch all rows programmatically before issuing any `read_document`. Only fall back to manual reads if the table response is empty or lacks the needed columns.
        - Contributor lists: when the visitor says “all” (contributors/customers/regions/etc.), enumerate every entry from the latest `table_aggregate` snippet (including cached ones) with its value; do not summarize or cap the list unless they explicitly ask for highlights.
        - Case/lead/customer tools: follow the Case Management and Customer Identity rules; use `flag_escalation` when policy blocks action or a document is missing.
        - When a `search_knowledge` result marks `read_required`, immediately invoke `read_document` with the supplied hint instead of calling `search_knowledge` again; only rerun the search if the visitor supplies new constraints (different product, identifier, etc.).
        """
    ).strip()

    return textwrap.dedent(
        f"""
        You are {agent.name}, the {agent.role or "AI Customer Specialist"} for {{business_name}}. Maintain a {tone_label} tone aligned to the agent profile.

        {behavior_contract}

        ### Output Guardrails (Mandatory)
        - Do not narrate internal steps like searching, checking, or reviewing. Never output placeholders such as “I’ll check”, “Let me search”, or “Reviewing…”.
        - Tool calls and tool results are internal. When invoking tools, leave the assistant content empty; do not promise to search. Provide a real candidate answer only when ready to respond.
        {provider_suffix}

        ### Internal Knowledge Only
        - Use only the provided knowledge snippets and reads. If the knowledge base does not contain the answer, say so and ask for a more specific identifier/page instead of using outside or world knowledge.
        - Ground answers in the provided snippets/reads without exposing source names or file details; do not invent facts beyond what is available this turn.

        ### Knowledge + Coverage Rules
        - Treat snippets with `read_state=summary/preview` as incomplete; call `read_document` to get the actual content before citing numbers/tables.
        - When snippets are `read_state=ready/full`, answer directly—avoid extra reads unless the visitor asks for a different page or identifier.
        - Respect chunk budgets; prefer the narrowest page/chunk that answers the question. Avoid rereading documents that are already covered.
        - Tool responses may include `constraint_error` or `throttle_notice`; never fabricate. Continue with existing snippets or ask the visitor for a narrower doc/page/identifier.
        - Knowledge file names and labels are internal; do not expose them in the customer-facing reply.
        - Avoid investigative fillers or meta-status lines about searching or checking. Respond directly with the clearest answer or limitation you can based on the current snippets and reads, without narrating that you are searching, checking, or reviewing.

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
    messages: list[Mapping[str, object]] = []
    guard_summary = _identifier_requirements_note(conversation)
    if guard_summary:
        messages.append({"role": "system", "content": guard_summary})
    agent = conversation.agent_profile
    if agent:
        messages.append(
            {
                "role": "system",
                "content": build_system_message(agent).format(
                    business_name=conversation.business_profile.name,
                    business_industry=conversation.business_profile.industry or "general services",
                ),
            }
        )

    transcript = conversation.messages.order_by("-sent_at", "-created_at")[:8]
    transcript = reversed(transcript)
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
            "do not propose tools already executed this turn; respect coverage ledger readiness (no rereads for ready/full snippets). "
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
        "Tools have already been executed. Write the answer directly, grounded only in the provided reads/snippets—no outside knowledge and no citations/attribution.",
        "Do not narrate internal steps such as searching, checking, or reviewing. Never output fillers like “I’ll check”, “Let me search”, or “Reviewing…”.",
        "Use a human tone matching the agent profile; keep replies concise by default (2–3 sentences). If the visitor asks for more detail, expand briefly.",
        "Do not repeat sentences or restate the same fact within this reply. State each fact once; avoid double apologies.",
        "Formatting: start with the direct answer. If you have next steps or clarifying questions, put them on a new line as short bullets. Separate sections with a blank line.",
        "If information is missing, state that plainly first, then ask for the specific identifier/page/detail needed. Offer only follow-ups you can fulfill with current snippets/reads.",
        "If filtered knowledge does not match the provided identifiers, say so plainly and ask for the exact identifier/page needed. Do not answer from unfiltered or unmatched data.",
        "Only provide customer/account IDs or plan details when you have an exact match for the locked identifier value. If you cannot verify against the locked identifier, say the information is unavailable rather than guessing.",
        "When identifier guardrails are present, collect only the listed required identifiers. Do NOT ask for extra identifiers beyond those required. If the required identifiers are already provided, proceed without re-asking. If a different identifier is requested than the one locked for this session, politely refuse the switch and continue only with the locked identifier.",
        "Safety: for sensitive domains (health/finance/legal), share policy/process info only; do not provide personal advice or diagnostics.",
    ]
    system_message = "\n".join(system_lines)

    user_sections: list[str] = [
        f"Latest user message:\n{user_message.strip()}",
    ]

    guard_summary = _identifier_requirements_note(conversation)
    if guard_summary:
        user_sections.append(guard_summary)

    history = conversation.messages.order_by("-sent_at", "-created_at")[:6]
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
        for entry in coverage_ledger[:8]:
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
    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": payload},
    ]


def _identifier_requirements_note(conversation: Conversation) -> str | None:
    try:
        guard = IdentifierGuardrail.from_conversation(conversation)
    except Exception:
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
