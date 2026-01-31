from __future__ import annotations

import dataclasses
import logging
from datetime import timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context

from apps.voice.models import CallEvent, CallSession, CallStatus, CallType, VoiceConfiguration, VoiceSuppressionEntry
from apps.voice.policy_engine import audit_policy_decision, evaluate_voice_compliance_policy
from apps.voice.twilio import load_twilio_config


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class VoiceCallWorkerResult:
    call_session_id: str
    status: str
    requeued: bool = False
    error: str = ""


def _log_event(session: CallSession, event_type: str, payload: dict[str, object] | None = None) -> None:
    try:
        if session.business_profile_id:
            with tenant_context(session.business_profile_id):
                CallEvent.objects.create(
                    call_session=session,
                    business_profile_id=session.business_profile_id,
                    event_type=event_type,
                    payload=payload or {},
                )
        else:
            with tenant_bypass():
                CallEvent.objects.create(
                    call_session=session,
                    business_profile_id=None,
                    event_type=event_type,
                    payload=payload or {},
                )
    except Exception:  # pragma: no cover
        logger.exception("voice.call_event_failed type=%s session=%s", event_type, session.id)


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


class VoiceCallWorkerService:
    def __init__(
        self,
        *,
        lease_seconds: float = 60.0,
        max_retry_delay_seconds: float = 900.0,
    ) -> None:
        self.lease_seconds = float(lease_seconds)
        self.max_retry_delay_seconds = float(max_retry_delay_seconds)

    def process_next_call(self) -> VoiceCallWorkerResult | None:
        session = self._claim_next_session()
        if session is None:
            return None

        if not session.business_profile_id:
            session.status = CallStatus.FAILED
            session.last_error = "missing_business_profile"
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "worker.failed", {"error": session.last_error})
            return VoiceCallWorkerResult(call_session_id=str(session.id), status=session.status, error=session.last_error)

        try:
            with tenant_context(session.business_profile_id):
                self._apply_worker_guardrails(session)
                self._initiate_twilio_call(session)
        except _VoiceCallRequeue as exc:
            return VoiceCallWorkerResult(
                call_session_id=str(session.id),
                status=session.status,
                requeued=session.status == CallStatus.QUEUED,
                error=str(exc),
            )
        except Exception as exc:
            logger.exception("voice.call_worker_failed session=%s", session.id)
            self._mark_failed_or_requeue(session, error=str(exc) or "worker_failed")
            return VoiceCallWorkerResult(call_session_id=str(session.id), status=session.status, requeued=session.status == CallStatus.QUEUED, error=str(exc))

        return VoiceCallWorkerResult(call_session_id=str(session.id), status=session.status)

    def _claim_next_session(self) -> CallSession | None:
        now = timezone.now()
        lease_until = now + timedelta(seconds=max(1.0, self.lease_seconds))

        with tenant_bypass():
            with transaction.atomic():
                candidate = (
                    CallSession.objects.select_for_update(skip_locked=True)
                    .filter(status=CallStatus.QUEUED)
                    .filter(business_profile__isnull=False)
                    .filter(Q(run_after__isnull=True) | Q(run_after__lte=now))
                    .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now))
                    .filter(attempt_count__lt=F("max_attempts"))
                    .order_by("queued_at")
                    .first()
                )
                if not candidate:
                    return None

                candidate.status = CallStatus.INITIATING
                candidate.attempt_count = int(candidate.attempt_count or 0) + 1
                candidate.lease_expires_at = lease_until
                candidate.save(update_fields=["status", "attempt_count", "lease_expires_at", "updated_at"])

        _log_event(candidate, "worker.claimed", {"lease_seconds": self.lease_seconds, "attempt": candidate.attempt_count})
        return candidate

    def _apply_worker_guardrails(self, session: CallSession) -> None:
        config = VoiceConfiguration.objects.filter(business_profile_id=session.business_profile_id).first()
        if not config:
            session.status = CallStatus.CANCELLED
            session.last_error = "voice_not_configured"
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "guard.no_config", {})
            raise _VoiceCallRequeue("voice_not_configured")

        if session.call_type == CallType.MARKETING and not config.marketing_calls_enabled:
            session.status = CallStatus.CANCELLED
            session.last_error = "marketing_calls_disabled"
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "guard.marketing_disabled", {})
            raise _VoiceCallRequeue("marketing_calls_disabled")

        if session.call_type == CallType.SERVICE and not config.service_calls_enabled:
            session.status = CallStatus.CANCELLED
            session.last_error = "service_calls_disabled"
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "guard.service_disabled", {})
            raise _VoiceCallRequeue("service_calls_disabled")

        allowed_countries = config.allowed_countries if isinstance(config.allowed_countries, list) else []
        if not allowed_countries:
            allowed_countries = list(getattr(settings, "VOICE_DEFAULT_ALLOWED_COUNTRIES", []) or [])
        allowed_set = {str(code).upper()[:2] for code in allowed_countries if str(code or "").strip()}
        if session.country and allowed_set and session.country.upper() not in allowed_set:
            session.status = CallStatus.CANCELLED
            session.last_error = "country_not_allowed"
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "guard.country_blocked", {"country": session.country})
            raise _VoiceCallRequeue("country_not_allowed")

        if VoiceSuppressionEntry.objects.filter(
            business_profile_id=session.business_profile_id,
            phone_number=session.to_phone_number,
        ).exists():
            session.status = CallStatus.CANCELLED
            session.last_error = "suppressed_number"
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "guard.suppressed", {})
            raise _VoiceCallRequeue("suppressed_number")

        decision = evaluate_voice_compliance_policy(
            business_profile_id=session.business_profile_id,
            agent_profile_id=session.agent_profile_id,
            call_type=str(session.call_type or ""),
            country=str(session.country or ""),
        )
        audit_policy_decision(
            business_profile_id=session.business_profile_id,
            call_session_id=session.id,
            actor_user_id=session.created_by_id,
            actor_agent_id=session.agent_profile_id,
            decision=decision,
        )
        if isinstance(session.metadata, dict):
            session.metadata["policy"] = decision.to_dict()
        if not decision.allowed:
            session.status = CallStatus.CANCELLED
            session.last_error = decision.reason_code
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "metadata", "updated_at"])
            _log_event(session, "guard.policy_blocked", {"reason": decision.reason_code, "details": decision.details})
            raise _VoiceCallRequeue(decision.reason_code)

        owner_max_concurrent = int(getattr(settings, "VOICE_OWNER_MAX_CONCURRENT_CALLS", 0) or 0)
        effective_concurrent = _effective_owner_cap(int(config.max_concurrent_calls or 0), owner_max_concurrent)
        if effective_concurrent > 0:
            active_statuses = {CallStatus.INITIATING, CallStatus.RINGING, CallStatus.IN_PROGRESS}
            active_count = (
                CallSession.objects.filter(business_profile_id=session.business_profile_id, status__in=active_statuses)
                .exclude(id=session.id)
                .count()
            )
            if active_count >= effective_concurrent:
                self._requeue(session, delay_seconds=10, reason="concurrency_limit")
                raise _VoiceCallRequeue("concurrency_limit")

        owner_max_calls_per_day = int(getattr(settings, "VOICE_OWNER_MAX_CALLS_PER_DAY", 0) or 0)
        effective_calls_per_day = _effective_owner_cap(int(config.max_calls_per_day or 0), owner_max_calls_per_day)
        if effective_calls_per_day > 0:
            now = timezone.now()
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            calls_today = (
                CallSession.objects.filter(
                    business_profile_id=session.business_profile_id,
                    created_at__gte=day_start,
                    created_at__lt=day_end,
                )
                .exclude(id=session.id)
                .count()
            )
            if calls_today >= effective_calls_per_day:
                session.status = CallStatus.CANCELLED
                session.last_error = "daily_call_cap_exceeded"
                session.lease_expires_at = None
                session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
                _log_event(session, "guard.daily_cap_exceeded", {"cap": effective_calls_per_day})
                raise _VoiceCallRequeue("daily_call_cap_exceeded")

        owner_max_duration = int(getattr(settings, "VOICE_OWNER_MAX_CALL_DURATION_SECONDS", 0) or 0)
        if owner_max_duration > 0 and int(session.max_duration_seconds or 0) > owner_max_duration:
            session.max_duration_seconds = owner_max_duration
            session.save(update_fields=["max_duration_seconds", "updated_at"])

        budget = Decimal(str(config.monthly_budget_usd or "0.00"))
        if budget > Decimal("0.00"):
            month_start = _month_start(timezone.now())
            spent = (
                CallSession.objects.filter(
                    business_profile_id=session.business_profile_id,
                    created_at__gte=month_start,
                ).aggregate(total=Sum("cost_total_usd"))["total"]
                or Decimal("0.00")
            )
            estimate = Decimal(str(session.cost_estimate_usd or "0.00"))
            if spent + estimate > budget:
                session.status = CallStatus.CANCELLED
                session.last_error = "monthly_budget_exceeded"
                session.lease_expires_at = None
                session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
                _log_event(session, "guard.budget_exceeded", {"spent": str(spent), "estimate": str(estimate), "budget": str(budget)})
                raise _VoiceCallRequeue("monthly_budget_exceeded")

    def _initiate_twilio_call(self, session: CallSession) -> None:
        twilio = load_twilio_config(require_from_number=False)
        if not session.from_phone_number:
            session.from_phone_number = twilio.default_from_number
            session.save(update_fields=["from_phone_number", "updated_at"])
        if not session.from_phone_number:
            raise ValueError("missing_from_phone_number")

        twiml_url = f"{twilio.webhook_base_url}/voice/twilio/twiml/{session.id}/"
        status_cb = f"{twilio.webhook_base_url}/voice/twilio/status/{session.id}/"

        resp = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{twilio.account_sid}/Calls.json",
            auth=(twilio.account_sid, twilio.auth_token),
            headers={"Accept": "application/json"},
            data={
                "From": session.from_phone_number,
                "To": session.to_phone_number,
                "Url": twiml_url,
                "Method": "POST",
                "StatusCallback": status_cb,
                "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            },
            timeout=20,
        )

        if resp.status_code >= 400:
            raise RuntimeError(f"twilio_error:{resp.status_code}:{resp.text[:200]}")

        call_sid = ""
        try:
            data = resp.json()
            call_sid = str(data.get("sid") or "")
        except Exception:
            call_sid = ""

        now = timezone.now()
        session.twilio_call_sid = call_sid
        session.status = CallStatus.RINGING
        session.started_at = session.started_at or now
        session.lease_expires_at = None
        session.save(update_fields=["twilio_call_sid", "status", "started_at", "lease_expires_at", "updated_at"])
        _log_event(session, "twilio.call_create.ok", {"call_sid": call_sid})

    def _mark_failed_or_requeue(self, session: CallSession, *, error: str) -> None:
        attempt = int(session.attempt_count or 0)
        max_attempts = int(session.max_attempts or 0) or 1
        if attempt < max_attempts:
            delay = min(self.max_retry_delay_seconds, float(max(5, 2 ** attempt)))
            session.status = CallStatus.QUEUED
            session.run_after = timezone.now() + timedelta(seconds=delay)
            session.last_error = error
            session.lease_expires_at = None
            session.save(update_fields=["status", "run_after", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "worker.requeued", {"error": error, "delay_seconds": delay})
        else:
            session.status = CallStatus.FAILED
            session.last_error = error
            session.lease_expires_at = None
            session.save(update_fields=["status", "last_error", "lease_expires_at", "updated_at"])
            _log_event(session, "worker.failed", {"error": error})

    def _requeue(self, session: CallSession, *, delay_seconds: float, reason: str) -> None:
        delay = min(self.max_retry_delay_seconds, float(max(1, delay_seconds)))
        session.status = CallStatus.QUEUED
        session.run_after = timezone.now() + timedelta(seconds=delay)
        session.last_error = reason
        session.lease_expires_at = None
        session.save(update_fields=["status", "run_after", "last_error", "lease_expires_at", "updated_at"])
        _log_event(session, "worker.requeued", {"reason": reason, "delay_seconds": delay})


class _VoiceCallRequeue(RuntimeError):
    pass
