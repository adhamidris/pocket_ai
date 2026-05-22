from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from apps.conversations.models import Conversation

from .... import tools as mcp_tools
from ....types import ToolExecutionContext


@dataclass(frozen=True)
class _EmailSendDraftResult:
    tool_result: object | None
    call_origin: str
    effective_arguments: Mapping[str, object]


class McpTurnEmailToolsMixin:
    def _execute_email_send_draft_tool(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
        tool_name: str,
        effective_arguments: Mapping[str, object],
        tool_call_id: str,
        tool_event_id: str,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_tool_approval: bool,
    ) -> _EmailSendDraftResult:
        tool_result: object | None = None
        call_origin = "live"
        draft_id = str(
            effective_arguments.get("draft_id") or effective_arguments.get("draftId") or ""
        ).strip()
        email_account = self._resolve_email_account_for_tool_call(
            conversation=conversation,
            arguments=effective_arguments,
        )
        if self._looks_like_placeholder_draft_id(draft_id) or not draft_id:
            pending = self._pending_email_draft_for_conversation(
                conversation=conversation,
                email_account_id=getattr(email_account, "id", None) if email_account else None,
            )
            if pending:
                effective_arguments = dict(effective_arguments)
                effective_arguments.pop("draftId", None)
                effective_arguments["draft_id"] = pending["draft_id"]
                draft_id = pending["draft_id"]
        approval_needed = False
        approval_reason = "draft_plus_approval_default"
        if email_account and draft_id:
            approval_needed, approval_reason = self._email_send_requires_approval(
                conversation=conversation,
                email_account=email_account,
                draft_id=draft_id,
            )
        if approval_needed:
            approved, _, approval_result = self._maybe_request_email_tool_approval(
                conversation=conversation,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_event_id=tool_event_id,
                arguments=effective_arguments,
                reason=approval_reason,
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
            if email_account and isinstance(tool_result, Mapping):
                self._record_email_send_audit(
                    conversation=conversation,
                    email_account=email_account,
                    tool_result=tool_result,
                )
                status_value = str(tool_result.get("status") or "").strip().lower()
                if status_value == "ok":
                    resolved_draft_id = str(
                        tool_result.get("draft_id")
                        or tool_result.get("draftId")
                        or draft_id
                    ).strip()
                    self._clear_pending_email_draft(
                        conversation=conversation,
                        email_account_id=getattr(email_account, "id", None),
                        draft_id=resolved_draft_id,
                    )
        return _EmailSendDraftResult(
            tool_result=tool_result,
            call_origin=call_origin,
            effective_arguments=effective_arguments,
        )
