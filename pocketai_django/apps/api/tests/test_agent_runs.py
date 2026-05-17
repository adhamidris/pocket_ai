from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.agent_runs.models import AgentRun
from apps.assistants.models import CustomAssistant
from apps.automations.models import Automation, AutomationTriggerType
from apps.conversations.models import Conversation


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
        self.assertFalse(AgentRun.objects.filter(automation_id=assistant.id).exists())

        session_response = self.client.post(
            reverse("api:custom-assistant-sessions", args=[self.agent.id, assistant.id]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(session_response.status_code, 201)
        self.assertEqual(session_response.json()["session"]["customAssistantId"], str(assistant.id))

    def test_automation_crud_and_manual_run_use_automation_id(self) -> None:
        create_response = self.client.post(
            reverse("api:automations", args=[self.agent.id]),
            data=json.dumps(
                {
                    "name": "Daily Sales Summary",
                    "status": "active",
                    "triggerType": "schedule",
                    "triggerConfig": {"cron": "0 9 * * *"},
                    "instructions": {"goal": "Summarize sales daily."},
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(create_response.status_code, 201)
        automation_id = create_response.json()["automation"]["id"]
        automation = Automation.objects.get(id=uuid.UUID(automation_id))
        self.assertEqual(automation.trigger_type, AutomationTriggerType.SCHEDULE)

        run_response = self.client.post(
            reverse("api:automation-runs", args=[self.agent.id, automation.id]),
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(run_response.status_code, 201)
        self.assertEqual(run_response.json()["run"]["automationId"], automation_id)
        self.assertNotIn("workflowId", run_response.json()["run"])
        self.assertEqual(AgentRun.objects.get(id=uuid.UUID(run_response.json()["run"]["id"])).automation_id, automation.id)

    def test_removed_workflow_routes_are_not_registered(self) -> None:
        with self.assertRaises(NoReverseMatch):
            reverse("api:agent-workflows", args=[self.agent.id])
        with self.assertRaises(NoReverseMatch):
            reverse("api:agent-workflow-runs", args=[self.agent.id, uuid.uuid4()])
