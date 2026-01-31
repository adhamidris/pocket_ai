from __future__ import annotations

import dataclasses
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from core.tenancy import tenant_context

from apps.voice.models import (
    CallType,
    VoiceCallAuditAction,
    VoiceCallAuditEvent,
    VoiceConfiguration,
    VoiceCountryPolicy,
)


logger = logging.getLogger(__name__)


class VoicePolicyAction:
    AI_DISCLOSURE = "ai_disclosure"
    RECORDING_CONSENT = "recording_consent"


@dataclasses.dataclass(frozen=True)
class VoicePolicyDecision:
    allowed: bool
    reason_code: str
    required_actions: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    details: dict[str, object] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": bool(self.allowed),
            "reason_code": self.reason_code,
            "required_actions": list(self.required_actions),
            "warnings": list(self.warnings),
            "details": dict(self.details),
        }


def _now_utc(now_utc: datetime | None) -> datetime:
    if now_utc is None:
        return timezone.now()
    if timezone.is_naive(now_utc):
        return timezone.make_aware(now_utc, timezone=timezone.utc)
    return now_utc


def _safe_country(country: str) -> str:
    return (country or "").strip().upper()[:2]


def _required_actions_for_config(config: VoiceConfiguration | None, country_policy: VoiceCountryPolicy | None) -> list[str]:
    actions: list[str] = []

    actions.append(VoicePolicyAction.AI_DISCLOSURE)

    owner_requires_consent = bool(getattr(settings, "VOICE_RECORDING_CONSENT_REQUIRED", True))
    config_requires_consent = bool(getattr(config, "recording_consent_required", True)) if config else True
    country_requires_consent = bool(getattr(country_policy, "recording_consent_required", True)) if country_policy else True

    if owner_requires_consent and config_requires_consent and country_requires_consent:
        actions.append(VoicePolicyAction.RECORDING_CONSENT)

    return actions


def _is_within_call_window(*, now_local: datetime, policy: VoiceCountryPolicy) -> tuple[bool, dict[str, object]]:
    cfg = policy.policy_config if isinstance(policy.policy_config, dict) else {}
    if not bool(cfg.get("enforce_call_window", False)):
        return True, {"enforced": False}

    weekdays = policy.allowed_weekdays if isinstance(policy.allowed_weekdays, list) else []
    if weekdays:
        try:
            weekday = int(now_local.weekday())
        except Exception:
            weekday = now_local.weekday()
        allowed = {int(x) for x in weekdays if isinstance(x, int) or str(x).isdigit()}
        if allowed and weekday not in allowed:
            return False, {"weekday": weekday, "allowed_weekdays": sorted(list(allowed))}

    start = policy.allowed_call_time_start
    end = policy.allowed_call_time_end
    if not start or not end:
        return True, {}

    now_t = now_local.time()
    if start <= end:
        in_window = start <= now_t <= end
    else:
        in_window = now_t >= start or now_t <= end

    return in_window, {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "now": now_t.isoformat(),
    }


def evaluate_voice_compliance_policy(
    *,
    business_profile_id: object,
    agent_profile_id: object | None,
    call_type: str,
    country: str,
    now_utc: datetime | None = None,
) -> VoicePolicyDecision:
    """
    Phase 3 compliance gating.

    This is intentionally separate from "resource guardrails" (budget/caps),
    which remain enforced in the tool + worker.
    """

    country = _safe_country(country)
    now_utc = _now_utc(now_utc)

    with tenant_context(business_profile_id):
        config = VoiceConfiguration.objects.filter(business_profile_id=business_profile_id).first()
    if not config:
        return VoicePolicyDecision(allowed=False, reason_code="voice_not_configured")

    policy = VoiceCountryPolicy.objects.filter(country=country).first()
    required_actions = _required_actions_for_config(config, policy)

    if not policy:
        if call_type == CallType.MARKETING:
            return VoicePolicyDecision(
                allowed=False,
                reason_code="marketing_country_policy_missing",
                required_actions=required_actions,
                details={"country": country},
            )
        return VoicePolicyDecision(
            allowed=True,
            reason_code="ok",
            required_actions=required_actions,
            warnings=["country_policy_missing"],
            details={"country": country},
        )

    if not policy.is_active:
        return VoicePolicyDecision(
            allowed=False,
            reason_code="country_inactive",
            required_actions=required_actions,
            details={"country": country},
        )

    if call_type == CallType.SERVICE and not policy.service_calls_allowed:
        return VoicePolicyDecision(
            allowed=False,
            reason_code="service_calls_not_allowed_in_country",
            required_actions=required_actions,
            details={"country": country},
        )

    if call_type == CallType.MARKETING and not policy.marketing_calls_allowed:
        return VoicePolicyDecision(
            allowed=False,
            reason_code="marketing_calls_not_allowed_in_country",
            required_actions=required_actions,
            details={"country": country},
        )

    if not policy.recording_allowed:
        return VoicePolicyDecision(
            allowed=False,
            reason_code="recording_not_allowed_in_country",
            required_actions=required_actions,
            details={"country": country},
        )

    tz_name = (policy.timezone or "").strip()
    now_local = now_utc
    warnings: list[str] = []
    if tz_name:
        try:
            now_local = now_utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            warnings.append("invalid_country_timezone")

    in_window, window_details = _is_within_call_window(now_local=now_local, policy=policy)
    if not in_window:
        return VoicePolicyDecision(
            allowed=False,
            reason_code="outside_allowed_hours",
            required_actions=required_actions,
            warnings=warnings,
            details={"country": country, "timezone": tz_name, "window": window_details},
        )

    return VoicePolicyDecision(
        allowed=True,
        reason_code="ok",
        required_actions=required_actions,
        warnings=warnings,
        details={"country": country, "timezone": tz_name},
    )


def write_voice_call_audit_event(
    *,
    business_profile_id: object | None,
    call_session_id: object | None,
    actor_user_id: object | None,
    actor_agent_id: object | None,
    action: str,
    description: str = "",
    metadata: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    if not business_profile_id:
        return

    with tenant_context(business_profile_id):
        VoiceCallAuditEvent.objects.create(
            business_profile_id=business_profile_id,
            call_session_id=call_session_id,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
            action=action,
            description=description,
            metadata=metadata or {},
            occurred_at=occurred_at or timezone.now(),
        )


def audit_policy_decision(
    *,
    business_profile_id: object | None,
    call_session_id: object | None,
    actor_user_id: object | None,
    actor_agent_id: object | None,
    decision: VoicePolicyDecision,
) -> None:
    action = VoiceCallAuditAction.POLICY_EVALUATED if decision.allowed else VoiceCallAuditAction.POLICY_BLOCKED
    try:
        write_voice_call_audit_event(
            business_profile_id=business_profile_id,
            call_session_id=call_session_id,
            actor_user_id=actor_user_id,
            actor_agent_id=actor_agent_id,
            action=action,
            description=decision.reason_code,
            metadata={"policy": decision.to_dict()},
        )
    except Exception:  # pragma: no cover
        logger.exception(
            "voice.audit_policy_decision_failed business=%s call=%s action=%s",
            business_profile_id,
            call_session_id,
            action,
        )
