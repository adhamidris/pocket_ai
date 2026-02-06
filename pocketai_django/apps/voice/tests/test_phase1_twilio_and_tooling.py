from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation, ConversationChannel
from apps.conversations.models import AgentRun, AgentRunEvent, AgentRunStatus
from apps.voice.mcp_tools import initiate_phone_call_tool
from apps.voice.models import CallSession, CallStatus, VoiceProviderConnection
from apps.voice.twilio import build_twilio_signature
from apps.voice.views_twilio import twilio_consent, twilio_twiml
from core.tenancy import tenant_context
from apps.mcp.types import ToolExecutionContext


User = get_user_model()


class VoicePhase1TwilioAndToolingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._env_before = dict(os.environ)
        os.environ["VOICE_WS_BASE_URL"] = "wss://ws.example.com"
        os.environ["TWILIO_VALIDATE_SIGNATURES"] = "true"
        self.user = User.objects.create_user(email="phase1-owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Ops Agent",
            status="active",
        )
        with tenant_context(self.business.id):
            connection = VoiceProviderConnection(
                business_profile=self.business,
                created_by=self.user,
                provider=VoiceProviderConnection.Provider.TWILIO,
                enabled=True,
            )
            connection.credentials = {
                "account_sid": "AC123",
                "auth_token": "secret",
                "webhook_base_url": "https://example.com",
                "from_number": "+15551234567",
            }
            connection.save()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_before)
        super().tearDown()

    def test_twilio_twiml_validates_signature(self) -> None:
        session = CallSession.objects.create(
            business_profile=self.business,
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
            business_profile=self.business,
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
        self.assertIn(f"/voice/stream/{session.id}", body)

        session.refresh_from_db()
        self.assertTrue(session.consent_obtained)
        self.assertTrue(session.stream_token)
        self.assertIn(session.stream_token, body)

    @override_settings(VOICE_GLOBAL_ENABLED=True, VOICE_AUTO_CREATE_CONFIG=True)
    def test_initiate_phone_call_tool_creates_queued_session(self) -> None:
        conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            channel=ConversationChannel.API,
        )

        with tenant_context(self.business.id):
            result = initiate_phone_call_tool(
                {"phone_number": "+201234567890", "objective": "Confirm appointment"},
                conversation,
                ToolExecutionContext(),
            )

        self.assertEqual(result.get("status"), "ok", msg=str(result))
        call_session_id = result.get("call_session_id")
        self.assertTrue(call_session_id)
        agent_run_id = result.get("agent_run_id")
        self.assertTrue(agent_run_id, msg=str(result))

        call = CallSession.objects.get(id=call_session_id)
        self.assertEqual(call.status, CallStatus.QUEUED)
        self.assertEqual(call.country, "EG")
        self.assertEqual(str(call.metadata.get("agent_run_id") or ""), str(agent_run_id))

        run = AgentRun.objects.get(id=agent_run_id)
        self.assertEqual(run.status, AgentRunStatus.WAITING_EXTERNAL)
        self.assertEqual(run.metadata.get("kind"), "voice_call")
        self.assertEqual(run.metadata.get("voice_call_session_id"), str(call.id))

        self.assertTrue(
            AgentRunEvent.objects.filter(run_id=run.id).exists(),
            msg="Expected at least one AgentRunEvent so the Tasks panel can render the run.",
        )
