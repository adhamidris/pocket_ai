from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timezone

from django.http import HttpRequest, HttpResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.voice.models import CallSession, CallStatus
from apps.voice.twilio import load_twilio_config, validate_twilio_request


logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _twiml_response(xml: str) -> HttpResponse:
    return HttpResponse(xml, content_type="text/xml; charset=utf-8", status=200)


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _ws_base_url() -> str:
    """
    Public WSS base URL for the production Media Streams WebSocket server.

    Example: wss://<domain-or-ngrok>
    """

    return (os.getenv("VOICE_WS_BASE_URL") or "").strip().rstrip("/")


def _require_valid_signature(request: HttpRequest) -> bool:
    enabled = (os.getenv("TWILIO_VALIDATE_SIGNATURES") or "true").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return True
    try:
        cfg = load_twilio_config(require_from_number=False)
    except Exception:
        return False
    return validate_twilio_request(request, auth_token=cfg.auth_token, webhook_base_url=cfg.webhook_base_url)


def _twiml_decline() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Say>Goodbye.</Say>"
        "<Hangup/>"
        "</Response>"
    )


def _say(*, text: str, language: str | None = None, voice: str | None = None) -> str:
    attrs: list[str] = []
    if language:
        attrs.append(f'language="{_xml_escape(language)}"')
    if voice:
        attrs.append(f'voice="{_xml_escape(voice)}"')
    attr_text = (" " + " ".join(attrs)) if attrs else ""
    return f"<Say{attr_text}>{_xml_escape(text)}</Say>"


def _twilio_language_tag(*, session: CallSession) -> str | None:
    lang = str(session.language or "").strip().lower()
    if lang != "ar":
        return None

    country = str(session.country or "").strip().upper()
    mapping = {
        "EG": "ar-EG",
        "AE": "ar-AE",
        "SA": "ar-SA",
        "QA": "ar-QA",
        "KW": "ar-KW",
        "JO": "ar-JO",
    }
    return mapping.get(country, "ar-SA")


def _twilio_voice_name(*, session: CallSession) -> str | None:
    lang = str(session.language or "").strip().lower()
    if lang == "ar":
        return (os.getenv("VOICE_TWILIO_VOICE_AR") or "").strip() or "Polly.Zeina"
    return (os.getenv("VOICE_TWILIO_VOICE_EN") or "").strip() or None


def _twiml_gather_consent(*, session: CallSession, cfg) -> str:
    consent_url = f"{cfg.webhook_base_url}/voice/twilio/consent/{session.id}/"

    lang_tag = _twilio_language_tag(session=session)
    voice_name = _twilio_voice_name(session=session)

    default_disclosure_en = "Hello. This is an AI assistant calling."
    default_disclosure_ar = "مرحباً. أنا مساعد ذكاء اصطناعي أتصل بك."

    lang = str(session.language or "").strip().lower()
    disclosure = default_disclosure_ar if lang == "ar" else default_disclosure_en
    disclosure_env_key = "VOICE_AI_DISCLOSURE_DEFAULT_AR" if lang == "ar" else "VOICE_AI_DISCLOSURE_DEFAULT"
    disclosure = (os.getenv(disclosure_env_key) or "").strip() or disclosure

    if lang == "ar":
        notice = (
            f"{disclosure} "
            "سيتم تسجيل هذه المكالمة. "
            "اضغط 1 للموافقة والمتابعة. "
            "إذا لم توافق، يرجى إنهاء المكالمة."
        )
        prompt = "اضغط 1 للمتابعة."
        no_consent = "لم يتم استلام الموافقة. مع السلامة."
    else:
        notice = (
            f"{disclosure} "
            "This call will be recorded. "
            "Press 1 to consent and continue. "
            "If you do not consent, please hang up."
        )
        prompt = "Press 1 to continue."
        no_consent = "No consent received. Goodbye."

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{_say(text=notice, language=lang_tag, voice=voice_name)}"
        f'<Gather numDigits="1" action="{_xml_escape(consent_url)}" method="POST" timeout="8">'
        f"{_say(text=prompt, language=lang_tag, voice=voice_name)}"
        "</Gather>"
        f"{_say(text=no_consent, language=lang_tag, voice=voice_name)}"
        "<Hangup/>"
        "</Response>"
    )


def _twiml_after_consent(*, session: CallSession, cfg) -> str:
    ws_base = _ws_base_url()
    if not ws_base:
        return _twiml_decline()

    token = session.stream_token
    if not token:
        token = secrets.token_urlsafe(32)
        session.stream_token = token
        session.save(update_fields=["stream_token", "updated_at"])

    stream_url = f"{ws_base}/voice/stream/{session.id}?token={token}"
    recording_cb = f"{cfg.webhook_base_url}/voice/twilio/recording/{session.id}/"

    lang = str(session.language or "").strip().lower()
    lang_tag = _twilio_language_tag(session=session)
    voice_name = _twilio_voice_name(session=session)

    thanks = "Thank you. Please hold."
    if lang == "ar":
        thanks = "شكراً. الرجاء الانتظار."

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"{_say(text=thanks, language=lang_tag, voice=voice_name)}"
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


@csrf_exempt
@require_http_methods(["POST", "GET"])
def twilio_twiml(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    if not _require_valid_signature(request):
        return HttpResponse(status=403)

    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return _twiml_response(_twiml_decline())

    try:
        cfg = load_twilio_config(require_from_number=False)
    except Exception:
        return _twiml_response(_twiml_decline())

    call_sid = str(request.POST.get("CallSid") or request.GET.get("CallSid") or "").strip()
    if call_sid and call_sid != session.twilio_call_sid:
        session.twilio_call_sid = call_sid

    session.status = CallStatus.IN_PROGRESS
    session.save(update_fields=["twilio_call_sid", "status", "updated_at"])

    return _twiml_response(_twiml_gather_consent(session=session, cfg=cfg))


@csrf_exempt
@require_http_methods(["POST"])
def twilio_consent(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    if not _require_valid_signature(request):
        return HttpResponse(status=403)

    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return _twiml_response(_twiml_decline())

    digits = str(request.POST.get("Digits") or "").strip()
    if digits == "1":
        try:
            cfg = load_twilio_config(require_from_number=False)
        except Exception:
            return _twiml_response(_twiml_decline())

        session.consent_obtained = True
        session.consent_obtained_at = _now()
        session.consent_method = "dtmf"
        session.save(update_fields=["consent_obtained", "consent_obtained_at", "consent_method", "updated_at"])
        return _twiml_response(_twiml_after_consent(session=session, cfg=cfg))

    session.status = CallStatus.CANCELLED
    session.save(update_fields=["status", "updated_at"])
    return _twiml_response(_twiml_decline())


@csrf_exempt
@require_http_methods(["POST"])
def twilio_status(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    if not _require_valid_signature(request):
        return HttpResponse(status=403)

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
        "answered": CallStatus.IN_PROGRESS,
        "completed": CallStatus.COMPLETED,
        "busy": CallStatus.FAILED,
        "failed": CallStatus.FAILED,
        "no-answer": CallStatus.FAILED,
        "canceled": CallStatus.CANCELLED,
        "cancelled": CallStatus.CANCELLED,
    }.get(call_status)
    if mapped:
        session.status = mapped

    if session.status in {CallStatus.COMPLETED, CallStatus.CANCELLED, CallStatus.FAILED} and not session.ended_at:
        session.ended_at = dj_timezone.now()

    session.save(update_fields=["twilio_call_sid", "status", "ended_at", "updated_at"])
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def twilio_recording_callback(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    if not _require_valid_signature(request):
        return HttpResponse(status=403)

    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return HttpResponse(status=204)

    recording_sid = str(request.POST.get("RecordingSid") or "").strip()
    recording_url = str(request.POST.get("RecordingUrl") or "").strip()

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
