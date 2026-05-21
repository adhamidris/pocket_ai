from __future__ import annotations

import json
import logging
import select
import time
import uuid
from typing import Iterable

from django.conf import settings
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.api.portal_chat.request_context import (
    _json_error,
    _parse_json_body,
    _resolve_request_conversation,
    _service,
)
from apps.api.portal_chat.serializers import (
    _portal_turn_to_dict,
    _safe_canonicalize_blocks,
    _safe_canonicalize_turn_event,
)
from apps.api.portal_chat.streaming import (
    _open_portal_turn_listen_connection,
    _parse_turn_since_seq,
)
from apps.conversations.models import (
    ConversationMessage,
    PortalTurn,
    PortalTurnStatus,
)
from apps.conversations.portal import (
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalValidationError,
)
from apps.conversations.portal_session.stream_trace import PortalStreamTrace
from apps.conversations.portal_turn.events import (
    append_turn_event,
    get_portal_redis_client,
    list_turn_events,
    portal_turn_redis_stream_key,
)
from apps.rag.observability.logging import structured_log
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


@require_GET
def portal_turn_events(request: HttpRequest, turn_id: uuid.UUID) -> StreamingHttpResponse:
    service = _service()
    try:
        conversation, _session = _resolve_request_conversation(
            service=service,
            request=request,
            include_messages=False,
        )
    except PortalValidationError:
        return StreamingHttpResponse(status=400)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        return StreamingHttpResponse(status=status)
    except PortalNotFoundError:
        return StreamingHttpResponse(status=404)

    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.filter(id=turn_id, conversation_id=conversation.id).first()
    if not turn:
        return StreamingHttpResponse(status=404)

    since = _parse_turn_since_seq(request)

    def event_stream() -> Iterable[str]:
        metrics_enabled = bool(getattr(settings, "PORTAL_STREAM_METRICS", False))
        trace = PortalStreamTrace(turn_id=turn.id, component="sse")
        conn_id = uuid.uuid4().hex[:8]
        started_at = time.perf_counter()
        first_event_at: float | None = None
        events_sent = 0
        keepalives_sent = 0
        yield ": stream_open\n\n"
        last_seq = int(since or 0)
        keepalive_seconds = 15.0
        last_keepalive = time.monotonic()
        event_bus = str(getattr(settings, "PORTAL_TURN_EVENT_BUS", "postgres") or "postgres").strip().lower()
        turn_log_mode = str(getattr(settings, "PORTAL_TURN_EVENT_LOG_MODE", "db") or "db").strip().lower()
        if event_bus == "postgres":
            # Postgres-backed SSE requires the DB event log. This clamp is intentionally
            # runtime (not import-time only) so tests using `override_settings()` can't
            # accidentally create an invalid combination.
            turn_log_mode = "db"
        redis_conn = None
        redis_stream_key = None
        redis_last_id = f"{last_seq}-0"
        sent_turn_persisted = False
        if event_bus == "redis":
            redis_conn = get_portal_redis_client(socket_timeout_seconds=keepalive_seconds + 5.0)
            if redis_conn is not None:
                redis_stream_key = portal_turn_redis_stream_key(turn_id=turn.id)
            else:
                event_bus = "postgres"

        listen_conn = _open_portal_turn_listen_connection() if event_bus == "postgres" else None
        listen_enabled = listen_conn is not None
        trace.record(
            "sse.open",
            {
                "conn": conn_id,
                "since_seq": int(since or 0),
                "event_bus": str(event_bus or "postgres"),
                "turn_log_mode": str(turn_log_mode or "db"),
                "listen_enabled": bool(listen_enabled),
            },
        )

        try:
            while True:
                if redis_conn is not None and redis_stream_key:
                    # Avoid "block forever" so we can emit keepalives and survive slow first-token turns.
                    # After we deliver `turn_persisted`, prefer a short block window so the
                    # SSE stream can observe `FINALIZED` and close promptly.
                    block_ms = 200 if sent_turn_persisted else int(max(200, keepalive_seconds * 1000))
                    from_id = str(redis_last_id)
                    t0 = time.perf_counter()
                    try:
                        entries = redis_conn.xread({redis_stream_key: redis_last_id}, count=250, block=block_ms)
                    except Exception:
                        # Degrade to Postgres if Redis is unavailable.
                        redis_conn = None
                        redis_stream_key = None
                        if listen_conn is None:
                            listen_conn = _open_portal_turn_listen_connection()
                            listen_enabled = bool(listen_conn is not None)
                        event_bus = "postgres"
                        continue

                    if entries:
                        trace.record(
                            "sse.redis.xread",
                            {
                                "conn": conn_id,
                                "from_id": from_id,
                                "dt_ms": int(max(0.0, (time.perf_counter() - t0) * 1000.0)),
                                "block_ms": int(block_ms),
                            },
                        )
                        entry_count = 0
                        batch_types: dict[str, int] = {}
                        batch_text_chars = 0
                        for _stream_key, stream_entries in entries:
                            for entry_id, fields in stream_entries:
                                entry_count += 1
                                entry_id_str = (
                                    entry_id.decode("utf-8", errors="replace") if isinstance(entry_id, (bytes, bytearray)) else str(entry_id)
                                )
                                redis_last_id = entry_id_str
                                seq_value = None
                                try:
                                    seq_value = int(entry_id_str.split("-", 1)[0])
                                except Exception:
                                    seq_value = None

                                raw_type = fields.get(b"type") if isinstance(fields, dict) else None
                                if raw_type is None and isinstance(fields, dict):
                                    raw_type = fields.get("type")  # type: ignore[index]
                                event_type = (
                                    raw_type.decode("utf-8", errors="replace") if isinstance(raw_type, (bytes, bytearray)) else str(raw_type or "")
                                ).strip() or "event"

                                raw_payload = fields.get(b"payload") if isinstance(fields, dict) else None
                                if raw_payload is None and isinstance(fields, dict):
                                    raw_payload = fields.get("payload")  # type: ignore[index]
                                payload_text = raw_payload.decode("utf-8", errors="replace") if isinstance(raw_payload, (bytes, bytearray)) else str(raw_payload or "")
                                try:
                                    payload_obj = json.loads(payload_text) if payload_text else {}
                                except Exception:
                                    payload_obj = {}
                                # Events from Redis are already canonicalized by _finalize_turn;
                                # re-canonicalizing here is a lossy round-trip.  Skip it.

                                if seq_value is None:
                                    # Fallback: keep Last-Event-ID monotonic even if the Redis stream ID is unexpected.
                                    seq_value = int(payload_obj.get("seq") or 0) if isinstance(payload_obj, dict) else 0
                                last_seq = max(last_seq, int(seq_value or 0))

                                payload = {
                                    "turn_id": str(turn.id),
                                    "seq": int(seq_value or 0),
                                    "type": event_type,
                                    "payload": payload_obj or {},
                                }
                                if event_type.strip().lower() == "text_delta":
                                    # Block-only portal stream contract: ignore legacy raw text events.
                                    continue
                                batch_types[event_type] = batch_types.get(event_type, 0) + 1
                                if event_type.strip().lower() == "turn_persisted":
                                    sent_turn_persisted = True
                                if first_event_at is None:
                                    first_event_at = time.perf_counter()
                                events_sent += 1
                                yield f"id: {payload['seq']}\n"
                                yield "event: turnEvent\n"
                                yield f"data: {json.dumps(payload)}\n\n"
                        trace.record(
                            "sse.batch",
                            {
                                "conn": conn_id,
                                "source": "redis",
                                "events": int(entry_count),
                                "types": batch_types,
                                "text_chars": int(batch_text_chars),
                                "last_seq": int(last_seq),
                            },
                        )
                        continue

                if redis_conn is None or not redis_stream_key:
                    # Postgres-backed streaming (Phase 1/2 behavior).
                    t0 = time.perf_counter()
                    with tenant_context(business_id):
                        events = list(list_turn_events(turn_id=turn.id, since_seq=last_seq, limit=250))
                    trace.record(
                        "sse.db.poll",
                        {
                            "conn": conn_id,
                            "dt_ms": int(max(0.0, (time.perf_counter() - t0) * 1000.0)),
                            "events": int(len(events)),
                            "since_seq": int(last_seq),
                        },
                    )
                    if events:
                        batch_types: dict[str, int] = {}
                        batch_text_chars = 0
                        for evt in events:
                            last_seq = int(evt.seq or 0)
                            payload = {
                                "turn_id": str(turn.id),
                                "seq": last_seq,
                                "type": evt.type,
                                "payload": _safe_canonicalize_turn_event(evt.type, evt.payload or {}),
                            }
                            event_type = str(evt.type or "").strip() or "event"
                            if event_type.strip().lower() == "text_delta":
                                # Block-only portal stream contract: ignore legacy raw text events.
                                continue
                            batch_types[event_type] = batch_types.get(event_type, 0) + 1
                            if str(evt.type or "").strip().lower() == "turn_persisted":
                                sent_turn_persisted = True
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
                            yield f"id: {last_seq}\n"
                            yield "event: turnEvent\n"
                            yield f"data: {json.dumps(payload)}\n\n"
                        trace.record(
                            "sse.batch",
                            {
                                "conn": conn_id,
                                "source": "db",
                                "events": int(len(events)),
                                "types": batch_types,
                                "text_chars": int(batch_text_chars),
                                "last_seq": int(last_seq),
                            },
                        )
                        continue

                with tenant_context(business_id):
                    latest = (
                        PortalTurn.objects.filter(id=turn.id)
                        .values_list("status", "last_event_seq")
                        .first()
                    )
                if latest:
                    latest_status, latest_seq = latest
                    if redis_conn is not None and redis_stream_key:
                        # Redis is a live bus; Postgres remains source-of-truth for replay.
                        # If Redis missed/trimmed events, backfill from Postgres.
                        if turn_log_mode == "db" and int(latest_seq or 0) > last_seq:
                            with tenant_context(business_id):
                                events = list(list_turn_events(turn_id=turn.id, since_seq=last_seq, limit=250))
                            if events:
                                for evt in events:
                                    last_seq = int(evt.seq or 0)
                                    payload = {
                                        "turn_id": str(turn.id),
                                        "seq": last_seq,
                                        "type": evt.type,
                                        "payload": evt.payload or {},
                                    }
                                    if first_event_at is None:
                                        first_event_at = time.perf_counter()
                                    events_sent += 1
                                    yield f"id: {last_seq}\n"
                                    yield "event: turnEvent\n"
                                    yield f"data: {json.dumps(payload)}\n\n"
                                redis_last_id = f"{last_seq}-0"
                                continue
                    if latest_status in {PortalTurnStatus.FINALIZED, PortalTurnStatus.FAILED, PortalTurnStatus.CANCELLED}:
                        if redis_conn is not None and redis_stream_key:
                            drained = False
                            # Drain any remaining Redis entries (non-blocking) before closing.
                            drained_total = 0
                            while True:
                                try:
                                    # Redis Streams: BLOCK 0 means "block forever". Omit BLOCK for a truly non-blocking drain.
                                    drain_entries = redis_conn.xread({redis_stream_key: redis_last_id}, count=250)
                                except Exception:
                                    drain_entries = []
                                if not drain_entries:
                                    break
                                drained = True
                                drain_batch = 0
                                for _stream_key, stream_entries in drain_entries:
                                    for entry_id, fields in stream_entries:
                                        drain_batch += 1
                                        entry_id_str = (
                                            entry_id.decode("utf-8", errors="replace")
                                            if isinstance(entry_id, (bytes, bytearray))
                                            else str(entry_id)
                                        )
                                        redis_last_id = entry_id_str
                                        seq_value = None
                                        try:
                                            seq_value = int(entry_id_str.split("-", 1)[0])
                                        except Exception:
                                            seq_value = None

                                        raw_type = fields.get(b"type") if isinstance(fields, dict) else None
                                        if raw_type is None and isinstance(fields, dict):
                                            raw_type = fields.get("type")  # type: ignore[index]
                                        event_type = (
                                            raw_type.decode("utf-8", errors="replace")
                                            if isinstance(raw_type, (bytes, bytearray))
                                            else str(raw_type or "")
                                        ).strip() or "event"

                                        raw_payload = fields.get(b"payload") if isinstance(fields, dict) else None
                                        if raw_payload is None and isinstance(fields, dict):
                                            raw_payload = fields.get("payload")  # type: ignore[index]
                                        payload_text = (
                                            raw_payload.decode("utf-8", errors="replace")
                                            if isinstance(raw_payload, (bytes, bytearray))
                                            else str(raw_payload or "")
                                        )
                                        try:
                                            payload_obj = json.loads(payload_text) if payload_text else {}
                                        except Exception:
                                            payload_obj = {}
                                        # Events from Redis are already canonicalized by _finalize_turn.

                                        if seq_value is None:
                                            seq_value = int(payload_obj.get("seq") or 0) if isinstance(payload_obj, dict) else 0
                                        last_seq = max(last_seq, int(seq_value or 0))

                                        payload = {
                                            "turn_id": str(turn.id),
                                            "seq": int(seq_value or 0),
                                            "type": event_type,
                                            "payload": payload_obj or {},
                                        }
                                        if event_type.strip().lower() == "text_delta":
                                            # Block-only portal stream contract: ignore legacy raw text events.
                                            continue
                                        if event_type.strip().lower() == "turn_persisted":
                                            sent_turn_persisted = True
                                        if first_event_at is None:
                                            first_event_at = time.perf_counter()
                                        events_sent += 1
                                        yield f"id: {payload['seq']}\n"
                                        yield "event: turnEvent\n"
                                        yield f"data: {json.dumps(payload)}\n\n"
                                drained_total += drain_batch
                                trace.record(
                                    "sse.drain_batch",
                                    {
                                        "conn": conn_id,
                                        "events": int(drain_batch),
                                        "drained_total": int(drained_total),
                                        "last_seq": int(last_seq),
                                    },
                                )
                            # If we drained at least once, loop back to status check to avoid a tight close/open race.
                            if drained:
                                continue
                            break
                        else:
                            # Degraded mode: when turn events aren't persisted to Postgres (Phase 5+),
                            # still deliver the final persisted assistant message once it's available.
                            if not sent_turn_persisted and turn_log_mode in {"minimal", "off"} and latest_status == PortalTurnStatus.FINALIZED:
                                with tenant_context(business_id):
                                    message_id = (
                                        PortalTurn.objects.filter(id=turn.id)
                                        .values_list("message_id", flat=True)
                                        .first()
                                    )
                                    msg = ConversationMessage.objects.filter(id=message_id).first() if message_id else None
                                if msg is not None:
                                    last_seq += 1
                                    payload = {
                                        "turn_id": str(turn.id),
                                        "seq": int(last_seq),
                                        "type": "turn_persisted",
                                        "payload": {
                                            "text": msg.body or "",
                                            "message_id": str(msg.id),
                                            "session_status": None,
                                            "metadata_version": 1,
                                            "content_blocks": _safe_canonicalize_blocks(
                                                body=msg.body or "",
                                                blocks=msg.content_blocks or [],
                                            ),
                                        },
                                    }
                                    sent_turn_persisted = True
                                    if first_event_at is None:
                                        first_event_at = time.perf_counter()
                                    events_sent += 1
                                    yield f"id: {payload['seq']}\n"
                                    yield "event: turnEvent\n"
                                    yield f"data: {json.dumps(payload)}\n\n"
                                    break
                            if (
                                latest_status == PortalTurnStatus.FINALIZED
                                and not sent_turn_persisted
                                and turn_log_mode in {"minimal", "off"}
                            ):
                                # Final message not visible yet; keep the stream alive and retry.
                                continue
                            if int(latest_seq or 0) <= last_seq:
                                break

                if listen_conn is not None:
                    now = time.monotonic()
                    timeout = max(0.0, keepalive_seconds - (now - last_keepalive))
                    try:
                        readable, _, _ = select.select([listen_conn], [], [], timeout)
                    except Exception:
                        readable = []
                    if not readable:
                        # Keep the SSE connection warm.
                        keepalives_sent += 1
                        yield ": keepalive\n\n"
                        last_keepalive = time.monotonic()
                        continue

                    try:
                        listen_conn.poll()
                    except Exception:
                        # On any LISTEN connection issue, fall back to keepalive pacing.
                        keepalives_sent += 1
                        yield ": keepalive\n\n"
                        last_keepalive = time.monotonic()
                        continue

                    matched = False
                    try:
                        while getattr(listen_conn, "notifies", None):
                            notify = listen_conn.notifies.pop(0)
                            raw = getattr(notify, "payload", "") or ""
                            try:
                                note = json.loads(raw) if raw else {}
                            except Exception:
                                note = {}
                            if str(note.get("turn_id") or "") == str(turn.id):
                                matched = True
                                break
                    except Exception:
                        matched = True
                    if matched:
                        continue
                    # Notification was for a different turn; keep waiting.
                    continue

                # Fallback: if LISTEN isn't available, yield periodic keepalives and re-check.
                now = time.monotonic()
                if now - last_keepalive >= keepalive_seconds:
                    keepalives_sent += 1
                    yield ": keepalive\n\n"
                    last_keepalive = now
                time.sleep(0.15)
        finally:
            trace.record(
                "sse.close",
                {
                    "conn": conn_id,
                    "elapsed_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
                    "events_sent": int(events_sent),
                    "keepalives_sent": int(keepalives_sent),
                    "event_bus": str(event_bus or "postgres"),
                    "final_seq": int(last_seq),
                },
            )
            trace.close()
            if listen_conn is not None:
                try:
                    listen_conn.close()
                except Exception:
                    pass
            if metrics_enabled:
                structured_log(
                    "portal",
                    "stream.turn_sse",
                    {
                        "turn_id": str(turn.id),
                        "conversation_id": str(getattr(conversation, "id", "") or ""),
                        "business_id": str(business_id or ""),
                        "elapsed_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
                        "first_event_ms": int(max(0.0, (first_event_at - started_at) * 1000.0)) if first_event_at else None,
                        "events_sent": int(events_sent),
                        "keepalives_sent": int(keepalives_sent),
                        "listen_enabled": bool(listen_enabled),
                        "event_bus": str(event_bus or "postgres"),
                        "since_seq": int(since or 0),
                        "final_seq": int(last_seq),
                    },
                )

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    response["X-Portal-Stream-Protocol-Version"] = str(getattr(settings, "PORTAL_STREAM_PROTOCOL_VERSION", 1))
    return response


@require_POST
def portal_turn_cancel(request: HttpRequest, turn_id: uuid.UUID) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    try:
        conversation, _session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=False,
        )
    except PortalNotFoundError as exc:
        return _json_error("not_found", str(exc), status=404)
    except PortalAuthorizationError as exc:
        status = 401 if str(exc) == "Authentication is required." else 403
        code = "auth_required" if status == 401 else "forbidden"
        return _json_error(code, str(exc), status=status)
    except PortalValidationError as exc:
        return _json_error("validation_error", str(exc))

    business_id = getattr(conversation, "business_profile_id", None)
    with tenant_context(business_id):
        turn = PortalTurn.objects.filter(id=turn_id, conversation_id=conversation.id).first()
        if not turn:
            return _json_error("not_found", "Turn not found.", status=404)
        if turn.status in {PortalTurnStatus.FINALIZED, PortalTurnStatus.FAILED, PortalTurnStatus.CANCELLED}:
            return JsonResponse(
                {
                    "turn": _portal_turn_to_dict(turn),
                    "cancelled": False,
                }
            )
        PortalTurn.objects.filter(id=turn.id).update(
            status=PortalTurnStatus.CANCELLED,
            updated_at=timezone.now(),
        )
        try:
            append_turn_event(
                turn_id=turn.id,
                event_type="turn_cancelled",
                payload={"turn_id": str(turn.id)},
            )
        except Exception:  # pragma: no cover - best effort only
            logger.exception("portal turn cancel event failed turn=%s", turn.id)

    return JsonResponse(
        {
            "turn": _portal_turn_to_dict(turn),
            "cancelled": True,
        }
    )
