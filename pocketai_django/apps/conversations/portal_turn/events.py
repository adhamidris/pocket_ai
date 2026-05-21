from __future__ import annotations

import os
import json
import uuid
from functools import lru_cache
from typing import Iterable

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.conversations.models import PortalTurn, PortalTurnEvent


PORTAL_TURN_EVENTS_NOTIFY_CHANNEL = "portal_turn_events"

MINIMAL_TURN_EVENT_TYPES = {
    # "turn_persisted" is emitted once and carries canonical `content_blocks`.
    "turn_persisted",
    # Cancellation markers are useful for audit/debug without per-token noise.
    "turn_cancelled",
    # Tool lifecycle blocks are high-signal and relatively low-volume.
    "block_tool_use",
    "block_tool_result",
    # Status updates are low-volume but helpful for diagnosing "stuck" turns.
    "status",
}


def portal_turn_redis_stream_key(*, turn_id: uuid.UUID) -> str:
    prefix = str(getattr(settings, "PORTAL_TURN_EVENT_BUS_REDIS_STREAM_PREFIX", "portal:turn") or "portal:turn").strip()
    prefix = prefix.rstrip(":")
    return f"{prefix}:{turn_id}:events"


def portal_turn_redis_seq_key(*, turn_id: uuid.UUID) -> str:
    prefix = str(getattr(settings, "PORTAL_TURN_EVENT_BUS_REDIS_STREAM_PREFIX", "portal:turn") or "portal:turn").strip()
    prefix = prefix.rstrip(":")
    return f"{prefix}:{turn_id}:seq"


@lru_cache(maxsize=4)
def _portal_redis_client(redis_url: str, socket_timeout_seconds: float) -> object:
    import redis  # type: ignore[import-not-found]

    return redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=0.5,
        socket_timeout=max(0.1, float(socket_timeout_seconds)),
        decode_responses=False,
    )


def get_portal_redis_client(*, socket_timeout_seconds: float) -> object | None:
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        return None
    try:
        return _portal_redis_client(redis_url, float(socket_timeout_seconds))
    except Exception:
        return None


def _redis_event_bus_enabled() -> bool:
    # Intentionally do not require REDIS_URL here: tests may patch
    # get_portal_redis_client() directly, and production should fall back
    # gracefully if Redis is misconfigured/unavailable.
    return str(getattr(settings, "PORTAL_TURN_EVENT_BUS", "postgres") or "postgres").strip().lower() == "redis"


def _turn_event_log_mode() -> str:
    # Postgres-backed SSE requires the DB event log. This clamp is intentionally
    # runtime (not import-time only) so tests using `override_settings()` can't
    # accidentally create an invalid combination.
    bus = str(getattr(settings, "PORTAL_TURN_EVENT_BUS", "postgres") or "postgres").strip().lower()
    if bus == "postgres":
        return "db"
    return str(getattr(settings, "PORTAL_TURN_EVENT_LOG_MODE", "db") or "db").strip().lower()


def _should_persist_event_to_db(event_type: str) -> bool:
    mode = _turn_event_log_mode()
    if mode == "db":
        return True
    if mode == "off":
        return False
    # mode == "minimal"
    return str(event_type or "").strip().lower() in MINIMAL_TURN_EVENT_TYPES


def _publish_turn_event_to_redis_allocating_seq(*, turn_id: uuid.UUID, event_type: str, payload: dict) -> int | None:
    """
    Publish the event to the Redis turn stream and return the allocated `seq`.

    We keep `seq` as a small monotonic integer (1,2,3,...) to preserve the portal's
    `since=` resume contract and to avoid JS integer precision problems.
    """

    conn = get_portal_redis_client(socket_timeout_seconds=0.5)
    if conn is None:
        return None

    stream_key = portal_turn_redis_stream_key(turn_id=turn_id)
    seq_key = portal_turn_redis_seq_key(turn_id=turn_id)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    ttl_seconds = int(getattr(settings, "PORTAL_TURN_EVENT_BUS_REDIS_STREAM_TTL_SECONDS", 3600) or 3600)
    ttl_seconds = max(60, int(ttl_seconds))
    maxlen = int(getattr(settings, "PORTAL_TURN_EVENT_BUS_REDIS_STREAM_MAXLEN", 20000) or 20000)
    maxlen = max(1000, int(maxlen))

    def _attempt_publish(seq: int) -> bool:
        entry_id = f"{int(seq)}-0"
        pipe = conn.pipeline()
        pipe.xadd(
            stream_key,
            {"type": str(event_type or "").strip() or "event", "payload": payload_json},
            id=entry_id,
            maxlen=maxlen,
            approximate=True,
        )
        pipe.expire(stream_key, ttl_seconds)
        pipe.expire(seq_key, ttl_seconds)
        pipe.execute()
        return True

    try:
        seq = int(conn.incr(seq_key))
        _attempt_publish(seq)
        return seq
    except Exception as exc:
        # Rare recovery path: if the seq key was lost but the stream still has entries,
        # INCR will start from 1 and XADD with "1-0" will fail because IDs must be increasing.
        msg = str(exc or "")
        if "ID specified in XADD" in msg or "equal or smaller" in msg:
            try:
                last = conn.xrevrange(stream_key, max="+", min="-", count=1)
            except Exception:
                last = []
            last_seq = 0
            if last:
                last_id = last[0][0]
                last_id_str = (
                    last_id.decode("utf-8", errors="replace") if isinstance(last_id, (bytes, bytearray)) else str(last_id)
                )
                try:
                    last_seq = int(last_id_str.split("-", 1)[0])
                except Exception:
                    last_seq = 0
            try:
                conn.set(seq_key, int(last_seq or 0), ex=ttl_seconds)
                seq = int(conn.incr(seq_key))
                _attempt_publish(seq)
                return seq
            except Exception:
                return None
        return None


def _publish_turn_event_to_redis(*, turn_id: uuid.UUID, seq: int, event_type: str, payload: dict) -> None:
    conn = get_portal_redis_client(socket_timeout_seconds=0.5)
    if conn is None:
        return

    stream_key = portal_turn_redis_stream_key(turn_id=turn_id)
    entry_id = f"{int(seq)}-0"
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    ttl_seconds = int(getattr(settings, "PORTAL_TURN_EVENT_BUS_REDIS_STREAM_TTL_SECONDS", 3600) or 3600)
    ttl_seconds = max(60, ttl_seconds)
    maxlen = int(getattr(settings, "PORTAL_TURN_EVENT_BUS_REDIS_STREAM_MAXLEN", 20000) or 20000)
    maxlen = max(1000, int(maxlen))
    seq_key = portal_turn_redis_seq_key(turn_id=turn_id)

    try:
        pipe = conn.pipeline()
        pipe.xadd(
            stream_key,
            {"type": str(event_type or "").strip() or "event", "payload": payload_json},
            id=entry_id,
            maxlen=maxlen,
            approximate=True,
        )
        pipe.expire(stream_key, ttl_seconds)
        pipe.expire(seq_key, ttl_seconds)
        pipe.execute()
    except Exception:
        # Redis is an optimization; publishing must never break turn execution.
        return


def _best_effort_notify(*, turn_id: uuid.UUID, seq: int) -> None:
    # Transactional NOTIFY: delivered only after the surrounding transaction commits.
    try:
        notify_payload = json.dumps({"turn_id": str(turn_id), "seq": int(seq)}, separators=(",", ":"))
        with connection.cursor() as cursor:
            cursor.execute(f"NOTIFY {PORTAL_TURN_EVENTS_NOTIFY_CHANNEL}, %s", [notify_payload])
    except Exception:  # pragma: no cover - best effort only
        # Streaming must not fail if NOTIFY is unavailable (e.g., during migrations/tests).
        return


def _append_turn_event_to_db(*, turn_id: uuid.UUID, event_type: str, payload: dict) -> PortalTurnEvent:
    event_type_norm = str(event_type or "").strip() or "event"
    payload_out = payload if isinstance(payload, dict) else {}
    with transaction.atomic():
        turn = PortalTurn.objects.select_for_update().filter(id=turn_id).first()
        if not turn:
            raise PortalTurn.DoesNotExist(f"PortalTurn not found: {turn_id}")
        seq = int(turn.last_event_seq or 0) + 1
        event = PortalTurnEvent.objects.create(
            turn=turn,
            seq=seq,
            type=event_type_norm,
            payload=payload_out,
        )
        PortalTurn.objects.filter(id=turn.id).update(last_event_seq=seq, updated_at=timezone.now())
        _best_effort_notify(turn_id=turn.id, seq=seq)
    return event


def append_turn_event(*, turn_id: uuid.UUID, event_type: str, payload: dict | None = None) -> PortalTurnEvent | None:
    """Append a portal turn event with a monotonic sequence number.

    Phase 5: when `PORTAL_TURN_EVENT_BUS=redis` and `PORTAL_TURN_EVENT_LOG_MODE!=db`,
    we avoid writing per-token PortalTurnEvent rows to Postgres. Redis Streams becomes
    the live event log for streaming; Postgres remains the source-of-truth for the
    final persisted assistant message.
    """
    payload_out = payload if isinstance(payload, dict) else {}
    event_type_norm = str(event_type or "").strip() or "event"
    log_mode = _turn_event_log_mode()

    # Optimized path: Redis streaming + no per-token DB event log.
    if _redis_event_bus_enabled() and log_mode in {"minimal", "off"}:
        seq = _publish_turn_event_to_redis_allocating_seq(turn_id=turn_id, event_type=event_type_norm, payload=payload_out)
        if seq is None:
            # If Redis is unavailable, fall back to a minimal DB log (no per-token deltas).
            if str(event_type_norm or "").strip().lower() in MINIMAL_TURN_EVENT_TYPES or event_type_norm.strip().lower() == "turn_persisted":
                if _should_persist_event_to_db(event_type_norm) or event_type_norm.strip().lower() == "turn_persisted":
                    return _append_turn_event_to_db(turn_id=turn_id, event_type=event_type_norm, payload=payload_out)
            return None

        if _should_persist_event_to_db(event_type_norm):
            # Persist a minimal audit log (no per-token deltas).
            with transaction.atomic():
                turn = PortalTurn.objects.select_for_update().filter(id=turn_id).first()
                if not turn:
                    raise PortalTurn.DoesNotExist(f"PortalTurn not found: {turn_id}")
                event = PortalTurnEvent.objects.create(
                    turn=turn,
                    seq=seq,
                    type=event_type_norm,
                    payload=payload_out,
                )
                # Keep last_event_seq monotonic for operational introspection (not used for streaming in this mode).
                if int(turn.last_event_seq or 0) < seq:
                    PortalTurn.objects.filter(id=turn.id).update(last_event_seq=seq, updated_at=timezone.now())
                _best_effort_notify(turn_id=turn.id, seq=seq)
            return event
        return None

    # Legacy / debug path: Postgres event log is the source-of-truth for replay.
    with transaction.atomic():
        turn = PortalTurn.objects.select_for_update().filter(id=turn_id).first()
        if not turn:
            raise PortalTurn.DoesNotExist(f"PortalTurn not found: {turn_id}")
        seq = int(turn.last_event_seq or 0) + 1
        event = None
        if _should_persist_event_to_db(event_type_norm):
            event = PortalTurnEvent.objects.create(
                turn=turn,
                seq=seq,
                type=event_type_norm,
                payload=payload_out,
            )
        PortalTurn.objects.filter(id=turn.id).update(last_event_seq=seq, updated_at=timezone.now())
        _best_effort_notify(turn_id=turn.id, seq=seq)
        if _redis_event_bus_enabled():
            transaction.on_commit(
                lambda: _publish_turn_event_to_redis(
                    turn_id=turn.id,
                    seq=seq,
                    event_type=event_type_norm,
                    payload=payload_out,
                )
            )
    return event


def list_turn_events(*, turn_id: uuid.UUID, since_seq: int = 0, limit: int = 250) -> Iterable[PortalTurnEvent]:
    since = int(since_seq or 0)
    limit = max(1, min(int(limit or 0), 500))
    return PortalTurnEvent.objects.filter(turn_id=turn_id, seq__gt=since).order_by("seq")[:limit]
