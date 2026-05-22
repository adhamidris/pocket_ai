from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.agent_runs.models import AgentRun
from apps.assistants.models import CustomAssistant
from apps.agentic_tasks.models import AgenticTask
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender


User = get_user_model()


class AgentRunsApiTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="owner@example.com", password="changeme123", first_name="Owner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme Co",
            industry="Retail",
            status="active",
        )
        self.agent = AgentProfile.objects.create(business_profile=self.business, user=self.user, name="Ops Agent")
        self.client.force_login(self.user)

    def test_custom_assistant_crud_and_session_creation_use_custom_assistant_id(self) -> None:
        response = self.client.post(
            reverse("api:custom-assistants", args=[self.agent.id]),
            data=json.dumps(
                {
                    "name": "Proposal Assistant",
                    "status": "active",
                    "instructions": {"goal": "Help with proposal writing."},
                    "createSession": True,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()["customAssistant"]
        assistant = CustomAssistant.objects.get(id=uuid.UUID(payload["id"]))
        self.assertEqual(payload["name"], "Proposal Assistant")
        self.assertEqual(payload["sessionCount"], 1)
        self.assertEqual(Conversation.objects.get(custom_assistant=assistant).custom_assistant_id, assistant.id)
        self.assertFalse(AgentRun.objects.filter(agentic_task_id=assistant.id).exists())

        session_response = self.client.post(
            reverse("api:custom-assistant-sessions", args=[self.agent.id, assistant.id]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(session_response.status_code, 201)
        self.assertEqual(session_response.json()["session"]["customAssistantId"], str(assistant.id))

    def test_agentic_task_crud_and_manual_run_use_agentic_task_id(self) -> None:
        create_response = self.client.post(
            reverse("api:agentic-tasks", args=[self.agent.id]),
            data=json.dumps(
                {
                    "name": "Daily Sales Summary",
                    "status": "active",
                    "scheduleEnabled": True,
                    "scheduleConfig": {"cron": "0 9 * * *"},
                    "instructions": {"goal": "Summarize sales daily."},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(create_response.status_code, 201)
        agentic_task_id = create_response.json()["agenticTask"]["id"]
        agentic_task = AgenticTask.objects.get(id=uuid.UUID(agentic_task_id))
        self.assertTrue(agentic_task.schedule_enabled)
        self.assertEqual(agentic_task.schedule_config["cron"], "0 9 * * *")

        run_response = self.client.post(
            reverse("api:agentic-task-runs", args=[self.agent.id, agentic_task.id]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(run_response.status_code, 201)
        self.assertEqual(run_response.json()["run"]["agenticTaskId"], agentic_task_id)
        self.assertNotIn("workflowId", run_response.json()["run"])
        self.assertEqual(AgentRun.objects.get(id=uuid.UUID(run_response.json()["run"]["id"])).agentic_task_id, agentic_task.id)

    def test_removed_workflow_routes_are_not_registered(self) -> None:
        with self.assertRaises(NoReverseMatch):
            reverse("api:agent-workflows", args=[self.agent.id])
        with self.assertRaises(NoReverseMatch):
            reverse("api:agent-workflow-runs", args=[self.agent.id, uuid.uuid4()])

    def test_portal_agentic_task_approval_activates_draft_and_patches_block(self) -> None:
        conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.user,
            session_token="task-approval-session",
            metadata={},
        )
        task = AgenticTask.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Sales Sheet Tracker",
            status="draft",
            schedule_enabled=True,
            schedule_config={"cron": "*/5 * * * *"},
            instructions={"goal": "Track sales stage changes."},
        )
        message = ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.AI,
            body="Draft created.",
            content_blocks=[
                {
                    "block_id": "draft_tool_1",
                    "type": "tool_use",
                    "payload": {
                        "event_id": "draft_tool_event_1",
                        "phase": "finished",
                        "status": "ok",
                        "tool_name": "draft_agentic_task",
                        "output_preview": {
                            "tool": "draft_agentic_task",
                            "status": "ok",
                            "activation_required": True,
                            "agentic_task": {"id": str(task.id), "name": task.name},
                        },
                    },
                }
            ],
        )

        response = self.client.post(
            reverse("api:chat-portal-agentic-tasks-approval"),
            data=json.dumps(
                {
                    "sessionToken": conversation.session_token,
                    "agenticTaskId": str(task.id),
                    "messageId": str(message.id),
                    "blockId": "draft_tool_1",
                    "action": "approve",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "active")
        self.assertIsNotNone(task.next_trigger_at)
        message.refresh_from_db()
        output_preview = message.content_blocks[0]["payload"]["output_preview"]
        self.assertEqual(output_preview["approval_status"], "approved")
        self.assertEqual(output_preview["agentic_task"]["status"], "active")

    def test_portal_agentic_task_approval_allows_manual_draft_without_cron(self) -> None:
        conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.user,
            session_token="manual-task-approval-session",
            metadata={},
        )
        task = AgenticTask.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Manual Pipeline Review",
            status="draft",
            schedule_enabled=True,
            schedule_config={"type": "cron", "timezone": "UTC"},
            instructions={"goal": "Review the pipeline when asked."},
        )

        response = self.client.post(
            reverse("api:chat-portal-agentic-tasks-approval"),
            data=json.dumps(
                {
                    "sessionToken": conversation.session_token,
                    "agenticTaskId": str(task.id),
                    "action": "approve",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "active")
        self.assertFalse(task.schedule_enabled)
        self.assertEqual(task.schedule_config, {})
        self.assertIsNone(task.next_trigger_at)
        self.assertFalse(response.json()["agenticTask"]["scheduleEnabled"])

    def test_portal_agentic_task_manual_run_with_message_uses_task_conversation(self) -> None:
        portal_conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.user,
            session_token="task-thread-session",
            metadata={},
        )
        task = AgenticTask.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Manual Pipeline Review",
            status="active",
            schedule_enabled=False,
            schedule_config={},
            instructions={"goal": "Review the pipeline when asked."},
        )

        response = self.client.post(
            reverse("api:chat-portal-agentic-tasks-run"),
            data=json.dumps(
                {
                    "sessionToken": portal_conversation.session_token,
                    "agenticTaskId": str(task.id),
                    "message": "Review the latest sales sheet.",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        task.refresh_from_db()
        self.assertIsNotNone(task.active_conversation_id)
        run = AgentRun.objects.get(id=uuid.UUID(response.json()["run"]["id"]))
        self.assertEqual(run.agentic_task_id, task.id)
        self.assertEqual(run.execution_conversation_id, task.active_conversation_id)
        self.assertEqual(run.conversation_id, task.active_conversation_id)
        message = ConversationMessage.objects.get(conversation_id=task.active_conversation_id, sender=ConversationSender.CUSTOMER)
        self.assertEqual(message.body, "Review the latest sales sheet.")
        self.assertEqual(response.json()["agenticTask"]["messages"][-1]["body"], "Review the latest sales sheet.")
