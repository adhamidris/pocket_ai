from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.voice.models import CallEvent, CallSession, CallStatus, CallType

try:
    import phonenumbers  # type: ignore
except Exception:  # pragma: no cover - optional dependency for spike
    phonenumbers = None


logger = logging.getLogger(__name__)

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _detect_country(e164: str) -> str:
    if not phonenumbers:
        return ""
    try:
        parsed = phonenumbers.parse(e164, None)
        region = phonenumbers.region_code_for_number(parsed) or ""
        return str(region).upper()[:2]
    except Exception:
        return ""


def _json_body(request: HttpRequest) -> dict[str, Any]:
    if not request.body:
        return {}
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _twilio_account_sid() -> str:
    return (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()


def _twilio_auth_token() -> str:
    return (os.getenv("TWILIO_AUTH_TOKEN") or "").strip()


def _twilio_from_number() -> str:
    return (os.getenv("TWILIO_FROM_NUMBER") or "").strip()


def _twilio_webhook_base_url() -> str:
    return (os.getenv("TWILIO_WEBHOOK_BASE_URL") or "").strip().rstrip("/")


def _spike_ws_base_url() -> str:
    """
    Public WSS base URL for the Media Streams WebSocket server.

    Example: wss://<ngrok-domain>
    """

    return (os.getenv("VOICE_SPIKE_WS_BASE_URL") or "").strip().rstrip("/")


def _spike_ws_stream_url(session_id: uuid.UUID) -> str:
    base = _spike_ws_base_url()
    return f"{base}/voice/spike/stream/{session_id}"


def _twiml_response(xml: str) -> HttpResponse:
    return HttpResponse(xml, content_type="text/xml; charset=utf-8", status=200)


def _log_event(session: CallSession, event_type: str, payload: dict[str, Any] | None = None) -> None:
    try:
        CallEvent.objects.create(call_session=session, event_type=event_type, payload=payload or {})
    except Exception:  # pragma: no cover - logging must not break webhooks
        logger.exception("Failed to persist CallEvent type=%s session=%s", event_type, session.id)


def _twiml_gather_consent(*, session: CallSession) -> str:
    """
    Mandatory everywhere:
    - AI disclosure
    - Recording notice
    - Explicit consent (DTMF) before recording starts
    """

    consent_url = f"{_twilio_webhook_base_url()}/voice/spike/consent/{session.id}/"
    # Minimal, safe messaging. Keep it short to reduce hangups.
    disclosure = (
        "Hello. This is an AI assistant calling. "
        "This call will be recorded. "
        "Press 1 to consent and continue. "
        "If you do not consent, please hang up."
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Say>{_xml_escape(disclosure)}</Say>"
        f'<Gather numDigits="1" action="{_xml_escape(consent_url)}" method="POST" timeout="8">'
        "<Say>Press 1 to continue.</Say>"
        "</Gather>"
        "<Say>No consent received. Goodbye.</Say>"
        "<Hangup/>"
        "</Response>"
    )


def _twiml_after_consent(*, session: CallSession) -> str:
    stream_url = _spike_ws_stream_url(session.id)
    recording_cb = f"{_twilio_webhook_base_url()}/voice/spike/recording/{session.id}/"
    # Start recording only after consent, then connect the media stream.
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Say>Thank you. Please hold.</Say>"
        "<Start>"
        f'<Record recordingStatusCallback="{_xml_escape(recording_cb)}" '
        'recordingStatusCallbackMethod="POST" '
        'trim="trim-silence" />'
        "</Start>"
        "<Connect>"
        f'<Stream url="{_xml_escape(stream_url)}" />'
        "</Connect>"
        "</Response>"
    )


def _twiml_decline() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Say>Understood. Goodbye.</Say>"
        "<Hangup/>"
        "</Response>"
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


@csrf_exempt
@require_http_methods(["POST"])
def spike_start_call(request: HttpRequest) -> JsonResponse:
    """
    Start an outbound call via Twilio for Phase 0 spike testing.

    Body (JSON):
    - to_phone_number: E.164
    - objective: string
    - call_type: service|marketing (default: service; marketing is allowed for spike testing but should be gated later)
    - language: en|ar (default: en)
    """

    payload = _json_body(request)
    to_phone_number = str(payload.get("to_phone_number") or "").strip()
    objective = str(payload.get("objective") or "").strip()
    call_type = str(payload.get("call_type") or CallType.SERVICE).strip().lower()
    language = str(payload.get("language") or "en").strip().lower()

    if not E164_RE.match(to_phone_number):
        return JsonResponse({"error": "invalid_phone_number", "detail": "Expected E.164 like +201234567890"}, status=400)
    if not objective:
        return JsonResponse({"error": "missing_objective"}, status=400)

    ws_base = _spike_ws_base_url()
    if not ws_base:
        return JsonResponse({"error": "missing_ws_base_url", "detail": "Set VOICE_SPIKE_WS_BASE_URL=wss://..."}, status=400)

    account_sid = _twilio_account_sid()
    auth_token = _twilio_auth_token()
    from_number = _twilio_from_number()
    webhook_base = _twilio_webhook_base_url()
    if not (account_sid and auth_token and from_number and webhook_base):
        return JsonResponse(
            {
                "error": "missing_twilio_config",
                "detail": "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_WEBHOOK_BASE_URL",
            },
            status=400,
        )

    if call_type not in {CallType.SERVICE, CallType.MARKETING}:
        call_type = CallType.SERVICE
    if language not in {"en", "ar"}:
        language = "en"

    country = _detect_country(to_phone_number)
    if not country:
        return JsonResponse(
            {
                "error": "unknown_country",
                "detail": "Could not infer country from E.164 number. Install `phonenumbers` and use a valid E.164.",
            },
            status=400,
        )

    session = CallSession.objects.create(
        objective=objective,
        call_type=call_type,
        language=language,
        country=country,
        to_phone_number=to_phone_number,
        from_phone_number=from_number,
        status=CallStatus.INITIATING,
        metadata={"spike": True},
    )
    _log_event(session, "spike.created", {"to": to_phone_number, "country": session.country, "call_type": call_type})

    twiml_url = f"{webhook_base}/voice/spike/twiml/{session.id}/"
    status_cb = f"{webhook_base}/voice/spike/status/{session.id}/"

    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls.json",
            auth=(account_sid, auth_token),
            headers={"Accept": "application/json"},
            data={
                "From": from_number,
                "To": to_phone_number,
                "Url": twiml_url,
                "Method": "POST",
                "StatusCallback": status_cb,
                "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            },
            timeout=20,
        )
    except Exception as exc:
        session.status = CallStatus.FAILED
        session.last_error = str(exc)
        session.save(update_fields=["status", "last_error", "updated_at"])
        _log_event(session, "twilio.call_create.failed", {"error": str(exc)})
        return JsonResponse({"error": "twilio_request_failed", "detail": str(exc), "call_session_id": str(session.id)}, status=502)

    if resp.status_code >= 400:
        session.status = CallStatus.FAILED
        session.last_error = f"twilio_error:{resp.status_code}"
        session.save(update_fields=["status", "last_error", "updated_at"])
        _log_event(session, "twilio.call_create.failed", {"status": resp.status_code, "body": resp.text[:500]})
        return JsonResponse(
            {"error": "twilio_error", "status": resp.status_code, "detail": resp.text[:500], "call_session_id": str(session.id)},
            status=502,
        )

    # Twilio returns form-encoded by default unless Accept JSON. Try parse safely.
    call_sid = ""
    try:
        data = resp.json()
        call_sid = str(data.get("sid") or "")
    except Exception:
        pass
    if call_sid:
        session.twilio_call_sid = call_sid
        session.status = CallStatus.RINGING
        session.save(update_fields=["twilio_call_sid", "status", "updated_at"])
        _log_event(session, "twilio.call_create.ok", {"call_sid": call_sid})

    return JsonResponse(
        {
            "call_session_id": str(session.id),
            "status": session.status,
            "country": session.country,
            "twilio_call_sid": call_sid,
        }
    )


@csrf_exempt
@require_http_methods(["POST", "GET"])
def spike_twiml(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return _twiml_response(_twiml_decline())

    # Twilio hits this when the call connects.
    call_sid = str(request.POST.get("CallSid") or request.GET.get("CallSid") or "").strip()
    if call_sid and call_sid != session.twilio_call_sid:
        session.twilio_call_sid = call_sid
        session.save(update_fields=["twilio_call_sid", "updated_at"])
    session.status = CallStatus.IN_PROGRESS
    session.save(update_fields=["status", "updated_at"])
    _log_event(session, "twilio.twiml.requested", {"call_sid": call_sid})

    return _twiml_response(_twiml_gather_consent(session=session))


@csrf_exempt
@require_http_methods(["POST"])
def spike_consent(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return _twiml_response(_twiml_decline())

    digits = str(request.POST.get("Digits") or "").strip()
    _log_event(session, "twilio.consent.input", {"digits": digits})

    if digits == "1":
        session.consent_obtained = True
        session.consent_obtained_at = _now()
        session.consent_method = "dtmf"
        session.save(update_fields=["consent_obtained", "consent_obtained_at", "consent_method", "updated_at"])
        _log_event(session, "consent.granted", {"method": "dtmf"})
        return _twiml_response(_twiml_after_consent(session=session))

    session.status = CallStatus.CANCELLED
    session.save(update_fields=["status", "updated_at"])
    _log_event(session, "consent.denied", {"method": "dtmf"})
    return _twiml_response(_twiml_decline())


@csrf_exempt
@require_http_methods(["POST"])
def spike_status(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return HttpResponse(status=204)

    call_status = str(request.POST.get("CallStatus") or "").strip().lower()
    call_sid = str(request.POST.get("CallSid") or "").strip()
    if call_sid and call_sid != session.twilio_call_sid:
        session.twilio_call_sid = call_sid

    mapped = {
        "queued": CallStatus.QUEUED,
        "initiated": CallStatus.INITIATING,
        "ringing": CallStatus.RINGING,
        "in-progress": CallStatus.IN_PROGRESS,
        "completed": CallStatus.COMPLETED,
        "busy": CallStatus.FAILED,
        "failed": CallStatus.FAILED,
        "no-answer": CallStatus.FAILED,
        "canceled": CallStatus.CANCELLED,
        "cancelled": CallStatus.CANCELLED,
    }.get(call_status)
    if mapped:
        session.status = mapped
    session.save(update_fields=["twilio_call_sid", "status", "updated_at"])
    _log_event(session, "twilio.status", {"call_status": call_status, "call_sid": call_sid})
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def spike_recording_callback(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return HttpResponse(status=204)

    recording_sid = str(request.POST.get("RecordingSid") or "").strip()
    recording_url = str(request.POST.get("RecordingUrl") or "").strip()
    _log_event(session, "twilio.recording", {"recording_sid": recording_sid, "recording_url": recording_url})

    changed_fields: list[str] = ["updated_at"]
    if recording_sid and recording_sid != session.recording_sid:
        session.recording_sid = recording_sid
        changed_fields.append("recording_sid")
    if recording_url and recording_url != session.recording_url:
        session.recording_url = recording_url
        changed_fields.append("recording_url")
    if len(changed_fields) > 1:
        session.save(update_fields=changed_fields)
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def spike_hangup(request: HttpRequest, session_id: uuid.UUID) -> JsonResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return JsonResponse({"error": "not_found"}, status=404)

    account_sid = _twilio_account_sid()
    auth_token = _twilio_auth_token()
    if not (account_sid and auth_token and session.twilio_call_sid):
        return JsonResponse({"error": "missing_twilio_config_or_call_sid"}, status=400)

    try:
        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{session.twilio_call_sid}.json",
            auth=(account_sid, auth_token),
            data={"Status": "completed"},
            timeout=20,
        )
    except Exception as exc:
        _log_event(session, "twilio.hangup.failed", {"error": str(exc)})
        return JsonResponse({"error": "twilio_request_failed", "detail": str(exc)}, status=502)

    if resp.status_code >= 400:
        _log_event(session, "twilio.hangup.failed", {"status": resp.status_code, "body": resp.text[:500]})
        return JsonResponse({"error": "twilio_error", "status": resp.status_code, "detail": resp.text[:500]}, status=502)

    session.status = CallStatus.CANCELLED
    session.save(update_fields=["status", "updated_at"])
    _log_event(session, "twilio.hangup.ok", {})
    return JsonResponse({"ok": True, "status": session.status})
