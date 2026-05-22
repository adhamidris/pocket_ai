from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from apps.conversations.models import Conversation

from core.otel import otel_trace

from ... import prompts
from ...runtime.portal_block_stream import _PortalBlockStream
from ...types import ToolExecutionContext


TRACER = otel_trace.get_tracer(__name__)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _InitialPassResult:
    first_stream_message: dict[str, object]
    first_stream_tool_calls: list[Mapping[str, object]]
    first_content_raw: str
    pending_assistant: dict[str, object] | None


class McpTurnInitialPassMixin:
    def _run_initial_pass(
        self,
        *,
        conversation: Conversation,
        transcript: list[Mapping[str, object]],
        initial_tools: Sequence[Mapping[str, object]],
        streaming_allowed: bool,
        tool_context: ToolExecutionContext,
        portal_block_stream: _PortalBlockStream,
        first_stream_chunk: Callable[[str], None],
        on_stream_tool_call_delta: Callable[[Mapping[str, object] | None], None],
        split_portal_tool_calls: Callable[
            [Sequence[Mapping[str, object]]],
            tuple[list[Mapping[str, object]], list[Mapping[str, object]]],
        ],
        on_tool_decision: Callable[[str], None] | None = None,
        on_reasoning_event: Callable[[Mapping[str, object]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> _InitialPassResult:
        # Limit the initial payload so the provider only sees the guardrails and
        # the latest transcript entries needed for intent selection.
        primary_messages = prompts.limit_messages_for_stage(transcript, stage="initial_pass")
        self._log_prompt("primary", conversation=conversation, messages=primary_messages)
        with TRACER.start_as_current_span("portal.mcp.initial_pass") as initial_span:
            if initial_span.is_recording():
                initial_span.set_attribute("mcp.message_count", len(primary_messages))
                initial_span.set_attribute("mcp.tools_enabled", bool(initial_tools))
            first_payload = self._chat_with_context_governor(
                conversation=conversation,
                stage="initial_pass",
                messages=primary_messages,
                tools=initial_tools,
                on_stream_delta=first_stream_chunk if streaming_allowed else None,
                on_tool_call_start=None,
                on_tool_call_delta=on_stream_tool_call_delta,
                tool_context=tool_context,
                on_reasoning_event=on_reasoning_event,
                reasoning_label="Initial pass",
                should_cancel=should_cancel,
            )
        first_message = self._coerce_assistant_message(first_payload)
        first_stream_message = dict(first_message or {})
        first_stream_tool_calls_raw = list(first_stream_message.get("tool_calls") or [])
        first_stream_tool_calls, portal_tool_calls = split_portal_tool_calls(first_stream_tool_calls_raw)
        if portal_tool_calls:
            portal_block_stream.ingest_tool_calls(portal_tool_calls)
        if on_tool_decision:
            try:
                on_tool_decision("used" if first_stream_tool_calls else "no_tools")
            except Exception:  # pragma: no cover - defensive
                logger.exception("on_tool_decision callback failed")
        first_content_raw = ""
        if first_stream_message:
            first_content_raw = str(first_stream_message.get("content") or "").strip()
        pending_assistant = None
        if first_stream_tool_calls:
            # Defer appending until we process the tool call in the loop; initial
            # stream chunks stay buffered alongside the first pass message.
            pending_assistant = first_stream_message
        else:
            # No tools; keep streamed assistant content in transcript.
            transcript.append(
                {
                    "role": "assistant",
                    "content": first_content_raw,
                }
            )
        return _InitialPassResult(
            first_stream_message=first_stream_message,
            first_stream_tool_calls=first_stream_tool_calls,
            first_content_raw=first_content_raw,
            pending_assistant=pending_assistant,
        )
