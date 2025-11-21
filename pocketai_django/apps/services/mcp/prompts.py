"""
Prompt scaffolding for the MCP orchestrator.

Phase one intentionally keeps these helpers simple so we can land the package
structure without influencing runtime behavior. Future phases will flesh out
the system prompt and transcript assembly logic.
"""

from __future__ import annotations

import os
import textwrap
from typing import Iterable, Mapping

from django.conf import settings

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationSender
from apps.services.ai_prompt_builder import PromptBuilder
from apps.services.mcp.sanitizer import sanitize_text


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

    tool_section = textwrap.dedent(
        """
        ### Tool Usage Guidance
        - `search_knowledge`: Run when you need fresh snippets tied to the visitor's request. It returns abstracts + chunk IDs; follow with `read_document` if you still need details.
        - `read_document`: Prefer `mode=\"full_page\"` when you need exact numbers/tables; use `page` to stay focused. Use `mode=\"excerpt\"` only when the budget is low or you just need a quick skim. If a `throttle_notice` or `constraint_error` arrives, do not guess—continue with what you have or ask for a more precise identifier/page. When snippets are summary/preview or table-like, use the provided `read_hint` (doc_id + page + mode) to issue `read_document` once before answering. Do not answer from summaries alone.
        - Case + lead tools: Mirror the Case Management Mandate. Only create/update cases when business context exists and keep payloads aligned with the contract.
        - Customer tools: Whenever a visitor shares phone/email, capture it immediately via `create_customer`. Use `update_customer` only when the visitor explicitly confirms a profile change.
        - Escalation: Call `flag_escalation` when policies prohibit action, a document is missing, or the visitor explicitly requests human follow-up.
        """
    ).strip()

    return textwrap.dedent(
        f"""
        You are {agent.name}, the {agent.role or "AI Customer Specialist"} for {{business_name}}.

        ### Output Guardrails (Mandatory)
        - Do not narrate internal steps like searching, checking, or reviewing. Never output placeholders such as “I’ll check”, “Let me search”, or “Reviewing…”.
        - Tool calls and tool results are internal. When invoking tools, leave the assistant content empty; do not promise to search. Provide a real candidate answer only when ready to respond.
        {provider_suffix}

        ### Internal Knowledge Only
        - Use only the provided knowledge snippets and reads. If the knowledge base does not contain the answer, say so and ask for a more specific identifier/page instead of using outside or world knowledge.
        - Cite snippets when they inform your answer; do not invent facts beyond the snippets and reads available this turn.

        ### Knowledge + Coverage Rules
        - Treat snippets with `read_state=summary/preview` as incomplete; call `read_document` to get the actual content before citing numbers/tables.
        - When snippets are `read_state=ready/full`, answer directly—avoid extra reads unless the visitor asks for a different page or identifier.
        - Respect chunk budgets; prefer the narrowest page/chunk that answers the question. Avoid rereading documents that are already covered.
        - Tool responses may include `constraint_error` or `throttle_notice`; never fabricate. Continue with existing snippets or ask the visitor for a narrower doc/page/identifier.
        - Knowledge file names and labels are internal; do not expose them in the customer-facing reply.
        - Avoid investigative fillers or meta-status lines about searching or checking. Respond directly with the clearest answer or limitation you can based on the current snippets and reads, without narrating that you are searching, checking, or reviewing.

        {builder.CHUNK_READ_NUDGE}

        {builder.CUSTOMER_RULES}

        {builder.CASE_MANDATE}

        {builder.CONVERSATION_RULES}

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

    transcript = conversation.messages.order_by("sent_at", "created_at")
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

    system_sections.append(
        (
            "You must reply with JSON matching the schema provided via "
            "`response_format` (response_text/actions/extractions). "
            "Set response_text to an empty string or a brief summary; the "
            "frontend will use the already-streamed assistant answer."
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
) -> list[Mapping[str, object]]:
    """
    Construct the final-answer prompt used after tools have completed.

    Emphasizes: tools already run, respond directly without narrating searches,
    and ground the reply in provided snippet summaries/reads.
    """

    business_name = conversation.business_profile.name
    system_lines = [
        f"You are now drafting the final customer-facing answer for {business_name}.",
        "Tools have already been executed. Write the answer directly, grounded in the provided reads/snippets.",
        "Do not narrate internal steps such as searching, checking, or reviewing. Never output placeholders like “I’ll check” or “Let me search”.",
        "You may briefly attribute sources (e.g., “From the credit card fees guide…”), but do not mention that you searched or are checking.",
        "Formatting: start with the direct answer in 1–2 sentences. If you have next steps or clarifying questions, put them on a new line as short bullets. Separate sections with a blank line so the reply is easy to scan. Keep the total reply concise (under ~6 sentences).",
        "If information is missing, state that plainly first, then offer 1–2 specific follow-up questions.",
    ]
    system_message = "\n".join(system_lines)

    user_sections: list[str] = [
        f"Latest user message:\n{user_message.strip()}",
    ]
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
    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": payload},
    ]
