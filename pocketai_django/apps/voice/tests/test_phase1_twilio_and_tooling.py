from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation, ConversationChannel
from apps.voice.mcp_tools import initiate_phone_call_tool
from apps.voice.models import CallSession, CallStatus
from apps.voice.twilio import build_twilio_signature
from apps.voice.views_twilio import twilio_consent, twilio_twiml
from core.tenancy import tenant_context
from apps.mcp.types import ToolExecutionContext


User = get_user_model()


class VoicePhase1TwilioAndToolingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env_before = dict(os.environ)
        os.environ["TWILIO_ACCOUNT_SID"] = "AC123"
        os.environ["TWILIO_AUTH_TOKEN"] = "secret"
        os.environ["TWILIO_WEBHOOK_BASE_URL"] = "https://example.com"
        os.environ["TWILIO_FROM_NUMBER"] = "+15551234567"
        os.environ["VOICE_WS_BASE_URL"] = "wss://ws.example.com"
        os.environ["TWILIO_VALIDATE_SIGNATURES"] = "true"

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)
        super().tearDown()

    def test_twilio_twiml_validates_signature(self) -> None:
        session = CallSession.objects.create(
            objective="Test",
            to_phone_number="+15551230000",
            from_phone_number="+15551234567",
            status=CallStatus.RINGING,
        )
        rf = RequestFactory()
        path = f"/voice/twilio/twiml/{session.id}/"
        request = rf.post(path, data={"CallSid": "CA123"})
        url = f"https://example.com{path}"
        signature = build_twilio_signature(url=url, params={"CallSid": "CA123"}, auth_token="secret")
        request.META["HTTP_X_TWILIO_SIGNATURE"] = signature

        response = twilio_twiml(request, session.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn("<Gather", response.content.decode("utf-8"))

        session.refresh_from_db()
        self.assertEqual(session.status, CallStatus.IN_PROGRESS)

    def test_twilio_consent_generates_stream_token_and_stream_url(self) -> None:
        session = CallSession.objects.create(
            objective="Test",
            to_phone_number="+15551230000",
            from_phone_number="+15551234567",
            status=CallStatus.IN_PROGRESS,
        )
        rf = RequestFactory()
        path = f"/voice/twilio/consent/{session.id}/"
        request = rf.post(path, data={"Digits": "1"})
        url = f"https://example.com{path}"
        signature = build_twilio_signature(url=url, params={"Digits": "1"}, auth_token="secret")
        request.META["HTTP_X_TWILIO_SIGNATURE"] = signature

        response = twilio_consent(request, session.id)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("<Record", body)
        self.assertIn(f"/voice/stream/{session.id}?token=", body)

        session.refresh_from_db()
        self.assertTrue(session.consent_obtained)
        self.assertTrue(session.stream_token)

    @override_settings(VOICE_GLOBAL_ENABLED=True, VOICE_AUTO_CREATE_CONFIG=True)
    def test_initiate_phone_call_tool_creates_queued_session(self) -> None:
        user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        registration = RegistrationSession.objects.create(user=user)
        business = BusinessProfile.objects.create(
            user=user,
            registration_session=registration,
            name="Acme Co",
            industry="Retail",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": True}},
        )
        agent = AgentProfile.objects.create(business_profile=business, user=user, name="Ops Agent", status="active")
        conversation = Conversation.objects.create(
            business_profile=business,
            agent_profile=agent,
            channel=ConversationChannel.API,
        )

        with tenant_context(business.id):
            result = initiate_phone_call_tool(
                {"phone_number": "+201234567890", "objective": "Confirm appointment"},
                conversation,
                ToolExecutionContext(),
            )

        self.assertEqual(result.get("status"), "ok", msg=str(result))
        call_session_id = result.get("call_session_id")
        self.assertTrue(call_session_id)

        call = CallSession.objects.get(id=call_session_id)
        self.assertEqual(call.status, CallStatus.QUEUED)
        self.assertEqual(call.country, "EG")
