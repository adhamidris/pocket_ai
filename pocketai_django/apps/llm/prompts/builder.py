from __future__ import annotations

import dataclasses
import textwrap
from datetime import datetime
from typing import Mapping, Sequence

from core.otel import otel_trace

from apps.accounts.models import AgentProfile
from apps.accounts.constants import DEFAULT_ASSISTANT_ROLE
from apps.conversations.models import Conversation, ConversationMessage
from apps.conversations.instruction_contracts import (
    active_custom_assistant_name,
    build_custom_assistant_instruction_note,
)

TRACER = otel_trace.get_tracer(__name__)


@dataclasses.dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str
    transcript: Sequence[Mapping[str, str]]
    knowledge_snippets: Sequence[Mapping[str, object]]
    actions_catalog: Sequence[Mapping[str, str]]


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
        ### CRM Action Availability
        - Legacy CRM and support actions are retired in the current runtime.
        - Do not propose, plan, or reference case/customer/lead/appointment actions unless a future action catalog explicitly reintroduces them.
        - Focus on knowledge retrieval, grounded answers, and any non-CRM actions present in the provided catalog.
        """
    ).strip()

    CONVERSATION_RULES = textwrap.dedent(
        """
        ### Conversation + Summarisation Rules
        - Identify whether the visitor raised multiple requests. If yes, summarise them separately in your reply and create follow-up actions (cases, leads, appointments) per request when enabled.
        - Make sure the visitor understands what happens next by summarising outcomes or asking for any missing information. Focus on what is true now and what the visitor can do, not on narrating your internal steps.
        - Lead the conversation yourself—never promise that external employees, agents, or relationship managers will follow up later. Gather the needed details directly in chat and describe the concrete outcome or guidance you are providing.
        - Reference knowledge snippets explicitly when they helped decide an answer, and never invent policies or offers beyond the uploaded knowledge base.
        - When the knowledge base does not confirm a requested detail, state that it is not yet confirmed and ask the visitor if they would like to be transferred to a human call or continue the chat while you gather more information.
        - Keep internal workflows invisible unless you must open a follow-up case because the requested info is unavailable; in that situation, briefly confirm the case was filed and a follow-up will come from the business.
        - When a visitor asks about case status, only mention the latest status if it directly answers their question; otherwise keep the workflow behind the scenes.
        - Ask only for missing information required to locate or verify the requested item (document name, identifier, date, email/phone). Do not brainstorm options or scenarios outside the loaded knowledge.
        - Do not repeat the same acknowledgement or promise in consecutive replies. If you already confirmed a fact or said you would review a document, move forward with the new information instead of restating the earlier message.
        - When the visitor pivots to a different product variant (for example, another card tier or benefit), assume the relevant data is already loaded and move straight to the requested details. If you already have the figures, respond directly with the concrete fees, limits, or features instead of saying that you will check.
        - Use clean, reader-friendly Markdown. For short single-fact answers, reply naturally without headings. Use a heading (`##`) or bold label only when there are multiple topics/products or the visitor explicitly asks for a structured breakdown. Use bullet/numbered lists for checklists or step-by-step instructions. For numeric comparisons/metrics: use a short bullet/numbered list for 1-2 items; if 3+ rows are involved, switch to a Markdown table and do not restate the exact figures elsewhere. Always close any `**`, `_`, or ``` markers before sending, and never call retrieval tools just to improve formatting; reuse the data already returned by the latest tools.
        - When a table is appropriate, surface the numeric details only once inside that table. Skip repeating the same figures in prose beforehand; instead, add a short “Key observations” paragraph after the table if extra context is needed.
        - When you report derived numbers (totals, averages, percentages), compute them carefully from the evidence and sanity-check that they add up before stating them.
        """
    ).strip()

    ACTION_RULES = textwrap.dedent(
        """
        ### Action Output Contract
        - `actions[]` must align with the provided catalog. Each entry needs `action` and `payload`.
        - Only emit actions that are explicitly present in the provided catalog for this turn.
        - Retrieval runs through the tool interface (e.g., `search_knowledge`, `read_knowledge`). Do not emit retrieval actions in `actions[]`; instead, call the appropriate tool invisibly and respond with the results.
        - If the catalog does not contain a relevant business-write action, do not invent one. Ask clarifying questions or explain the current limitation plainly.
        - `extractions[]` capture structured signals that may help downstream systems; keep them grounded in the actual conversation.
        - These actions are internal. Acknowledge outcomes to the visitor only when it helps them, and never narrate hidden workflows.
        - Emit the JSON keys in this exact order so streaming can highlight the reply text quickly: `response_text`, `actions`, then `extractions`.
        ### Placeholder Output Rules
        - At most one placeholder (before the first retrieval) is allowed per visitor message (user turn), and it must be short, visitor-facing, and immediately promise the concrete data you’re pulling.
        - After that first acknowledgement, stay silent until you can answer fully—if another tool is required, respond with `tool_calls` only and leave `content` empty.
        - Do NOT narrate internal steps like "I'll search", "Let me check", "I'm going to look this up", or similar. The visitor should see the answer and any clarifying questions, not the internal workflow.
        - Never start `response_text` with phrases such as "I'll", "I will", "Let me", "I'm going to", "Reviewing", or "Searching". Start directly with helpful content or a clear, concise clarification.
        - Keep replies grounded in the current snippets and state what you can confirm. If something is pending a read, mention it only when you’re delivering the final answer, not between tool calls.
        """
    ).strip()

    KNOWLEDGE_RULES = textwrap.dedent(
        """
        ### Knowledge Retrieval Rules
        - Use the Knowledge Ledger in this prompt as your source of truth. Each snippet lists its `status`, `read` scope, last usage, and coverage topics that were already delivered.
        - When `status=ready`, the backend already loaded the full document. You already have this data—respond immediately and only call the designated read tool (for example, `read_knowledge`) if the visitor explicitly asks for content outside the listed coverage.
        - For snippets still marked summary-only or preview, call the provided read tool with the supplied identifiers before citing details so you can quote the real document.
        - Retrieval tools available this turn may include `search_knowledge`, `read_knowledge`, chunk loaders, or upload-specific helpers. Treat them as authoritative signals of what the backend already executed.
        - When the visitor quotes an internal identifier (slug, SKU, policy code, booking ID), prefer the snippet whose `aliases` list contains that exact identifier before falling back to descriptions.
        - When the visitor names a specific product, location, offer, or entity, prefer the snippet whose `entity_name` or `entity_type` matches that request—even if snippets share the same source document. Only fall back to other chunks when no entity-aligned snippet exists.
        - After you answer a question with a snippet, reflect that topic in the coverage list so future turns avoid redundant reads.
        - Cite snippets naturally when they inform an answer, but keep internal file names and retrieval steps invisible to the visitor.
        - If no snippet confirms the requested detail, say so plainly and offer escalation or follow-up. If a snippet is labeled as a system notice (document unavailable), explain the limitation and propose an alternative.
        - When `status=not_found`, you must tell the visitor that the knowledge base does not contain their identifier and either ask for clarification or offer to escalate.
        - When snippet metadata indicates `truncated=true` or issues referencing truncation, warn the visitor that some data may be missing before quoting partial details.
        - If no snippet matches the requested topic at all, state that the knowledge base does not cover it and ask for a specific document name, identifier, or detail to search again. Do NOT propose services, offers, or examples that are not present in the knowledge ledger.
        """
    ).strip()

    SEARCH_DISAMBIGUATION_RULES = textwrap.dedent(
        """
        ### Search + Disambiguation Rules
        - Treat any business-like request as a search trigger even without exact IDs: applications, orders, bookings, policies, claims, invoices, payments, subscriptions, accounts, requests, tickets, cases, appointments, or phrases like "applied", "status", "track", "check", "order number".
        - When matches are partial or fuzzy, present the top candidates with their identifiers, entity names, and document labels, then ask the visitor to confirm the correct one or share the missing detail (ID, date, email, phone) to disambiguate. Present candidates directly—do not narrate that you are searching.
        - Never invent identifiers—only surface IDs, aliases, or names that appear in the knowledge snippets.
        - If nothing matches confidently, say so plainly and ask only for the exact identifier/term you need (document name, ID, email, phone, date). Avoid offering hypothetical options or categories not present in the knowledge snippets.
        - Keep wording industry-agnostic ("record", "request", "order", "application") unless a snippet provides a specific entity name; adopt the snippet's name when available.
        """
    ).strip()

    CUSTOMER_RULES = textwrap.dedent(
        """
        ### Customer Identity Rules
        - Ask for identifiers only when they are necessary to answer the request from available knowledge or tools.
        - Do not promise that the platform created or updated a customer record unless a future CRM action catalog explicitly enables that workflow.
        - Use identifiers to disambiguate records or retrieve information, not to imply hidden CRM side effects.
        """
    ).strip()

    CHUNK_READ_NUDGE = textwrap.dedent(
        """
        ### Retrieval Focus Directive
        - When you need more context from a knowledge snippet, request that exact snippet ID (chunk) rather than the entire document, unless you truly need the whole document.
        - Prefer loading a narrow window around that chunk via `load_chunk_contents`; avoid whole-document reads unless necessary.
        - Respect chunk read budgets. If the ledger warns that the budget was reached, ask the visitor for a more specific identifier instead of requesting more chunks.
        """
    ).strip()

    OUTPUT_FORMATTING_RULES = textwrap.dedent(
        """
        ### Output Formatting (Chat UI)
        Write in clean Markdown optimized for a web chat renderer.

        Newlines:
        - Use a blank line between paragraphs (two newlines: "\\n\\n").
        - Do not hard-wrap lines inside a paragraph; let the UI wrap naturally.
        - Use single newlines only for list items, code blocks, or genuinely line-based content (addresses, poetry).

        Structure:
        - Prefer short paragraphs (1-3 sentences).
        - Use headings "##" or "###" for sections.
        - For bullets use "- " (not "•"); for numbered lists use "1. " (not "1)").
        - Put a blank line before and after lists.
        - Never embed list markers inside a sentence (bad: "I 1. ... 2. ...", "you 1. ... 2. ..."). If you introduce steps or options, end the lead-in with ":" then start the list on the next line.
        - Use fenced code blocks (```...```) for code/logs.
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
        with TRACER.start_as_current_span("prompt.build_bundle") as span:
            if span.is_recording():
                span.set_attribute("conversation.id", str(getattr(conversation, "id", "")))
                span.set_attribute("knowledge.count", len(knowledge_snippets))
                span.set_attribute("transcript.count", len(transcript))
                span.set_attribute("actions.count", len(actions_catalog))
            business = conversation.business_profile
            industry = (business.industry or "").strip() or "general services"
            assistant_context = {
                "role": DEFAULT_ASSISTANT_ROLE,
                "tone": self.agent.tone or "friendly",
                "business_name": business.name,
                "agent_name": active_custom_assistant_name(conversation) or self.agent.name,
                "business_industry": industry,
            }

            case_context = self._case_context(conversation)

            system_prompt = textwrap.dedent(
                f"""
                You are {assistant_context['agent_name']}, the {assistant_context['role']} for {business.name}, a company in the {industry} industry. Maintain a {assistant_context['tone']} tone, stay factual, and never hallucinate policy or pricing.

                {self.CASE_MANDATE}

                {self.CONVERSATION_RULES}

                {self.ACTION_RULES}

                {self.KNOWLEDGE_RULES}

                {self.SEARCH_DISAMBIGUATION_RULES}

                {self.CHUNK_READ_NUDGE}

                {self.OUTPUT_FORMATTING_RULES}

                {self.CUSTOMER_RULES}
                """
            ).strip()
            custom_assistant_note = build_custom_assistant_instruction_note(conversation)
            if custom_assistant_note:
                system_prompt = f"{system_prompt}\n\n{custom_assistant_note.strip()}"

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
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "summary": s.get("summary"),
                    "source": s.get("source"),
                    "content": s.get("content"),
                    "public_label": s.get("public_label"),
                    "structuredTables": s.get("structuredTables") or [],
                    "issues": s.get("issues") or [],
                    "pageSummaries": s.get("pageSummaries") or [],

                    # important for tool choice:
                    "status": s.get("status"),
                    "read_state": s.get("read_state"),
                    "coverage": s.get("coverage") or [],
                    "last_used_for": s.get("last_used_for"),
                    "last_used_at": s.get("last_used_at"),
                    "pin": bool(s.get("pin")),
                    "entity_type": s.get("entity_type"),
                    "entity_name": s.get("entity_name"),
                    "entity_business": s.get("entity_business"),
                    "is_table_chunk": bool(s.get("is_table_chunk")),
                    "chunk_index": s.get("chunk_index"),
                    "chunk_id": s.get("chunk_id"),
                    "aliases": s.get("aliases") or [],
                    "search_stage": s.get("search_stage"),
                    "confidence_score": s.get("confidence_score"),
                    "truncated": bool(s.get("truncated")),
                    "source_diagnostics": s.get("source_diagnostics") or {},
                    "partial_index": bool(s.get("partial_index")),
                    "truncation_note": s.get("truncation_note") or "",
                }
                for s in knowledge_snippets
            ]

            if span.is_recording():
                span.set_attribute("knowledge.ready_count", sum(1 for entry in knowledge_payload if entry.get("status") == "ready"))
            return PromptBundle(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                transcript=transcript_payload,
                knowledge_snippets=knowledge_payload,
                actions_catalog=actions_catalog,
            )

    def _case_context(self, conversation: Conversation) -> str:
        del conversation
        return "No legacy case context is attached to this session."

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
        with TRACER.start_as_current_span("prompt.compose_user_prompt") as span:
            if span.is_recording():
                span.set_attribute("transcript.count", len(transcript))
                span.set_attribute("knowledge.count", len(knowledge_snippets))
                span.set_attribute("actions.count", len(actions_catalog))
                span.set_attribute("knowledge_log.count", len(knowledge_log or ()))
            transcript_lines = []
            for message in transcript:
                sender = message.sender.upper()
                transcript_lines.append(f"- [{sender}] {message.body}")
            transcript_block = "\n".join(transcript_lines) or "(no prior messages)"

            use_envelope = True
            if use_envelope:
                knowledge_block = self.build_knowledge_context_envelope(
                    knowledge_snippets,
                    use_json_envelope=True
                )
            else:
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

            prompt = textwrap.dedent(
                f"""
                ### Conversation Transcript
                {transcript_block}

                ### Session Context (internal reference only — do not mention in replies unless asked)
                {case_context}

                ### Business Context
                - Industry: {business_industry}

                ### Knowledge Ledger
               {knowledge_block}
                Ledger directive: When a snippet shows status=ready, you already have that data—respond now. Only invoke the read tool (for example, `read_knowledge`) for summary-only/preview snippets or when the visitor asks for topics outside the listed coverage.
                Ledger directive (chunk focus): When you need more context from a knowledge snippet, request that exact snippet ID (chunk) rather than the entire document, unless you truly need the whole document.

                ### Previously Delivered
                {previous_deliveries_block}

                ### Available Actions
                {actions_block}

                ### Tasks
                1. Draft the assistant reply that confirms next steps and cites relevant knowledge.
                2. Decide which structured actions to take using only the actions explicitly present in the provided catalog.
                3. Do not invent retired CRM/support actions when they are absent from the catalog.
                4. If you invoke retrieval tools mid-turn, you may acknowledge the first lookup briefly, but after that stay silent until the tool returns data. Never emit multiple placeholders—subsequent retrieval turns must return only tool_calls with no assistant narration.
                """
            ).strip()
            if span.is_recording():
                span.set_attribute("prompt.length", len(prompt))
            return prompt

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
            lines.append("    Structured tables detected (call the designated read tool to access full rows):")
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

    def build_knowledge_context_envelope(
        self,
        knowledge_snippets: Sequence[Mapping[str, object]],
        *,
        use_json_envelope: bool = True,
    ) -> str:
        """
        Build JSON envelope wrapper for knowledge context (Claude-style)
        
        This provides better structure for the LLM to understand document sources,
        tables, and metadata when reasoning about knowledge base content.
        
        Args:
            knowledge_snippets: List of knowledge snippet dictionaries
            use_json_envelope: Whether to wrap in JSON structure (default: True)
        
        Returns:
            Formatted context string (JSON envelope or plain text)
        """
        import json

        with TRACER.start_as_current_span("prompt.build_knowledge_envelope") as span:
            if span.is_recording():
                span.set_attribute("knowledge.count", len(knowledge_snippets))
                span.set_attribute("envelope.json", bool(use_json_envelope))

            if not use_json_envelope:
                # Fall back to existing knowledge block format
                return self._build_plain_knowledge_block(knowledge_snippets)

            # Build JSON envelope structure
            documents = []
            total_tables = 0

            for idx, snippet in enumerate(knowledge_snippets, start=1):
                # Build document structure
                doc = {
                    "index": idx,
                    "media_type": snippet.get("mime_type") or "text/plain",
                    "source": snippet.get("public_label") or snippet.get("title") or f"document_{snippet.get('id')}",
                    "text": snippet.get("content") or snippet.get("summary") or "",
                }

                # Add structured tables if available
                tables = snippet.get("structuredTables") or []
                if tables:
                    doc["tables"] = []
                    for t in tables[:5]:
                        entry = {
                            "title": t.get("title"),
                            "page_number": t.get("page_number") or t.get("pageNumber"),
                            "column_schema": t.get("column_schema") or t.get("columnSchema") or [],
                            "row_count": len(t.get("rows") or t.get("rowsSample") or []),
                        }
                        rows = t.get("rows") or t.get("rowsSample") or []
                        if rows:
                            entry["rowsSample"] = rows[:5]   # keep it small and consistent
                        doc["tables"].append(entry)
                        total_tables += 1

                # Add page summaries if available
                page_summaries = snippet.get("pageSummaries") or []
                if page_summaries:
                    doc["pages"] = [
                        {
                            "page_number": p.get("page_number"),
                            "summary": p.get("summary"),
                        }
                        for p in page_summaries[:10]  # Limit to 10 pages
                    ]

                # Add metadata
                doc["metadata"] = {
                    "status": snippet.get("status"),
                    "read_state": snippet.get("read_state"),
                    "coverage": snippet.get("coverage") or [],
                    "last_used_for": snippet.get("last_used_for"),
                    "entity_type": snippet.get("entity_type"),
                    "entity_name": snippet.get("entity_name"),
                    "entity_business": snippet.get("entity_business"),
                    "is_table_chunk": bool(snippet.get("is_table_chunk")),
                    "chunk_index": snippet.get("chunk_index"),
                    "chunk_id": snippet.get("chunk_id"),
                    "search_stage": snippet.get("search_stage"),
                    "confidence_score": snippet.get("confidence_score"),
                    "truncated": bool(snippet.get("truncated")),
                    "aliases": snippet.get("aliases") or [],
                    "issues": snippet.get("issues") or [],
                    "partial_index": bool(snippet.get("partial_index")),
                    "truncation_note": snippet.get("truncation_note") or "",
                }

                documents.append(doc)

            if span.is_recording():
                span.set_attribute("knowledge.tables", total_tables)
                span.set_attribute("knowledge.documents", len(documents))

            # Wrap in envelope
            envelope = {"documents": documents}

            # Return as formatted JSON with instruction
            return f"""<documents>
{json.dumps(envelope, indent=2, ensure_ascii=False)}
</documents>

Use the documents above to answer the user's question. Reference documents by their index and source when citing information."""

    def _build_plain_knowledge_block(
        self,
        knowledge_snippets: Sequence[Mapping[str, object]]
    ) -> str:
        """
        Legacy plain text knowledge block (your existing format)
        """
        knowledge_block_lines = []
        for snippet in knowledge_snippets:
            knowledge_block_lines.extend(self._render_snippet_entry(snippet))
        return "\n".join(knowledge_block_lines) if knowledge_block_lines else "- No knowledge snippets were retrieved"
