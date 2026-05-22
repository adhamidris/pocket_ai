from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Iterable

from django.conf import settings
from django.core.cache import cache
from django.db import close_old_connections
from django.db.models import Q
from django.http import HttpRequest, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.agent_runs.models import AgentRunEvent
from apps.api.portal_chat.activity_snapshots import (
    _build_portal_agent_requests_snapshot,
    _build_portal_agent_runs_snapshot,
    _serialize_agent_request_for_portal,
    _serialize_agent_run_event_for_portal,
    _serialize_agent_run_for_portal,
)
from apps.api.portal_chat.request_context import (
    _resolve_request_conversation,
    _service,
)
from apps.api.portal_chat.streaming import _parse_session_since_id
from apps.conversations.models import AgentRequest
from apps.conversations.portal import (
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalValidationError,
)
from apps.conversations.portal_session.event_bus import (
    portal_session_agent_requests_stream_key,
    portal_session_agent_agentic_task_runs_stream_key,
    portal_session_conversation_stream_key,
)
from apps.conversations.portal_turn.events import get_portal_redis_client
from apps.rag.observability.logging import structured_log
from core.tenancy import tenant_context


def _portal_redis_client(*, socket_timeout_seconds: float):
    try:
        from apps.api import chat_portal as chat_portal_facade

        client_factory = getattr(chat_portal_facade, "get_portal_redis_client", get_portal_redis_client)
    except Exception:
        client_factory = get_portal_redis_client
    return client_factory(socket_timeout_seconds=socket_timeout_seconds)


@require_GET
def events(request: HttpRequest) -> StreamingHttpResponse:
    service = _service()
    try:
        conversation, session = _resolve_request_conversation(
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

    conversation_id = getattr(conversation, "id", None)
    business_id = getattr(conversation, "business_profile_id", None)
    agent_profile_id = getattr(conversation, "agent_profile_id", None)
    agent_workforce_enabled = True

    def event_stream() -> Iterable[str]:
        metrics_enabled = bool(getattr(settings, "PORTAL_STREAM_METRICS", False))
        started_at = time.perf_counter()
        first_event_at: float | None = None
        events_sent = 0
        keepalives_sent = 0
        yield "event: statusChanged\n"
        yield f"data: {json.dumps({'status': session.status})}\n\n"
        run_since = timezone.now()
        request_since = timezone.now()
        message_since = timezone.now()
        if conversation_id and agent_workforce_enabled:
            try:
                snapshot = _build_portal_agent_runs_snapshot(
                    conversation_id=conversation_id,
                    business_id=business_id,
                    agent_profile_id=agent_profile_id,
                )
                yield "event: agentRunsSnapshot\n"
                yield f"data: {json.dumps(snapshot)}\n\n"
                cursor = snapshot.get("cursor") if isinstance(snapshot, dict) else {}
                since_raw = cursor.get("since") if isinstance(cursor, dict) else None
                since = None
                if isinstance(since_raw, str) and since_raw.strip():
                    raw = since_raw.strip().replace("Z", "+00:00")
                    try:
                        since = datetime.fromisoformat(raw)
                    except ValueError:
                        since = None
                    if since is not None and since.tzinfo is None:
                        since = since.replace(tzinfo=timezone.utc)
                if since is None:
                    since = timezone.now()
                run_since = since
            except Exception:  # pragma: no cover - snapshot is best effort only
                snapshot = None
                run_since = timezone.now()

            try:
                request_snapshot = _build_portal_agent_requests_snapshot(
                    business_id=business_id,
                    agent_profile_id=agent_profile_id,
                )
                yield "event: agentRequestsSnapshot\n"
                yield f"data: {json.dumps(request_snapshot)}\n\n"
                cursor = request_snapshot.get("cursor") if isinstance(request_snapshot, dict) else {}
                since_raw = cursor.get("since") if isinstance(cursor, dict) else None
                since = None
                if isinstance(since_raw, str) and since_raw.strip():
                    raw = since_raw.strip().replace("Z", "+00:00")
                    try:
                        since = datetime.fromisoformat(raw)
                    except ValueError:
                        since = None
                    if since is not None and since.tzinfo is None:
                        since = since.replace(tzinfo=timezone.utc)
                if since is None:
                    since = timezone.now()
                request_since = since
            except Exception:  # pragma: no cover - snapshot is best effort only
                request_since = timezone.now()
            message_since = timezone.now()
        else:
            run_since = timezone.now()
            request_since = timezone.now()
            message_since = timezone.now()

        seen: set[tuple[str, int]] = set()
        seen_order: list[tuple[str, int]] = []
        seen_limit = 2000
        seen_requests: set[tuple[str, str]] = set()
        seen_requests_order: list[tuple[str, str]] = []
        seen_messages: set[str] = set()
        seen_messages_order: list[str] = []
        last_heartbeat = time.monotonic()
        session_bus = str(getattr(settings, "PORTAL_SESSION_EVENT_BUS", "postgres") or "postgres").strip().lower()
        redis_conn = None
        redis_stream_positions: dict[str, str] = {}
        if session_bus == "redis":
            redis_conn = _portal_redis_client(socket_timeout_seconds=20.0)
            if redis_conn is not None and conversation_id:
                start_id = _parse_session_since_id(request)
                if not start_id:
                    start_ms = max(0, int(time.time() * 1000) - 1)
                    start_id = f"{start_ms}-0"
                redis_stream_positions[portal_session_conversation_stream_key(conversation_id=conversation_id)] = start_id
                if agent_workforce_enabled and agent_profile_id:
                    redis_stream_positions[portal_session_agent_requests_stream_key(agent_profile_id=agent_profile_id)] = start_id
                    redis_stream_positions[portal_session_agent_agentic_task_runs_stream_key(agent_profile_id=agent_profile_id)] = start_id
            else:
                redis_conn = None
                session_bus = "postgres"

        try:
            while True:
                close_old_connections()
                if redis_conn is not None and redis_stream_positions:
                    try:
                        entries = redis_conn.xread(redis_stream_positions, count=250, block=15_000)
                    except Exception:
                        redis_conn = None
                        session_bus = "postgres"
                        continue

                    if entries:
                        for raw_stream, raw_entries in entries:
                            stream_key = (
                                raw_stream.decode("utf-8", errors="replace")
                                if isinstance(raw_stream, (bytes, bytearray))
                                else str(raw_stream)
                            )
                            for raw_id, fields in raw_entries:
                                entry_id = (
                                    raw_id.decode("utf-8", errors="replace")
                                    if isinstance(raw_id, (bytes, bytearray))
                                    else str(raw_id)
                                )
                                redis_stream_positions[stream_key] = entry_id

                                raw_event = fields.get(b"event") if isinstance(fields, dict) else None
                                if raw_event is None and isinstance(fields, dict):
                                    raw_event = fields.get("event")  # type: ignore[index]
                                event_name = (
                                    raw_event.decode("utf-8", errors="replace")
                                    if isinstance(raw_event, (bytes, bytearray))
                                    else str(raw_event or "")
                                ).strip()
                                if not event_name:
                                    continue
                                raw_payload = fields.get(b"payload") if isinstance(fields, dict) else None
                                if raw_payload is None and isinstance(fields, dict):
                                    raw_payload = fields.get("payload")  # type: ignore[index]
                                payload_text = (
                                    raw_payload.decode("utf-8", errors="replace")
                                    if isinstance(raw_payload, (bytes, bytearray))
                                    else str(raw_payload or "")
                                )
                                try:
                                    payload = json.loads(payload_text) if payload_text else {}
                                except Exception:
                                    payload = {}

                                if event_name == "agentRunEvent" and isinstance(payload, dict):
                                    event_obj = payload.get("event") if isinstance(payload.get("event"), dict) else {}
                                    key = (str(event_obj.get("runId") or ""), int(event_obj.get("sequenceIndex") or 0))
                                    if key in seen:
                                        continue
                                    seen.add(key)
                                    seen_order.append(key)
                                    if len(seen_order) > seen_limit:
                                        old = seen_order.pop(0)
                                        seen.discard(old)
                                elif event_name == "agentRequestEvent" and isinstance(payload, dict):
                                    req_obj = payload.get("request") if isinstance(payload.get("request"), dict) else {}
                                    key = (str(req_obj.get("id") or ""), str(req_obj.get("updatedAt") or ""))
                                    if key in seen_requests:
                                        continue
                                    seen_requests.add(key)
                                    seen_requests_order.append(key)
                                    if len(seen_requests_order) > seen_limit:
                                        old = seen_requests_order.pop(0)
                                        seen_requests.discard(old)
                                elif event_name == "conversationMessage" and isinstance(payload, dict):
                                    msg_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
                                    msg_id = str(msg_obj.get("id") or "")
                                    if msg_id and msg_id in seen_messages:
                                        continue
                                    if msg_id:
                                        seen_messages.add(msg_id)
                                        seen_messages_order.append(msg_id)
                                        if len(seen_messages_order) > seen_limit:
                                            old = seen_messages_order.pop(0)
                                            seen_messages.discard(old)

                                if first_event_at is None:
                                    first_event_at = time.perf_counter()
                                events_sent += 1
                                yield f"id: {entry_id}\n"
                                yield f"event: {event_name}\n"
                                yield f"data: {json.dumps(payload)}\n\n"
                        continue

                if session_bus != "postgres":
                    # Waiting for more Redis stream events.
                    now = time.monotonic()
                    if now - last_heartbeat >= 15.0:
                        keepalives_sent += 1
                        yield "event: heartbeat\n"
                        yield "data: {}\n\n"
                        last_heartbeat = now
                    continue

                if conversation_id and agent_workforce_enabled:
                    with tenant_context(business_id):
                        event_filter = Q(run__conversation_id=conversation_id)
                        if agent_profile_id:
                            event_filter |= Q(
                                run__business_profile_id=business_id,
                                run__agent_profile_id=agent_profile_id,
                                run__agentic_task_id__isnull=False,
                            )
                        events_batch = list(
                            AgentRunEvent.objects.select_related("run")
                            .filter(event_filter)
                            .filter(created_at__gte=run_since)
                            .order_by("created_at", "run_id", "sequence_index")[:250]
                        )
                    if events_batch:
                        latest_created_at = run_since
                        for event in events_batch:
                            if event.created_at and event.created_at > latest_created_at:
                                latest_created_at = event.created_at
                            key = (str(event.run_id), int(event.sequence_index))
                            if key in seen:
                                continue
                            seen.add(key)
                            seen_order.append(key)
                            if len(seen_order) > seen_limit:
                                old = seen_order.pop(0)
                                seen.discard(old)
                            run_obj = getattr(event, "run", None)
                            payload = {
                                "run": _serialize_agent_run_for_portal(run_obj)
                                if run_obj
                                else {"id": str(event.run_id)},
                                "event": _serialize_agent_run_event_for_portal(event),
                            }
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
                            yield "event: agentRunEvent\n"
                            yield f"data: {json.dumps(payload)}\n\n"
                        run_since = latest_created_at

                if conversation_id and agent_workforce_enabled:
                    from apps.conversations.models import ConversationMessage

                    def _serialize_message(msg: ConversationMessage) -> dict[str, object]:
                        return {
                            "id": str(msg.id),
                            "sender": msg.sender,
                            "body": msg.body or "",
                            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
                            "metadata": msg.metadata
                            if isinstance(getattr(msg, "metadata", None), dict)
                            else {},
                            "content_blocks": msg.content_blocks
                            if isinstance(getattr(msg, "content_blocks", None), list)
                            else [],
                        }

                    with tenant_context(business_id):
                        messages_batch = list(
                            ConversationMessage.objects.filter(conversation_id=conversation_id)
                            .filter(created_at__gte=message_since)
                            .filter(Q(metadata__source="agent_run") | Q(metadata__source="voice_call"))
                            .order_by("created_at", "id")[:250]
                        )
                    if messages_batch:
                        latest_created_at = message_since
                        for msg in messages_batch:
                            if msg.created_at and msg.created_at > latest_created_at:
                                latest_created_at = msg.created_at
                            key = str(msg.id)
                            if key in seen_messages:
                                continue
                            seen_messages.add(key)
                            seen_messages_order.append(key)
                            if len(seen_messages_order) > seen_limit:
                                old = seen_messages_order.pop(0)
                                seen_messages.discard(old)
                            payload = {"message": _serialize_message(msg)}
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
                            yield "event: conversationMessage\n"
                            yield f"data: {json.dumps(payload)}\n\n"
                        message_since = latest_created_at

                if business_id and agent_profile_id and agent_workforce_enabled:
                    with tenant_context(business_id):
                        requests_batch = list(
                            AgentRequest.objects.select_related("from_agent_profile", "to_agent_profile")
                            .filter(business_profile_id=business_id)
                            .filter(
                                Q(to_agent_profile_id=agent_profile_id)
                                | Q(from_agent_profile_id=agent_profile_id)
                            )
                            .filter(updated_at__gte=request_since)
                            .order_by("updated_at", "id")[:250]
                        )
                    if requests_batch:
                        latest_updated_at = request_since
                        for req in requests_batch:
                            if req.updated_at and req.updated_at > latest_updated_at:
                                latest_updated_at = req.updated_at
                            updated_key = req.updated_at.isoformat() if req.updated_at else ""
                            key = (str(req.id), updated_key)
                            if key in seen_requests:
                                continue
                            seen_requests.add(key)
                            seen_requests_order.append(key)
                            if len(seen_requests_order) > seen_limit:
                                old = seen_requests_order.pop(0)
                                seen_requests.discard(old)
                            payload = {"request": _serialize_agent_request_for_portal(req)}
                            if first_event_at is None:
                                first_event_at = time.perf_counter()
                            events_sent += 1
                            yield "event: agentRequestEvent\n"
                            yield f"data: {json.dumps(payload)}\n\n"
                        request_since = latest_updated_at

                # Poll for voice call transcript events (faster polling for real-time feel)
                if conversation_id:
                    cache_key = f"voice_transcript:{conversation_id}"
                    transcript_events = cache.get(cache_key) or []
                    if isinstance(transcript_events, list) and transcript_events:
                        # Use atomic pop pattern: get, process, then clear only what we processed
                        cache.delete(cache_key)
                        for evt in transcript_events:
                            if isinstance(evt, dict):
                                if first_event_at is None:
                                    first_event_at = time.perf_counter()
                                events_sent += 1
                                yield "event: voiceCallTranscript\n"
                                yield f"data: {json.dumps(evt)}\n\n"
                        # Shorter sleep when actively streaming transcripts
                        time.sleep(0.15)
                        continue

                now = time.monotonic()
                if now - last_heartbeat >= 15.0:
                    keepalives_sent += 1
                    yield "event: heartbeat\n"
                    yield "data: {}\n\n"
                    last_heartbeat = now
                time.sleep(0.5)  # Reduced from 1.0s for better responsiveness
        finally:
            if metrics_enabled:
                structured_log(
                    "portal",
                    "stream.session_sse",
                    {
                        "conversation_id": str(conversation_id or ""),
                        "business_id": str(business_id or ""),
                        "agent_id": str(agent_profile_id or ""),
                        "elapsed_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
                        "first_event_ms": int(max(0.0, (first_event_at - started_at) * 1000.0)) if first_event_at else None,
                        "events_sent": int(events_sent),
                        "heartbeats_sent": int(keepalives_sent),
                        "agent_workforce_enabled": bool(agent_workforce_enabled),
                        "event_bus": str(session_bus or "postgres"),
                    },
                )

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    response["X-Portal-Stream-Protocol-Version"] = str(getattr(settings, "PORTAL_STREAM_PROTOCOL_VERSION", 1))
    return response
