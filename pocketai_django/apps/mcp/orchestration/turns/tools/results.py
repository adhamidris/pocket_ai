from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from apps.conversations.models import Conversation

from ....runtime.tool_artifacts import (
    build_prompt_view_for_remote_tool_result,
    store_remote_tool_output_artifact,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ToolResultFinalization:
    tool_result: object | None
    call_duration_ms: float | None


class McpTurnToolResultsMixin:
    def _finalize_tool_result_after_execution(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        tool_call_id: str,
        tool_event_id: str,
        tool_result: object | None,
        call_start: float | None,
        remote_event_id: str | None,
        remote_event_payload: Mapping[str, object] | None,
        internal_event_payload: Mapping[str, object] | None,
        on_tool_event: Callable[[Mapping[str, object]], None] | None,
    ) -> _ToolResultFinalization:
        call_duration_ms: float | None = None
        if call_start is not None:
            call_duration_ms = (time.perf_counter() - call_start) * 1000.0
        if remote_event_id and remote_event_payload and isinstance(tool_result, Mapping):
            # Phase 1: Store full external MCP tool outputs out-of-band (tenant-scoped)
            # and feed only a compact prompt_view + artifact_id back into the LLM loop.
            try:
                prompt_view = build_prompt_view_for_remote_tool_result(tool_result)
                artifact_id = store_remote_tool_output_artifact(
                    conversation=conversation,
                    tool_call_id=tool_call_id,
                    tool_event_id=tool_event_id,
                    invoked_tool=tool_name,
                    remote_event_payload=remote_event_payload,
                    tool_result=tool_result,
                )
                remote_safe: dict[str, object] = {}
                remote_meta = (
                    remote_event_payload.get("remote")
                    if isinstance(remote_event_payload.get("remote"), Mapping)
                    else None
                )
                if remote_meta:
                    connection_name = remote_meta.get("connection_name")
                    remote_tool = remote_meta.get("remote_tool")
                    if connection_name:
                        remote_safe["connection_name"] = str(connection_name)[:240]
                    if remote_tool:
                        remote_safe["tool"] = str(remote_tool)[:240]
                tool_id_for_model = str(remote_event_payload.get("tool_name") or "").strip()
                tool_result = {
                    "tool": tool_name,
                    "status": tool_result.get("status"),
                    "error_code": tool_result.get("error_code"),
                    "error": tool_result.get("error"),
                    "hint": tool_result.get("hint"),
                    "is_error": bool(tool_result.get("is_error")),
                    "tool_id": tool_id_for_model,
                    **({"artifact_id": artifact_id} if artifact_id else {}),
                    **({"remote": remote_safe} if remote_safe else {}),
                    "prompt_view": prompt_view,
                    "prompt_compact": True,
                }
            except Exception:  # pragma: no cover - must never break tool loop
                logger.exception("mcp tool output isolation failed")
                tool_result = {
                    "tool": tool_name,
                    "status": tool_result.get("status"),
                    "error_code": tool_result.get("error_code"),
                    "error": tool_result.get("error"),
                    "hint": tool_result.get("hint"),
                    "is_error": bool(tool_result.get("is_error")),
                    "truncated": True,
                    "prompt_compact": True,
                }
        if remote_event_id and remote_event_payload and on_tool_event:
            try:
                finish_payload = dict(remote_event_payload)
                finish_payload["phase"] = "finished"
                finish_payload["duration_ms"] = (
                    int(call_duration_ms) if call_duration_ms is not None else 0
                )
                if isinstance(tool_result, Mapping):
                    finish_payload["status"] = str(tool_result.get("status") or "") or "ok"
                    finish_payload["output"] = dict(tool_result)
                on_tool_event(finish_payload)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal tool event finish callback failed")
        if internal_event_payload and on_tool_event:
            try:
                finish_payload = dict(internal_event_payload)
                finish_payload["phase"] = "finished"
                finish_payload["duration_ms"] = (
                    int(call_duration_ms) if call_duration_ms is not None else 0
                )
                if isinstance(tool_result, Mapping):
                    finish_payload["status"] = str(tool_result.get("status") or "") or "ok"
                    if self._is_email_tool(tool_name):
                        finish_payload["output"] = self._email_tool_event_output(tool_name, tool_result)
                    else:
                        finish_payload["output"] = self._compact_tool_payload_for_prompt(
                            tool_name,
                            tool_result,
                            **self._prompt_compaction_limits(),
                        )
                on_tool_event(finish_payload)
            except Exception:  # pragma: no cover - UI callback must not break tools
                logger.exception("mcp portal tool event finish callback failed")

        return _ToolResultFinalization(
            tool_result=tool_result,
            call_duration_ms=call_duration_ms,
        )
