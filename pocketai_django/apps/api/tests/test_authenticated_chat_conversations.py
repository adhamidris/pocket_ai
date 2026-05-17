from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import (
    Automation,
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
    PortalTurn,
    PortalTurnStatus,
)


User = get_user_model()


@override_settings(PORTAL_TURN_EXECUTION_MODE="worker")
class AuthenticatedConversationApiTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(email="chat-owner@example.com", password="changeme123", first_name="Owner")
        self.outsider = User.objects.create_user(email="chat-outsider@example.com", password="changeme123", first_name="Outsider")
        self.registration = RegistrationSession.objects.create(user=self.owner)
        self.business = BusinessProfile.objects.create(
            user=self.owner,
            registration_session=self.registration,
            name="Workspace Co",
            industry="Support",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"agent_workforce_v1": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.owner,
            name="Sarah",
            slug="sarah",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.owner,
            session_token="owner-conversation",
        )
        self.message = ConversationMessage.objects.create(
            conversation=self.conversation,
            sender=ConversationSender.CUSTOMER,
            body="Hello from owner",
        )
        self.turn = PortalTurn.objects.create(
            conversation=self.conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            run_after=timezone.now(),
            user_message="Need help",
            metadata={"source": "test"},
        )
        self.approval = ConversationToolApproval.objects.create(
            conversation=self.conversation,
            tool_name="mcp_tool",
            remote_tool_name="create_issue",
            status=ConversationToolApprovalStatus.PENDING,
            tool_call_id="call-1",
            event_id="event-1",
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    def test_conversations_collection_lists_owned_conversations(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("api:chat-conversations"),
            {"business_slug": self.business.slug, "agent_slug": self.agent.slug},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["conversations"]), 1)
        self.assertEqual(payload["conversations"][0]["conversation_id"], str(self.conversation.id))
        self.assertEqual(payload["conversations"][0]["session_type"], "chat")

    def test_conversations_collection_marks_workflow_threads(self) -> None:
        workflow_thread = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.owner,
            session_token="workflow-thread",
            metadata={"type": "workflow_thread"},
        )
        workflow = Automation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.owner,
            conversation=workflow_thread,
            name="Daily report",
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("api:chat-conversations"),
            {"business_slug": self.business.slug, "agent_slug": self.agent.slug},
        )

        self.assertEqual(response.status_code, 200)
        task = next(item for item in response.json()["conversations"] if item["conversation_id"] == str(workflow_thread.id))
        self.assertEqual(task["session_type"], "task")
        self.assertEqual(task["workflow_id"], str(workflow.id))
        self.assertEqual(task["workflow_name"], "Daily report")
        self.assertEqual(task["title"], "New session")

        messages_response = self.client.get(reverse("api:chat-conversation-messages", args=[workflow_thread.id]))
        self.assertEqual(messages_response.status_code, 200)
        self.assertEqual(messages_response.json()["session"]["session_type"], "task")

    def test_conversations_collection_includes_workflow_agent_sessions(self) -> None:
        workflow = Automation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.owner,
            name="Daily sales reviewer",
        )
        workflow_session = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            workflow=workflow,
            owner_user=self.owner,
            session_token="reports-workflow-session",
            metadata={
                "type": "workflow_agent_session",
                "workflow_id": str(workflow.id),
                "workflow_name": workflow.name,
            },
        )
        ConversationMessage.objects.create(
            conversation=workflow_session,
            sender=ConversationSender.CUSTOMER,
            body="This should not replace the Custom Assistant name.",
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("api:chat-conversations"),
            {"business_slug": self.business.slug, "agent_slug": self.agent.slug},
        )

        self.assertEqual(response.status_code, 200)
        conversations = response.json()["conversations"]
        ids = {item["conversation_id"] for item in conversations}
        self.assertIn(str(workflow_session.id), ids)
        task = next(item for item in conversations if item["conversation_id"] == str(workflow_session.id))
        self.assertEqual(task["session_type"], "task")
        self.assertEqual(task["workflow_id"], str(workflow.id))
        self.assertEqual(task["workflow_name"], "Daily sales reviewer")
        self.assertFalse(any(key.startswith("workflow_dept") for key in task))
        self.assertEqual(task["workflow_agent_name"], "Sarah")
        self.assertEqual(task["title"], "This should not replace the Custom Assistant name.")

    def test_conversations_collection_hides_internal_agent_notification_surfaces(self) -> None:
        Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.owner,
            session_token="agent-notification-surface",
            metadata={
                "type": "canonical_main_primary",
                "purpose": "agent_notification_surface",
            },
            summary="Primary communication thread for Sarah.",
            last_activity_at=timezone.now() + timedelta(minutes=5),
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("api:chat-conversations"),
            {"business_slug": self.business.slug, "agent_slug": self.agent.slug},
        )

        self.assertEqual(response.status_code, 200)
        conversations = response.json()["conversations"]
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["conversation_id"], str(self.conversation.id))

    def test_conversations_collection_orders_by_recent_activity(self) -> None:
        older = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.owner,
            session_token="older-conversation",
            last_activity_at=timezone.now() - timedelta(days=1),
        )
        newer = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.owner,
            session_token="newer-conversation",
            last_activity_at=timezone.now(),
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("api:chat-conversations"),
            {"business_slug": self.business.slug, "agent_slug": self.agent.slug},
        )

        self.assertEqual(response.status_code, 200)
        ids = [item["conversation_id"] for item in response.json()["conversations"]]
        self.assertLess(ids.index(str(newer.id)), ids.index(str(older.id)))

    def test_conversations_collection_requires_auth(self) -> None:
        response = self.client.get(
            reverse("api:chat-conversations"),
            {"business_slug": self.business.slug, "agent_slug": self.agent.slug},
        )
        self.assertEqual(response.status_code, 401)

    def test_conversation_create_and_messages_use_conversation_id(self) -> None:
        self.client.force_login(self.owner)

        create_response = self.client.post(
            reverse("api:chat-conversations"),
            data=json.dumps(
                {
                    "business_slug": self.business.slug,
                    "agent_slug": self.agent.slug,
                    "metadata": {"source": "dashboard"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201)
        created_id = create_response.json()["session"]["conversation_id"]

        messages_response = self.client.get(reverse("api:chat-conversation-messages", args=[created_id]))
        self.assertEqual(messages_response.status_code, 200)
        self.assertEqual(messages_response.json()["messages"], [])

        turn_response = self.client.post(
            reverse("api:chat-conversation-turns", args=[created_id]),
            data=json.dumps({"body": "Start a new task"}),
            content_type="application/json",
        )
        self.assertEqual(turn_response.status_code, 201)
        self.assertIn("turn", turn_response.json())

    def test_conversation_create_can_start_workflow_agent_session(self) -> None:
        workflow = Automation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.owner,
            name="Software Engineer",
            trigger_type="manual",
            instructions={"goal": "You are an expert designer in stack HTML and CSS."},
        )
        self.client.force_login(self.owner)

        create_response = self.client.post(
            reverse("api:chat-conversations"),
            data=json.dumps(
                {
                    "business_slug": self.business.slug,
                    "agent_slug": self.agent.slug,
                    "workflow_id": str(workflow.id),
                    "title": "New session",
                    "metadata": {"source": "dashboard"},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(create_response.status_code, 201)
        payload = create_response.json()
        self.assertEqual(payload["session"]["session_type"], "task")
        self.assertEqual(payload["session"]["workflow_id"], str(workflow.id))
        self.assertEqual(payload["session"]["workflow_name"], "Software Engineer")
        conversation = Conversation.objects.get(id=payload["session"]["conversation_id"])
        self.assertEqual(conversation.workflow_id, workflow.id)
        self.assertEqual(conversation.agent_profile_id, self.agent.id)
        self.assertEqual(conversation.metadata["type"], "workflow_agent_session")

    def test_messages_and_turns_forbid_outsider_on_owned_conversation(self) -> None:
        owner_client = self.client
        owner_client.force_login(self.owner)
        ok_messages = owner_client.get(reverse("api:chat-conversation-messages", args=[self.conversation.id]))
        self.assertEqual(ok_messages.status_code, 200)

        outsider_client = self.client_class()
        outsider_client.force_login(self.outsider)
        forbidden_messages = outsider_client.get(reverse("api:chat-conversation-messages", args=[self.conversation.id]))
        self.assertEqual(forbidden_messages.status_code, 403)

        forbidden_turn = outsider_client.post(
            reverse("api:chat-conversation-turns", args=[self.conversation.id]),
            data=json.dumps({"body": "Try to hijack"}),
            content_type="application/json",
        )
        self.assertEqual(forbidden_turn.status_code, 403)

    def test_session_events_require_owned_conversation(self) -> None:
        owner_client = self.client
        owner_client.force_login(self.owner)
        ok = owner_client.get(
            reverse("api:chat-events"),
            {"conversation_id": str(self.conversation.id)},
        )
        self.assertEqual(ok.status_code, 200)

        outsider_client = self.client_class()
        outsider_client.force_login(self.outsider)
        forbidden = outsider_client.get(
            reverse("api:chat-events"),
            {"conversation_id": str(self.conversation.id)},
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_turn_events_and_cancel_require_owned_conversation(self) -> None:
        owner_client = self.client
        owner_client.force_login(self.owner)
        ok = owner_client.get(
            reverse("api:chat-turns-events", args=[self.turn.id]),
            {"conversation_id": str(self.conversation.id)},
        )
        self.assertEqual(ok.status_code, 200)

        cancel = owner_client.post(
            reverse("api:chat-turns-cancel", args=[self.turn.id]),
            data=json.dumps({"conversation_id": str(self.conversation.id)}),
            content_type="application/json",
        )
        self.assertEqual(cancel.status_code, 200)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.status, PortalTurnStatus.CANCELLED)

        second_conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.owner,
            session_token="second-conversation",
        )
        second_turn = PortalTurn.objects.create(
            conversation=second_conversation,
            agent_profile=self.agent,
            status=PortalTurnStatus.STREAMING,
            run_after=timezone.now(),
            user_message="Still open",
            metadata={"source": "test"},
        )
        outsider_client = self.client_class()
        outsider_client.force_login(self.outsider)
        forbidden = outsider_client.post(
            reverse("api:chat-turns-cancel", args=[second_turn.id]),
            data=json.dumps({"conversation_id": str(second_conversation.id)}),
            content_type="application/json",
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_tool_approval_can_authorize_by_conversation_id(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("api:chat-portal-tools-approve"),
            data=json.dumps(
                {
                    "conversation_id": str(self.conversation.id),
                    "approval_id": str(self.approval.id),
                    "decision": "approve",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.status, ConversationToolApprovalStatus.APPROVED)

    def test_legacy_public_session_endpoints_are_deprecated(self) -> None:
        self.client.force_login(self.owner)

        list_response = self.client.post(
            reverse("api:chat-portal-sessions-list"),
            data=json.dumps({"session_tokens": [self.conversation.session_token]}),
            content_type="application/json",
        )
        create_response = self.client.post(
            reverse("api:chat-portal-sessions-create"),
            data=json.dumps({"business_slug": self.business.slug, "agent_slug": self.agent.slug}),
            content_type="application/json",
        )

        self.assertEqual(list_response.status_code, 410)
        self.assertEqual(create_response.status_code, 410)
