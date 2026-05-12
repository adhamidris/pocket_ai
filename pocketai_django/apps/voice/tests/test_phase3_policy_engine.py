from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.voice.models import CallType, VoiceCallAuditAction, VoiceCallAuditEvent, VoiceConfiguration, VoiceCountryPolicy
from apps.voice.policy_engine import audit_policy_decision, evaluate_voice_compliance_policy
from core.tenancy import tenant_context


User = get_user_model()


class VoicePhase3PolicyEngineTests(TestCase):
    def setUp(self) -> None:
        super().setUp()

        user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        registration = RegistrationSession.objects.create(user=user)
        self.business = BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Acme Co",
            industry="Retail",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": True}},
        )
        self.agent = AgentProfile.objects.create(business_profile=self.business, user=user, name="Ops Agent")

        with tenant_context(self.business.id):
            VoiceConfiguration.objects.create(
                business_profile=self.business,
                trust_tier="verified",
                service_calls_enabled=True,
                marketing_calls_enabled=True,
                allowed_countries=["EG"],
                max_concurrent_calls=1,
                max_calls_per_day=10,
                max_call_duration_seconds=600,
                monthly_budget_usd="0.00",
                default_language="en",
                ai_disclosure_template="Hello. This is an AI assistant calling.",
                ai_disclosure_template_ar="مرحباً. أنا مساعد ذكاء اصطناعي أتصل بك.",
                recording_consent_required=True,
            )

    def test_marketing_calls_blocked_when_country_disallows_marketing(self) -> None:
        with tenant_context(self.business.id):
            VoiceCountryPolicy.objects.update_or_create(
                country="EG",
                defaults={
                    "timezone": "Africa/Cairo",
                    "is_active": True,
                    "service_calls_allowed": True,
                    "marketing_calls_allowed": False,
                    "recording_allowed": True,
                    "recording_consent_required": True,
                    "ai_disclosure_required": True,
                    "policy_config": {"enforce_call_window": False},
                },
            )

            decision = evaluate_voice_compliance_policy(
                business_profile_id=self.business.id,
                agent_profile_id=self.agent.id,
                call_type=CallType.MARKETING,
                country="EG",
                now_utc=datetime.datetime(2026, 1, 31, 12, 0, tzinfo=datetime.timezone.utc),
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "marketing_calls_not_allowed_in_country")

    def test_service_calls_blocked_outside_allowed_hours_when_enforced(self) -> None:
        with tenant_context(self.business.id):
            VoiceCountryPolicy.objects.update_or_create(
                country="EG",
                defaults={
                    "timezone": "Africa/Cairo",
                    "is_active": True,
                    "service_calls_allowed": True,
                    "marketing_calls_allowed": False,
                    "recording_allowed": True,
                    "recording_consent_required": True,
                    "ai_disclosure_required": True,
                    "allowed_weekdays": [0, 1, 2, 3, 4, 5, 6],
                    "allowed_call_time_start": datetime.time(9, 0),
                    "allowed_call_time_end": datetime.time(21, 0),
                    "policy_config": {"enforce_call_window": True},
                },
            )

            decision = evaluate_voice_compliance_policy(
                business_profile_id=self.business.id,
                agent_profile_id=self.agent.id,
                call_type=CallType.SERVICE,
                country="EG",
                # Africa/Cairo local time will be before 09:00 here.
                now_utc=datetime.datetime(2026, 1, 31, 3, 0, tzinfo=datetime.timezone.utc),
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "outside_allowed_hours")

    def test_policy_decision_writes_audit_event(self) -> None:
        with tenant_context(self.business.id):
            decision = evaluate_voice_compliance_policy(
                business_profile_id=self.business.id,
                agent_profile_id=self.agent.id,
                call_type=CallType.SERVICE,
                country="EG",
                now_utc=datetime.datetime(2026, 1, 31, 12, 0, tzinfo=datetime.timezone.utc),
            )
            audit_policy_decision(
                business_profile_id=self.business.id,
                call_session_id=None,
                actor_user_id=None,
                actor_agent_id=self.agent.id,
                decision=decision,
            )

            event = VoiceCallAuditEvent.objects.filter(business_profile=self.business).order_by("-occurred_at").first()

        self.assertIsNotNone(event)
        self.assertEqual(event.action, VoiceCallAuditAction.POLICY_EVALUATED)

