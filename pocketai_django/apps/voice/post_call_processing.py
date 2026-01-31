from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.tenancy import tenant_context

from apps.conversations.models import (
    Conversation,
    ConversationChannel,
    ConversationMessage,
    ConversationSender,
    ConversationStatus,
)
from apps.llm.ai_prompt_builder import PromptBundle
from apps.llm.llm_provider import load_default_provider
from apps.voice.models import CallEvent, CallSession
from apps.voice.r2_storage import build_r2_client, load_r2_config
from apps.voice.twilio import load_twilio_config


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PostCallProcessingResult:
    transcript_messages_written: int
    summary_written: bool
    recording_uploaded: bool


def _decimal(value: object, default: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _safe_text(value: object) -> str:
    text = str(value or "").strip()
    return text


def _iter_transcript_events(session: CallSession) -> list[CallEvent]:
    return list(
        session.events.filter(event_type__in=["stt.final", "llm.response.final"]).order_by("created_at", "id")
    )


def ensure_execution_conversation(session: CallSession) -> Conversation:
    if session.execution_conversation_id:
        conv = Conversation.objects.filter(id=session.execution_conversation_id).first()
        if conv:
            return conv

    conv = Conversation.objects.create(
        business_profile_id=session.business_profile_id,
        agent_profile_id=session.agent_profile_id,
        channel=ConversationChannel.OTHER,
        status=ConversationStatus.CLOSED,
        metadata={
            "source": "voice_call",
            "call_session_id": str(session.id),
            "to_phone_number": session.to_phone_number,
            "from_phone_number": session.from_phone_number,
        },
    )
    session.execution_conversation_id = conv.id
    session.save(update_fields=["execution_conversation", "updated_at"])
    return conv


def write_transcript_messages(*, session: CallSession, conversation: Conversation) -> int:
    events = _iter_transcript_events(session)
    ConversationMessage.objects.filter(conversation_id=conversation.id).delete()

    messages: list[ConversationMessage] = []
    for ev in events:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        text = _safe_text(payload.get("text"))
        if not text:
            continue
        sender = ConversationSender.CUSTOMER if ev.event_type == "stt.final" else ConversationSender.AI
        sent_at = ev.created_at or timezone.now()
        messages.append(
            ConversationMessage(
                conversation_id=conversation.id,
                sender=sender,
                body=text,
                metadata={
                    "source": "voice_call",
                    "call_session_id": str(session.id),
                    "event_id": int(ev.id),
                    "event_type": ev.event_type,
                },
                sent_at=sent_at,
            )
        )

    if messages:
        ConversationMessage.objects.bulk_create(messages, batch_size=200)
    return len(messages)


def generate_summary_and_actions(session: CallSession) -> tuple[str, list[dict[str, object]]]:
    provider = load_default_provider()
    if not provider:
        return "", []

    events = _iter_transcript_events(session)
    lines: list[str] = []
    for ev in events[-200:]:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        text = _safe_text(payload.get("text"))
        if not text:
            continue
        speaker = "Customer" if ev.event_type == "stt.final" else "Agent"
        lines.append(f"{speaker}: {text}")

    transcript_text = "\n".join(lines).strip()
    if not transcript_text:
        return "", []

    system_prompt = (
        "You summarize phone calls for a business workspace.\n"
        "Be factual and concise. Do not hallucinate.\n"
        "Write the summary in the call's language (Arabic/English) when possible.\n"
        "Return JSON with keys: response_text (summary string), actions (array), extractions (empty array).\n"
        "If the customer asked for a follow-up, include a follow_up action in actions.\n"
    )
    user_prompt = (
        f"Call objective: {session.objective}\n"
        f"Country: {session.country}\n"
        f"Language: {session.language}\n"
        f"Call status: {session.status}\n\n"
        "Transcript:\n"
        f"{transcript_text}\n"
    )
    bundle = PromptBundle(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        transcript=[],
        knowledge_snippets=[],
        actions_catalog=[],
        agent_traits={},
    )
    try:
        result = provider.generate(bundle)
    except Exception as exc:
        logger.exception("voice.post_call_summary_failed session=%s error=%s", session.id, exc)
        return "", []

    summary = ""
    actions: list[dict[str, object]] = []
    if isinstance(result, dict):
        summary = _safe_text(result.get("response_text"))
        raw_actions = result.get("actions")
        if isinstance(raw_actions, list):
            actions = [item for item in raw_actions if isinstance(item, dict)]
    return summary, actions


def maybe_upload_recording_to_r2(session: CallSession) -> tuple[bool, str, str, str]:
    """
    Returns (uploaded, bucket, key, etag).
    """

    if not session.recording_url or session.recording_r2_key:
        return False, "", "", ""

    r2_cfg = load_r2_config()
    if not r2_cfg:
        return False, "", "", ""

    twilio_cfg = load_twilio_config(require_from_number=False)

    url = str(session.recording_url).strip()
    if url and not url.endswith(".mp3") and not url.endswith(".wav"):
        url = url + ".mp3"

    key = f"voice/recordings/{session.business_profile_id}/{session.id}/{session.recording_sid or 'recording'}.mp3"
    client = build_r2_client(r2_cfg)

    resp = requests.get(url, auth=(twilio_cfg.account_sid, twilio_cfg.auth_token), stream=True, timeout=60)
    resp.raise_for_status()
    try:
        put = client.put_object(
            Bucket=r2_cfg.bucket,
            Key=key,
            Body=resp.raw,
            ContentType="audio/mpeg",
        )
        etag = str(put.get("ETag") or "").strip('"')
    finally:
        resp.close()

    session.recording_r2_bucket = r2_cfg.bucket
    session.recording_r2_key = key
    session.recording_r2_etag = etag
    session.save(update_fields=["recording_r2_bucket", "recording_r2_key", "recording_r2_etag", "updated_at"])

    return True, r2_cfg.bucket, key, etag


def compute_cost_total_usd(session: CallSession) -> Decimal:
    per_minute = _decimal(getattr(settings, "VOICE_COST_ESTIMATE_USD_PER_MINUTE", "0.12"), "0.12")
    if not session.started_at or not session.ended_at:
        return Decimal("0.0000")
    seconds = max(0.0, (session.ended_at - session.started_at).total_seconds())
    cost = (Decimal(str(seconds)) / Decimal("60")) * per_minute
    return cost.quantize(Decimal("0.0001"))


def process_post_call(session: CallSession) -> PostCallProcessingResult:
    if not session.business_profile_id:
        raise RuntimeError("missing_business_profile")

    with tenant_context(session.business_profile_id):
        session = CallSession.objects.select_related("agent_profile").get(id=session.id)

        recording_uploaded = False
        summary_written = False

        with transaction.atomic():
            conversation = ensure_execution_conversation(session)
            messages_written = write_transcript_messages(session=session, conversation=conversation)

            if not session.summary:
                summary, actions = generate_summary_and_actions(session)
                if summary:
                    session.summary = summary
                    session.action_items = actions
                    summary_written = True

            session.cost_total_usd = compute_cost_total_usd(session)
            session.save(update_fields=["summary", "action_items", "cost_total_usd", "updated_at"])

        try:
            uploaded, bucket, key, etag = maybe_upload_recording_to_r2(session)
            recording_uploaded = uploaded
            if uploaded:
                logger.info("voice.recording_uploaded session=%s bucket=%s key=%s etag=%s", session.id, bucket, key, etag)
        except Exception as exc:
            logger.exception("voice.recording_upload_failed session=%s error=%s", session.id, exc)
            session.post_processing_error = f"recording_upload_failed:{exc}"
            session.save(update_fields=["post_processing_error", "updated_at"])

        return PostCallProcessingResult(
            transcript_messages_written=messages_written,
            summary_written=summary_written,
            recording_uploaded=recording_uploaded,
        )
