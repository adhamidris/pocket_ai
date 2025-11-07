from __future__ import annotations

import dataclasses
import textwrap
from typing import Mapping, Sequence

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationMessage


@dataclasses.dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str
    transcript: Sequence[Mapping[str, str]]
    knowledge_snippets: Sequence[Mapping[str, str]]
    actions_catalog: Sequence[Mapping[str, str]]
    agent_traits: Mapping[str, str]


class PromptBuilder:
    """
    Lightweight prompt builder that assembles structured context for the LLM.

    Designed so we can swap in richer prompt templating (YAML/JSON) when the
    final provider is ready without changing the orchestrator API surface.
    """

    def __init__(self, agent: AgentProfile) -> None:
        self.agent = agent

    CASE_MANDATE = textwrap.dedent(
        """
        ### Case Management Mandate
        - Every conversation session MUST have a case. If none is linked, you must propose a new case via the `create_case` action.
        - Case payloads require: `title`, `description`, `priority`, `ai_diagnosis`, `ai_actions_taken`, `ai_suggested_actions` (array), and `metadata.source="ai_orchestrator"`.
        - When a case already exists, either update its status (`update_case_status`) or enrich it with new diagnosis/actions.
        - If multiple independent customer intents are detected, summarise each in the assistant reply, but prioritise the highest impact intent when filling the primary case payload.
        - These requirements are internal to the agent. Do NOT mention creating/updating cases unless the visitor explicitly asks about case status.
        """
    ).strip()

    CONVERSATION_RULES = textwrap.dedent(
        """
        ### Conversation + Summarisation Rules
        - Identify whether the visitor raised multiple requests. If yes, summarise them separately in your reply and create follow-up actions (cases, leads, appointments) per request when enabled.
        - Always mention next steps and clarifications in the assistant reply so the customer knows what will happen.
        - Reference knowledge snippets explicitly when they helped decide an answer.
        - Keep internal workflows invisible. Do NOT mention cases, leads, CRM records, or internal notes unless the visitor explicitly asks for that information.
        """
    ).strip()

    ACTION_RULES = textwrap.dedent(
        """
        ### Action Output Contract
        - `actions[]` must align with the provided catalog. Each entry needs `action` and `payload`.
        - Use `create_case` when no case exists or when a new major topic is introduced.
        - Use `update_case_status` when the customer confirms resolution or closure.
        - Use `flag_escalation`, `create_customer`, `create_lead`, or `create_appointment` when the scenario demands it and the action is enabled.
        - `extractions[]` capture structured signals (lead, appointment, complaint, escalation) that need human follow-up.
        - These actions are internal—acknowledge outcomes to the visitor only when it helps them (e.g., “I’ve captured your appointment request”), never outline the workflow itself or mention the word “case” unless the visitor asked about it.
        - Emit the JSON keys in this exact order so streaming can highlight the reply text quickly: `response_text`, `actions`, then `extractions`.
        """
    ).strip()

    def build(
        self,
        *,
        conversation: Conversation,
        knowledge_snippets: Sequence[Mapping[str, str]],
        transcript: Sequence[ConversationMessage],
        actions_catalog: Sequence[Mapping[str, str]],
    ) -> PromptBundle:
        business = conversation.business_profile
        industry = (business.industry or "").strip() or "general services"
        agent_traits = {
            "role": self.agent.role or "AI Customer Specialist",
            "tone": self.agent.tone or "friendly",
            "business_name": business.name,
            "agent_name": self.agent.name,
            "business_industry": industry,
        }

        case_context = self._case_context(conversation)

        system_prompt = textwrap.dedent(
            f"""
            You are {self.agent.name}, the {agent_traits['role']} for {business.name}, a company in the {industry} industry. Maintain a {agent_traits['tone']} tone, stay factual, and never hallucinate policy or pricing.

            {self.CASE_MANDATE}

            {self.CONVERSATION_RULES}

            {self.ACTION_RULES}
            """
        ).strip()

        user_prompt = self._compose_user_prompt(
            case_context=case_context,
            knowledge_snippets=knowledge_snippets,
            actions_catalog=actions_catalog,
            transcript=transcript,
            business_industry=industry,
        )

        transcript_payload = [
            {
                "sender": message.sender,
                "content": message.body,
                "sent_at": message.sent_at.isoformat(),
            }
            for message in transcript
        ]

        knowledge_payload = [
            {
                "id": snippet.get("id"),
                "title": snippet.get("title"),
                "summary": snippet.get("summary"),
                "source": snippet.get("source"),
            }
            for snippet in knowledge_snippets
        ]

        return PromptBundle(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            transcript=transcript_payload,
            knowledge_snippets=knowledge_payload,
            actions_catalog=actions_catalog,
            agent_traits=agent_traits,
        )

    def _case_context(self, conversation: Conversation) -> str:
        if conversation.case:
            case = conversation.case
            status = case.status
            priority = case.priority
            title = case.title
            desc = (case.description or "")[:200]
            return textwrap.dedent(
                f"""
                Existing case:
                - Title: {title}
                - Status: {status}
                - Priority: {priority}
                - Summary: {desc}
                """
            ).strip()
        return "No case is attached to this session; you must propose a new case."

    def _compose_user_prompt(
        self,
        *,
        case_context: str,
        knowledge_snippets: Sequence[Mapping[str, str]],
        actions_catalog: Sequence[Mapping[str, str]],
        transcript: Sequence[ConversationMessage],
        business_industry: str,
    ) -> str:
        transcript_lines = []
        for message in transcript:
            sender = message.sender.upper()
            transcript_lines.append(f"- [{sender}] {message.body}")
        transcript_block = "\n".join(transcript_lines) or "(no prior messages)"

        knowledge_block = []
        for snippet in knowledge_snippets:
            knowledge_block.append(f"- {snippet.get('title')}: {snippet.get('summary')}")
        knowledge_block = "\n".join(knowledge_block) if knowledge_block else "- No knowledge snippets were retrieved"

        actions_block = []
        for action in actions_catalog:
            status = "ENABLED" if action.get("enabled") else "DISABLED"
            actions_block.append(f"- {action.get('key')}: {action.get('description')} ({status})")
        actions_block = "\n".join(actions_block)

        return textwrap.dedent(
            f"""
            ### Conversation Transcript
            {transcript_block}

            ### Case Context (internal reference only — do not mention in replies unless asked)
            {case_context}

            ### Business Context
            - Industry: {business_industry}

            ### Knowledge Snippets
            {knowledge_block}

            ### Available Actions
            {actions_block}

            ### Tasks
            1. Draft the assistant reply that confirms next steps and cites relevant knowledge.
            2. Decide which structured actions to take so the platform can persist cases, leads, appointments, or escalations.
            3. Always produce at least one `create_case` or `update_case_status` action so the conversation is tracked.
            """
        ).strip()
