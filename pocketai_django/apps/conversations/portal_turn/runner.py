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
    ensure_assistant_text_blocks,
    extract_text_from_content_blocks,
)
from apps.conversations.portal import ChatPortalService
from apps.conversations.portal_session.stream_trace import PortalStreamTrace
from apps.conversations.portal_turn.blocks import PortalTurnBlockMixin
from apps.conversations.portal_turn.reasoning import PortalTurnReasoningMixin
from apps.conversations.portal_turn.text_stream import PortalTurnTextStreamMixin
from apps.conversations.portal_turn.tools import PortalTurnToolEventMixin
from apps.conversations.rich_blocks import RichBlockStreamBuilder, coerce_block_event
from apps.conversations.portal_turn.events import append_turn_event
from apps.conversations.portal_turn.folding import fold_turn_events, fold_turn_events_from_redis
from apps.conversations.models import (
    Conversation,
    ConversationMessage,
    ConversationSender,
    PortalTurn,
    PortalTurnEvent,
    PortalTurnStatus,
)
from apps.mcp.text.sanitizer import sanitize_with_diagnostics
from apps.rag.observability.logging import structured_log
from core.tenancy import tenant_context

logger = logging.getLogger(__name__)


class PortalTurnEventBuilder(PortalTurnBlockMixin, PortalTurnTextStreamMixin, PortalTurnReasoningMixin, PortalTurnToolEventMixin):
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
        # Track text blocks streamed before tool/no-tool decision is known.
        # If tools are later used, these blocks are pruned via a dedicated stream event.
        self._pre_tool_stream_block_ids: list[str] = []
        self._tool_decision: str = "unknown"  # unknown | used | no_tools
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
        if lowered == "block_delta":
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
        from apps.llm.llm_provider import load_mcp_provider
        from apps.mcp.orchestrator import McpOrchestratorService

        provider = load_mcp_provider()
        return McpOrchestratorService(agent=agent, provider=provider)

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
        existing_message_blocks: list[dict[str, object]] = []
        if self.turn.message_id:
            with tenant_context(getattr(self.conversation, "business_profile_id", None)):
                existing = ConversationMessage.objects.filter(id=self.turn.message_id).first()
            if existing is not None:
                existing_message_blocks = existing.content_blocks or []
                message_body = existing.body
                if isinstance(getattr(existing, "metadata", None), Mapping):
                    existing_message_metadata = dict(existing.metadata)
                if existing_message_blocks:
                    blocks_source = "existing_message"

        # Streamed builder blocks are canonical for portal turns.
        if getattr(self.builder, "blocks", None):
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
        if not blocks and existing_message_blocks:
            blocks = existing_message_blocks
            blocks_source = "existing_message"

        orchestrator_text = str(getattr(stream_context, "response_text", "") or "").strip() if stream_context else ""
        body_from_blocks = extract_text_from_content_blocks(blocks).strip()
        persisted_body = (message_body or "").strip()
        streamed_text = "".join(getattr(stream_context, "streamed_chunks", None) or ()).strip() if stream_context else ""

        if body_from_blocks:
            body_text = body_from_blocks
            body_source = "content_blocks"
        elif orchestrator_text:
            body_text = orchestrator_text
            body_source = "orchestrator_response"
        elif persisted_body:
            body_text = persisted_body
            body_source = "message_body"
        elif streamed_text:
            body_text = streamed_text
            body_source = "streamed_chunks"
        else:
            body_text = "(no content)"
            body_source = "empty"
        self.builder.trace.record(
            "turn.finalize.materialized",
            {
                "blocks_source": blocks_source,
                "blocks": int(len(blocks)),
                "body_source": body_source,
                "orchestrator_body_len": int(len(orchestrator_text or "")),
                "body_from_blocks_len": int(len(body_from_blocks or "")),
                "body_len": int(len(body_text or "")),
            },
        )

        # Fallback only: if no streamed/folded blocks exist, synthesize from final text.
        if not blocks and body_text:
            sanitized_body, _ = sanitize_with_diagnostics(
                body_text,
                conversation=self.conversation,
                stage="portal_turn_finalize_fallback",
            )
            if sanitized_body:
                body_text = sanitized_body
            synthesized = ensure_assistant_text_blocks(
                body_text,
                existing_blocks=[],
                force_regenerate_text=True,
            )
            if synthesized:
                blocks = synthesized
                blocks_source = "fallback_from_text"
                body_from_blocks = extract_text_from_content_blocks(blocks).strip()
                if body_from_blocks:
                    body_text = body_from_blocks
                    body_source = "content_blocks"
            self.builder.trace.record(
                "turn.finalize.fallback_blocks_synthesized",
                {
                    "blocks": int(len(blocks)),
                    "body_len": int(len(body_text or "")),
                },
            )

        block_types: dict[str, int] = {}
        for block in blocks:
            if not isinstance(block, Mapping):
                continue
            t = str(block.get("type") or "").strip().lower() or "unknown"
            block_types[t] = block_types.get(t, 0) + 1
        self.builder.trace.record(
            "turn.finalize.canonical",
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
            "on_stream_complete": self.builder.on_stream_complete,
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
        if "on_tool_decision" in parameters:
            stream_kwargs["on_tool_decision"] = self.builder.on_tool_decision
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
