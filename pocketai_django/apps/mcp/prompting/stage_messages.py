from __future__ import annotations

import textwrap
from typing import Mapping, Sequence

from core.otel import otel_trace
from pocketai.language import normalize_language_code

from apps.conversations.models import Conversation, ConversationSender
from apps.mcp.text.sanitizer import sanitize_text


TRACER = otel_trace.get_tracer(__name__)


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
