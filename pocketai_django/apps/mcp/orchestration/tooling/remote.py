from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Mapping

from apps.conversations.models import Conversation
from apps.rag.observability.logging import structured_log

from ...connectors import mcp_connection_auth_headers
from ...remote_client import (
    McpRemoteError,
    McpRemoteHttpStatusError,
    McpRemoteProtocolError,
    McpRemoteSsrBlockedError,
    McpRemoteTransportError,
    call_mcp_tool_streamable_http,
)


logger = logging.getLogger(__name__)


class McpRemoteToolsMixin:

    @staticmethod
    def _mcp_idempotency_key(*, conversation_id: object, event_id: str) -> str:
        seed = f"{conversation_id}:{event_id}".encode("utf-8", errors="ignore")
        digest = hashlib.sha256(seed).hexdigest()[:32]
        return f"pocketai-mcp-{digest}"

    @staticmethod
    def _deterministic_retry_delay_seconds(seed: str, attempt: int) -> float:
        normalized_attempt = max(1, int(attempt))
        base = 0.25 * (2 ** min(6, normalized_attempt - 1))
        base = min(2.0, base)
        digest = hashlib.sha256(f"{seed}:{normalized_attempt}".encode("utf-8", errors="ignore")).digest()
        jitter = int.from_bytes(digest[:2], "big") / 65536.0
        return min(2.0, base + (0.25 * jitter))

    def _execute_remote_mcp_tool(
        self,
        *,
        tool_name: str,
        remote_tool_name: str,
        connection: object,
        arguments: Mapping[str, object],
        conversation: Conversation,
        idempotency_key: str | None = None,
        operation_type: str | None = None,
    ) -> Mapping[str, object]:
        """
        Execute an externally configured MCP tool call by forwarding to the remote MCP server.

        Returned mapping is shaped to be prompt-friendly and to flow through existing
        tool trace + compaction logic.
        """

        connection_id = getattr(connection, "id", None)
        connection_name = getattr(connection, "name", None) or ""
        endpoint_url = getattr(connection, "server_url", None) or ""
        connection_status = str(getattr(connection, "status", "") or "").lower()

        remote_meta = {
            "connection_id": str(connection_id) if connection_id else None,
            "connection_name": connection_name or None,
            "tool": remote_tool_name,
            "endpoint_url": endpoint_url,
        }

        if connection_status and connection_status != "enabled":
            return {
                "tool": tool_name,
                "status": "blocked",
                "error_code": "mcp_disabled",
                "error": "MCP connection is disabled.",
                "hint": "Enable this MCP connection to use its tools.",
                "remote": remote_meta,
            }

        headers = mcp_connection_auth_headers(connection)  # type: ignore[arg-type]
        try:
            operation_norm = str(operation_type or "").strip().lower()
            safe_retry = operation_norm == "read"
            max_attempts = 3 if safe_retry else 1
            attempts = 0
            while True:
                attempts += 1
                try:
                    result = call_mcp_tool_streamable_http(
                        endpoint_url=endpoint_url,
                        tool_name=remote_tool_name,
                        arguments=dict(arguments),
                        headers=headers,
                        idempotency_key=idempotency_key,
                    )
                    break
                except McpRemoteSsrBlockedError as exc:
                    raise exc
                except McpRemoteProtocolError as exc:
                    raise exc
                except McpRemoteTransportError as exc:
                    status_code = getattr(exc, "status_code", None)
                    if not safe_retry or attempts >= max_attempts:
                        raise exc
                    if isinstance(exc, McpRemoteHttpStatusError):
                        retryable_statuses = {408, 429, 500, 502, 503, 504}
                        if status_code is not None and status_code not in retryable_statuses:
                            raise exc
                    delay_s = self._deterministic_retry_delay_seconds(
                        idempotency_key or f"{conversation.id}:{remote_tool_name}",
                        attempts,
                    )
                    retry_after_value = getattr(exc, "retry_after", None)
                    if status_code == 429 and retry_after_value and str(retry_after_value).strip().isdigit():
                        retry_after_s = float(str(retry_after_value).strip())
                        if 0.0 < retry_after_s <= 2.0:
                            delay_s = max(delay_s, retry_after_s)
                    time.sleep(delay_s)
        except McpRemoteError as exc:
            structured_log(
                "mcp",
                "remote_tool_call_failed",
                {
                    "tool": tool_name,
                    "remote_tool": remote_tool_name,
                    "error": str(exc),
                    "status_code": getattr(exc, "status_code", None),
                },
                indent=1,
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
                level=logging.WARNING,
            )
            return {
                "tool": tool_name,
                "status": "error",
                "error_code": "mcp_call_failed",
                "error": str(exc)[:800],
                "hint": "Test the MCP connection and verify authentication.",
                "remote": remote_meta,
            }

        is_error = bool(result.get("is_error"))
        status = "error" if is_error else "ok"
        error_code = "mcp_tool_error" if is_error else None

        return {
            "tool": tool_name,
            "status": status,
            **({"error_code": error_code} if error_code else {}),
            "is_error": is_error,
            "text": str(result.get("text") or ""),
            "content": result.get("content") if isinstance(result.get("content"), list) else [],
            "remote": remote_meta,
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

    @staticmethod
    def _tool_signature(tool_name: str, arguments: Mapping[str, object]) -> str:
        """
        Generate a stable signature for a tool invocation so we can detect
        duplicate/no-progress tool loops.
        """
        try:
            args_json = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            args_json = str(arguments)
        return f"{tool_name}:{args_json}"
