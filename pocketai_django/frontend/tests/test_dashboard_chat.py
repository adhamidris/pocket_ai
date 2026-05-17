from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation


User = get_user_model()


class DashboardChatViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="chat-owner@example.com", password="changeme123")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="CIB",
            industry="Banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Sarah",
        )
        self.url = reverse("frontend:dashboard-chat")

    def _bootstrap_payload(self, *, session_token: str = "sess_123", conversation_id: str = "conv_123") -> dict[str, object]:
        return {
            "business": {"name": self.business.name, "slug": self.business.slug},
            "agent": {"name": self.agent.name, "slug": self.agent.slug},
            "session": {
                "conversation_id": conversation_id,
                "session_token": session_token,
                "status": "active",
            },
            "messages": [],
            "capabilities": {"subAgentsEnabled": False},
        }

    def test_dashboard_chat_requires_login(self) -> None:
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    @patch("frontend.views._call_portal_bootstrap_api")
    def test_dashboard_chat_renders_authenticated_surface(self, bootstrap_mock) -> None:
        self.client.force_login(self.user)
        bootstrap_mock.return_value = self._bootstrap_payload()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        portal = response.context["portal"]
        self.assertEqual(portal["chat_surface"], "dashboard")
        self.assertTrue(portal["is_authenticated_chat"])
        self.assertEqual(portal["business"]["slug"], self.business.slug)
        self.assertEqual(portal["agent"]["slug"], self.agent.slug)
        self.assertEqual(portal["endpoints"]["conversations"], reverse("api:chat-conversations"))

        bootstrap_mock.assert_called_once()
        kwargs = bootstrap_mock.call_args.kwargs
        self.assertEqual(kwargs["business_slug"], self.business.slug)
        self.assertEqual(kwargs["agent_slug"], self.agent.slug)
        self.assertEqual(kwargs["metadata"]["surface"], "dashboard")
        self.assertEqual(kwargs["metadata"]["actor_user_id"], str(self.user.id))

    @patch("frontend.views.get_authorized_conversation")
    @patch("frontend.views._call_portal_bootstrap_api")
    def test_dashboard_chat_can_resume_owned_conversation(self, bootstrap_mock, auth_conversation_mock) -> None:
        self.client.force_login(self.user)
        bootstrap_mock.return_value = self._bootstrap_payload(session_token="sess_existing", conversation_id="conv_existing")
        auth_conversation_mock.return_value = SimpleNamespace(session_token="sess_existing")

        response = self.client.get(self.url, {"conversation": "0b39f4e6-14ba-4bfd-8d8f-47a6ad8737f0"})

        self.assertEqual(response.status_code, 200)
        auth_conversation_mock.assert_called_once()
        kwargs = auth_conversation_mock.call_args.kwargs
        self.assertEqual(kwargs["user"], self.user)
        self.assertIsNotNone(kwargs["service"])
        self.assertFalse(kwargs["include_messages"])
        self.assertEqual(bootstrap_mock.call_args.kwargs["existing_session_token"], "sess_existing")

    @patch("frontend.views._call_portal_bootstrap_api")
    @patch("frontend.views.ChatPortalService.list_owned_sessions")
    def test_dashboard_chat_refresh_uses_latest_owned_conversation(self, list_sessions_mock, bootstrap_mock) -> None:
        self.client.force_login(self.user)
        latest = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.user,
            session_token="sess_latest",
        )
        list_sessions_mock.return_value = [
            SimpleNamespace(conversation_id=latest.id, session_token="sess_latest"),
        ]
        bootstrap_mock.return_value = self._bootstrap_payload(session_token="sess_latest", conversation_id=str(latest.id))

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        list_sessions_mock.assert_called_once()
        kwargs = list_sessions_mock.call_args.kwargs
        self.assertEqual(kwargs["owner_user"], self.user)
        self.assertEqual(kwargs["business_slug"], self.business.slug)
        self.assertEqual(kwargs["agent_slug"], self.agent.slug)
        self.assertEqual(kwargs["limit"], 1)
        self.assertEqual(bootstrap_mock.call_args.kwargs["existing_session_token"], "sess_latest")
