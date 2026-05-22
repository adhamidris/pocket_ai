from __future__ import annotations

import json
import logging
from typing import Mapping

from django.conf import settings

from apps.conversations.models import Conversation
from apps.rag.observability.logging import structured_log

from ....types import ToolExecutionContext


logger = logging.getLogger(__name__)


class McpTurnToolTraceMixin:
    def _append_tool_result_to_trace_and_transcript(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
        transcript: list[dict[str, object]],
        iteration_executed_tools: list[tuple[str, str]],
        tool_name: str,
        tool_call_id: object,
        tool_result: object | None,
        arguments: Mapping[str, object],
        llm_requested_tool_name: str,
        llm_requested_arguments: Mapping[str, object],
        call_duration_ms: float | None,
        call_origin: str,
        cache_hit: bool,
    ) -> object | None:
        trace_index: int | None = None
        if isinstance(tool_result, Mapping):
            # Layer 2: tool responses carry budget telemetry instead of mid-loop
            # injected system messages.
            if tool_name in {"search_knowledge", "read_knowledge"}:
                if bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True)):
                    tool_result = dict(tool_result)
                    tool_result["budget"] = tool_context.budget_snapshot()

            diagnostics = (
                tool_result.get("diagnostics")
                if isinstance(tool_result.get("diagnostics"), Mapping)
                else {}
            )
            engine_tool = tool_result.get("engine_tool") or diagnostics.get("engine_tool")
            mode = tool_result.get("mode") or diagnostics.get("mode")
            page = tool_result.get("page") or diagnostics.get("page")
            token_budget = tool_result.get("token_budget") or diagnostics.get("token_budget")

            trace_arguments = arguments
            if self._is_email_tool(tool_name):
                trace_arguments = self._email_tool_trace_arguments(tool_name, arguments)

            output_summary: dict[str, object] | None = None
            try:
                output_summary = self._tool_trace_output_summary(tool_name, tool_result)
            except Exception:  # pragma: no cover - must never break tool loop
                logger.exception("mcp tool trace output summary failed")

            tool_context.add_tool_trace(
                {
                    "tool": tool_name,
                    "arguments": trace_arguments,
                    "llm_request": {
                        "tool": llm_requested_tool_name,
                        "arguments": llm_requested_arguments,
                    },
                    "result_keys": sorted(tool_result.keys()),
                    "status": tool_result.get("status"),
                    "error_code": tool_result.get("error_code"),
                    "hint": tool_result.get("hint"),
                    "engine": tool_result.get("engine"),
                    "engine_tool": engine_tool,
                    "mode": mode,
                    "page": page,
                    "token_budget": token_budget,
                    "throttle_notice": bool(tool_result.get("throttle_notice")),
                    "duration_ms": int(call_duration_ms) if call_duration_ms is not None else 0,
                    "origin": call_origin,
                    "cache_hit": cache_hit,
                    "output_summary": output_summary,
                }
            )
            trace_index = len(tool_context.tool_trace) - 1

            if self._is_knowledge_tool(tool_name):
                self._record_knowledge_outputs(tool_context, tool_result)
        tool_status_value = ""
        if isinstance(tool_result, Mapping):
            tool_status_value = str(tool_result.get("status") or "").strip().lower()
        iteration_executed_tools.append((tool_name, tool_status_value))
        limits = self._prompt_compaction_limits()
        prompt_tool_result: object
        if isinstance(tool_result, Mapping):
            prompt_tool_result = self._compact_tool_payload_for_prompt(
                tool_name,
                tool_result,
                **limits,
            )
        else:
            prompt_tool_result = {
                "tool": tool_name,
                "result": self._clip_text(tool_result, 2000) if tool_result is not None else None,
                "prompt_compact": True,
            }

        raw_tool_json = json.dumps(prompt_tool_result, ensure_ascii=False)
        truncated_tool_json = self._truncate_tool_message_for_prompt(tool_name, raw_tool_json)
        # Observability: track when we had to truncate a *knowledge* tool message before it
        # hit the LLM prompt (this should trend to ~0 in agentic-v2 mode).
        try:
            tool_output_limit = self._tool_output_max_chars()
        except Exception:
            tool_output_limit = 0
        if (
            tool_output_limit
            and self._is_knowledge_tool(tool_name)
            and isinstance(raw_tool_json, str)
            and len(raw_tool_json) > tool_output_limit
        ):
            structured_log(
                "mcp",
                "prompt.tool_output_truncated",
                {
                    "tool": tool_name,
                    "raw_chars": len(raw_tool_json),
                    "limit": int(tool_output_limit),
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
                level=logging.WARNING,
            )

        if (
            trace_index is not None
            and isinstance(getattr(tool_context, "tool_trace", None), list)
            and 0 <= trace_index < len(tool_context.tool_trace)
            and isinstance(tool_context.tool_trace[trace_index], dict)
        ):
            try:
                tool_context.tool_trace[trace_index]["prompt_compaction"] = {
                    "raw_chars": int(len(raw_tool_json) if isinstance(raw_tool_json, str) else 0),
                    "truncated_chars": int(
                        len(truncated_tool_json) if isinstance(truncated_tool_json, str) else 0
                    ),
                    "limit_chars": int(tool_output_limit or 0),
                    "truncated": bool(
                        isinstance(raw_tool_json, str)
                        and isinstance(truncated_tool_json, str)
                        and raw_tool_json != truncated_tool_json
                    ),
                }
                tool_context.tool_trace[trace_index]["llm_response"] = {
                    "tool_call_id": str(tool_call_id or ""),
                    "content": truncated_tool_json,
                }
            except Exception:  # pragma: no cover - must never break tool loop
                logger.exception("mcp tool trace prompt compaction patch failed")

        transcript.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": truncated_tool_json,
            }
        )
        return tool_result
