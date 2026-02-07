from __future__ import annotations

import copy
import inspect
import logging
import threading
import time
import uuid
from typing import Any, Callable, Mapping

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from apps.conversations.content_blocks import (
    extract_text_from_content_blocks,
    new_block_id,
    normalize_assistant_content_blocks,
)
from apps.conversations.portal import ChatPortalService
from apps.conversations.portal_stream_trace import PortalStreamTrace
from apps.conversations.rich_blocks import RichBlockStreamBuilder, apply_block_ops, coerce_block_event
from apps.conversations.portal_turn_events import append_turn_event
from apps.conversations.portal_turn_folding import fold_turn_events, fold_turn_events_from_redis
from apps.conversations.models import (
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    PortalTurn,
    PortalTurnEvent,
    PortalTurnStatus,
)
from apps.llm.llm_provider import load_default_provider
from apps.mcp.sanitizer import sanitize_with_diagnostics
from apps.mcp.tool_artifacts import store_remote_tool_output_artifact
from apps.rag.ai_orchestrator import AiOrchestratorService
from apps.rag.rag_logging import structured_log
from core.tenancy import tenant_context

logger = logging.getLogger(__name__)

TOOL_EVENT_PHASES = {"started", "finished", "approval_requested", "approval_resolved"}


def _business_prefers_mcp(business: object | None, *, conversation: Conversation | None = None) -> bool:
    global_default = getattr(settings, "RAG_USE_MCP_ORCHESTRATOR", False)
    convo_meta = getattr(conversation, "metadata", None)
    if isinstance(convo_meta, dict) and convo_meta.get("mcp_required"):
        return True
    if business is None:
        return global_default
    metadata = getattr(business, "metadata", None)
    if not isinstance(metadata, dict):
        return global_default
    override = metadata.get("mcp_orchestrator_enabled")
    if override is None:
        return global_default
    return bool(override)


def _clip_debug_text(value: object, *, limit: int = 480) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return f"{text[: max(0, limit - 1)].rstrip()}…"
    return text


def _json_safe_debug(value: object, *, depth: int = 3, string_limit: int = 240, list_limit: int = 12) -> object:
    def _is_numeric_metric(v: object) -> bool:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            candidate = v.strip()
            if not candidate:
                return False
            try:
                float(candidate)
            except ValueError:
                return False
            return True
        return False

    if value is None:
        return None
    if depth <= 0:
        return _clip_debug_text(value, limit=string_limit)
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return _clip_debug_text(value, limit=string_limit)
        return value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= list_limit:
                out["…"] = f"+{max(0, len(value) - list_limit)} more keys"
                break
            key_str = str(key or "").strip() or f"key_{idx}"
            lowered = key_str.lower()
            if any(token in lowered for token in ("password", "secret", "api_key", "apikey")):
                out[key_str] = "[REDACTED]"
                continue
            if "token" in lowered:
                safe_token_metrics = {
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "token_limit",
                    "token_count",
                    "tokens",
                }
                if lowered not in safe_token_metrics and not _is_numeric_metric(item):
                    out[key_str] = "[REDACTED]"
                    continue
            out[key_str] = _json_safe_debug(item, depth=depth - 1, string_limit=string_limit, list_limit=list_limit)
        return out
    if isinstance(value, (list, tuple, set)):
        out_list: list[object] = []
        for idx, entry in enumerate(value):
            if idx >= list_limit:
                out_list.append(f"+{max(0, len(value) - list_limit)} more")
                break
            out_list.append(_json_safe_debug(entry, depth=depth - 1, string_limit=string_limit, list_limit=list_limit))
        return out_list
    return _clip_debug_text(value, limit=string_limit)


class PortalTurnEventBuilder:
    def __init__(self, *, turn: PortalTurn, conversation: Conversation) -> None:
        self.turn = turn
        self.conversation = conversation
        self.trace = PortalStreamTrace(turn_id=turn.id, component="worker")
        self.blocks: list[dict[str, object]] = []
        self.blocks_by_id: dict[str, dict[str, object]] = {}
        self.block_ops_active = False
        self.rich_builder = RichBlockStreamBuilder()
        self.tool_use_block_id_by_event_id: dict[str, str] = {}
        self.reasoning_block_id_by_call_id: dict[str, str] = {}
        # Backpressure/coalescing: reduce per-token event spam by batching adjacent block_delta ops.
        # Force-disable coalescing to ensure normalized (smooth) streaming delta-by-delta.
        self.coalesce_block_deltas = False
        self.delta_flush_interval_ms = int(getattr(settings, "PORTAL_TURN_DELTA_FLUSH_INTERVAL_MS", 25) or 25)
        self.delta_flush_interval_ms = max(5, self.delta_flush_interval_ms)
        self.delta_flush_max_ops = int(getattr(settings, "PORTAL_TURN_DELTA_FLUSH_MAX_OPS", 30) or 30)
        self.delta_flush_max_ops = max(10, self.delta_flush_max_ops)
        self._pending_delta_block_id: str | None = None
        self._pending_delta_ops: list[dict[str, object]] = []
        self._pending_delta_last_flush_perf: float | None = None
        # Metrics (Phase 1 observability): keep low overhead; log once per turn.
        self.event_count = 0
        self.db_write_ms = 0.0
        self.first_event_type: str | None = None
        self.first_event_at_perf: float | None = None
        self.last_event_type: str | None = None
        self.had_tool_events = False
        self.trace.record(
            "builder.init",
            {
                "event_bus": str(getattr(settings, "PORTAL_TURN_EVENT_BUS", "postgres") or "postgres"),
                "turn_log_mode": str(getattr(settings, "PORTAL_TURN_EVENT_LOG_MODE", "db") or "db"),
                "coalesce_block_deltas": bool(self.coalesce_block_deltas),
                "delta_flush_interval_ms": int(self.delta_flush_interval_ms or 0),
                "delta_flush_max_ops": int(self.delta_flush_max_ops or 0),
            },
        )

    def _flush_pending_block_delta(self) -> None:
        if not self.coalesce_block_deltas:
            return
        if not self._pending_delta_block_id or not self._pending_delta_ops:
            return
        block_id = self._pending_delta_block_id
        ops = self._pending_delta_ops
        self._pending_delta_block_id = None
        self._pending_delta_ops = []
        self._pending_delta_last_flush_perf = time.perf_counter()
        # Emit a single merged block_delta event.
        self.append_event("block_delta", {"block_id": block_id, "ops": ops})

    def append_event(self, event_type: str, payload: dict | None = None) -> None:
        # Preserve strict ordering: flush any pending deltas before emitting a non-delta event.
        if str(event_type or "").strip().lower() != "block_delta":
            self._flush_pending_block_delta()
        start = time.perf_counter()
        if self.first_event_at_perf is None:
            self.first_event_at_perf = start
            self.first_event_type = event_type
        payload_obj = payload or {}
        # Trace before append so we can see what the worker tried to emit even if persistence fails.
        trace_meta: dict[str, object] = {"type": str(event_type or "event")}
        lowered = str(event_type or "").strip().lower()
        if lowered == "text_delta":
            trace_meta["text_len"] = int(len(str(payload_obj.get("text") or "")))
        elif lowered == "block_delta":
            ops = payload_obj.get("ops")
            trace_meta["ops"] = int(len(ops)) if isinstance(ops, list) else 0
        elif lowered == "block_start":
            block = payload_obj.get("block")
            if isinstance(block, Mapping):
                trace_meta["block_type"] = str(block.get("type") or "")
        elif lowered == "turn_persisted":
            trace_meta["text_len"] = int(len(str(payload_obj.get("text") or "")))
            blocks = payload_obj.get("content_blocks")
            trace_meta["blocks"] = int(len(blocks)) if isinstance(blocks, list) else 0
        self.trace.record("event.append", trace_meta)

        append_turn_event(turn_id=self.turn.id, event_type=event_type, payload=payload_obj)
        self.event_count += 1
        self.db_write_ms += max(0.0, (time.perf_counter() - start) * 1000.0)
        self.last_event_type = event_type
        self.trace.record(
            "event.appended",
            {
                "type": str(event_type or "event"),
                "append_ms": int(max(0.0, (time.perf_counter() - start) * 1000.0)),
                "event_count": int(self.event_count or 0),
            },
        )

    def _append_content_block(self, block: dict[str, object]) -> dict[str, object]:
        block_id = str(block.get("block_id") or "").strip()
        if not block_id:
            block_id = new_block_id()
            block["block_id"] = block_id
        self.blocks.append(block)
        self.blocks_by_id[block_id] = block
        return block

    def _get_content_block(self, block_id: str | None) -> dict[str, object] | None:
        key = (block_id or "").strip()
        if not key:
            return None
        return self.blocks_by_id.get(key)

    def _apply_block_event(self, event: Mapping[str, object]) -> None:
        event_type = str(event.get("type") or "").strip().lower()
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if event_type == "block_start":
            block = payload.get("block")
            if not isinstance(block, Mapping):
                return
            block_id = str(block.get("block_id") or "").strip()
            if not block_id:
                return
            existing = self.blocks_by_id.get(block_id)
            if existing is not None:
                existing.clear()
                existing.update(block)
            else:
                self.blocks.append(dict(block))
                self.blocks_by_id[block_id] = self.blocks[-1]
            return
        if event_type == "block_delta":
            block_id = str(payload.get("block_id") or "").strip()
            if not block_id:
                return
            ops = payload.get("ops")
            if not isinstance(ops, list):
                return
            block = self.blocks_by_id.get(block_id)
            if not block:
                return
            apply_block_ops(block, ops)
            return
        if event_type == "block_end":
            return

    def _apply_block_events_internally(self, events: list[dict[str, object]]) -> None:
        """Apply block events to internal state (blocks/blocks_by_id) without emitting to Redis."""
        if events:
            counts: dict[str, int] = {}
            for ev in events:
                if not isinstance(ev, Mapping):
                    continue
                t = str(ev.get("type") or "").strip().lower() or "event"
                counts[t] = counts.get(t, 0) + 1
            if counts:
                self.trace.record("blocks.apply_internal", {"events": int(len(events)), "types": counts})
        for event in events:
            if not isinstance(event, Mapping):
                continue
            self._apply_block_event(event)

    def emit_block_events(self, events: list[dict[str, object]]) -> None:
        for event in events:
            if not isinstance(event, Mapping):
                continue
            event_type = str(event.get("type") or "").strip()
            payload = dict(event.get("payload") or {})
            lowered = event_type.strip().lower()
            if lowered in {"block_start", "block_delta", "block_end"}:
                self._apply_block_event({"type": event_type, "payload": payload})

            if self.coalesce_block_deltas and lowered == "block_delta":
                block_id = str(payload.get("block_id") or "").strip()
                ops = payload.get("ops")
                if block_id and isinstance(ops, list) and ops:
                    should_flush = False
                    now = time.perf_counter()
                    if self._pending_delta_block_id and self._pending_delta_block_id != block_id:
                        should_flush = True
                    if self._pending_delta_last_flush_perf is not None:
                        elapsed_ms = (now - self._pending_delta_last_flush_perf) * 1000.0
                        if elapsed_ms >= float(self.delta_flush_interval_ms):
                            should_flush = True
                    # Flush if we're accumulating too many ops (avoid huge payloads).
                    if len(self._pending_delta_ops) + len(ops) >= int(self.delta_flush_max_ops):
                        should_flush = True
                    if should_flush:
                        self._flush_pending_block_delta()
                    if not self._pending_delta_block_id:
                        self._pending_delta_block_id = block_id
                    # Copy ops to avoid accidental mutation by upstream components.
                    for op in ops:
                        if isinstance(op, dict):
                            self._pending_delta_ops.append(dict(op))
                        else:
                            self._pending_delta_ops.append({"op": str(op)})
                    if self._pending_delta_last_flush_perf is None:
                        self._pending_delta_last_flush_perf = now
                    # Continue without emitting per-op block_delta events.
                    continue

            self.append_event(event_type, payload)

    def on_block_event(self, event: Mapping[str, object] | None) -> None:
        if not event:
            return
        normalized = coerce_block_event(event)
        if not normalized:
            return
        if not self.block_ops_active:
            self.block_ops_active = True
        self.emit_block_events([normalized])

    def on_response_text_delta(self, chunk: str) -> None:
        if not chunk:
            return
        if self.block_ops_active:
            self.trace.record_text("delta.dropped", chunk, {"reason": "block_ops_active"})
            return
        self.trace.record_text("delta.in", chunk)
        # Emit raw text_delta to Redis for immediate frontend rendering via marked.js.
        self.append_event("text_delta", {"text": chunk})
        # Still feed the rich builder for finalization block building (persistence only).
        events = self.rich_builder.feed_text(chunk)
        if events:
            self.trace.record("rich.feed_text", {"events": int(len(events))})
        if events:
            self._apply_block_events_internally(events)

    def on_reasoning_event(self, event: Mapping[str, object] | None) -> None:
        if not event or not isinstance(event, Mapping):
            return
        event_type = str(event.get("type") or "").strip().lower()
        if event_type not in {"reasoning_delta", "reasoning_end"}:
            return
        call_id = str(event.get("call_id") or "").strip()
        if not call_id:
            return
        stage = str(event.get("stage") or "").strip() or "llm"
        label = str(event.get("label") or "").strip() or stage.replace("_", " ").strip() or "LLM"
        block_id = self.reasoning_block_id_by_call_id.get(call_id)

        if event_type == "reasoning_delta":
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta:
                return
            if not block_id:
                block = {
                    "block_id": new_block_id(),
                    "type": "reasoning",
                    "created_at": timezone.now().isoformat(),
                    "payload": {
                        "title": label,
                        "stage": stage,
                        "collapsed": False,
                        "code": "",
                    },
                }
                self._append_content_block(block)
                block_id = str(block.get("block_id") or "").strip()
                if not block_id:
                    return
                self.reasoning_block_id_by_call_id[call_id] = block_id
                self.append_event("block_start", {"block": copy.deepcopy(block)})
            block = self._get_content_block(block_id)
            if not block:
                return
            ops = [{"op": "append_code", "text": delta}]
            apply_block_ops(block, ops)
            self.append_event("block_delta", {"block_id": block_id, "ops": ops})
            return

        if event_type == "reasoning_end":
            if not block_id:
                return
            block = self._get_content_block(block_id)
            if block:
                payload_raw = block.get("payload")
                payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
                payload["collapsed"] = True
                payload["completed_at"] = timezone.now().isoformat()
                block["payload"] = payload
            self.append_event("block_end", {"block_id": block_id})

    def on_status_change(self, state: object) -> None:
        if not state:
            return
        code: str | None = None
        label: str | None = None
        meta: dict | None = None
        if isinstance(state, str):
            code = state.strip()
        elif isinstance(state, Mapping):
            raw_code = state.get("code") or state.get("state")
            if isinstance(raw_code, str):
                code = raw_code.strip()
            raw_label = state.get("label")
            if isinstance(raw_label, str):
                label = raw_label.strip()
            raw_meta = state.get("meta")
            if isinstance(raw_meta, dict):
                meta = raw_meta
        if not code:
            return
        payload: dict[str, object] = {"state": code}
        if label is not None:
            payload["label"] = label
        if meta:
            payload["meta"] = meta
        self.append_event("status", payload)

    def finalize_text(self) -> None:
        if not self.block_ops_active:
            events = self.rich_builder.finalize()
            if events:
                self.trace.record("rich.finalize", {"events": int(len(events))})
                self._apply_block_events_internally(events)
        # Ensure any buffered block_delta ops are emitted before finalization persists.
        self._flush_pending_block_delta()
        self.trace.record(
            "builder.finalize_text",
            {
                "blocks": int(len(self.blocks)),
                "block_ops_active": bool(self.block_ops_active),
                "had_tool_events": bool(self.had_tool_events),
            },
        )

    def on_tool_event(self, event: Mapping[str, object] | None) -> None:
        if not event or not isinstance(event, Mapping):
            return
        self.had_tool_events = True
        phase = str(event.get("phase") or "").strip().lower()
        if phase not in TOOL_EVENT_PHASES:
            return
        tool_name = str(event.get("tool_name") or "").strip()
        kind = str(event.get("kind") or "").strip() or "tool"
        status_value = str(event.get("status") or "").strip()
        if not status_value:
            if phase == "started":
                status_value = "running"
            elif phase == "approval_requested":
                status_value = "pending_approval"

        event_id_value = str(event.get("event_id") or "").strip()
        tool_call_id_value = str(event.get("tool_call_id") or "").strip()
        candidate_keys: list[str] = []
        if tool_call_id_value:
            candidate_keys.append(tool_call_id_value)
        if event_id_value and event_id_value not in candidate_keys:
            candidate_keys.append(event_id_value)
        if not candidate_keys:
            return

        payload: dict[str, object] = {
            "event_id": event_id_value or candidate_keys[0],
            "phase": phase,
            "status": status_value,
            "tool_call_id": tool_call_id_value,
            "kind": kind,
            "tool_name": tool_name,
        }

        remote = event.get("remote") if isinstance(event.get("remote"), Mapping) else None
        if not remote:
            output_hint = event.get("output") if isinstance(event.get("output"), Mapping) else None
            remote_hint = output_hint.get("remote") if isinstance(output_hint, Mapping) else None
            if isinstance(remote_hint, Mapping):
                remote = remote_hint
        if remote:
            safe_remote: dict[str, object] = {}
            connection_name = remote.get("connection_name") or remote.get("connectionName")
            remote_tool = (
                remote.get("remote_tool")
                or remote.get("remoteTool")
                or remote.get("tool")
                or remote.get("tool_name")
                or remote.get("toolName")
            )
            if connection_name:
                safe_remote["connection_name"] = _clip_debug_text(connection_name, limit=120)
            if remote_tool:
                safe_remote["remote_tool"] = _clip_debug_text(remote_tool, limit=120)
            if safe_remote:
                payload["remote"] = safe_remote

        approval_payload = event.get("approval") if isinstance(event.get("approval"), Mapping) else None
        approval_id = event.get("approval_id") or event.get("approvalId")
        if approval_payload:
            payload["approval"] = _json_safe_debug(approval_payload, depth=4, string_limit=480, list_limit=24)
            if not approval_id:
                approval_id = approval_payload.get("id")
        if approval_id:
            payload["approval_id"] = str(approval_id)
            # Link approval to this turn for traceability.
            try:
                with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                    ConversationToolApproval.objects.filter(
                        id=approval_id,
                        turn_id__isnull=True,
                    ).update(turn_id=self.turn.id)
            except Exception:  # pragma: no cover - best effort
                logger.exception("portal turn approval link failed approval=%s", approval_id)
            if phase == "approval_requested":
                PortalTurn.objects.filter(id=self.turn.id).update(status=PortalTurnStatus.WAITING_APPROVAL)
            elif phase == "approval_resolved":
                PortalTurn.objects.filter(id=self.turn.id).update(status=PortalTurnStatus.STREAMING)

        input_payload = event.get("input")
        if input_payload is not None and phase in {"started", "approval_requested", "finished", "approval_resolved"}:
            input_string_limit = 720
            input_list_limit = 32
            if tool_name.strip().lower() == "email_create_draft":
                input_string_limit = 12_000
                input_list_limit = 96
            payload["input"] = _json_safe_debug(
                input_payload,
                depth=3,
                string_limit=input_string_limit,
                list_limit=input_list_limit,
            )

        tool_use_block_id: str | None = None
        for key in candidate_keys:
            tool_use_block_id = self.tool_use_block_id_by_event_id.get(key)
            if tool_use_block_id:
                break
        tool_use_block = self._get_content_block(tool_use_block_id) if tool_use_block_id else None
        if not tool_use_block:
            # Tools are first-class UI blocks. If we're still streaming assistant text using
            # the RichBlockStreamBuilder (text → block ops), we must end the active text flow
            # before inserting the tool card so post-tool text cannot append "above" it.
            if not self.block_ops_active:
                try:
                    boundary_events = self.rich_builder.break_flow()
                except Exception:  # pragma: no cover - defensive
                    boundary_events = []
                if boundary_events:
                    self._apply_block_events_internally(boundary_events)
            tool_use_block = {
                "block_id": new_block_id(),
                "type": "tool_use",
                "created_at": timezone.now().isoformat(),
                "payload": {},
            }
            self._append_content_block(tool_use_block)
            tool_use_block_id = str(tool_use_block.get("block_id") or "").strip()
            if tool_use_block_id:
                for key in candidate_keys:
                    self.tool_use_block_id_by_event_id[key] = tool_use_block_id
        if tool_use_block_id:
            for key in candidate_keys:
                self.tool_use_block_id_by_event_id[key] = tool_use_block_id

        existing_payload = tool_use_block.get("payload")
        merged_payload: dict[str, object] = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
        merged_payload.update(payload)
        tool_use_block["payload"] = merged_payload

        if phase in {"finished", "approval_resolved"}:
            duration = event.get("duration_ms")
            try:
                payload["duration_ms"] = int(duration) if duration is not None else 0
            except (TypeError, ValueError):
                payload["duration_ms"] = 0
            output_payload = event.get("output")
            artifact_id: str | None = None
            output_preview: object | None = None
            if output_payload is not None:
                scrubbed_output: object = output_payload
                if isinstance(output_payload, Mapping):
                    output_copy: dict[str, object] = dict(output_payload)
                    remote_out = output_copy.get("remote")
                    if isinstance(remote_out, Mapping):
                        safe_out_remote: dict[str, object] = {}
                        connection_name = remote_out.get("connection_name")
                        remote_tool = remote_out.get("tool") or remote_out.get("remote_tool")
                        if connection_name:
                            safe_out_remote["connection_name"] = _clip_debug_text(connection_name, limit=120)
                        if remote_tool:
                            safe_out_remote["remote_tool"] = _clip_debug_text(remote_tool, limit=120)
                        if safe_out_remote:
                            output_copy["remote"] = safe_out_remote
                        else:
                            output_copy.pop("remote", None)
                    scrubbed_output = output_copy

                output_preview = _json_safe_debug(scrubbed_output, depth=3, string_limit=720, list_limit=24)
                kind_lower = kind.lower().strip()
                if isinstance(output_payload, Mapping):
                    artifact_raw = output_payload.get("artifact_id") or output_payload.get("artifactId")
                    if isinstance(artifact_raw, str) and artifact_raw.strip():
                        artifact_id = artifact_raw.strip()
                    prompt_view = output_payload.get("prompt_view") or output_payload.get("promptView")
                    if prompt_view is not None:
                        output_preview = _json_safe_debug(prompt_view, depth=3, string_limit=720, list_limit=24)

                if artifact_id is None and kind_lower.startswith("mcp"):
                    try:
                        output_artifact = _json_safe_debug(scrubbed_output, depth=6, string_limit=4800, list_limit=96)
                        with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                            artifact_id = store_remote_tool_output_artifact(
                                conversation=self.conversation,
                                tool_call_id=tool_call_id_value,
                                tool_event_id=event_id_value or candidate_keys[0],
                                invoked_tool=tool_name,
                                remote_event_payload=payload,
                                tool_result=output_artifact
                                if isinstance(output_artifact, Mapping)
                                else {"output": output_artifact},
                            )
                    except Exception:  # pragma: no cover
                        artifact_id = None

            if artifact_id:
                payload["artifact_id"] = artifact_id
            if output_preview is not None:
                payload["output_preview"] = output_preview

            existing_payload = tool_use_block.get("payload")
            merged_payload = dict(existing_payload) if isinstance(existing_payload, Mapping) else {}
            merged_payload.update(payload)
            tool_use_block["payload"] = merged_payload

            self.append_event("block_tool_result", {"block": copy.deepcopy(tool_use_block)})

            try:
                if isinstance(output_payload, Mapping) and str(payload.get("status") or "").strip().lower() in {"ok", "success"}:
                    created_blocks: list[dict[str, object]] = []
                    tool_lower = tool_name.strip().lower()

                    if tool_lower in {"pdf_generate", "pdf_merge", "pdf_extract_pages"}:
                        artifact = output_payload.get("artifact")
                        if isinstance(artifact, Mapping):
                            file_id_raw = artifact.get("file_id") or artifact.get("fileId") or artifact.get("id")
                            filename = str(artifact.get("filename") or "").strip()
                            try:
                                file_uuid = uuid.UUID(str(file_id_raw))
                            except (TypeError, ValueError):
                                file_uuid = None
                            if file_uuid:
                                from apps.conversations.models import ConversationFile
                                from apps.conversations.portal_files import portal_file_block

                                with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                                    file_obj = ConversationFile.objects.filter(id=file_uuid, conversation=self.conversation).first()
                                if file_obj is not None:
                                    label_map = {
                                        "pdf_generate": "Generated",
                                        "pdf_merge": "Merged",
                                        "pdf_extract_pages": "Extracted pages",
                                    }
                                    created_blocks.append(portal_file_block(file_obj, label=label_map.get(tool_lower, "Generated")))
                                else:
                                    created_blocks.append(
                                        {
                                            "block_id": new_block_id(),
                                            "type": "file",
                                            "created_at": timezone.now().isoformat(),
                                            "payload": {
                                                "file_id": str(file_uuid),
                                                "filename": filename or "document.pdf",
                                                "content_type": "application/pdf",
                                                "size_bytes": 0,
                                                "page_count": 0,
                                                "kind": "artifact",
                                                "status": "ready",
                                                "label": "Generated",
                                            },
                                        }
                                    )

                    elif tool_lower == "pdf_extract_text":
                        file_meta = output_payload.get("file")
                        text_value = output_payload.get("text")
                        if isinstance(file_meta, Mapping) and isinstance(text_value, str) and text_value.strip():
                            file_id_raw = file_meta.get("id") or file_meta.get("file_id") or file_meta.get("fileId")
                            filename = str(file_meta.get("filename") or "").strip() or "document.pdf"
                            try:
                                file_uuid = uuid.UUID(str(file_id_raw))
                            except (TypeError, ValueError):
                                file_uuid = None
                            if file_uuid:
                                from apps.conversations.portal_files import portal_file_text_block

                                created_blocks.append(
                                    portal_file_text_block(
                                        file_id=file_uuid,
                                        filename=filename,
                                        page_count=int(file_meta.get("page_count") or 0),
                                        text=text_value.strip(),
                                        title=f"Extracted text from {filename}",
                                        collapsed=True,
                                    )
                                )

                    for block in created_blocks:
                        self._append_content_block(block)
                        self.append_event("block_start", {"block": copy.deepcopy(block)})
            except Exception:  # pragma: no cover
                logger.exception("portal turn file block creation failed for tool=%s", tool_name)
        else:
            self.append_event("block_tool_use", {"block": copy.deepcopy(tool_use_block)})


class PortalTurnRunner:
    def __init__(self, *, turn: PortalTurn, conversation: Conversation) -> None:
        self.turn = turn
        self.conversation = conversation
        self.service = ChatPortalService()
        self.builder = PortalTurnEventBuilder(turn=turn, conversation=conversation)

    def _select_orchestrator(self):
        agent = self.conversation.agent_profile
        if not agent:
            raise ValueError("Agent profile is missing")
        use_mcp = _business_prefers_mcp(self.conversation.business_profile, conversation=self.conversation)
        if use_mcp:
            from apps.llm.llm_provider import load_mcp_provider
            from apps.mcp.orchestrator import McpOrchestratorService

            provider = load_mcp_provider()
            orchestrator = McpOrchestratorService(agent=agent, provider=provider)
            return orchestrator
        provider = load_default_provider()
        return AiOrchestratorService(agent=agent, provider=provider)

    def _has_turn_persisted_event(self) -> bool:
        # Phase 5: we may not persist PortalTurnEvent rows. Use a durable marker first.
        try:
            flagged = PortalTurn.objects.filter(id=self.turn.id, metadata__turn_persisted_emitted=True).exists()
            if flagged:
                return True
        except Exception:
            pass
        return PortalTurnEvent.objects.filter(turn_id=self.turn.id, type="turn_persisted").exists()

    def _finalize_turn(self, *, stream_context: object | None) -> None:
        current_status = (
            PortalTurn.objects.filter(id=self.turn.id)
            .values_list("status", flat=True)
            .first()
        )
        if current_status != PortalTurnStatus.CANCELLED:
            PortalTurn.objects.filter(id=self.turn.id).update(
                status=PortalTurnStatus.FINALIZING,
                updated_at=timezone.now(),
            )

        # Materialize content blocks for deterministic persistence.
        # Phase 5: Prefer in-memory builder state, then Redis stream replay, then Postgres event log.
        blocks: list[dict[str, object]] = []
        message_body: str | None = None
        existing_message_metadata: dict[str, object] = {}
        blocks_source = "none"
        if self.turn.message_id:
            with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                existing = ConversationMessage.objects.filter(id=self.turn.message_id).first()
            if existing is not None:
                blocks = existing.content_blocks or []
                message_body = existing.body
                if isinstance(getattr(existing, "metadata", None), Mapping):
                    existing_message_metadata = dict(existing.metadata)
                if blocks:
                    blocks_source = "existing_message"

        if not blocks and getattr(self.builder, "blocks", None):
            try:
                blocks = copy.deepcopy(self.builder.blocks)
            except Exception:
                blocks = list(self.builder.blocks)
            if blocks:
                blocks_source = "builder"

        if not blocks:
            blocks = fold_turn_events_from_redis(turn_id=self.turn.id)
            if blocks:
                blocks_source = "redis_fold"
        if not blocks:
            with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                blocks = fold_turn_events(turn_id=self.turn.id)
            if blocks:
                blocks_source = "db_fold"

        normalized_blocks = normalize_assistant_content_blocks(blocks)
        if normalized_blocks != blocks:
            self.builder.trace.record(
                "turn.finalize.blocks_normalized",
                {
                    "before": int(len(blocks)),
                    "after": int(len(normalized_blocks)),
                    "source": blocks_source,
                },
            )
            blocks = normalized_blocks

        body_from_blocks = extract_text_from_content_blocks(blocks).strip()
        body_text = body_from_blocks or (message_body or "").strip()
        if not body_text:
            streamed_text = "".join(getattr(stream_context, "streamed_chunks", None) or ()).strip() if stream_context else ""
            body_text = streamed_text or "(no content)"
        self.builder.trace.record(
            "turn.finalize.materialized",
            {
                "blocks_source": blocks_source,
                "blocks": int(len(blocks)),
                "body_len_pre_sanitize": int(len(body_text or "")),
            },
        )

        # Sanitize final text the same way the portal does for persistence.
        body_text, _ = sanitize_with_diagnostics(
            body_text,
            conversation=self.conversation,
            stage="portal_turn_finalize",
        )
        block_types: dict[str, int] = {}
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            t = str(block.get("type") or "").strip().lower() or "unknown"
            block_types[t] = block_types.get(t, 0) + 1
        self.builder.trace.record(
            "turn.finalize.sanitized",
            {
                "blocks_source": blocks_source,
                "blocks": int(len(blocks)),
                "block_types": block_types,
                "body_len": int(len(body_text or "")),
            },
        )

        debug_tools_payload: dict[str, object] | None = None
        persisted_message_metadata: dict[str, object] | None = None
        if stream_context and getattr(settings, "PORTAL_DEBUG_TOOL_TRACE", False):
            try:
                from apps.api.chat_portal import _serialize_debug_tools_payload

                debug_tools_candidate = _serialize_debug_tools_payload(stream_context)
                if isinstance(debug_tools_candidate, dict) and debug_tools_candidate:
                    debug_tools_payload = debug_tools_candidate
                    persisted_message_metadata = dict(existing_message_metadata)
                    persisted_message_metadata["debug_tools"] = debug_tools_payload
            except Exception:
                pass  # Best-effort; never break finalization for debug data.

        if self.turn.message_id:
            self.service.update_message(
                session_token=self.conversation.session_token,
                message_id=self.turn.message_id,
                body=body_text,
                metadata=persisted_message_metadata,
                content_blocks=blocks,
                conversation=self.conversation,
            )
            message_id = self.turn.message_id
        else:
            message = self.service.append_message(
                session_token=self.conversation.session_token,
                sender=ConversationSender.AI,
                body=body_text,
                metadata=persisted_message_metadata,
                content_blocks=blocks,
                conversation=self.conversation,
            )
            message_id = message.id
            self.turn.message_id = message_id
            PortalTurn.objects.filter(id=self.turn.id).update(message_id=message_id)

        try:
            session_state = self.service.get_session_state(
                session_token=self.conversation.session_token,
                conversation=self.conversation,
            )
        except Exception:  # pragma: no cover - session snapshot is best effort
            session_state = None

        final_payload = {
            "text": body_text,
            "message_id": str(message_id),
            "session_status": getattr(session_state, "status", None) if session_state else None,
            "metadata_version": 1,
            "content_blocks": blocks,
        }

        # Inject debug tool trace payload to event stream (and persist in message metadata above).
        if debug_tools_payload:
            final_payload["debug_tools"] = debug_tools_payload

        emitted = self._has_turn_persisted_event()
        if not emitted:
            self.builder.append_event("turn_persisted", final_payload)
            try:
                current_meta = (
                    PortalTurn.objects.filter(id=self.turn.id)
                    .values_list("metadata", flat=True)
                    .first()
                )
            except Exception:
                current_meta = None
            next_meta = dict(current_meta or {}) if isinstance(current_meta, dict) else {}
            next_meta["turn_persisted_emitted"] = True
            PortalTurn.objects.filter(id=self.turn.id).update(metadata=next_meta, updated_at=timezone.now())
        self.builder.trace.record(
            "turn.finalize.done",
            {
                "blocks_source": blocks_source,
                "turn_persisted_emitted": bool(not emitted),
                "message_id": str(message_id),
                "blocks": int(len(blocks)),
                "body_len": int(len(body_text or "")),
            },
        )

        latest_status = (
            PortalTurn.objects.filter(id=self.turn.id)
            .values_list("status", flat=True)
            .first()
        )
        final_status = (
            PortalTurnStatus.CANCELLED if latest_status == PortalTurnStatus.CANCELLED else PortalTurnStatus.FINALIZED
        )
        PortalTurn.objects.filter(id=self.turn.id).update(
            status=final_status,
            finalized_at=timezone.now(),
            updated_at=timezone.now(),
        )

    def run(self) -> None:
        close_old_connections()
        run_start = time.perf_counter()
        error: str | None = None
        user_message = str(self.turn.user_message or "").strip()
        current_status = (
            PortalTurn.objects.filter(id=self.turn.id)
            .values_list("status", flat=True)
            .first()
        )
        if current_status in {PortalTurnStatus.CANCELLED, PortalTurnStatus.FINALIZED, PortalTurnStatus.FAILED}:
            self.builder.trace.record("turn.skip", {"status": str(current_status or "")})
            self.builder.trace.close()
            return
        if current_status == PortalTurnStatus.FINALIZING:
            try:
                self._finalize_turn(stream_context=None)
            except Exception as exc:
                error = str(exc or "")
                raise
            finally:
                if getattr(settings, "PORTAL_STREAM_METRICS", False):
                    first_ms = None
                    if self.builder.first_event_at_perf is not None:
                        first_ms = int(max(0.0, (self.builder.first_event_at_perf - run_start) * 1000.0))
                    structured_log(
                        "portal",
                        "stream.turn_runner",
                        {
                            "turn_id": str(self.turn.id),
                            "conversation_id": str(getattr(self.conversation, "id", "") or ""),
                            "business_id": str(getattr(self.conversation, "business_profile_id", "") or ""),
                            "agent_id": str(getattr(self.conversation, "agent_profile_id", "") or ""),
                            "elapsed_ms": int(max(0.0, (time.perf_counter() - run_start) * 1000.0)),
                            "events": int(self.builder.event_count or 0),
                            "db_write_ms": int(self.builder.db_write_ms or 0),
                            "first_event_ms": first_ms,
                            "first_event_type": self.builder.first_event_type,
                            "last_event_type": self.builder.last_event_type,
                            "had_tool_events": bool(self.builder.had_tool_events),
                            "block_ops_active": bool(self.builder.block_ops_active),
                            "error": error or None,
                        },
                        level=logging.INFO if not error else logging.ERROR,
                    )
                self.builder.trace.close()
            return

        if not user_message:
            raise ValueError("PortalTurn.user_message is empty")

        orchestrator = self._select_orchestrator()

        PortalTurn.objects.filter(id=self.turn.id).update(status=PortalTurnStatus.STREAMING, updated_at=timezone.now())

        cancel_state = {"last_check": 0.0, "cancelled": False}
        portal_block_attempt = {"logged": False, "count": 0}

        def _on_model_block_event(event: Mapping[str, object] | None) -> None:
            """
            Portal turns are server-built-blocks only.

            If the model attempts to stream structured portal blocks (via the
            portal_emit_blocks tool), ignore them to prevent mixed-mode streaming
            (block_ops_active flips and subsequent text deltas get dropped).
            """

            if not event:
                return

            portal_block_attempt["count"] += 1
            if portal_block_attempt["logged"]:
                return
            portal_block_attempt["logged"] = True

            normalized: Mapping[str, object] | None
            try:
                normalized = coerce_block_event(event)
            except Exception:
                normalized = None
            debug_event = normalized if isinstance(normalized, Mapping) else event

            event_type = str(debug_event.get("type") or "").strip()
            payload = debug_event.get("payload") if isinstance(debug_event.get("payload"), Mapping) else {}
            block_id: str | None = None
            lowered = event_type.lower()
            if lowered == "block_start":
                block = payload.get("block") if isinstance(payload, Mapping) else None
                if isinstance(block, Mapping):
                    block_id_value = str(block.get("block_id") or "").strip()
                    if block_id_value:
                        block_id = block_id_value
            elif lowered in {"block_delta", "block_end"}:
                block_id_value = str(payload.get("block_id") or "").strip()
                if block_id_value:
                    block_id = block_id_value

            structured_log(
                "portal",
                "stream.portal_emit_blocks_ignored",
                {
                    "turn_id": str(self.turn.id),
                    "conversation_id": str(getattr(self.conversation, "id", "") or ""),
                    "business_id": str(getattr(self.conversation, "business_profile_id", "") or ""),
                    "event_type": event_type or None,
                    "block_id": block_id,
                },
                level=logging.WARNING,
            )

        def _should_cancel() -> bool:
            now = time.monotonic()
            if now - cancel_state["last_check"] < 0.5:
                return cancel_state["cancelled"]
            cancel_state["last_check"] = now
            status = (
                PortalTurn.objects.filter(id=self.turn.id)
                .values_list("status", flat=True)
                .first()
            )
            cancel_state["cancelled"] = status == PortalTurnStatus.CANCELLED
            return cancel_state["cancelled"]

        stream_kwargs = {
            "conversation": self.conversation,
            "user_message": user_message,
            "on_response_text_delta": self.builder.on_response_text_delta,
            "on_status_change": self.builder.on_status_change,
            "on_placeholder_response": lambda _text: None,
            "on_stream_complete": lambda: None,
            "on_spinner_update": lambda _text: None,
            "on_tool_event": self.builder.on_tool_event,
            "on_block_event": _on_model_block_event,
            "on_reasoning_event": self.builder.on_reasoning_event,
            "should_cancel": _should_cancel,
        }
        try:
            parameters = inspect.signature(orchestrator.stream_turn).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "wait_for_tool_approval" in parameters:
            stream_kwargs["wait_for_tool_approval"] = True
        if "portal_emit_blocks_enabled" in parameters:
            # Portal turns stream server-built blocks only (no model-driven portal_emit_blocks).
            stream_kwargs["portal_emit_blocks_enabled"] = False
        stream_context = None
        try:
            stream_context = orchestrator.stream_turn(**stream_kwargs)
            self.builder.finalize_text()
            self._finalize_turn(stream_context=stream_context)
        except Exception as exc:
            error = str(exc or "")
            raise
        finally:
            if getattr(settings, "PORTAL_STREAM_METRICS", False):
                first_ms = None
                if self.builder.first_event_at_perf is not None:
                    first_ms = int(max(0.0, (self.builder.first_event_at_perf - run_start) * 1000.0))
                structured_log(
                    "portal",
                    "stream.turn_runner",
                    {
                        "turn_id": str(self.turn.id),
                        "conversation_id": str(getattr(self.conversation, "id", "") or ""),
                        "business_id": str(getattr(self.conversation, "business_profile_id", "") or ""),
                        "agent_id": str(getattr(self.conversation, "agent_profile_id", "") or ""),
                        "elapsed_ms": int(max(0.0, (time.perf_counter() - run_start) * 1000.0)),
                        "events": int(self.builder.event_count or 0),
                        "db_write_ms": int(self.builder.db_write_ms or 0),
                        "first_event_ms": first_ms,
                        "first_event_type": self.builder.first_event_type,
                        "last_event_type": self.builder.last_event_type,
                        "had_tool_events": bool(self.builder.had_tool_events),
                        "block_ops_active": bool(self.builder.block_ops_active),
                            "error": error or None,
                        },
                        level=logging.INFO if not error else logging.ERROR,
                    )
            self.builder.trace.close()


def run_turn_background(*, turn_id: uuid.UUID, business_id: object | None = None) -> None:
    def _run() -> None:
        close_old_connections()

        def _run_with_context() -> None:
            turn = PortalTurn.objects.select_related("conversation").filter(id=turn_id).first()
            if not turn:
                return
            runner = PortalTurnRunner(turn=turn, conversation=turn.conversation)
            runner.run()

        if business_id is not None:
            with tenant_context(business_id):
                _run_with_context()
        else:  # pragma: no cover - fallback for non-tenant callers
            logger.warning("portal turn runner missing business_id for turn=%s", turn_id)
            _run_with_context()

    threading.Thread(target=_run, daemon=True).start()
