"""
Prompt scaffolding for the MCP orchestrator.

Phase one intentionally keeps these helpers simple so we can land the package
structure without influencing runtime behavior. Future phases will flesh out
the system prompt and transcript assembly logic.
"""

from __future__ import annotations

import textwrap
from typing import Iterable, Mapping

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationSender
from apps.services.ai_prompt_builder import PromptBuilder


def build_system_message(agent: AgentProfile) -> str:
    """
    Construct the MCP system prompt for the supplied agent profile.

    Reuses core rule blocks from the legacy prompt builder so business mandates
    stay synchronized across both orchestration strategies.
    """

    builder = PromptBuilder(agent)
    tool_section = textwrap.dedent(
        """
        ### Tool Usage Guidance
        - `search_knowledge`: Run when you need fresh snippets tied to the visitor's request. Prefer concise queries referencing identifiers or product names the visitor provided.
        - `read_document`: Use only for snippets still marked summary-only/preview or when you must cite precise numbers/examples not included in the snippet summary. Request the exact `document_id` returned by `search_knowledge`.
        - Case + lead tools: Mirror the Case Management Mandate. Only create/update cases when business context exists and keep payloads aligned with the contract.
        - Customer tools: Whenever a visitor shares phone/email, capture it immediately via `create_customer`. Use `update_customer` only when the visitor explicitly confirms a profile change.
        - Escalation: Call `flag_escalation` when policies prohibit action, a document is missing, or the visitor explicitly requests human follow-up.
        """
    ).strip()

    return textwrap.dedent(
        f"""
        You are {agent.name}, the {agent.role or "AI Customer Specialist"} for {{business_name}}.

        {builder.CASE_MANDATE}

        {builder.CONVERSATION_RULES}

        {builder.CUSTOMER_RULES}

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
        messages.append(
            {
                "role": role,
                "content": entry.body,
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

    messages: list[Mapping[str, object]] = [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_payload},
    ]
    return messages
