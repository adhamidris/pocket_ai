from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession


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
        )

    def test_agents_dashboard_renders_workforce_tabs(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard-agents"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Create department", content)
        self.assertIn("Create agent", content)
        self.assertIn('data-agent-tab="tasks"', content)
        self.assertIn('data-agent-tab="runs"', content)
        self.assertIn('data-agent-tab="memory"', content)
        self.assertIn('data-agent-tab="permissions"', content)
        self.assertIn("Task processing is not active", content)
        self.assertNotIn("Escalation on", content)
        self.assertNotIn("No KPIs selected", content)
        self.assertNotIn("data-subagents-enabled", content)

    def test_connectors_dashboard_unifies_native_and_mcp_surfaces(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(reverse("frontend:dashboard-connectors"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Connectors", content)
        self.assertIn("Native integrations", content)
        self.assertIn("MCP marketplace", content)
        self.assertIn("data-mcp-connection-list", content)
