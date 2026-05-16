from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import AssistantWorkflow, AssistantWorkflowKind, AssistantWorkflowTriggerType


User = get_user_model()


class AgentsDashboardTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="agents-owner@example.com", password="changeme123")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Acme",
            industry="Services",
            status="active",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Mona",
            role="Operations",
            agent_type=AgentProfile.AgentTypeChoices.MAIN,
        )

    def test_agents_dashboard_redirects_to_custom_assistants(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard-agents"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("frontend:dashboard-custom-assistants"))

    def test_custom_assistants_dashboard_replaces_agent_card(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard-custom-assistants"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Custom Assistants", content)
        self.assertIn("Create assistant", content)
        self.assertIn('data-assistant-drawer aria-hidden="true"', content)
        self.assertIn('data-custom-assistants-page', content)
        self.assertNotIn("data-agent-panel", content)
        self.assertNotIn('data-agent-tab="tasks"', content)
        self.assertNotIn("Manage the main agent", content)

    def test_automations_dashboard_is_separate_from_custom_assistants(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard-automations"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Automations", content)
        self.assertIn("Create automation", content)
        self.assertIn('data-automation-drawer aria-hidden="true"', content)
        self.assertIn("Run history", content)
        self.assertIn('data-automations-page', content)
        self.assertNotIn("data-agent-panel", content)

    def test_product_api_filters_custom_assistants_and_automations(self) -> None:
        manual = AssistantWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Proposal Assistant",
            trigger_type=AssistantWorkflowTriggerType.MANUAL,
        )
        automation = AssistantWorkflow.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            created_by=self.user,
            name="Daily Digest",
            trigger_type=AssistantWorkflowTriggerType.SCHEDULE,
        )
        self.client.force_login(self.user)

        assistants = self.client.get(
            reverse("api:custom-assistants", args=[self.agent.id]),
        ).json()["workflows"]
        automations = self.client.get(
            reverse("api:automations", args=[self.agent.id]),
        ).json()["workflows"]

        manual.refresh_from_db()
        automation.refresh_from_db()
        self.assertEqual(manual.kind, AssistantWorkflowKind.CUSTOM_ASSISTANT)
        self.assertEqual(automation.kind, AssistantWorkflowKind.AUTOMATION)
        self.assertEqual([item["id"] for item in assistants], [str(manual.id)])
        self.assertEqual([item["id"] for item in automations], [str(automation.id)])

    def test_connectors_dashboard_unifies_native_and_mcp_surfaces(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard-connectors"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Connectors", content)
        self.assertIn("Native integrations", content)
        self.assertIn("MCP marketplace", content)
        self.assertIn("data-mcp-connection-list", content)
