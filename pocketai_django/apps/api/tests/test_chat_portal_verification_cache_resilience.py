from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from apps.api import chat_portal
from core.cache_resilience import CacheUnavailableError


class _StubPortalService:
    def __init__(self) -> None:
        business = SimpleNamespace(id=uuid.uuid4(), metadata={})
        agent = SimpleNamespace(id=uuid.uuid4(), business_profile=business)
        self.conversation = SimpleNamespace(
            id=uuid.uuid4(),
            business_profile=business,
            business_profile_id=business.id,
            agent_profile=agent,
            metadata={},
        )
        self.session = SimpleNamespace(
            conversation_id=self.conversation.id,
            session_token="session-token",
            status="open",
            started_at=datetime.now(timezone.utc),
            expires_at=None,
        )

    def get_conversation(self, session_token: str, include_messages: bool = False):
        return self.conversation

    def get_session_state(self, session_token: str, conversation=None):
        return self.session


class PortalVerificationCacheResilienceTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.stub_service = _StubPortalService()

    def test_start_returns_503_when_cooldown_cache_unavailable(self) -> None:
        request = self.factory.post(
            "/api/chat/portal/verify/start/",
            data=json.dumps(
                {
                    "session_token": "session-token",
                    "method": "email",
                    "destination": "owner@example.com",
                }
            ),
            content_type="application/json",
        )

        with (
            mock.patch.object(chat_portal, "_service", return_value=self.stub_service),
            mock.patch.object(chat_portal, "_portal_verification_policy", return_value={"enabled": True}),
            mock.patch.object(chat_portal, "_conversation_is_verified_for_lookup", return_value=False),
            mock.patch.object(
                chat_portal,
                "reserve_cooldown_key",
                side_effect=CacheUnavailableError("redis down"),
            ),
        ):
            response = chat_portal.portal_verification_start(request)

        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual((payload.get("error") or {}).get("code"), "verification_unavailable")

    def test_confirm_returns_503_when_challenge_cache_unavailable(self) -> None:
        request = self.factory.post(
            "/api/chat/portal/verify/confirm/",
            data=json.dumps(
                {
                    "session_token": "session-token",
                    "challenge_id": str(uuid.uuid4()),
                    "code": "123456",
                }
            ),
            content_type="application/json",
        )

        with (
            mock.patch.object(chat_portal, "_service", return_value=self.stub_service),
            mock.patch.object(
                chat_portal,
                "load_json_state",
                side_effect=CacheUnavailableError("redis down"),
            ),
        ):
            response = chat_portal.portal_verification_confirm(request)

        payload = json.loads(response.content.decode("utf-8"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual((payload.get("error") or {}).get("code"), "verification_unavailable")
