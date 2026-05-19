from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_POST

from apps.api.portal_chat.request_context import (
    _json_error,
    _parse_json_body,
    _resolve_request_conversation,
    _service,
)
from apps.api.portal_chat.serializers import (
    _serialize_tool_approval,
    _session_to_dict,
)
from apps.conversations.models import ConversationToolApproval
from apps.conversations.portal import (
    PortalAuthorizationError,
    PortalNotFoundError,
    PortalValidationError,
)
from core.tenancy import tenant_context


@require_POST
def portal_tool_history(request: HttpRequest) -> JsonResponse:
    service = _service()
    try:
        payload = _parse_json_body(request)
    except PortalValidationError as exc:
        return _json_error("invalid_json", str(exc))

    raw_limit = payload.get("limit")
    try:
        limit = int(raw_limit) if raw_limit is not None else 100
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 250))

    try:
        conversation, session = _resolve_request_conversation(
            service=service,
            request=request,
            payload=payload,
            include_messages=True,
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
    approvals: list[dict[str, object]] = []
    tool_events: list[dict[str, object]] = []

    with tenant_context(business_id):
        approvals_qs = (
            ConversationToolApproval.objects.select_related("connection")
            .filter(conversation=conversation)
            .order_by("-requested_at")[:limit]
        )
        approvals = [
            {
                **_serialize_tool_approval(item),
                "connection_name": getattr(getattr(item, "connection", None), "name", "") or "",
            }
            for item in approvals_qs
        ]

        seen: set[tuple[str, str]] = set()
        remote_by_event_id: dict[str, dict[str, str]] = {}

        for message in getattr(conversation, "messages", ()).all():
            blocks = message.content_blocks if isinstance(getattr(message, "content_blocks", None), list) else []
            if not blocks:
                continue
            message_id_value = str(message.id)
            message_sent_at = message.sent_at.isoformat() if getattr(message, "sent_at", None) else None

            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = str(block.get("type") or "").strip().lower()
                payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}

                if block_type == "tool_use":
                    event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
                    phase = str(payload.get("phase") or "").strip().lower() or "started"
                    if not event_id:
                        continue
                    key = (event_id, phase)
                    if key in seen:
                        continue
                    seen.add(key)

                    remote = payload.get("remote") if isinstance(payload.get("remote"), dict) else {}
                    connection_name = str(remote.get("connection_name") or "").strip()
                    remote_tool_name = str(remote.get("remote_tool") or "").strip()
                    if connection_name or remote_tool_name:
                        remote_by_event_id[event_id] = {
                            "connection_name": connection_name,
                            "remote_tool_name": remote_tool_name,
                        }

                    summary: dict[str, object] = {
                        "event_id": event_id,
                        "phase": phase,
                        "status": str(payload.get("status") or "").strip(),
                        "tool_name": str(payload.get("tool_name") or payload.get("toolName") or "").strip(),
                        "connection_name": connection_name,
                        "remote_tool_name": remote_tool_name,
                        "duration_ms": payload.get("duration_ms") if payload.get("duration_ms") is not None else None,
                        "message_id": message_id_value,
                        "message_sent_at": message_sent_at,
                    }
                    tool_events.append(summary)
                    continue

                if block_type == "tool_result":
                    event_id = str(payload.get("event_id") or payload.get("eventId") or "").strip()
                    if not event_id:
                        continue
                    phase = "finished"
                    key = (event_id, phase)
                    if key in seen:
                        continue
                    seen.add(key)

                    remote_hint = remote_by_event_id.get(event_id) or {}
                    summary = {
                        "event_id": event_id,
                        "phase": phase,
                        "status": str(payload.get("status") or "").strip(),
                        "tool_name": str(payload.get("tool_name") or payload.get("toolName") or "").strip(),
                        "connection_name": remote_hint.get("connection_name", ""),
                        "remote_tool_name": remote_hint.get("remote_tool_name", ""),
                        "duration_ms": payload.get("duration_ms") if payload.get("duration_ms") is not None else None,
                        "message_id": message_id_value,
                        "message_sent_at": message_sent_at,
                    }
                    tool_events.append(summary)

    tool_events.sort(key=lambda item: (item.get("message_sent_at") or "", item.get("event_id") or "", item.get("phase") or ""))
    return JsonResponse(
        {
            "session": _session_to_dict(session),
            "history": {
                "approvals": approvals,
                "toolEvents": tool_events[-limit:],
            },
        }
    )
