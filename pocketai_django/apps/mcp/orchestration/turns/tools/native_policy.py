from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from apps.accounts.models import McpToolOperationType
from apps.conversations.models import Conversation


@dataclass(frozen=True)
class _NativePolicyResult:
    tool_result: object | None
    call_origin: str
    effective_arguments: Mapping[str, object]


class McpTurnNativePolicyMixin:
    def _apply_native_tool_policy(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        effective_arguments: Mapping[str, object],
        tool_call_id: str,
        tool_event_id: str,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_tool_approval: bool,
    ) -> _NativePolicyResult:
        tool_result: object | None = None
        call_origin = "live"
        native_tool_policy = self._resolve_native_integration_policy(
            conversation=conversation,
            tool_name=tool_name,
            arguments=effective_arguments,
        )
        decision = str(native_tool_policy.get("decision") or "").strip()
        if decision == "deny":
            tool_result = (
                dict(native_tool_policy.get("error_payload") or {})
                if isinstance(native_tool_policy.get("error_payload"), Mapping)
                else {
                    "tool": tool_name,
                    "status": "error",
                    "error": str(native_tool_policy.get("reason_code") or "not_connected"),
                    "error_code": str(native_tool_policy.get("reason_code") or "not_connected"),
                    "hint": "Integration tool call is blocked by policy.",
                }
            )
            call_origin = "policy"
        elif decision == "allow_with_confirmation":
            approval_requirement = {
                "requires_approval": True,
                "approval_mode": native_tool_policy.get("approval_mode")
                or self._effective_tool_approval_mode(conversation=conversation),
                "operation_type": native_tool_policy.get("operation_type")
                or McpToolOperationType.UNKNOWN,
                "reason": native_tool_policy.get("reason") or "native_integration_write",
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
                tool_result = dict(approval_result) if isinstance(approval_result, Mapping) else {}
                tool_result["tool"] = tool_name
                tool_result["error"] = "approval_required"
                tool_result["error_code"] = "approval_required"
                if not str(tool_result.get("hint") or "").strip():
                    tool_result["hint"] = (
                        "User approval is required before this integration action can run."
                    )
                call_origin = "policy"

        resolved_account_id = str(
            native_tool_policy.get("resolved_integration_account_id") or ""
        ).strip()
        if tool_result is None and resolved_account_id:
            effective_arguments = dict(effective_arguments)
            effective_arguments["integration_account_id"] = resolved_account_id
        return _NativePolicyResult(
            tool_result=tool_result,
            call_origin=call_origin,
            effective_arguments=effective_arguments,
        )
