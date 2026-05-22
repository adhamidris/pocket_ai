from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

from ....runtime.budget_guidance import search_budget_exceeded_payload
from ....types import ToolExecutionContext
from .ui import _tool_spinner_text as _tool_spinner_text_impl


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _PreparedToolCall:
    tool_name: str
    raw_arguments: Mapping[str, object] | object
    arguments: dict[str, object]
    llm_requested_tool_name: str
    llm_requested_arguments: Mapping[str, object]
    tool_call_id: str
    tool_event_id: str
    policy_tool_result: Mapping[str, object] | None
    ui_spinner_text: str


@dataclass(frozen=True)
class _PreparedInternalToolExecution:
    effective_arguments: dict[str, object]
    internal_event_payload: dict[str, object]


class McpTurnToolPreparationMixin:
    def _prepare_tool_call_for_execution(
        self,
        *,
        tool_call: Mapping[str, object],
        tool_context: ToolExecutionContext,
    ) -> _PreparedToolCall:
        tool_name = self._tool_name(tool_call)
        raw_arguments = self._tool_arguments(tool_call)
        arguments = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
        llm_requested_arguments = copy.deepcopy(raw_arguments) if isinstance(raw_arguments, Mapping) else {}

        # Strip UI-only hints from tool arguments so they never leak into tool execution.
        # Spinner UX is driven by backend status/tool events (single source of truth).
        arguments.pop("__ui", None)
        arguments.pop("spinner_text", None)
        tool_call_id = str(tool_call.get("id") or "").strip()
        tool_event_id = tool_call_id or str(uuid.uuid4())

        # Cross-turn continuity: if the model invents non-UUID ref IDs on
        # follow-up read_knowledge turns, repair from persisted search refs.
        if tool_name == "read_knowledge":
            arguments = self._repair_read_knowledge_refs_from_context(arguments, tool_context)

        policy_tool_result = None
        missing_fields = self._missing_required_fields(tool_name, arguments)
        if missing_fields:
            policy_tool_result = self._missing_required_payload(tool_name, missing_fields)

        if tool_name == "search_knowledge" and not policy_tool_result:
            remaining_searches = self._search_budget_remaining(tool_context)
            if remaining_searches == 0:
                policy_tool_result = search_budget_exceeded_payload(
                    tool_context,
                    reason="orchestrator_policy",
                )

        return _PreparedToolCall(
            tool_name=tool_name,
            raw_arguments=raw_arguments,
            arguments=arguments,
            llm_requested_tool_name=str(tool_name),
            llm_requested_arguments=llm_requested_arguments,
            tool_call_id=tool_call_id,
            tool_event_id=tool_event_id,
            policy_tool_result=policy_tool_result,
            ui_spinner_text=_tool_spinner_text_impl(raw_arguments, clip_text=self._clip_text),
        )

    def _prepare_internal_tool_execution(
        self,
        *,
        tool_name: str,
        arguments: Mapping[str, object],
        tool_call_id: str,
        tool_event_id: str,
        ui_spinner_text: str,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
    ) -> _PreparedInternalToolExecution:
        is_email_tool = self._is_email_tool(tool_name)
        effective_arguments = (
            self._sanitize_email_tool_arguments(tool_name, arguments)
            if is_email_tool
            else arguments
        )
        internal_event_payload = {
            "event_id": tool_event_id,
            "phase": "started",
            "status": "running",
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": "email" if is_email_tool else "mcp_internal",
        }
        if ui_spinner_text:
            internal_event_payload["spinner_text"] = ui_spinner_text
        # Keep internal tool inputs minimal; portal UI should render user-facing
        # results via dedicated blocks rather than surfacing full tool arguments.
        if is_email_tool:
            email_input = self._email_tool_event_input(tool_name, effective_arguments)
            if email_input:
                internal_event_payload["input"] = email_input
        elif tool_name in {"mcp_search_tools", "search_knowledge", "search_conversation_files"}:
            query_text = ""
            query_values = effective_arguments.get("queries")
            if isinstance(query_values, list):
                for value in query_values:
                    query_text = str(value or "").strip()
                    if query_text:
                        break
            if not query_text:
                query_value = effective_arguments.get("query")
                if isinstance(query_value, str):
                    query_text = query_value.strip()
                elif query_value is not None:
                    query_text = str(query_value).strip()
            if query_text:
                internal_event_payload["input"] = {
                    "query": self._clip_text(query_text, 280),
                }
        elif tool_name == "request_user_input":
            prompt_value = effective_arguments.get("prompt")
            prompt_text = str(prompt_value).strip() if prompt_value is not None else ""
            raw_questions = effective_arguments.get("questions")
            questions: list[str] = []
            if isinstance(raw_questions, list):
                for q in raw_questions:
                    if not isinstance(q, str):
                        continue
                    qt = q.strip()
                    if qt:
                        questions.append(self._clip_text(qt, 200))
            if prompt_text or questions:
                payload: dict[str, object] = {}
                if prompt_text:
                    payload["prompt"] = self._clip_text(prompt_text, 280)
                if questions:
                    payload["questions"] = questions[:5]
                internal_event_payload["input"] = payload
        if on_tool_event:
            try:
                on_tool_event(internal_event_payload)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal tool event start callback failed")
        return _PreparedInternalToolExecution(
            effective_arguments=effective_arguments,
            internal_event_payload=internal_event_payload,
        )
