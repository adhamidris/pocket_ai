from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.api import chat_portal
from apps.conversations.portal_session_event_bus import portal_session_conversation_stream_key
from apps.conversations.models import Conversation


User = get_user_model()


class PortalSessionRedisEventBusTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(email="session-redis@example.com", password="changeme123", first_name="Redis")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Session Redis Co",
            industry="Support",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": False}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Session Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-session-redis",
        )

    @override_settings(PORTAL_SESSION_EVENT_BUS="redis")
    def test_session_events_reads_from_redis_stream(self) -> None:
        request = self.factory.get(f"/api/chat/events/?session_token={self.conversation.session_token}")

        stream_key = portal_session_conversation_stream_key(conversation_id=self.conversation.id)
        redis_conn = mock.Mock()
        payload = {
            "run": {"id": "run_1"},
            "event": {"runId": "run_1", "sequenceIndex": 1},
        }
        redis_conn.xread.return_value = [
            (stream_key, [(b"1700000000000-0", {b"event": b"agentRunEvent", b"payload": json.dumps(payload).encode("utf-8")})])
        ]

        with mock.patch("apps.accounts.feature_flags.FeatureFlagService.snapshot", return_value=mock.Mock(sub_agents_v1=True)):
            with mock.patch.object(chat_portal, "get_portal_redis_client", return_value=redis_conn):
                response = chat_portal.events(request)

                it = iter(response.streaming_content)
                chunks = []
                for _ in range(9):
                    chunks.append(next(it))
        raw = b"".join([c if isinstance(c, (bytes, bytearray)) else str(c).encode("utf-8") for c in chunks])
        text = raw.decode("utf-8")

        self.assertIn("event: statusChanged", text)
        self.assertIn("event: agentRunEvent", text)
        self.assertIn("id: 1700000000000-0", text)
