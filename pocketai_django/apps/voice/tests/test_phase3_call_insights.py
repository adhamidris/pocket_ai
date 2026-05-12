from __future__ import annotations

import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import ConversationMessage
from apps.voice.call_insights import generate_call_insights
from apps.voice.models import CallSession, CallStatus
from apps.voice.post_call_processing import process_post_call


User = get_user_model()


class VoicePhase3CallInsightsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env_before = dict(os.environ)
        # Keep tests offline/deterministic even when API keys are configured locally.
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("DEEPSEEK_API_KEY", None)
        os.environ.pop("LLM_PROVIDER", None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)
        super().tearDown()

    def test_post_call_processing_persists_insights_and_writes_message(self) -> None:
        user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        registration = RegistrationSession.objects.create(user=user)
        business = BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Acme Co",
            industry="Retail",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": True}},
        )
        agent = AgentProfile.objects.create(business_profile=business, user=user, name="Ops Agent")

        now = timezone.now()
        session = CallSession.objects.create(
            business_profile=business,
            agent_profile=agent,
            objective="Inform customer about fees",
            to_phone_number="+15551230000",
            from_phone_number="+15551234567",
            status=CallStatus.CANCELLED,
            consent_obtained=True,
            started_at=now - timedelta(minutes=1),
            ended_at=now,
            twilio_call_sid="CA_TEST",
        )

        # Transcript events.
        session.events.create(event_type="stt.final", payload={"text": "I'm in a meeting."})
        session.events.create(event_type="llm.response.final", payload={"text": "No problem — when should I call you back?"})
        session.events.create(event_type="stt.final", payload={"text": "Tomorrow at 3pm"})

        # Runtime signals (Phase 2).
        session.events.create(event_type="call.busy", payload={"text": "I'm in a meeting."})
        session.events.create(event_type="call.callback_time.captured", payload={"text": "Tomorrow at 3pm"})

        process_post_call(session)

        session.refresh_from_db()
        self.assertIsInstance(session.insights, dict)
        self.assertEqual(session.insights.get("schema_version"), 1)
        self.assertEqual((session.insights.get("outcome") or {}).get("label"), "callback_requested")

        follow_ups = session.insights.get("follow_ups")
        self.assertIsInstance(follow_ups, list)
        callback = next((item for item in follow_ups if isinstance(item, dict) and item.get("type") == "callback"), None)
        self.assertIsInstance(callback, dict)
        assert isinstance(callback, dict)
        self.assertEqual(callback.get("callback_time_text"), "Tomorrow at 3pm")
        self.assertTrue(callback.get("callback_time_iso"))

        self.assertTrue(session.execution_conversation_id)
        exists = ConversationMessage.objects.filter(
            conversation_id=session.execution_conversation_id,
            metadata__type="call_insights",
            metadata__call_session_id=str(session.id),
        ).exists()
        self.assertTrue(exists)

    def test_insights_schema_stability_without_llm(self) -> None:
        user = User.objects.create_user(email="owner2@example.com", password="changeme123", first_name="Owner")
        registration = RegistrationSession.objects.create(user=user)
        business = BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Acme Co",
            industry="Retail",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": True}},
        )
        agent = AgentProfile.objects.create(business_profile=business, user=user, name="Ops Agent")

        session = CallSession.objects.create(
            business_profile=business,
            agent_profile=agent,
            objective="Inform customer about fees",
            to_phone_number="+15551230001",
            from_phone_number="+15551234567",
            status=CallStatus.CANCELLED,
            consent_obtained=True,
            twilio_call_sid="CA_TEST",
        )
        wrong_ev = session.events.create(event_type="call.wrong_person", payload={"text": "wrong number"})

        insights = generate_call_insights(session)
        self.assertIsInstance(insights, dict)
        self.assertEqual(insights.get("schema_version"), 1)
        self.assertIsInstance(insights.get("generated_at"), str)
        self.assertTrue(insights.get("generated_at"))
        self.assertIsInstance(insights.get("language"), str)
        self.assertIsInstance(insights.get("country"), str)

        outcome = insights.get("outcome")
        self.assertIsInstance(outcome, dict)
        assert isinstance(outcome, dict)
        self.assertEqual(outcome.get("label"), "wrong_person")
        self.assertEqual(outcome.get("source_event_ids"), [int(wrong_ev.id)])

        self.assertIsInstance(insights.get("topics"), list)
        self.assertIsInstance(insights.get("follow_ups"), list)
