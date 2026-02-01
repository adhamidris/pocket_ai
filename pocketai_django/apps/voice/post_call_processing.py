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

from apps.conversations.models import AgentRun, AgentRunEventStream, AgentRunEventType, AgentRunStatus
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
from apps.voice.agent_run_bridge import append_agent_run_event, get_agent_run_id_from_call_session_metadata
from apps.voice.call_insights import CALL_INSIGHTS_SCHEMA_VERSION, format_call_insights_message, generate_call_insights
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


def write_call_insights_message(*, session: CallSession, conversation: Conversation, insights: dict[str, object]) -> bool:
    body = format_call_insights_message(insights)
    if not body:
        return False
    ConversationMessage.objects.create(
        conversation_id=conversation.id,
        sender=ConversationSender.AI,
        body=body,
        metadata={
            "source": "voice_call",
            "type": "call_insights",
            "schema_version": CALL_INSIGHTS_SCHEMA_VERSION,
            "call_session_id": str(session.id),
            "outcome": (insights.get("outcome") or {}).get("label") if isinstance(insights, dict) else "",
        },
        sent_at=timezone.now(),
    )
    Conversation.objects.filter(id=conversation.id).update(last_activity_at=timezone.now())
    return True


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
        insights_payload: dict[str, object] = {}

        with transaction.atomic():
            conversation = ensure_execution_conversation(session)
            messages_written = write_transcript_messages(session=session, conversation=conversation)

        if not session.summary:
            summary, actions = generate_summary_and_actions(session)
            if summary:
                session.summary = summary
                session.action_items = actions
                summary_written = True

        try:
            insights_payload = generate_call_insights(session)
            session.insights = insights_payload
        except Exception:  # pragma: no cover - best effort only
            logger.exception("voice.post_call_insights_failed session=%s", session.id)

        session.cost_total_usd = compute_cost_total_usd(session)
        with transaction.atomic():
            session.save(update_fields=["summary", "action_items", "insights", "cost_total_usd", "updated_at"])
            try:
                if insights_payload:
                    write_call_insights_message(session=session, conversation=conversation, insights=insights_payload)
            except Exception:  # pragma: no cover - best effort only
                logger.exception("voice.post_call_insights_message_failed session=%s", session.id)

        try:
            uploaded, bucket, key, etag = maybe_upload_recording_to_r2(session)
            recording_uploaded = uploaded
            if uploaded:
                logger.info("voice.recording_uploaded session=%s bucket=%s key=%s etag=%s", session.id, bucket, key, etag)
        except Exception as exc:
            logger.exception("voice.recording_upload_failed session=%s error=%s", session.id, exc)
            session.post_processing_error = f"recording_upload_failed:{exc}"
            session.save(update_fields=["post_processing_error", "updated_at"])

        post_summary_to_initiator = True
        # Bridge the completed call into the Tasks panel (AgentRun) when the call
        # was initiated via a sub-agent task.
        try:
            from apps.voice.models import CallStatus

            run_id = get_agent_run_id_from_call_session_metadata(session)
            if run_id and session.business_profile_id:
                run = AgentRun.objects.filter(id=run_id).only("metadata", "status").first()
                meta = run.metadata if run and isinstance(getattr(run, "metadata", None), dict) else {}
                is_voice_run = str(meta.get("kind") or "").strip().lower() == "voice_call"
                voice_session_id = str(meta.get("voice_call_session_id") or "").strip()
                if is_voice_run:
                    post_summary_to_initiator = True
                    if not voice_session_id or voice_session_id == str(session.id):
                        terminal_status = None
                        if session.status == CallStatus.COMPLETED:
                            terminal_status = AgentRunStatus.COMPLETED
                        elif session.status == CallStatus.CANCELLED:
                            terminal_status = AgentRunStatus.CANCELLED
                        elif session.status == CallStatus.FAILED:
                            terminal_status = AgentRunStatus.FAILED

                        update_fields: dict[str, object] = {
                            "execution_conversation_id": conversation.id,
                        }
                        if terminal_status:
                            update_fields["status"] = terminal_status
                            update_fields["finished_at"] = timezone.now()
                            if terminal_status == AgentRunStatus.FAILED:
                                update_fields["error_detail"] = (session.post_processing_error or session.last_error or "")[
                                    :2000
                                ]
                        if session.summary:
                            update_fields["result"] = {"response_text": str(session.summary)[:6000]}

                        append_agent_run_event(
                            run_id=run_id,
                            business_id=session.business_profile_id,
                            stream=AgentRunEventStream.EXECUTED,
                            event_type=AgentRunEventType.RESULT if session.summary else AgentRunEventType.PROGRESS,
                            label="Call summary ready" if session.summary else "Call transcript saved",
                            payload={
                                "call_session_id": str(session.id),
                                "status": session.status,
                                "transcript_messages_written": int(messages_written),
                                "recording_uploaded": bool(recording_uploaded),
                            },
                            update_run_fields=update_fields,
                        )
                elif run and run.status in {AgentRunStatus.WAITING_EXTERNAL, AgentRunStatus.PAUSED}:
                    post_summary_to_initiator = False
                    now = timezone.now()
                    next_meta = dict(meta) if isinstance(meta, dict) else {}
                    inputs = next_meta.get("external_inputs")
                    if not isinstance(inputs, list):
                        inputs = []
                    already = any(
                        isinstance(item, dict)
                        and str(item.get("type") or "").strip() == "phone_call"
                        and str(item.get("id") or "").strip() == str(session.id)
                        for item in inputs
                    )
                    if not already:
                        resolution_text = (session.summary or "").strip()
                        if not resolution_text:
                            resolution_text = f"Call completed. Status: {session.status}."
                        inputs.append(
                            {
                                "type": "phone_call",
                                "id": str(session.id),
                                "subject": "Phone call summary",
                                "resolution": resolution_text[:4000],
                                "at": now.isoformat(),
                            }
                        )
                    next_meta["external_inputs"] = inputs[-10:]
                    next_meta.pop("pending_call_session_id", None)
                    append_agent_run_event(
                        run_id=run_id,
                        business_id=session.business_profile_id,
                        stream=AgentRunEventStream.EXECUTED,
                        event_type=AgentRunEventType.PROGRESS,
                        label="Call summary ready",
                        payload={
                            "call_session_id": str(session.id),
                            "status": session.status,
                            "transcript_messages_written": int(messages_written),
                            "recording_uploaded": bool(recording_uploaded),
                        },
                        update_run_fields={
                            "status": AgentRunStatus.QUEUED,
                            "run_after": now,
                            "lease_expires_at": None,
                            "error_detail": "",
                            "metadata": next_meta,
                        },
                    )
        except Exception:  # pragma: no cover - best effort only
            logger.exception("voice.post_call_agent_run_bridge_failed session=%s", session.id)

        if post_summary_to_initiator and session.summary and session.initiating_conversation_id:
            try:
                summary_text = str(session.summary).strip()
                if summary_text:
                    exists = ConversationMessage.objects.filter(
                        conversation_id=session.initiating_conversation_id,
                        metadata__type="call_summary",
                        metadata__call_session_id=str(session.id),
                    ).exists()
                    if not exists:
                        body = f"📞 Call completed.\n\nSummary:\n{summary_text}"
                        ConversationMessage.objects.create(
                            conversation_id=session.initiating_conversation_id,
                            sender=ConversationSender.AI,
                            body=body,
                            metadata={
                                "source": "voice_call",
                                "type": "call_summary",
                                "call_session_id": str(session.id),
                            },
                        )
                        Conversation.objects.filter(id=session.initiating_conversation_id).update(
                            last_activity_at=timezone.now()
                        )
            except Exception:  # pragma: no cover - best effort only
                logger.exception("voice.post_call_summary_message_failed session=%s", session.id)

        return PostCallProcessingResult(
            transcript_messages_written=messages_written,
            summary_written=summary_written,
            recording_uploaded=recording_uploaded,
        )
