from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from apps.accounts.models import McpConnectionApprovalMode, McpToolOperationType
from apps.conversations.models import Conversation

from .... import tools as mcp_tools
from ....types import ToolExecutionContext


@dataclass(frozen=True)
class _InternalToolExecutionResult:
    tool_result: object | None
    call_origin: str
    created_email_draft: Mapping[str, str] | None


class McpTurnInternalToolsMixin:
    def _execute_internal_tool(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
        tool_name: str,
        effective_arguments: Mapping[str, object],
        tool_call_id: str,
        tool_event_id: str,
        apply_general_override: bool,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_tool_approval: bool,
    ) -> _InternalToolExecutionResult:
        tool_result: object | None = None
        call_origin = "live"
        created_email_draft: Mapping[str, str] | None = None
        if apply_general_override:
            general_override_mode = self._tool_approval_override_for_tool(
                conversation=conversation,
                tool_name=tool_name,
            )
        else:
            general_override_mode = ""
        if general_override_mode == "confirm":
            approval_requirement = {
                "requires_approval": True,
                "approval_mode": McpConnectionApprovalMode.APPROVE_ALL,
                "operation_type": McpToolOperationType.UNKNOWN,
                "reason": "controls_override_confirm",
            }
            approved, _, approval_result = self._maybe_request_tool_approval(
                conversation=conversation,
                connection=None,
                tool_name=tool_name,
                remote_tool_name="",
                tool_call_id=tool_call_id,
                tool_event_id=tool_event_id,
                arguments=effective_arguments,
                approval_requirement=approval_requirement,
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
        if tool_name == "email_create_draft" and isinstance(tool_result, Mapping):
            status_value = str(tool_result.get("status") or "").strip().lower()
            if status_value == "ok":
                account_id = self._try_parse_uuid(
                    str(tool_result.get("email_account_id") or "").strip()
                )
                draft_id = str(tool_result.get("draft_id") or "").strip()
                if account_id and draft_id:
                    created_email_draft = {
                        "draft_id": draft_id,
                        "email_account_id": str(account_id),
                    }
                    draft_preview = self._email_tool_event_input(
                        "email_create_draft",
                        effective_arguments,
                    )
                    self._set_pending_email_draft(
                        conversation=conversation,
                        email_account_id=account_id,
                        provider=str(tool_result.get("provider") or ""),
                        draft_id=draft_id,
                        message_id=str(tool_result.get("message_id") or "").strip(),
                        thread_id=str(tool_result.get("thread_id") or "").strip(),
                        preview=draft_preview if isinstance(draft_preview, Mapping) else None,
                    )

        return _InternalToolExecutionResult(
            tool_result=tool_result,
            call_origin=call_origin,
            created_email_draft=created_email_draft,
        )
