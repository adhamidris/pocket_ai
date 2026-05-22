from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from apps.conversations.models import Conversation

from ....connectors import get_tool_approval_requirement
from ....text.redaction import redact_tool_input_payload
from ....types import ToolExecutionContext


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RemoteToolExecutionResult:
    tool_result: object | None
    call_origin: str
    call_start: float | None
    remote_event_id: str | None
    remote_event_payload: dict[str, object] | None


class McpTurnGatewayToolsMixin:
    def _execute_gateway_mcp_call_tool(
        self,
        *,
        conversation: Conversation,
        arguments: Mapping[str, object],
        tool_context: ToolExecutionContext,
        tool_call_id: str,
        tool_event_id: str,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_tool_approval: bool,
    ) -> _RemoteToolExecutionResult:
        call_origin = "live"
        call_start: float | None = None
        remote_event_id: str | None = None
        remote_event_payload: dict[str, object] | None = None

        requested_tool_id = str(arguments.get("tool_id") or "").strip()
        raw_inner_args = arguments.get("arguments")
        inner_args = raw_inner_args if isinstance(raw_inner_args, Mapping) else None
        if inner_args is not None:
            # Guard reserved UI keys from leaking into remote MCP arguments.
            inner_args = dict(inner_args)
            inner_args.pop("__ui", None)
            inner_args.pop("spinner_text", None)
        if not requested_tool_id or inner_args is None:
            return _RemoteToolExecutionResult(
                tool_result={
                    "tool": "mcp_call_tool",
                    "status": "error",
                    "error": "invalid_arguments",
                    "error_code": "invalid_arguments",
                    "output": None,
                    "hint": "Provide tool_id and arguments (object) from mcp_search_tools results.",
                },
                call_origin="validation",
                call_start=call_start,
                remote_event_id=remote_event_id,
                remote_event_payload=remote_event_payload,
            )

        remote_entry = self._remote_tool_registry.get(requested_tool_id)
        catalog_entry = (
            tool_context.mcp_gateway_catalog.get(requested_tool_id)
            if isinstance(getattr(tool_context, "mcp_gateway_catalog", None), Mapping)
            else None
        )
        input_schema = (
            catalog_entry.get("input_schema")
            if isinstance(catalog_entry, Mapping) and isinstance(catalog_entry.get("input_schema"), Mapping)
            else None
        )
        effective_inner_args = inner_args
        defaults_applied: list[str] = []
        if remote_entry:
            try:
                effective_inner_args, defaults_applied = self._apply_mcp_setup_defaults(
                    inner_args,
                    connection=remote_entry[0],
                    input_schema=input_schema,
                )
            except Exception:  # pragma: no cover - defensive
                effective_inner_args = inner_args
                defaults_applied = []
        missing_fields, type_errors = self._validate_gateway_tool_arguments(
            effective_inner_args, input_schema
        )
        if not remote_entry:
            return _RemoteToolExecutionResult(
                tool_result={
                    "tool": "mcp_call_tool",
                    "status": "error",
                    "error": "unknown_tool_id",
                    "error_code": "unknown_tool_id",
                    "tool_id": requested_tool_id,
                    "output": None,
                    "hint": "Call mcp_search_tools to get a valid tool_id for this agent.",
                },
                call_origin="validation",
                call_start=call_start,
                remote_event_id=remote_event_id,
                remote_event_payload=remote_event_payload,
            )
        if missing_fields or type_errors:
            remote_meta = None
            if isinstance(catalog_entry, Mapping):
                remote_meta = {
                    "connection_id": str(catalog_entry.get("connection_id") or "").strip() or None,
                    "connection_name": str(catalog_entry.get("connection_name") or "").strip() or None,
                    "tool": str(catalog_entry.get("remote_tool") or "").strip() or None,
                }
            return _RemoteToolExecutionResult(
                tool_result={
                    "tool": "mcp_call_tool",
                    "status": "error",
                    "error": "validation_failed",
                    "error_code": "validation_failed",
                    "tool_id": requested_tool_id,
                    "missing_fields": missing_fields,
                    "type_errors": type_errors,
                    "output": None,
                    **({"remote": remote_meta} if remote_meta else {}),
                    "hint": "Fix the tool arguments and retry mcp_call_tool. Use mcp_search_tools results[].required_args as a guide.",
                },
                call_origin="validation",
                call_start=call_start,
                remote_event_id=remote_event_id,
                remote_event_payload=remote_event_payload,
            )

        connection, remote_tool_name = remote_entry
        tool_name_for_remote = requested_tool_id
        approval_requirement = get_tool_approval_requirement(
            connection,
            remote_tool_name,
            agent=getattr(conversation, "agent_profile", None),
        )
        skip_remote_execution = False
        tool_result: object | None = None
        if approval_requirement.get("requires_approval"):
            approved, _, approval_result = self._maybe_request_tool_approval(
                conversation=conversation,
                connection=connection,
                tool_name=tool_name_for_remote,
                remote_tool_name=remote_tool_name,
                tool_call_id=tool_call_id,
                tool_event_id=tool_event_id,
                arguments=inner_args,
                approval_requirement=approval_requirement,
                on_tool_event=on_tool_event,
                wait_for_approval=wait_for_tool_approval,
            )
            if not approved:
                tool_result = approval_result
                call_origin = "policy"
                skip_remote_execution = True
        if not skip_remote_execution:
            call_start = time.perf_counter()
            remote_event_id = tool_event_id
            sensitive_keys = self._mcp_setup_fields_for_connection(connection).keys()
            redacted_input = redact_tool_input_payload(inner_args, sensitive_keys=sensitive_keys)
            if not isinstance(redacted_input, Mapping):
                redacted_input = {}
            remote_event_payload = {
                "event_id": remote_event_id,
                "phase": "started",
                "status": "running",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name_for_remote,
                "kind": "mcp_remote",
                "remote": {
                    "connection_id": str(getattr(connection, "id", "") or ""),
                    "connection_name": str(getattr(connection, "name", "") or ""),
                    "endpoint_url": str(getattr(connection, "server_url", "") or ""),
                    "remote_tool": remote_tool_name,
                },
                "input": dict(redacted_input),
            }
            if defaults_applied:
                remote_event_payload["defaults_applied"] = list(defaults_applied)
            if on_tool_event:
                try:
                    on_tool_event(remote_event_payload)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal tool event start callback failed")
            tool_result = self._execute_remote_mcp_tool(
                tool_name=tool_name_for_remote,
                remote_tool_name=remote_tool_name,
                connection=connection,
                arguments=effective_inner_args,
                conversation=conversation,
                idempotency_key=self._mcp_idempotency_key(
                    conversation_id=conversation.id,
                    event_id=tool_event_id,
                ),
                operation_type=str(approval_requirement.get("operation_type") or ""),
            )
        return _RemoteToolExecutionResult(
            tool_result=tool_result,
            call_origin=call_origin,
            call_start=call_start,
            remote_event_id=remote_event_id,
            remote_event_payload=remote_event_payload,
        )

    def _execute_registered_remote_tool(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
        tool_name: str,
        arguments: Mapping[str, object],
        remote_entry: tuple[object, str],
        tool_call_id: str,
        tool_event_id: str,
        ui_spinner_text: str,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
        wait_for_tool_approval: bool,
    ) -> _RemoteToolExecutionResult:
        call_origin = "live"
        call_start: float | None = None
        remote_event_id: str | None = None
        remote_event_payload: dict[str, object] | None = None

        connection, remote_tool_name = remote_entry
        approval_requirement = get_tool_approval_requirement(
            connection,
            remote_tool_name,
            agent=getattr(conversation, "agent_profile", None),
        )
        skip_remote_execution = False
        tool_result: object | None = None
        if approval_requirement.get("requires_approval"):
            approved, _, approval_result = self._maybe_request_tool_approval(
                conversation=conversation,
                connection=connection,
                tool_name=tool_name,
                remote_tool_name=remote_tool_name,
                tool_call_id=tool_call_id,
                tool_event_id=tool_event_id,
                arguments=arguments,
                approval_requirement=approval_requirement,
                on_tool_event=on_tool_event,
                wait_for_approval=wait_for_tool_approval,
            )
            if not approved:
                tool_result = approval_result
                call_origin = "policy"
                skip_remote_execution = True
        if not skip_remote_execution:
            call_start = time.perf_counter()
            catalog_entry = (
                tool_context.mcp_gateway_catalog.get(tool_name)
                if isinstance(getattr(tool_context, "mcp_gateway_catalog", None), Mapping)
                else None
            )
            input_schema = (
                catalog_entry.get("input_schema")
                if isinstance(catalog_entry, Mapping) and isinstance(catalog_entry.get("input_schema"), Mapping)
                else None
            )
            effective_remote_args, defaults_applied = self._apply_mcp_setup_defaults(
                arguments,
                connection=connection,
                input_schema=input_schema,
            )
            remote_event_id = tool_event_id
            sensitive_keys = self._mcp_setup_fields_for_connection(connection).keys()
            redacted_input = redact_tool_input_payload(arguments, sensitive_keys=sensitive_keys)
            if not isinstance(redacted_input, Mapping):
                redacted_input = {}
            remote_event_payload = {
                "event_id": remote_event_id,
                "phase": "started",
                "status": "running",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "kind": "mcp_remote",
                "remote": {
                    "connection_id": str(getattr(connection, "id", "") or ""),
                    "connection_name": str(getattr(connection, "name", "") or ""),
                    "endpoint_url": str(getattr(connection, "server_url", "") or ""),
                    "remote_tool": remote_tool_name,
                },
                "input": dict(redacted_input),
            }
            if ui_spinner_text:
                remote_event_payload["spinner_text"] = ui_spinner_text
            if defaults_applied:
                remote_event_payload["defaults_applied"] = list(defaults_applied)
            if on_tool_event:
                try:
                    on_tool_event(remote_event_payload)
                except Exception:  # pragma: no cover - UI callback must not break tools
                    logger.exception("mcp portal tool event start callback failed")
            tool_result = self._execute_remote_mcp_tool(
                tool_name=tool_name,
                remote_tool_name=remote_tool_name,
                connection=connection,
                arguments=effective_remote_args,
                conversation=conversation,
                idempotency_key=self._mcp_idempotency_key(
                    conversation_id=conversation.id,
                    event_id=tool_event_id,
                ),
                operation_type=str(approval_requirement.get("operation_type") or ""),
            )
        return _RemoteToolExecutionResult(
            tool_result=tool_result,
            call_origin=call_origin,
            call_start=call_start,
            remote_event_id=remote_event_id,
            remote_event_payload=remote_event_payload,
        )
