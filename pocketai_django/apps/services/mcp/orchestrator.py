"""
MCP-style orchestrator skeleton.

The goal is to keep this implementation self-contained so we can experiment
with standard tool-calling workflows without disturbing the legacy
AiOrchestratorService. Later phases will flesh out the orchestration loop,
tool dispatch, and plan construction logic.
"""

from __future__ import annotations

import json
import uuid
from typing import Callable, Mapping

from django.conf import settings

from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationExtractionType
from apps.services.ai_orchestrator import (
    AiOrchestratorPlan,
    PlannedAction,
    ExtractionPlan,
    KnowledgeSnippet,
    ActionType,
)

from . import prompts, tools
from .types import BaseMcpProvider, ToolExecutionContext


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
        self.max_tool_iterations = 6
        self.business_override_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
        default_chunk_reads = max(1, int(getattr(settings, "RAG_MAX_CHUNK_READS_PER_TURN", 3)))
        self.max_chunk_reads_per_turn = max(
            1,
            int(self._business_override(agent.business_profile, "max_chunk_reads_per_turn", default_chunk_reads)),
        )

    def run_turn(
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
        tool_context = ToolExecutionContext(max_chunk_reads_per_turn=self.max_chunk_reads_per_turn)

        if not self.provider:
            raise RuntimeError("MCP provider is not configured.")

        transcript = list(messages)
        final_assistant_message: dict[str, object] | None = None
        placeholder_sent = False

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
                        on_status_change("reading_document")
                    if on_placeholder_response and not placeholder_sent:
                        placeholder_text = str(assistant_message.get("content") or "").strip()
                        if placeholder_text:
                            on_placeholder_response(placeholder_text)
                            placeholder_sent = True

                tool_result = tools.execute_tool(
                    tool_name,
                    arguments,
                    conversation=conversation,
                    context=tool_context,
                )
                tool_context.add_tool_trace(
                    {
                        "tool": tool_name,
                        "arguments": arguments,
                        "result_keys": sorted(tool_result.keys()),
                    }
                )
                if self._is_knowledge_tool(tool_name):
                    self._record_knowledge_outputs(tool_context, tool_result)
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

        plan = self._build_plan_from_assistant(
            conversation=conversation,
            assistant_message=final_assistant_message or {},
            tool_context=tool_context,
        )
        del on_status_change, on_placeholder_response
        return plan

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
            "llm_strategy": "mcp_tools",
            "knowledge_reads": getattr(tool_context, "knowledge_reads", []),
            "tool_trace": getattr(tool_context, "tool_trace", []),
            "placeholder_response": assistant_message.get("placeholder_response"),
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
                    public_label=entry.get("public_label"),
                    structured_tables=entry.get("structuredTables") or (),
                    issues=entry.get("issues") or (),
                    page_summaries=entry.get("pageSummaries") or (),
                    read_state=entry.get("read_state") or "summary",
                    topic_hints=entry.get("topic_hints") or (),
                    is_pinned=bool(entry.get("pin")),
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
                )
            except Exception:
                continue
            citations.append(snippet)
        return tuple(citations)

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
