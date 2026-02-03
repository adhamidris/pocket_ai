from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Mapping

import logging

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.tenancy import tenant_context

from apps.conversations.models import Conversation
from apps.conversations.models import ConversationMessage, ConversationSender
from apps.conversations.models import (
    AgentRun,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
    AgentRunVisibility,
)
from apps.mcp.types import ToolExecutionContext
from apps.voice.agent_run_bridge import (
    append_agent_run_event,
    get_actor_user_id_from_conversation_metadata,
    get_agent_run_id_from_conversation_metadata,
    resolve_agent_run,
)
from apps.voice.models import (
    CallEvent,
    CallSession,
    CallStatus,
    CallType,
    VoiceConfiguration,
    VoicePhoneNumber,
    VoiceSuppressionEntry,
    VoiceTrustTier,
)
from apps.voice.policy_engine import audit_policy_decision, evaluate_voice_compliance_policy
from apps.voice.phone_utils import detect_country_iso2, is_valid_e164
from apps.voice.twilio import load_twilio_config


logger = logging.getLogger(__name__)


def _decimal(value: object, default: str) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _month_start(dt) -> object:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _effective_owner_cap(value: int, owner_cap: int) -> int:
    try:
        value_int = int(value)
    except Exception:
        value_int = 0
    try:
        cap_int = int(owner_cap)
    except Exception:
        cap_int = 0
    if cap_int > 0:
        if value_int <= 0:
            return cap_int
        return min(value_int, cap_int)
    return value_int


def _cleanup_stale_active_calls(*, business_id: object) -> int:
    now = timezone.now()
    pre_stream_timeout = int(getattr(settings, "VOICE_CALL_PRE_STREAM_TIMEOUT_SECONDS", 120) or 120)
    stale_grace = int(getattr(settings, "VOICE_CALL_STALE_GRACE_SECONDS", 120) or 120)
    active_statuses = {CallStatus.INITIATING, CallStatus.RINGING, CallStatus.IN_PROGRESS}

    stale_ids: list[uuid.UUID] = []
    sessions = CallSession.objects.filter(business_profile_id=business_id, status__in=active_statuses).only(
        "id",
        "status",
        "updated_at",
        "started_at",
        "queued_at",
        "max_duration_seconds",
        "twilio_stream_sid",
        "consent_obtained",
    )
    for session in sessions:
        base_time = session.started_at or session.updated_at or session.queued_at or now
        if not session.twilio_stream_sid:
            if session.updated_at and session.updated_at < now - timedelta(seconds=pre_stream_timeout):
                stale_ids.append(session.id)
                continue
        max_duration = int(session.max_duration_seconds or 0) or 600
        if base_time + timedelta(seconds=max_duration + stale_grace) < now:
            stale_ids.append(session.id)

    if not stale_ids:
        return 0

    with tenant_context(business_id):
        for session in CallSession.objects.filter(id__in=stale_ids):
            session.status = CallStatus.FAILED
            session.last_error = "stale_call_timeout"
            session.ended_at = now
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "ended_at", "lease_expires_at", "updated_at"])
            CallEvent.objects.create(
                call_session=session,
                business_profile_id=business_id,
                event_type="call.auto_failed_stale",
                payload={"reason": "stale_call_timeout"},
            )
    logger.warning("voice.stale_calls_cleared business=%s count=%s", business_id, len(stale_ids))
    return len(stale_ids)


def _ensure_voice_config(business_id: object) -> VoiceConfiguration | None:
    config = VoiceConfiguration.objects.filter(business_profile_id=business_id).first()
    if config:
        return config

    auto_create = bool(getattr(settings, "VOICE_AUTO_CREATE_CONFIG", False))
    if not auto_create:
        return None

    owner_max_duration = int(getattr(settings, "VOICE_OWNER_MAX_CALL_DURATION_SECONDS", 0) or 0) or 600
    default_allowed = list(getattr(settings, "VOICE_DEFAULT_ALLOWED_COUNTRIES", []) or [])
    return VoiceConfiguration.objects.create(
        business_profile_id=business_id,
        trust_tier=VoiceTrustTier.TRIAL,
        service_calls_enabled=True,
        marketing_calls_enabled=False,
        allowed_countries=default_allowed,
        max_concurrent_calls=1,
        max_calls_per_day=10,
        max_call_duration_seconds=min(600, owner_max_duration),
        monthly_budget_usd=Decimal("50.00"),
        default_language="en",
        ai_disclosure_template="",
        ai_disclosure_template_ar="",
        recording_consent_required=True,
    )


def initiate_phone_call_tool(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    """
    MCP tool: initiate_phone_call

    Creates a queued CallSession. Execution is handled by `voice_call_worker`.
    """

    if not bool(getattr(settings, "VOICE_GLOBAL_ENABLED", False)):
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "voice_disabled",
            "error_code": "voice_disabled",
            "hint": "Voice calling is disabled by the SaaS owner.",
        }

    business_id = getattr(conversation, "business_profile_id", None)
    agent_id = getattr(conversation, "agent_profile_id", None)
    if not business_id or not agent_id:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "missing_workspace_context",
            "error_code": "missing_workspace_context",
            "hint": "This tool requires a conversation bound to a business + agent profile.",
        }

    phone_number = str(arguments.get("phone_number") or arguments.get("phoneNumber") or "").strip()
    objective = str(arguments.get("objective") or "").strip()
    call_type = str(arguments.get("call_type") or arguments.get("callType") or CallType.SERVICE).strip().lower()
    language = str(arguments.get("language") or "").strip().lower()
    max_duration_minutes_raw = arguments.get("max_duration_minutes") or arguments.get("maxDurationMinutes")
    try:
        max_duration_minutes = int(max_duration_minutes_raw) if max_duration_minutes_raw is not None else 10
    except Exception:
        max_duration_minutes = 10
    max_duration_minutes = max(1, min(60, max_duration_minutes))

    context_items = arguments.get("context_items") or arguments.get("contextItems") or []
    if not isinstance(context_items, list):
        context_items = []

    if not is_valid_e164(phone_number):
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "invalid_phone_number",
            "error_code": "invalid_phone_number",
            "hint": "Expected E.164 like +201234567890.",
        }
    if not objective:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "missing_objective",
            "error_code": "missing_objective",
            "hint": "Provide a short objective for the call.",
        }

    if call_type not in {CallType.SERVICE, CallType.MARKETING}:
        call_type = CallType.SERVICE

    country = detect_country_iso2(phone_number)
    if not country:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "unknown_country",
            "error_code": "unknown_country",
            "hint": "Could not infer country from E.164 number.",
        }

    ws_base = (os.getenv("VOICE_WS_BASE_URL") or "").strip()
    if not ws_base:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "missing_ws_base_url",
            "error_code": "missing_ws_base_url",
            "hint": "Set VOICE_WS_BASE_URL=wss://... (public WSS base).",
        }

    try:
        twilio_cfg = load_twilio_config(require_from_number=False)
    except Exception as exc:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "missing_twilio_config",
            "error_code": "missing_twilio_config",
            "hint": str(exc) or "Set TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/TWILIO_WEBHOOK_BASE_URL.",
        }

    config = _ensure_voice_config(business_id)
    if not config:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "voice_not_configured",
            "error_code": "voice_not_configured",
            "hint": "VoiceConfiguration is missing for this workspace.",
        }

    if call_type == CallType.MARKETING and not config.marketing_calls_enabled:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "marketing_calls_disabled",
            "error_code": "marketing_calls_disabled",
            "hint": "Marketing calls are disabled for this workspace.",
        }
    if call_type == CallType.SERVICE and not config.service_calls_enabled:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "service_calls_disabled",
            "error_code": "service_calls_disabled",
            "hint": "Service calls are disabled for this workspace.",
        }

    allowed_countries = config.allowed_countries if isinstance(config.allowed_countries, list) else []
    if not allowed_countries:
        allowed_countries = list(getattr(settings, "VOICE_DEFAULT_ALLOWED_COUNTRIES", []) or [])
    allowed_set = {str(code).upper()[:2] for code in allowed_countries if str(code or "").strip()}
    if allowed_set and country.upper() not in allowed_set:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "country_not_allowed",
            "error_code": "country_not_allowed",
            "hint": f"Country {country} is not enabled for voice calls.",
        }

    if VoiceSuppressionEntry.objects.filter(business_profile_id=business_id, phone_number=phone_number).exists():
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "suppressed_number",
            "error_code": "suppressed_number",
            "hint": "This number is suppressed (do-not-call).",
        }

    decision = evaluate_voice_compliance_policy(
        business_profile_id=business_id,
        agent_profile_id=agent_id,
        call_type=call_type,
        country=country,
    )
    audit_policy_decision(
        business_profile_id=business_id,
        call_session_id=None,
        actor_user_id=None,
        actor_agent_id=agent_id,
        decision=decision,
    )
    if not decision.allowed:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": decision.reason_code,
            "error_code": decision.reason_code,
            "hint": "Call blocked by compliance policy.",
        }

    _cleanup_stale_active_calls(business_id=business_id)

    owner_max_concurrent = int(getattr(settings, "VOICE_OWNER_MAX_CONCURRENT_CALLS", 0) or 0)
    effective_concurrent = _effective_owner_cap(int(config.max_concurrent_calls or 0), owner_max_concurrent)
    if effective_concurrent > 0:
        active_statuses = {CallStatus.INITIATING, CallStatus.RINGING, CallStatus.IN_PROGRESS}
        active_count = CallSession.objects.filter(business_profile_id=business_id, status__in=active_statuses).count()
        if active_count >= effective_concurrent:
            return {
                "tool": "initiate_phone_call",
                "status": "throttled",
                "error": "concurrency_limit",
                "error_code": "concurrency_limit",
                "hint": "Workspace is at its max concurrent calls. Try again shortly.",
            }

    owner_max_calls_per_day = int(getattr(settings, "VOICE_OWNER_MAX_CALLS_PER_DAY", 0) or 0)
    effective_calls_per_day = _effective_owner_cap(int(config.max_calls_per_day or 0), owner_max_calls_per_day)
    if effective_calls_per_day > 0:
        now = timezone.now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        calls_today = CallSession.objects.filter(business_profile_id=business_id, created_at__gte=day_start, created_at__lt=day_end).count()
        if calls_today >= effective_calls_per_day:
            return {
                "tool": "initiate_phone_call",
                "status": "throttled",
                "error": "daily_call_cap_exceeded",
                "error_code": "daily_call_cap_exceeded",
                "hint": "Workspace exceeded its daily call cap.",
            }

    owner_max_duration = int(getattr(settings, "VOICE_OWNER_MAX_CALL_DURATION_SECONDS", 0) or 0)
    requested_seconds = max_duration_minutes * 60
    effective_duration = int(config.max_call_duration_seconds or requested_seconds) or requested_seconds
    if owner_max_duration > 0:
        effective_duration = min(effective_duration, owner_max_duration)
    effective_duration = min(effective_duration, requested_seconds)
    effective_duration = max(30, effective_duration)

    per_minute = _decimal(getattr(settings, "VOICE_COST_ESTIMATE_USD_PER_MINUTE", "0.12"), "0.12")
    estimate = (Decimal(effective_duration) / Decimal(60)) * per_minute
    estimate = estimate.quantize(Decimal("0.0001"))

    budget = _decimal(config.monthly_budget_usd, "0.00")
    if budget > Decimal("0.00"):
        month_start = _month_start(timezone.now())
        spent = (
            CallSession.objects.filter(business_profile_id=business_id, created_at__gte=month_start).aggregate(total=Sum("cost_total_usd"))["total"]
            or Decimal("0.00")
        )
        if spent + estimate > budget:
            return {
                "tool": "initiate_phone_call",
                "status": "throttled",
                "error": "monthly_budget_exceeded",
                "error_code": "monthly_budget_exceeded",
                "hint": "Workspace exceeded its monthly voice budget.",
            }

    from_number = ""
    voice_number_id = None
    active_number = (
        VoicePhoneNumber.objects.filter(business_profile_id=business_id, status=VoicePhoneNumber.Status.ACTIVE)
        .order_by("-updated_at")
        .first()
    )
    if active_number:
        from_number = active_number.phone_number
        voice_number_id = active_number.id
    else:
        from_number = twilio_cfg.default_from_number

    if not from_number:
        return {
            "tool": "initiate_phone_call",
            "status": "error",
            "error": "missing_from_number",
            "error_code": "missing_from_number",
            "hint": "No active VoicePhoneNumber and TWILIO_FROM_NUMBER is not configured.",
        }

    if not language:
        language = str(config.default_language or "en").strip().lower()
    if language not in {"en", "ar"}:
        language = "en"

    actor_user_id = get_actor_user_id_from_conversation_metadata(conversation)

    existing_run_id = get_agent_run_id_from_conversation_metadata(conversation)
    convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    is_agent_run_conversation = bool(existing_run_id) or str(convo_meta.get("source") or "").strip().lower() == "agent_run"
    existing_run = None
    if existing_run_id:
        existing_run = resolve_agent_run(agent_run_id=existing_run_id, business_id=business_id)

    anchor_conversation_id = (
        getattr(existing_run, "conversation_id", None) or getattr(conversation, "id", None)
    )

    call_run: AgentRun | None = existing_run
    created_run = False

    with tenant_context(business_id):
        with transaction.atomic():
            if call_run is None:
                title_parts = []
                if phone_number:
                    title_parts.append(f"Call {phone_number}")
                if objective:
                    title_parts.append(objective.strip())
                title = " — ".join(title_parts).strip()[:200] or "Phone call"
                call_run = AgentRun.objects.create(
                    business_profile_id=business_id,
                    agent_profile_id=agent_id,
                    conversation_id=anchor_conversation_id,
                    created_by_id=actor_user_id,
                    title=title,
                    source=AgentRunSource.CHAT,
                    status=AgentRunStatus.WAITING_EXTERNAL,
                    visibility=AgentRunVisibility.INITIATOR,
                    plan={
                        "steps": [
                            {"title": "Dial the number"},
                            {"title": "AI disclosure + recording consent"},
                            {"title": "Conduct the call"},
                            {"title": "Save transcript + summary"},
                        ]
                    },
                    metadata={
                        "kind": "voice_call",
                        "to_phone_number": phone_number,
                        "objective": objective[:600],
                        "call_type": call_type,
                        "country": country,
                        "language": language,
                        "source": "mcp_tool",
                    },
                )
                created_run = True

            now = timezone.now()
            session = CallSession.objects.create(
                business_profile_id=business_id,
                agent_profile_id=agent_id,
                created_by_id=actor_user_id,
                initiating_conversation_id=anchor_conversation_id,
                objective=objective,
                call_type=call_type,
                language=language,
                country=country,
                to_phone_number=phone_number,
                from_phone_number=from_number,
                voice_phone_number_id=voice_number_id,
                status=CallStatus.QUEUED,
                queued_at=now,
                run_after=now,
                max_duration_seconds=effective_duration,
                cost_estimate_usd=estimate,
                metadata={
                    "source": "mcp_tool",
                    "conversation_id": str(anchor_conversation_id) if anchor_conversation_id else "",
                    "agent_profile_id": str(agent_id),
                    "agent_run_id": str(call_run.id) if call_run else "",
                },
                context_items=context_items,
            )
            CallEvent.objects.create(
                call_session=session,
                business_profile_id=business_id,
                event_type="call.queued",
                payload={"agent_run_id": str(call_run.id) if call_run else ""},
            )

    if call_run:
        payload = {
            "call_session_id": str(session.id),
            "to_phone_number": phone_number,
            "country": country,
            "call_type": call_type,
            "objective": objective[:600],
        }
        update_fields = None
        if created_run:
            next_meta = dict(call_run.metadata or {}) if isinstance(call_run.metadata, dict) else {}
            next_meta["voice_call_session_id"] = str(session.id)
            update_fields = {
                "metadata": next_meta,
                "status": AgentRunStatus.WAITING_EXTERNAL,
                "run_after": None,
                "lease_expires_at": None,
                "finished_at": None,
                "error_detail": "",
            }
        try:
            append_agent_run_event(
                run_id=uuid.UUID(str(call_run.id)),
                business_id=business_id,
                stream=AgentRunEventStream.EXECUTED,
                event_type=AgentRunEventType.PROGRESS,
                label="Phone call queued",
                payload=payload,
                update_run_fields=update_fields,
            )
        except Exception:  # pragma: no cover - best effort
            logger.exception("voice.tool.agent_run_event_failed run=%s", getattr(call_run, "id", None))

    # We deliberately avoid injecting a synthetic chat message like "I'm placing the call now…".
    # The portal UI already renders deterministic tool + task cards for call progress.

    status_value = "needs_external" if is_agent_run_conversation else "ok"
    result: dict[str, object] = {
      "tool": "initiate_phone_call",
      "status": status_value,
        "call_session_id": str(session.id),
        "callSessionId": str(session.id),
        "call_status": session.status,
        "callStatus": session.status,
        "agent_run_id": str(call_run.id) if call_run else None,
        "agentRunId": str(call_run.id) if call_run else None,
        "country": country,
        "from_phone_number": from_number,
        "to_phone_number": phone_number,
        "estimate_usd": str(estimate),
    }
    if status_value == "needs_external":
        result["request"] = {
            "id": str(session.id),
            "type": "phone_call",
            "to_phone_number": phone_number,
            "objective": objective[:240],
        }
        result["hint"] = "Call queued. Await call completion, then continue with the workflow."
    return result
