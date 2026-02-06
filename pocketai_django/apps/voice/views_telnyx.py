from __future__ import annotations

import hmac
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone

from django.http import HttpRequest, HttpResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.conversations.models import AgentRunEventType
from apps.voice.models import CallSession, CallStatus, VoiceConfiguration
from apps.voice.provider_credentials import VOICE_PROVIDER_TELNYX, resolve_telnyx_config
from apps.voice.views_twilio import (
    _emit_agent_run_event,
    _run_update_for_terminal_call,
    _say,
    _twilio_language_tag,
    _twiml_decline,
    _twiml_response,
    _xml_escape,
)


logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ws_base_url() -> str:
    return (os.getenv("VOICE_WS_BASE_URL") or "").strip().rstrip("/")


def _require_valid_signature(_request: HttpRequest, *, business_id: object | None) -> bool:
    """
    Telnyx webhook verification for TeXML callbacks.

    Default is disabled to keep local setup simple. When enabled, a shared token
    check can be used as a pragmatic guard.
    """

    enabled = (os.getenv("TELNYX_VALIDATE_SIGNATURES") or "false").strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return True
    expected = (os.getenv("TELNYX_WEBHOOK_TOKEN") or "").strip()
    if not expected:
        return False
    received = (
        _request.headers.get("X-Telnyx-Webhook-Token")
        or _request.POST.get("WebhookToken")
        or _request.GET.get("WebhookToken")
        or ""
    ).strip()
    if not received:
        return False
    return hmac.compare_digest(received, expected)


def _telnyx_gather_consent(*, session: CallSession, cfg) -> str:
    consent_url = f"{cfg.webhook_base_url}/voice/telnyx/consent/{session.id}/"

    lang_tag = _twilio_language_tag(session=session)
    voice_name = None

    default_disclosure_en = "Hello. This is an AI assistant calling."
    default_disclosure_ar = "مرحباً. أنا مساعد ذكاء اصطناعي أتصل بك."

    lang = str(session.language or "").strip().lower()
    disclosure = default_disclosure_ar if lang == "ar" else default_disclosure_en

    disclosure_override = ""
    if session.business_profile_id:
        voice_cfg = VoiceConfiguration.objects.filter(business_profile_id=session.business_profile_id).first()
        if voice_cfg:
            if lang == "ar":
                disclosure_override = str(voice_cfg.ai_disclosure_template_ar or "").strip() or str(
                    voice_cfg.ai_disclosure_template or ""
                ).strip()
            else:
                disclosure_override = str(voice_cfg.ai_disclosure_template or "").strip()
    if disclosure_override:
        disclosure = disclosure_override

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


def _telnyx_after_consent(*, session: CallSession, cfg) -> str:
    ws_base = _ws_base_url()
    if not ws_base:
        return _twiml_decline()

    token = session.stream_token
    if not token:
        token = secrets.token_urlsafe(32)
        session.stream_token = token
        session.save(update_fields=["stream_token", "updated_at"])

    stream_url = f"{ws_base}/voice/stream/{session.id}/{token}"
    recording_cb = f"{cfg.webhook_base_url}/voice/telnyx/recording/{session.id}/"

    lang = str(session.language or "").strip().lower()
    lang_tag = _twilio_language_tag(session=session)
    voice_name = None
    thanks = "Thank you. Please hold." if lang != "ar" else "شكراً. الرجاء الانتظار."

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


def _extract_telnyx_call_sid(request: HttpRequest) -> str:
    return str(
        request.POST.get("CallSid")
        or request.POST.get("call_sid")
        or request.POST.get("CallControlId")
        or request.POST.get("call_control_id")
        or request.POST.get("CallSessionId")
        or request.POST.get("call_session_id")
        or request.GET.get("CallSid")
        or request.GET.get("call_sid")
        or request.GET.get("CallControlId")
        or request.GET.get("call_control_id")
        or request.GET.get("CallSessionId")
        or request.GET.get("call_session_id")
        or ""
    ).strip()


@csrf_exempt
@require_http_methods(["POST", "GET"])
def telnyx_twiml(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return _twiml_response(_twiml_decline())
    if not _require_valid_signature(request, business_id=session.business_profile_id):
        return HttpResponse(status=403)

    try:
        cfg = resolve_telnyx_config(
            business_id=session.business_profile_id,
            require_from_number=False,
        )
    except Exception:
        return _twiml_response(_twiml_decline())

    call_sid = _extract_telnyx_call_sid(request)
    update_fields = ["status", "transport_provider", "updated_at"]
    if call_sid and call_sid != str(session.provider_call_sid or ""):
        session.provider_call_sid = call_sid
        update_fields.append("provider_call_sid")
    session.transport_provider = VOICE_PROVIDER_TELNYX
    session.status = CallStatus.IN_PROGRESS
    session.save(update_fields=update_fields)

    _emit_agent_run_event(
        session,
        label="Call connected",
        payload={"status": session.status, "call_sid": call_sid, "transport_provider": VOICE_PROVIDER_TELNYX},
    )

    return _twiml_response(_telnyx_gather_consent(session=session, cfg=cfg))


@csrf_exempt
@require_http_methods(["POST"])
def telnyx_consent(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return _twiml_response(_twiml_decline())
    if not _require_valid_signature(request, business_id=session.business_profile_id):
        return HttpResponse(status=403)

    digits = str(request.POST.get("Digits") or "").strip()
    if digits == "1":
        try:
            cfg = resolve_telnyx_config(
                business_id=session.business_profile_id,
                require_from_number=False,
            )
        except Exception:
            return _twiml_response(_twiml_decline())

        session.consent_obtained = True
        session.consent_obtained_at = _now()
        session.consent_method = "dtmf"
        session.save(update_fields=["consent_obtained", "consent_obtained_at", "consent_method", "updated_at"])
        _emit_agent_run_event(session, label="Consent obtained", payload={"consent": True})
        return _twiml_response(_telnyx_after_consent(session=session, cfg=cfg))

    session.status = CallStatus.CANCELLED
    session.save(update_fields=["status", "updated_at"])
    _emit_agent_run_event(
        session,
        label="Consent denied",
        payload={"consent": False},
        event_type=AgentRunEventType.CANCELLED,
        update_run_fields=_run_update_for_terminal_call(session),
    )
    return _twiml_response(_twiml_decline())


@csrf_exempt
@require_http_methods(["POST"])
def telnyx_status(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return HttpResponse(status=204)
    if not _require_valid_signature(request, business_id=session.business_profile_id):
        return HttpResponse(status=403)

    call_status = str(
        request.POST.get("CallStatus")
        or request.POST.get("call_status")
        or request.POST.get("CallControlState")
        or request.POST.get("call_control_state")
        or ""
    ).strip().lower()
    call_sid = _extract_telnyx_call_sid(request)

    mapped = {
        "queued": CallStatus.QUEUED,
        "initiated": CallStatus.INITIATING,
        "ringing": CallStatus.RINGING,
        "in-progress": CallStatus.IN_PROGRESS,
        "answered": CallStatus.IN_PROGRESS,
        "bridging": CallStatus.IN_PROGRESS,
        "bridged": CallStatus.IN_PROGRESS,
        "completed": CallStatus.COMPLETED,
        "ended": CallStatus.COMPLETED,
        "hangup": CallStatus.COMPLETED,
        "busy": CallStatus.FAILED,
        "failed": CallStatus.FAILED,
        "no-answer": CallStatus.FAILED,
        "canceled": CallStatus.CANCELLED,
        "cancelled": CallStatus.CANCELLED,
    }.get(call_status)

    update_fields = ["transport_provider", "status", "ended_at", "updated_at"]
    session.transport_provider = VOICE_PROVIDER_TELNYX
    if call_sid and call_sid != str(session.provider_call_sid or ""):
        session.provider_call_sid = call_sid
        update_fields.append("provider_call_sid")
    if mapped:
        session.status = mapped
    if session.status in {CallStatus.COMPLETED, CallStatus.CANCELLED, CallStatus.FAILED} and not session.ended_at:
        session.ended_at = dj_timezone.now()
    session.save(update_fields=update_fields)

    label = ""
    evt_type = AgentRunEventType.PROGRESS
    update_run_fields = None
    if session.status == CallStatus.RINGING:
        label = "Ringing…"
    elif session.status == CallStatus.IN_PROGRESS:
        label = "In progress"
    elif session.status == CallStatus.COMPLETED:
        label = "Call completed"
        evt_type = AgentRunEventType.RESULT
        update_run_fields = _run_update_for_terminal_call(session)
    elif session.status == CallStatus.CANCELLED:
        label = "Call cancelled"
        evt_type = AgentRunEventType.CANCELLED
        update_run_fields = _run_update_for_terminal_call(session)
    elif session.status == CallStatus.FAILED:
        label = "Call failed"
        evt_type = AgentRunEventType.ERROR
        update_run_fields = _run_update_for_terminal_call(session)

    if label:
        _emit_agent_run_event(
            session,
            label=label,
            payload={"status": session.status, "telnyx_status": call_status},
            event_type=evt_type,
            update_run_fields=update_run_fields,
        )
    return HttpResponse(status=204)


@csrf_exempt
@require_http_methods(["POST"])
def telnyx_recording_callback(request: HttpRequest, session_id: uuid.UUID) -> HttpResponse:
    session = CallSession.objects.filter(id=session_id).first()
    if not session:
        return HttpResponse(status=204)
    if not _require_valid_signature(request, business_id=session.business_profile_id):
        return HttpResponse(status=403)

    recording_sid = str(
        request.POST.get("RecordingSid")
        or request.POST.get("recording_sid")
        or request.POST.get("RecordingId")
        or request.POST.get("recording_id")
        or ""
    ).strip()
    recording_url = str(
        request.POST.get("RecordingUrl")
        or request.POST.get("recording_url")
        or request.POST.get("RecordingUrlMp3")
        or request.POST.get("recording_url_mp3")
        or ""
    ).strip()

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
