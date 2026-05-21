from __future__ import annotations

import json
import time
import uuid
from http import HTTPStatus
from typing import Iterable

import requests
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.tenancy import tenant_bypass, tenant_context

from apps.voice.models import CallEvent, CallSession, CallStatus
from apps.voice.providers.credentials import (
    VOICE_PROVIDER_TELNYX,
    VOICE_PROVIDER_TWILIO,
    resolve_telnyx_config,
    resolve_twilio_config,
)


def _resolve_call_session(request: HttpRequest, call_id: uuid.UUID) -> tuple[CallSession | None, JsonResponse | None]:
    if not request.user.is_authenticated:
        return None, JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    with tenant_bypass():
        call = CallSession.objects.select_related("business_profile", "agent_profile").filter(id=call_id).first()
        if not call:
            return None, JsonResponse({"error": "NOT_FOUND", "message": "Call not found."}, status=HTTPStatus.NOT_FOUND)

        if request.user.is_staff:
            return call, None

        business = getattr(call, "business_profile", None)
        agent = getattr(call, "agent_profile", None)
        business_owner_id = getattr(business, "user_id", None)
        agent_user_id = getattr(agent, "user_id", None)
        if request.user.id not in {business_owner_id, agent_user_id}:
            return None, JsonResponse({"error": "FORBIDDEN", "message": "Forbidden."}, status=HTTPStatus.FORBIDDEN)

    return call, None


@require_GET
def voice_calls_collection(request: HttpRequest) -> JsonResponse:
    """
    List recent voice calls for a business.

    GET /api/voice/calls/?business_id=...
    """

    if not request.user.is_authenticated:
        return JsonResponse({"error": "UNAUTHORIZED", "message": "Login required."}, status=HTTPStatus.UNAUTHORIZED)

    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business_id = None
    if business_param:
        try:
            business_id = uuid.UUID(str(business_param))
        except (TypeError, ValueError):
            return JsonResponse({"error": "VALIDATION_ERROR", "message": "business_id must be a UUID."}, status=HTTPStatus.BAD_REQUEST)

    if not business_id:
        business = request.user.business_profiles.order_by("-created_at").first()
        business_id = getattr(business, "id", None)
    if not business_id:
        return JsonResponse({"error": "BUSINESS_REQUIRED", "message": "No business selected."}, status=HTTPStatus.BAD_REQUEST)

    if not request.user.is_staff and not request.user.business_profiles.filter(id=business_id).exists():
        return JsonResponse({"error": "FORBIDDEN", "message": "Forbidden."}, status=HTTPStatus.FORBIDDEN)

    with tenant_context(business_id):
        calls = (
            CallSession.objects.filter(business_profile_id=business_id)
            .order_by("-created_at")[:50]
        )
        payload = [
            {
                "id": str(call.id),
                "status": call.status,
                "transport_provider": call.transport_provider,
                "call_type": call.call_type,
                "to_phone_number": call.to_phone_number,
                "country": call.country,
                "objective": call.objective[:200],
                "created_at": call.created_at.isoformat().replace("+00:00", "Z") if call.created_at else None,
            }
            for call in calls
        ]

    return JsonResponse({"calls": payload})


@require_GET
def voice_call_detail(request: HttpRequest, call_id: uuid.UUID) -> JsonResponse:
    call, error = _resolve_call_session(request, call_id)
    if error:
        return error
    assert call is not None

    with tenant_context(call.business_profile_id):
        call = CallSession.objects.filter(id=call_id).first()
        if not call:
            return JsonResponse({"error": "NOT_FOUND", "message": "Call not found."}, status=HTTPStatus.NOT_FOUND)

    return JsonResponse(
        {
            "id": str(call.id),
            "status": call.status,
            "transport_provider": call.transport_provider,
            "provider_call_sid": call.provider_call_sid,
            "call_type": call.call_type,
            "language": call.language,
            "country": call.country,
            "to_phone_number": call.to_phone_number,
            "from_phone_number": call.from_phone_number,
            "objective": call.objective,
            "consent_obtained": bool(call.consent_obtained),
            "recording_sid": call.recording_sid,
            "recording_url": call.recording_url,
            "summary": call.summary or "",
            "action_items": call.action_items if isinstance(call.action_items, list) else [],
            "insights": call.insights if isinstance(getattr(call, "insights", None), dict) else {},
            "created_at": call.created_at.isoformat().replace("+00:00", "Z") if call.created_at else None,
            "started_at": call.started_at.isoformat().replace("+00:00", "Z") if call.started_at else None,
            "ended_at": call.ended_at.isoformat().replace("+00:00", "Z") if call.ended_at else None,
        }
    )


@require_GET
def voice_call_events(request: HttpRequest, call_id: uuid.UUID) -> StreamingHttpResponse:
    """
    Stream CallEvent rows as server-sent events.

    Query params:
    - after_id: last seen event id (optional)
    """

    call, error = _resolve_call_session(request, call_id)
    if error:
        return error  # type: ignore[return-value]
    assert call is not None

    try:
        after_id = int(request.GET.get("after_id") or request.GET.get("afterId") or 0)
    except Exception:
        after_id = 0

    business_id = call.business_profile_id

    def event_stream() -> Iterable[str]:
        nonlocal after_id
        yield "event: callSnapshot\n"
        yield f"data: {json.dumps({'call_id': str(call_id), 'status': call.status})}\n\n"

        last_heartbeat = time.monotonic()
        while True:
            with tenant_context(business_id):
                events = (
                    CallEvent.objects.filter(call_session_id=call_id, id__gt=after_id)
                    .order_by("id")[:200]
                )
                for ev in events:
                    after_id = max(after_id, int(ev.id))
                    payload = {
                        "id": int(ev.id),
                        "type": ev.event_type,
                        "created_at": ev.created_at.isoformat().replace("+00:00", "Z") if ev.created_at else None,
                        "payload": ev.payload if isinstance(ev.payload, dict) else {},
                    }
                    yield "event: callEvent\n"
                    yield f"data: {json.dumps(payload)}\n\n"

            now = time.monotonic()
            if now - last_heartbeat >= 10.0:
                yield "event: heartbeat\n"
                yield "data: {}\n\n"
                last_heartbeat = now
            time.sleep(0.5)

    return StreamingHttpResponse(event_stream(), content_type="text/event-stream")


@csrf_protect
@require_POST
def voice_call_hangup(request: HttpRequest, call_id: uuid.UUID) -> JsonResponse:
    call, error = _resolve_call_session(request, call_id)
    if error:
        return error
    assert call is not None

    provider = str(call.transport_provider or "").strip().lower()
    provider_call_sid = str(call.provider_call_sid or call.twilio_call_sid or "").strip()
    if not provider:
        provider = VOICE_PROVIDER_TWILIO if call.twilio_call_sid else ""

    if not provider_call_sid:
        return JsonResponse({"error": "MISSING_CALL_SID", "message": "Call SID not available yet."}, status=HTTPStatus.CONFLICT)

    if provider == VOICE_PROVIDER_TWILIO:
        try:
            cfg = resolve_twilio_config(
                business_id=call.business_profile_id,
                require_from_number=False,
            )
        except Exception as exc:
            return JsonResponse({"error": "MISSING_TWILIO_CONFIG", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        try:
            resp = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{cfg.account_sid}/Calls/{provider_call_sid}.json",
                auth=(cfg.account_sid, cfg.auth_token),
                data={"Status": "completed"},
                timeout=20,
            )
        except Exception as exc:
            return JsonResponse({"error": "TWILIO_REQUEST_FAILED", "message": str(exc)}, status=HTTPStatus.BAD_GATEWAY)

        if resp.status_code >= 400:
            return JsonResponse(
                {"error": "TWILIO_ERROR", "status": resp.status_code, "message": resp.text[:300]},
                status=HTTPStatus.BAD_GATEWAY,
            )
    elif provider == VOICE_PROVIDER_TELNYX:
        try:
            cfg = resolve_telnyx_config(
                business_id=call.business_profile_id,
                require_from_number=False,
            )
        except Exception as exc:
            return JsonResponse({"error": "MISSING_TELNYX_CONFIG", "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)

        try:
            resp = requests.post(
                f"https://api.telnyx.com/v2/texml/Accounts/{cfg.account_sid}/Calls/{provider_call_sid}",
                headers={"Authorization": f"Bearer {cfg.api_key}", "Accept": "application/json"},
                data={"Status": "completed"},
                timeout=20,
            )
        except Exception as exc:
            return JsonResponse({"error": "TELNYX_REQUEST_FAILED", "message": str(exc)}, status=HTTPStatus.BAD_GATEWAY)

        if resp.status_code >= 400:
            return JsonResponse(
                {"error": "TELNYX_ERROR", "status": resp.status_code, "message": resp.text[:300]},
                status=HTTPStatus.BAD_GATEWAY,
            )
    else:
        return JsonResponse(
            {"error": "UNSUPPORTED_PROVIDER", "message": "Call transport provider is unsupported."},
            status=HTTPStatus.BAD_REQUEST,
        )

    with tenant_context(call.business_profile_id):
        CallSession.objects.filter(id=call_id).update(status=CallStatus.CANCELLED, ended_at=timezone.now())

    return JsonResponse({"ok": True, "status": CallStatus.CANCELLED, "transport_provider": provider})
