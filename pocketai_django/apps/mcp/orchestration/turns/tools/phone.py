from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from apps.conversations.models import Conversation

from .... import tools as mcp_tools
from ....types import ToolExecutionContext


@dataclass(frozen=True)
class _PhoneToolResult:
    tool_result: object | None
    call_origin: str


class McpTurnPhoneToolsMixin:
    def _execute_phone_call_tool(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
        tool_name: str,
        effective_arguments: Mapping[str, object],
        tool_call_id: str,
        tool_event_id: str,
        seen_phone_call_signatures: set[str],
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_tool_approval: bool,
    ) -> _PhoneToolResult:
        tool_result: object | None = None
        call_origin = "live"
        dedupe_payload = self._phone_tool_input_payload(effective_arguments)
        dedupe_signature = self._tool_signature(tool_name, dedupe_payload)
        if dedupe_signature in seen_phone_call_signatures:
            tool_result = self._duplicate_phone_call_payload(tool_name)
            call_origin = "policy"
        else:
            seen_phone_call_signatures.add(dedupe_signature)
            approved, _, approval_result = self._maybe_request_phone_tool_approval(
                conversation=conversation,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_event_id=tool_event_id,
                arguments=effective_arguments,
                on_tool_event=on_tool_event,
                wait_for_approval=wait_for_tool_approval,
            )
            if not approved:
                tool_result = approval_result
                call_origin = "policy"
            if tool_result is None:
                tool_result = mcp_tools.execute_tool(
                    tool_name,
                    effective_arguments,
                    conversation=conversation,
                    context=tool_context,
                )
        return _PhoneToolResult(tool_result=tool_result, call_origin=call_origin)
