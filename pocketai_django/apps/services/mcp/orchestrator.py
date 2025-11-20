"""
MCP-style orchestrator skeleton.

The goal is to keep this implementation self-contained so we can experiment
with standard tool-calling workflows without disturbing the legacy
AiOrchestratorService. Later phases will flesh out the orchestration loop,
tool dispatch, and plan construction logic.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Callable, Mapping

from django.conf import settings

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationExtractionType
from apps.services.llm_provider import PromptGenerationError
from apps.services.ai_orchestrator import (
    AiOrchestratorPlan,
    PlannedAction,
    ExtractionPlan,
    KnowledgeSnippet,
    ActionType,
    StreamingTurnContext,
)

from . import prompts, tools
from django.core.cache import cache

from .types import (
    BaseMcpProvider,
    ToolExecutionContext,
    ToolConstraintError,
    ChunkReadBudgetExceeded,
    ChunkPageBudgetExceeded,
    CharacterBudgetExceeded,
)


logger = logging.getLogger(__name__)


class McpOrchestratorService:
    """
    Placeholder MCP orchestrator.

    This class mirrors the public API of AiOrchestratorService so the chat
    portal can swap between implementations via a feature flag. Real behavior
    will be implemented in later phases of the migration plan.
    """

    def __init__(self, *, agent: AgentProfile, provider: BaseMcpProvider | None) -> None:
        self.agent = agent
        self.provider = provider
        self.tool_definitions = tools.TOOL_DEFINITIONS
        self.max_tool_iterations = int(getattr(settings, "MCP_MAX_TOOL_ITERATIONS", 10))
        self.business_override_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
        default_chunk_reads = max(1, int(getattr(settings, "RAG_MAX_CHUNK_READS_PER_TURN", 3)))
        self.max_chunk_reads_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_reads_per_turn", default_chunk_reads)),
        )
        default_page_windows = max(1, int(getattr(settings, "RAG_MAX_CHUNK_PAGES_PER_TURN", 3)))
        self.max_chunk_pages_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_pages_per_turn", default_page_windows)),
        )
        self.default_char_budget_per_turn = max(
            4000,
            int(getattr(settings, "RAG_MAX_CHAR_BUDGET_PER_TURN", 48000)),
        )
        self.default_char_budget_per_minute = max(
            4000,
            int(getattr(settings, "RAG_MAX_CHAR_BUDGET_PER_MINUTE", 64000)),
        )
        self.char_budget_window_seconds = max(30, int(getattr(settings, "RAG_CHAR_BUDGET_WINDOW_SECONDS", 60)))

    def _execute_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        ) -> AiOrchestratorPlan:
        """
        Build the orchestration plan for the latest customer message.

        Not implemented yet – see later migration phases for the full MCP loop.
        """

        messages = prompts.build_messages(conversation=conversation, user_message=user_message)
        char_turn_limit = self._char_budget_per_turn(conversation.business_profile)
        char_minute_limit = self._char_budget_per_minute(conversation.business_profile)
        minute_reserver = self._build_minute_budget_reserver(conversation.business_profile, char_minute_limit)
        tool_context = ToolExecutionContext(
            max_chunk_reads_per_turn=self.max_chunk_reads_per_turn,
            max_chunk_pages_per_turn=self.max_chunk_pages_per_turn,
            char_budget_per_turn=char_turn_limit,
            char_budget_per_minute=char_minute_limit,
            minute_budget_reserver=minute_reserver,
        )

        if not self.provider:
            raise RuntimeError("MCP provider is not configured.")

        transcript = list(messages)
        final_assistant_message: dict[str, object] | None = None
        placeholder_sent = False

        if on_status_change:
            on_status_change({"code": "thinking", "label": "Thinking…"})

        # Phase 1: streaming + tools to obtain the final assistant answer.
        for _ in range(self.max_tool_iterations):
            payload = self.provider.chat(
                transcript,
                tools=self.tool_definitions,
                on_stream_delta=on_response_text_delta,
            )
            assistant_message = self._coerce_assistant_message(payload)
            tool_calls = list(assistant_message.get("tool_calls") or [])

            assistant_payload: dict[str, object] = {
                "role": "assistant",
                "content": assistant_message.get("content"),
            }
            if tool_calls:
                assistant_payload["tool_calls"] = tool_calls
            transcript.append(assistant_payload)

            if not tool_calls:
                final_assistant_message = assistant_message
                break

            for tool_call in tool_calls:
                tool_name = self._tool_name(tool_call)
                arguments = self._tool_arguments(tool_call)

                # Surface placeholder + status when we transition into a knowledge read.
                if self._is_knowledge_tool(tool_name):
                    if on_status_change:
                        if tool_name == "search_knowledge":
                            raw_query = arguments.get("query")
                            query = str(raw_query).strip() if raw_query is not None else ""
                            label = f"Searching: {query[:80]}" if query else "Searching knowledge…"
                            on_status_change({"code": "searching_knowledge", "label": label})
                        elif tool_name == "read_document":
                            raw_id = arguments.get("document_id")
                            doc_id = str(raw_id).strip() if raw_id is not None else ""
                            short_id = f"{doc_id[:8]}…" if doc_id else ""
                            base_label = "Reading document"
                            label = f"Reading: {short_id}" if short_id else base_label
                            on_status_change({"code": "reading_document", "label": label})
                    if on_placeholder_response and not placeholder_sent:
                        placeholder_text = str(assistant_message.get("content") or "").strip()
                        if placeholder_text:
                            on_placeholder_response(placeholder_text)
                            placeholder_sent = True

                try:
                    tool_result = tools.execute_tool(
                        tool_name,
                        arguments,
                        conversation=conversation,
                        context=tool_context,
                    )
                except ToolConstraintError as exc:
                    logger.warning(
                        "mcp.tool.constraint_violation tool=%s conversation=%s error=%s",
                        tool_name,
                        conversation.id,
                        exc,
                    )
                    tool_result = self._constraint_error_payload(tool_name, exc)
                tool_context.add_tool_trace(
                    {
                        "tool": tool_name,
                        "arguments": arguments,
                        "result_keys": sorted(tool_result.keys()),
                        "status": tool_result.get("status"),
                        "error_code": tool_result.get("error_code"),
                        "hint": tool_result.get("hint"),
                        "mode": tool_result.get("mode"),
                        "page": tool_result.get("page"),
                        "token_budget": tool_result.get("token_budget"),
                        "throttle_notice": bool(tool_result.get("throttle_notice")),
                    }
                )
                if self._is_knowledge_tool(tool_name):
                    self._record_knowledge_outputs(tool_context, tool_result)
                    if tool_name == "read_document" and on_status_change:
                        snippets = tool_result.get("snippets") if isinstance(tool_result, Mapping) else None
                        if isinstance(snippets, list) and snippets:
                            first = snippets[0]
                            if isinstance(first, Mapping):
                                label_source = (
                                    first.get("public_label")
                                    or first.get("title")
                                    or first.get("source")
                                )
                                if isinstance(label_source, str) and label_source.strip():
                                    on_status_change(
                                        {
                                            "code": "reading_document",
                                            "label": f"Reading: {label_source.strip()[:80]}",
                                        }
                                    )
                transcript.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": tool_name,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )
        else:
            raise RuntimeError("MCP tool loop exceeded iteration limit.")

        if on_status_change:
            on_status_change("responding")

        answer_text = ""
        if final_assistant_message is not None:
            answer_text = str(final_assistant_message.get("content") or "").strip()

        unmet_read_required = False
        for entry in getattr(tool_context, "knowledge_results", []):
            if isinstance(entry, Mapping) and entry.get("read_required"):
                unmet_read_required = True
                break
        if unmet_read_required and not getattr(tool_context, "knowledge_reads", []):
            # Enforce read-before-answer for table/identifier hits
            final_assistant_message = {
                "role": "assistant",
                "content": "",
                "actions": [],
                "extractions": [],
                "placeholder_response": "Need to read the recommended document/page before answering. Use read_hint (doc_id + page + mode).",
            }
            answer_text = ""

        planner_payload: dict[str, object] | None = None
        if answer_text:
            try:
                planner_payload = self._run_planner(
                    conversation=conversation,
                    user_message=user_message,
                    answer_text=answer_text,
                    tool_context=tool_context,
                    on_status_change=on_status_change,
                )
            except PromptGenerationError:
                planner_payload = None

        plan = self._build_plan_from_assistant(
            conversation=conversation,
            assistant_message=self._merge_planner_into_assistant(final_assistant_message or {}, planner_payload),
            tool_context=tool_context,
        )
        self._log_turn_metrics(conversation, tool_context)
        del on_status_change, on_placeholder_response
        return plan

    def stream_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_stream_complete: Callable[[], None] | None = None,
    ) -> StreamingTurnContext:
        plan = self._execute_turn(
            conversation=conversation,
            user_message=user_message,
            on_response_text_delta=on_response_text_delta,
            on_status_change=on_status_change,
            on_placeholder_response=on_placeholder_response,
        )
        llm_source = "provider"
        if plan.diagnostics and plan.diagnostics.get("llm_strategy"):
            llm_source = str(plan.diagnostics.get("llm_strategy"))
        if on_stream_complete:
            try:
                on_stream_complete()
            except Exception:  # pragma: no cover - defensive
                pass
        return StreamingTurnContext(
            conversation=conversation,
            response_text=plan.response_text,
            planned_actions=tuple(plan.planned_actions),
            extractions=tuple(plan.extractions),
            resolved_citations=tuple(plan.citations),
            knowledge_payload=tuple(),
            knowledge_reads=tuple(),
            knowledge_status=None,
            knowledge_diagnostics=plan.diagnostics or {},
            knowledge_loading=False,
            placeholder_response=None,
            prompt_bundle=None,
            tool_trace=tuple(),
            cached_snippet_count=0,
            llm_source=llm_source,
            streamed_chunks=tuple(),
            plan=plan,
        )

    def finalize_turn(self, context: StreamingTurnContext) -> AiOrchestratorPlan:
        return context.plan or AiOrchestratorPlan(
            response_text=context.response_text,
            citations=tuple(context.resolved_citations),
            planned_actions=tuple(context.planned_actions),
            extractions=tuple(context.extractions),
            diagnostics=dict(context.knowledge_diagnostics),
            ingestion_warnings=tuple(),
        )

    def run_turn(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        on_response_text_delta: Callable[[str], None] | None = None,
        on_status_change: Callable[[str], None] | None = None,
        on_placeholder_response: Callable[[str], None] | None = None,
        on_stream_complete: Callable[[], None] | None = None,
    ) -> AiOrchestratorPlan:
        context = self.stream_turn(
            conversation=conversation,
            user_message=user_message,
            on_response_text_delta=on_response_text_delta,
            on_status_change=on_status_change,
            on_placeholder_response=on_placeholder_response,
            on_stream_complete=on_stream_complete,
        )
        return self.finalize_turn(context)

    def _build_plan_from_assistant(
        self,
        *,
        conversation: Conversation,
        assistant_message: Mapping[str, object],
        tool_context: ToolExecutionContext,
    ) -> AiOrchestratorPlan:
        response_text = str(assistant_message.get("content") or "").strip()
        planned_actions = self._extract_planned_actions(conversation, assistant_message)
        extractions = self._extract_extractions(assistant_message)
        diagnostics = {
            "llm_strategy": "mcp_tools_stream_planner",
            "knowledge_reads": getattr(tool_context, "knowledge_reads", []),
            "tool_trace": getattr(tool_context, "tool_trace", []),
            "placeholder_response": assistant_message.get("placeholder_response"),
            "coverage_ledger": getattr(tool_context, "coverage_ledger", []),
        }
        ingestion_warnings = tuple(tool_context.ingestion_warnings)
        return AiOrchestratorPlan(
            response_text=response_text,
            citations=self._build_citations(tool_context),
            planned_actions=planned_actions,
            extractions=extractions,
            diagnostics=diagnostics,
            ingestion_warnings=ingestion_warnings,
        )

    def _extract_planned_actions(
        self,
        conversation: Conversation,
        assistant_message: Mapping[str, object],
    ) -> tuple[PlannedAction, ...]:
        actions_payload = assistant_message.get("actions") or []
        planned: list[PlannedAction] = []
        for item in actions_payload:
            if not isinstance(item, Mapping):
                continue
            action_name = item.get("action")
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
            if not action_name:
                continue
            try:
                action_type = ActionType(action_name)
            except Exception:
                continue
            planned.append(PlannedAction(action=action_type, payload=dict(payload)))
        return tuple(planned)

    def _extract_extractions(self, assistant_message: Mapping[str, object]) -> tuple[ExtractionPlan, ...]:
        extraction_payload = assistant_message.get("extractions") or []
        extractions: list[ExtractionPlan] = []
        for item in extraction_payload:
            if not isinstance(item, Mapping):
                continue
            extraction_type = item.get("type")
            payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
            try:
                extraction_enum = ConversationExtractionType(extraction_type)
            except Exception:
                continue
            extractions.append(ExtractionPlan(extraction_type=extraction_enum, payload=dict(payload)))
        return tuple(extractions)

    def _build_citations(self, tool_context: ToolExecutionContext) -> tuple[KnowledgeSnippet, ...]:
        citations: list[KnowledgeSnippet] = []
        for entry in getattr(tool_context, "knowledge_results", []):
            try:
                snippet = KnowledgeSnippet(
                    id=uuid.UUID(entry.get("id")) if entry.get("id") else uuid.uuid4(),
                    title=entry.get("title") or "Knowledge",
                    summary=entry.get("summary") or entry.get("content") or "",
                    source=entry.get("source") or "",
                    content=entry.get("content"),
                    content_mode=entry.get("content_mode"),
                    public_label=entry.get("public_label"),
                    structured_tables=entry.get("structuredTables") or (),
                    issues=entry.get("issues") or (),
                    page_summaries=entry.get("pageSummaries") or (),
                    read_state=entry.get("read_state") or "summary",
                    topic_hints=entry.get("topic_hints") or (),
                    is_pinned=bool(entry.get("pin")),
                    supplemental_sections=entry.get("supplemental_sections") or (),
                    upload_id=uuid.UUID(entry["upload_id"]) if entry.get("upload_id") else None,
                    chunk_id=uuid.UUID(entry["chunk_id"]) if entry.get("chunk_id") else None,
                    chunk_index=entry.get("chunk_index"),
                    entity_type=entry.get("entity_type"),
                    entity_name=entry.get("entity_name"),
                    entity_business=entry.get("entity_business"),
                    is_table_chunk=bool(entry.get("is_table_chunk")),
                    aliases=entry.get("aliases") or (),
                    search_stage=entry.get("search_stage"),
                    confidence_score=entry.get("confidence_score"),
                    truncated=bool(entry.get("truncated")),
                    source_diagnostics=entry.get("source_diagnostics") or {},
                    partial_index=bool(entry.get("partial_index")),
                    structured_table_count=entry.get("structured_table_count") or 0,
                    issue_count=entry.get("issue_count") or 0,
                    structured_table_hint=entry.get("structured_table_hint"),
                    page_number=entry.get("page_number"),
                    page_mode=entry.get("page_mode"),
                )
            except Exception:
                continue
            citations.append(snippet)
        return tuple(citations)

    def _run_planner(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        answer_text: str,
        tool_context: ToolExecutionContext | None = None,
        on_status_change: Callable[[str], None] | None = None,
    ) -> dict[str, object] | None:
        """
        Second, non-streaming pass that asks the MCP provider to propose
        actions and extractions in structured JSON form. The streamed
        `answer_text` is treated as the final assistant reply shown to the
        visitor; the planner focuses on backend intents only.
        """

        if not self.provider:
            raise PromptGenerationError("MCP provider is not configured for planning.")

        if on_status_change:
            on_status_change({"code": "planning_actions", "label": "Planning follow-up actions…" })

        planner_messages = prompts.build_planner_messages(
            conversation=conversation,
            user_message=user_message,
            answer_text=answer_text,
            tool_context_note=self._planner_tool_note(tool_context),
            tool_trace=tuple(tool_context.tool_trace),
            coverage_ledger=tuple(tool_context.coverage_ledger),
        )
        payload = self.provider.chat(planner_messages, tools=None, on_stream_delta=None)
        if not isinstance(payload, dict):
            return None
        return self._coerce_assistant_message(payload)

    @staticmethod
    def _merge_planner_into_assistant(
        assistant_message: Mapping[str, object],
        planner_message: Mapping[str, object] | None,
    ) -> Mapping[str, object]:
        """
        Combine the streamed assistant content with planner-produced metadata.

        The assistant `content` (answer text) comes from the streaming pass,
        while `actions`/`extractions` are injected from the planner payload.
        """

        if not planner_message:
            return assistant_message
        merged: dict[str, object] = dict(assistant_message)
        if isinstance(planner_message.get("actions"), list):
            merged["actions"] = planner_message.get("actions")
        if isinstance(planner_message.get("extractions"), list):
            merged["extractions"] = planner_message.get("extractions")
        if "placeholder_response" in planner_message:
            merged["placeholder_response"] = planner_message.get("placeholder_response")
        return merged

    @staticmethod
    def _is_knowledge_tool(name: str) -> bool:
        return name in {"search_knowledge", "read_document"}

    @staticmethod
    def _record_knowledge_outputs(context: ToolExecutionContext, tool_result: Mapping[str, object]) -> None:
        snippets = tool_result.get("snippets") if isinstance(tool_result, Mapping) else None
        if isinstance(snippets, list):
            for entry in snippets:
                if isinstance(entry, Mapping):
                    context.add_knowledge_result(entry)
                    coverage_entry = {
                        "id": entry.get("id"),
                        "title": entry.get("title") or entry.get("public_label") or "Knowledge",
                        "read_state": entry.get("read_state"),
                        "coverage": entry.get("coverage") if isinstance(entry.get("coverage"), (list, tuple)) else (),
                        "search_stage": entry.get("search_stage"),
                        "chunk_id": entry.get("chunk_id"),
                        "upload_id": entry.get("upload_id"),
                    }
                    context.add_coverage_entry(coverage_entry)
        reads = tool_result.get("knowledge_reads") if isinstance(tool_result, Mapping) else None
        if isinstance(reads, list):
            for read in reads:
                if isinstance(read, Mapping):
                    context.add_knowledge_read(read)
        warnings = tool_result.get("ingestion_warnings") if isinstance(tool_result, Mapping) else None
        if isinstance(warnings, list):
            for warning in warnings:
                if isinstance(warning, Mapping):
                    context.add_ingestion_warning(warning)

    @staticmethod
    def _coerce_assistant_message(payload: dict | None) -> dict[str, object]:
        """
        Normalize the provider payload into an assistant-style message dict.

        Supports both OpenAI-style response envelopes and simplified dictionaries.
        """

        if not payload:
            return {}
        if "choices" in payload:
            choices = payload.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                if isinstance(message, dict):
                    return message
        message = payload.get("message")
        if isinstance(message, dict):
            return message
        return payload

    @staticmethod
    def _planner_tool_note(tool_context: ToolExecutionContext | None) -> str | None:
        if not tool_context:
            return None

        lines: list[str] = []
        reads = getattr(tool_context, "knowledge_reads", [])
        if isinstance(reads, list) and reads:
            display: list[str] = []
            for entry in reads[:6]:
                if not isinstance(entry, Mapping):
                    continue
                label = entry.get("label") or "Knowledge"
                page = entry.get("page")
                mode = entry.get("mode")
                parts = [str(label)]
                if page:
                    parts.append(f"p{page}")
                if mode:
                    parts.append(str(mode))
                display.append(" ".join(parts))
            if display:
                lines.append("Knowledge reads: " + "; ".join(display))

        trace = getattr(tool_context, "tool_trace", [])
        if isinstance(trace, list) and trace:
            constraint = [t for t in trace if isinstance(t, Mapping) and t.get("status") == "constraint_error"]
            throttled = [t for t in trace if isinstance(t, Mapping) and t.get("throttle_notice")]
            if constraint:
                lines.append("Constraint errors: %s (ask for a narrower page/identifier or continue with existing snippets)" % len(constraint))
            if throttled:
                lines.append("Throttled reads: %s (budget low; avoid wide reads and stick to precise pages)" % len(throttled))

        warnings = getattr(tool_context, "ingestion_warnings", [])
        if isinstance(warnings, list) and warnings:
            lines.append(f"Ingestion warnings: {len(warnings)} (content may be partial; avoid guessing missing details)")

        coverage = getattr(tool_context, "coverage_ledger", [])
        if isinstance(coverage, list) and coverage:
            display: list[str] = []
            for entry in coverage[:6]:
                if not isinstance(entry, Mapping):
                    continue
                label = entry.get("label") or entry.get("title") or "Knowledge"
                state = entry.get("read_state") or "summary"
                topics = entry.get("coverage") or ()
                topics_display = ", ".join(topics[:3]) if isinstance(topics, (list, tuple)) else ""
                parts = [str(label), f"state={state}"]
                if topics_display:
                    parts.append(f"topics={topics_display}")
                display.append(" ".join(parts))
            if display:
                lines.append("Coverage ledger: " + "; ".join(display))

        return "\n".join(lines) if lines else None

    @staticmethod
    def _log_turn_metrics(conversation: Conversation, context: ToolExecutionContext) -> None:
        logger.info(
            "mcp.turn.metrics business=%s conversation=%s tools=%s knowledge_reads=%s chunk_reads=%s chunk_pages=%s characters=%s",
            conversation.business_profile_id,
            conversation.id,
            len(context.tool_trace),
            len(context.knowledge_reads),
            context.chunk_reads_used,
            context.chunk_pages_used,
            context.characters_used,
        )

    def _business_override(self, business_profile, key: str, default: int | float) -> int | float:
        metadata = getattr(business_profile, "metadata", None)
        overrides = metadata.get(self.business_override_key) if isinstance(metadata, dict) else None
        if not isinstance(overrides, Mapping):
            return default
        value = overrides.get(key)
        if isinstance(value, (int, float)):
            try:
                return type(default)(value)
            except (TypeError, ValueError):
                return default
        return default

    def _char_budget_per_turn(self, business_profile) -> int | None:
        limit = int(self._business_override(business_profile, "char_budget_per_turn", self.default_char_budget_per_turn))
        return limit if limit > 0 else None

    def _char_budget_per_minute(self, business_profile) -> int | None:
        limit = int(
            self._business_override(business_profile, "char_budget_per_minute", self.default_char_budget_per_minute)
        )
        return limit if limit > 0 else None

    def _build_minute_budget_reserver(self, business_profile, limit: int | None):
        if not limit:
            return None
        window = self.char_budget_window_seconds
        cache_key = f"rag:char_minute:{business_profile.id}"

        def _reserve(count: int) -> None:
            if count <= 0:
                return
            current = cache.get(cache_key)
            if current is None:
                if count > limit:
                    raise CharacterBudgetExceeded(
                        f"Per-minute character budget exceeded (requested {count}, max {limit})."
                    )
                cache.set(cache_key, count, timeout=window)
                return
            new_total = int(current) + count
            if new_total > limit:
                raise CharacterBudgetExceeded(
                    f"Per-minute character budget exceeded (requested {new_total}, max {limit})."
                )
            cache.set(cache_key, new_total, timeout=window)

        return _reserve

    @staticmethod
    def _constraint_error_payload(tool_name: str, exc: ToolConstraintError) -> Mapping[str, object]:
        if isinstance(exc, CharacterBudgetExceeded):
            code = "char_budget_exceeded"
            hint = "Character budget exhausted; continue with existing excerpts or respond."
        elif isinstance(exc, ChunkPageBudgetExceeded):
            code = "page_budget_exceeded"
            hint = "Page window budget exhausted; summarize what you already have."
        elif isinstance(exc, ChunkReadBudgetExceeded):
            code = "chunk_read_budget_exceeded"
            hint = "Chunk read budget exhausted; proceed without additional reads."
        else:
            code = "constraint_violation"
            hint = "Constraint violated."
        return {
            "tool": tool_name,
            "status": "constraint_error",
            "error": str(exc),
            "error_code": code,
            "hint": hint,
            "llm_hint": hint,
            "snippets": [],
        }

    @staticmethod
    def _tool_name(tool_call: Mapping[str, object]) -> str:
        func = tool_call.get("function")
        if isinstance(func, dict):
            name = func.get("name")
            if isinstance(name, str):
                return name
        value = tool_call.get("name")
        if isinstance(value, str):
            return value
        raise ValueError("Tool call did not include a function name.")

    @staticmethod
    def _tool_arguments(tool_call: Mapping[str, object]) -> dict[str, object]:
        func = tool_call.get("function")
        raw_args = None
        if isinstance(func, dict):
            raw_args = func.get("arguments")
        if raw_args is None:
            raw_args = tool_call.get("arguments")

        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
