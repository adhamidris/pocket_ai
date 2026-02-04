from __future__ import annotations

import json
import os
import uuid
from typing import Any

from django.conf import settings

from apps.conversations.portal_turn_events import get_portal_redis_client


def _session_bus_enabled() -> bool:
    if str(getattr(settings, "PORTAL_SESSION_EVENT_BUS", "postgres") or "postgres").strip().lower() != "redis":
        return False
    return bool((os.getenv("REDIS_URL") or "").strip())


def _prefix() -> str:
    value = str(getattr(settings, "PORTAL_SESSION_EVENT_BUS_REDIS_STREAM_PREFIX", "portal:session") or "portal:session").strip()
    return value.rstrip(":")


def portal_session_conversation_stream_key(*, conversation_id: uuid.UUID) -> str:
    return f"{_prefix()}:conversation:{conversation_id}:events"


def portal_session_agent_requests_stream_key(*, agent_profile_id: uuid.UUID) -> str:
    return f"{_prefix()}:agent:{agent_profile_id}:requests"


def publish_portal_session_stream_event(
    *,
    stream_key: str,
    event_name: str,
    payload: dict[str, Any],
    ttl_seconds: int | None = None,
) -> None:
    """
    Best-effort publish of a session SSE event to Redis Streams.

    Stored fields:
    - event: SSE event name (e.g. "agentRunEvent")
    - payload: JSON string (the `data:` body)
    """

    if not _session_bus_enabled():
        return
    conn = get_portal_redis_client(socket_timeout_seconds=0.5)
    if conn is None:
        return

    ttl = ttl_seconds
    if ttl is None:
        ttl = int(getattr(settings, "PORTAL_SESSION_EVENT_BUS_REDIS_STREAM_TTL_SECONDS", 3600) or 3600)
    ttl = max(60, int(ttl))
    maxlen = int(getattr(settings, "PORTAL_SESSION_EVENT_BUS_REDIS_STREAM_MAXLEN", 5000) or 5000)
    maxlen = max(100, int(maxlen))

    event_norm = str(event_name or "").strip()
    if not event_norm:
        return
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)

    try:
        pipe = conn.pipeline()
        pipe.xadd(stream_key, {"event": event_norm, "payload": payload_json}, id="*", maxlen=maxlen, approximate=True)
        pipe.expire(stream_key, ttl)
        pipe.execute()
    except Exception:
        return


def publish_portal_conversation_event(*, conversation_id: uuid.UUID, event_name: str, payload: dict[str, Any]) -> None:
    publish_portal_session_stream_event(
        stream_key=portal_session_conversation_stream_key(conversation_id=conversation_id),
        event_name=event_name,
        payload=payload,
    )


def publish_portal_agent_request_event(*, agent_profile_id: uuid.UUID, payload: dict[str, Any]) -> None:
    publish_portal_session_stream_event(
        stream_key=portal_session_agent_requests_stream_key(agent_profile_id=agent_profile_id),
        event_name="agentRequestEvent",
        payload=payload,
    )
