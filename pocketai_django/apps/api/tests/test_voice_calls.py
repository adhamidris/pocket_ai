from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.voice.models import CallSession, CallStatus
from core.tenancy import tenant_context


User = get_user_model()


class VoiceCallsApiTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": True}},
        )
        self.agent = AgentProfile.objects.create(business_profile=self.business, user=self.user, name="Ops Agent")
        self.client.force_login(self.user)

    def test_voice_call_detail_includes_summary_actions_and_insights(self) -> None:
        with tenant_context(self.business.id):
            call = CallSession.objects.create(
                business_profile=self.business,
                agent_profile=self.agent,
                objective="Inform about fees",
                to_phone_number="+15551230000",
                from_phone_number="+15551234567",
                status=CallStatus.COMPLETED,
                summary="Customer informed; requested callback.",
                action_items=[{"type": "follow_up", "payload": {"kind": "callback"}}],
                insights={
                    "schema_version": 1,
                    "outcome": {"label": "callback_requested", "source_event_ids": [1]},
                    "topics": [],
                    "follow_ups": [{"type": "callback", "callback_time_text": "tomorrow 3pm"}],
                },
            )

        url = reverse("api:voice-call-detail", args=[call.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("summary"), "Customer informed; requested callback.")
        self.assertEqual(data.get("action_items"), [{"type": "follow_up", "payload": {"kind": "callback"}}])
        self.assertEqual(data.get("insights", {}).get("schema_version"), 1)

