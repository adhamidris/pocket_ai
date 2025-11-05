from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List

from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET, require_POST


@dataclass
class ChatMessage:
    id: str
    author: str
    body: str
    sent_at: datetime


ACTIVE_SESSIONS: Dict[str, List[ChatMessage]] = {}


def _json_response(payload, status=200):
    return JsonResponse(payload, status=status)


def ensure_session(session_token: str) -> List[ChatMessage]:
    if session_token not in ACTIVE_SESSIONS:
        ACTIVE_SESSIONS[session_token] = [
            ChatMessage(id="welcome", author="Pocket AI", body="Hi! How can we help today?", sent_at=datetime.now(timezone.utc)),
        ]
    return ACTIVE_SESSIONS[session_token]


@require_POST
def send_message(request: HttpRequest) -> HttpResponse:
    data = json.loads(request.body.decode("utf-8"))
    session_token = data.get("session_token")
    body = data.get("body")
    if not session_token or not body:
        return _json_response({"error": "invalid_request"}, status=400)
    messages = ensure_session(session_token)
    messages.append(
        ChatMessage(
            id=f"user-{len(messages)}",
            author="Visitor",
            body=body,
            sent_at=datetime.now(timezone.utc),
        )
    )
    return _json_response({"ok": True})


@require_POST
def submit_csat(request: HttpRequest) -> HttpResponse:
    data = json.loads(request.body.decode("utf-8"))
    session_token = data.get("session_token")
    if not session_token:
        return _json_response({"error": "invalid_request"}, status=400)
    return _json_response({"conversation_id": session_token, "recorded_at": datetime.now(timezone.utc).isoformat()})


@require_POST
def stream_send(request: HttpRequest) -> StreamingHttpResponse:
    data = json.loads(request.body.decode("utf-8"))
    session_token = data.get("session_token")
    body = data.get("body")
    if not session_token or not body:
        return StreamingHttpResponse(status=400)
    messages = ensure_session(session_token)
    messages.append(
        ChatMessage(
            id=f"user-{len(messages)}",
            author="Visitor",
            body=body,
            sent_at=datetime.now(timezone.utc),
        )
    )

    def event_stream() -> Iterable[str]:
        yield "event: delta\n"
        yield "data: \"Pocket AI is thinking...\"\n\n"
        time.sleep(0.8)
        response = {
            "text": f"Our assistant received your message: {body}",
            "id": f"assistant-{len(messages)}",
        }
        messages.append(
            ChatMessage(
                id=response["id"],
                author="Pocket AI",
                body=response["text"],
                sent_at=datetime.now(timezone.utc),
            )
        )
        yield f"event: final\n"
        yield f"data: {json.dumps(response)}\n\n"

    return StreamingHttpResponse(event_stream(), content_type="text/event-stream")


@require_GET
def events(request: HttpRequest) -> StreamingHttpResponse:
    session_token = request.GET.get("session_token")
    if not session_token:
        return StreamingHttpResponse(status=400)

    def heartbeat() -> Iterable[str]:
        while True:
            yield "event: heartbeat\n"
            yield "data: {}\n\n"
            time.sleep(15)

    return StreamingHttpResponse(heartbeat(), content_type="text/event-stream")
