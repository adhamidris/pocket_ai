from __future__ import annotations

import dataclasses
import textwrap
from datetime import datetime
from typing import Mapping, Sequence

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationMessage


@dataclasses.dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str
    transcript: Sequence[Mapping[str, str]]
    knowledge_snippets: Sequence[Mapping[str, object]]
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
        - Create a case ONLY when the visitor shares business-related context (orders, payments, account issues, etc.). Ignore pure greetings or chit-chat.
        - Once legitimate business context exists and no case is linked, you must propose a new case via the `create_case` action.
        - Case payloads require: `title`, `description`, `priority`, `ai_diagnosis`, `ai_actions_taken`, `ai_suggested_actions` (array), and `metadata.source="ai_orchestrator"`.
        - Keep `ai_diagnosis`, `ai_actions_taken`, and `ai_suggested_actions` up to date. If the visitor supplies information you previously requested (e.g., account type, product, order number), immediately revise these fields to reflect the new facts—never leave them in a “pending info” state once the detail is confirmed.
        - `ai_actions_taken` must summarize the concrete steps you have already performed (e.g., “Captured corporate account request and queued relationship manager follow-up”), not generic statements like “Collect info.”
        - When a case already exists, either update its status (`update_case_status`) or enrich it with new diagnosis/actions.
        - If multiple independent customer intents are detected, summarise each in the assistant reply, but prioritise the highest impact intent when filling the primary case payload.
        - Case descriptions should only change when a major clarification within the same underlying context proves the earlier summary wrong (e.g., the customer clarifies the account is for a business). Otherwise, capture developments via case history entries.
        - These requirements are internal to the agent. Do NOT mention creating/updating cases unless the visitor explicitly asks about case status.
        """
    ).strip()

    CONVERSATION_RULES = textwrap.dedent(
        """
        ### Conversation + Summarisation Rules
        - Identify whether the visitor raised multiple requests. If yes, summarise them separately in your reply and create follow-up actions (cases, leads, appointments) per request when enabled.
        - Always mention next steps and clarifications in the assistant reply so the customer knows what will happen.
        - Lead the conversation yourself—never promise that external employees, agents, or relationship managers will follow up later. Gather the needed details directly in chat and describe what you will do next.
        - Reference knowledge snippets explicitly when they helped decide an answer, and never invent policies or offers beyond the uploaded knowledge base.
        - When the knowledge base does not confirm a requested detail, state that it is not yet confirmed and ask the visitor if they would like to be transferred to a human call or continue the chat while you gather more information.
        - Keep internal workflows invisible. Do NOT mention cases, leads, CRM records, or internal notes unless the visitor explicitly asks for that information.
        - When a visitor asks about case status, only mention the latest status if it directly answers their question; otherwise keep the workflow behind the scenes.
        - Do not repeat the same acknowledgement or promise in consecutive replies. If you already confirmed a fact or said you would “pull up” a document, move forward with the new information instead of restating the earlier message.
        - When the visitor pivots to a different product variant (for example, another card tier or benefit), assume the relevant data is already loaded and move straight to the requested details. Never say “I’ll check” if you already have the figures—respond with the concrete fees, limits, or features immediately.
        - Structure replies with lightweight Markdown (headings for card names, bullet lists for fees/features, tables when comparing tiers) so the customer can scan the answer quickly without feeling like it’s raw prose.
        """
    ).strip()

    ACTION_RULES = textwrap.dedent(
        """
        ### Action Output Contract
        - `actions[]` must align with the provided catalog. Each entry needs `action` and `payload`.
        - Use `create_case` only when the visitor shares business context (issues with products, services, payments, etc.).
        - Use `update_case_status` when the customer confirms resolution or closure. Only use status values `open` or `closed` (synonyms mapped accordingly).
        - Use `update_case_details` when a clarification updates facts inside the already-established context (e.g., the customer now specifies it is a business account). Include `allow_description_overwrite=true` only for those major same-context corrections.
        - Use `add_case_history` to log important updates, milestones, or clarifications once a case exists; default to this for ongoing conversations and only change the description when a major same-context clarification is confirmed.
        - Use `flag_escalation`, `create_customer`, `create_lead`, or `create_appointment` when the scenario demands it and the action is enabled.
        - Use `read_knowledge` only when a snippet is still summary-only/preview or when the visitor explicitly asks for a topic that is not covered in the Knowledge Ledger. When `status=ready`, you already have this data—respond immediately instead of rereading.
        - When you do need `read_knowledge`, provide the `knowledge_ids` listed in the ledger and keep the fetch invisible to the visitor.
        - On the first substantive response about a snippet that is still summary-only, pair your reply with `read_knowledge` so you quote the actual document instead of the hint.
        - Once a snippet is marked “ready”, skip investigative fillers (“I’ll check”) and go straight to the requested numbers/features.
        - `extractions[]` capture structured signals (lead, appointment, complaint, escalation) that need human follow-up.
        - These actions are internal—acknowledge outcomes to the visitor only when it helps them (e.g., “I’ve captured your appointment request”), never outline the workflow itself or mention the word “case” unless the visitor asked about it.
        - Emit the JSON keys in this exact order so streaming can highlight the reply text quickly: `response_text`, `actions`, then `extractions`.
        """
    ).strip()

    KNOWLEDGE_RULES = textwrap.dedent(
        """
        ### Knowledge Retrieval Rules
        - Use the Knowledge Ledger in this prompt as your source of truth. Each snippet lists its `status`, `read` scope, last usage, and coverage topics that were already delivered.
        - When `status=ready`, the backend already loaded the full document. You already have this data—respond immediately and only call `read_knowledge` if the visitor explicitly asks for content outside the listed coverage.
        - For snippets still marked summary-only or preview, call `read_knowledge` with the provided IDs before citing details so you can quote the real document.
        - After you answer a question with a snippet, reflect that topic in the coverage list so future turns avoid redundant reads.
        - Cite snippets naturally when they inform an answer, but keep internal file names and retrieval steps invisible to the visitor.
        - If no snippet confirms the requested detail, say so plainly and offer escalation or follow-up. If a snippet is labeled as a system notice (document unavailable), explain the limitation and propose an alternative.
        """
    ).strip()

    CUSTOMER_RULES = textwrap.dedent(
        """
        ### Customer Identity Rules
        - Treat phone numbers and emails as authoritative identifiers. Whenever either is shared you must immediately run `create_customer` with the provided identifier(s) so the backend can match existing records and attach the conversation/case to that customer.
        - If no customer matches the supplied identifier, still include at least the full name and any identifier you have, and actively request at least one identifier to include in `create_customer` so a fresh record can be created for future reuse.
        - When only a name is available (no phone/email), create a customer record with that name, set `refused_contact=true` to document the missing contact info, and NEVER attempt to match an existing customer using the name alone.
        - Do not update existing phone or email values using `update_customer`. Only adjust display name or metadata when the visitor explicitly confirms the change.
        - When the visitor continues after a case is opened, log evolving details using `add_case_history` rather than changing the description.
        """
    ).strip()

    def build(
        self,
        *,
        conversation: Conversation,
        knowledge_snippets: Sequence[Mapping[str, object]],
        transcript: Sequence[ConversationMessage],
        actions_catalog: Sequence[Mapping[str, str]],
        knowledge_log: Sequence[Mapping[str, object]] | None = None,
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

            {self.KNOWLEDGE_RULES}

            {self.CUSTOMER_RULES}
            """
        ).strip()

        user_prompt = self._compose_user_prompt(
            case_context=case_context,
            knowledge_snippets=knowledge_snippets,
            actions_catalog=actions_catalog,
            transcript=transcript,
            business_industry=industry,
            knowledge_log=knowledge_log or (),
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
                "content": snippet.get("content"),
                "public_label": snippet.get("public_label"),
                "structuredTables": snippet.get("structuredTables") or [],
                "issues": snippet.get("issues") or [],
                "pageSummaries": snippet.get("pageSummaries") or [],
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
        knowledge_snippets: Sequence[Mapping[str, object]],
        actions_catalog: Sequence[Mapping[str, str]],
        transcript: Sequence[ConversationMessage],
        business_industry: str,
        knowledge_log: Sequence[Mapping[str, object]],
    ) -> str:
        transcript_lines = []
        for message in transcript:
            sender = message.sender.upper()
            transcript_lines.append(f"- [{sender}] {message.body}")
        transcript_block = "\n".join(transcript_lines) or "(no prior messages)"

        knowledge_block_lines = []
        for snippet in knowledge_snippets:
            knowledge_block_lines.extend(self._render_snippet_entry(snippet))
        knowledge_block = "\n".join(knowledge_block_lines) if knowledge_block_lines else "- No knowledge snippets were retrieved"
        previous_deliveries_block = self._render_previous_deliveries(knowledge_log)

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

            ### Knowledge Ledger
            {knowledge_block}
            Ledger directive: When a snippet shows status=ready, you already have that data—respond now. Only invoke `read_knowledge` for summary-only/preview snippets or when the visitor asks for topics outside the listed coverage.

            ### Previously Delivered
            {previous_deliveries_block}

            ### Available Actions
            {actions_block}

            ### Tasks
            1. Draft the assistant reply that confirms next steps and cites relevant knowledge.
            2. Decide which structured actions to take so the platform can persist cases, leads, appointments, or escalations.
            3. Always produce at least one `create_case` or `update_case_status` action so the conversation is tracked.
            """
        ).strip()

    def _render_snippet_entry(self, snippet: Mapping[str, object]) -> list[str]:
        lines: list[str] = []
        lines.append(self._build_ledger_line(snippet))
        summary = snippet.get("summary") or "No summary available."
        lines.append(f"    Summary: {summary}")
        notice = snippet.get("system_notice")
        if notice == "missing_document":
            lines.append(
                "    System notice: Inform the visitor that this document is unavailable right now and offer a follow-up or alternative guidance."
            )
        content = snippet.get("content")
        if content:
            formatted = textwrap.indent(content.strip(), "    ")
            lines.append("    Full content:")
            lines.append(formatted)
        tables = snippet.get("structuredTables") or []
        if tables:
            lines.append("    Structured tables detected (call `read_knowledge` to access full rows):")
            for table in tables[:3]:
                table_title = table.get("title") or f"Table {table.get('order_index') or table.get('orderIndex')}"
                page_number = table.get("page_number") or table.get("pageNumber") or "n/a"
                columns = table.get("column_schema") or table.get("columnSchema") or []
                formatted_cols = ", ".join(columns[:6]) if isinstance(columns, (list, tuple)) else ""
                lines.append(f"      • {table_title} (page {page_number}) columns: {formatted_cols or 'unspecified'}")
        issues = snippet.get("issues") or []
        if issues:
            lines.append("    Known ingestion issues:")
            for issue in issues[:3]:
                lines.append(f"      • {issue.get('severity', '').upper()} {issue.get('code')}: {issue.get('description')}")
        return lines

    def _build_ledger_line(self, snippet: Mapping[str, object]) -> str:
        title = snippet.get("public_label") or snippet.get("title") or "Untitled knowledge"
        identifier = snippet.get("id") or "unknown-id"
        status = (snippet.get("status") or ("ready" if snippet.get("data_ready") else "summary-only")).lower()
        coverage_display = self._format_coverage(snippet.get("coverage"))
        last_used_for = snippet.get("last_used_for") or "not used yet"
        last_used_at = self._format_timestamp(snippet.get("last_used_at"))
        last_used = f"{last_used_for}{f' @ {last_used_at}' if last_used_at else ''}"
        read_state = self._describe_read_state(snippet.get("read_state"))
        pin_marker = " [PINNED]" if snippet.get("pin") else ""
        return (
            f"- [ID: {identifier}] {title}{pin_marker} — status={status}, read={read_state}, "
            f"coverage={coverage_display}, last_used={last_used}"
        )

    @staticmethod
    def _format_coverage(raw: object) -> str:
        if isinstance(raw, (list, tuple, set)):
            tokens = [str(item).strip().lower() for item in raw if isinstance(item, str) and item.strip()]
        elif isinstance(raw, str):
            tokens = [raw.strip().lower()]
        else:
            tokens = []
        if not tokens:
            return "none yet"
        return "/".join(tokens)

    @staticmethod
    def _format_timestamp(raw: object) -> str:
        if not raw:
            return ""
        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            return ""
        return parsed.strftime("%H:%M")

    @staticmethod
    def _describe_read_state(read_state: object) -> str:
        normalized = str(read_state or "").lower()
        if normalized == "full":
            return "full document"
        if normalized == "preview":
            return "chunk preview"
        return "summary-only"

    def _render_previous_deliveries(self, knowledge_log: Sequence[Mapping[str, object]]) -> str:
        entries = list(knowledge_log or [])
        if not entries:
            return "- No tracked deliveries yet"
        recent = entries[-5:]
        recent.reverse()
        lines: list[str] = []
        for entry in recent:
            label = entry.get("label") or entry.get("id") or "knowledge"
            topics = entry.get("topics") or []
            topic_display = "/".join(str(topic).strip().lower() for topic in topics if isinstance(topic, str) and topic.strip())
            descriptor = entry.get("usage") or f"{label} {topic_display}".strip()
            timestamp = self._format_timestamp(entry.get("used_at"))
            if timestamp:
                lines.append(f"- {descriptor or label} shared at {timestamp}")
            else:
                lines.append(f"- {descriptor or label} (shared earlier)")
        return "\n".join(lines)
