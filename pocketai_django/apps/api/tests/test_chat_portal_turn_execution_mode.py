from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.api import chat_portal
from apps.conversations.models import Conversation, PortalTurn


User = get_user_model()


class PortalTurnExecutionModeTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(email="portal-turn-mode@example.com", password="changeme123", first_name="Mode")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Mode Corp",
            industry="Support",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": False}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Mode Agent",
            status="active",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-portal-turn-mode",
        )

    @override_settings(PORTAL_TURN_EXECUTION_MODE="thread")
    def test_turn_create_thread_mode_spawns_background_thread(self) -> None:
        payload = {"session_token": self.conversation.session_token, "body": "Hello", "metadata": {}}
        request = self.factory.post(
            "/api/chat/turns/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        with mock.patch.object(chat_portal, "run_turn_background") as patched:
            response = chat_portal.portal_turn_create(request)

        self.assertEqual(response.status_code, 201)
        patched.assert_called_once()

        data = json.loads(response.content.decode("utf-8"))
        turn_id = data.get("turn", {}).get("id")
        self.assertTrue(turn_id)
        turn = PortalTurn.objects.get(id=turn_id)
        self.assertEqual(str((turn.metadata or {}).get("execution_mode") or ""), "thread")

    @override_settings(PORTAL_TURN_EXECUTION_MODE="worker")
    def test_turn_create_worker_mode_does_not_spawn_background_thread(self) -> None:
        payload = {"session_token": self.conversation.session_token, "body": "Hello", "metadata": {}}
        request = self.factory.post(
            "/api/chat/turns/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        with mock.patch.object(chat_portal, "run_turn_background") as patched:
            response = chat_portal.portal_turn_create(request)

        self.assertEqual(response.status_code, 201)
        patched.assert_not_called()

        data = json.loads(response.content.decode("utf-8"))
        turn_id = data.get("turn", {}).get("id")
        self.assertTrue(turn_id)
        turn = PortalTurn.objects.get(id=turn_id)
        self.assertEqual(str((turn.metadata or {}).get("execution_mode") or ""), "worker")

    @override_settings(PORTAL_TURN_EXECUTION_MODE="thread")
    def test_turn_create_persists_normalized_scope_selection(self) -> None:
        payload = {
            "session_token": self.conversation.session_token,
            "body": "Cheques",
            "metadata": {
                "scope_selection": {
                    "action": "category",
                    "categoryKey": "Cheques_42FC263D814C",
                    "categoryLabel": "Cheques",
                    "blockId": "scope-block:01",
                }
            },
        }
        request = self.factory.post(
            "/api/chat/turns/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        with mock.patch.object(chat_portal, "run_turn_background"):
            response = chat_portal.portal_turn_create(request)

        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content.decode("utf-8"))
        turn_id = data.get("turn", {}).get("id")
        self.assertTrue(turn_id)
        turn = PortalTurn.objects.get(id=turn_id)
        scope_selection = (turn.metadata or {}).get("scope_selection") or {}
        self.assertEqual(scope_selection.get("action"), "select_category")
        self.assertEqual(scope_selection.get("category_key"), "cheques_42fc263d814c")
        self.assertEqual(scope_selection.get("category_label"), "Cheques")
        self.assertEqual(scope_selection.get("block_id"), "scope-block:01")

    @override_settings(PORTAL_TURN_EXECUTION_MODE="thread")
    def test_turn_create_preserves_arabic_scope_category_key(self) -> None:
        payload = {
            "session_token": self.conversation.session_token,
            "body": "رسوم الشيكات",
            "metadata": {
                "scope_selection": {
                    "action": "select_category",
                    "categoryKey": "رسوم_الشيكات_42fc263d814c",
                    "categoryLabel": "رسوم الشيكات",
                    "blockId": "scope-block:ar-01",
                }
            },
        }
        request = self.factory.post(
            "/api/chat/turns/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        with mock.patch.object(chat_portal, "run_turn_background"):
            response = chat_portal.portal_turn_create(request)

        self.assertEqual(response.status_code, 201)
        data = json.loads(response.content.decode("utf-8"))
        turn_id = data.get("turn", {}).get("id")
        self.assertTrue(turn_id)
        turn = PortalTurn.objects.get(id=turn_id)
        scope_selection = (turn.metadata or {}).get("scope_selection") or {}
        self.assertEqual(scope_selection.get("action"), "select_category")
        self.assertEqual(scope_selection.get("category_key"), "رسوم_الشيكات_42fc263d814c")
        self.assertEqual(scope_selection.get("category_label"), "رسوم الشيكات")
        self.assertEqual(scope_selection.get("block_id"), "scope-block:ar-01")
