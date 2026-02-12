from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    McpConnectionApprovalMode,
    McpToolOperationType,
    RegistrationSession,
    User,
)
from apps.mcp.models import (
    AgentMcpToolSetting,
    McpConnection,
)
from apps.mcp.connectors import get_tool_approval_requirement
from core.tenancy import tenant_context


class McpToolApprovalAgentSettingsTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-agent-settings@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="MCP Bank",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Agent",
            status="active",
        )
        self.connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="GitHub",
            server_url="https://example.com/mcp",
            default_approval_mode=McpConnectionApprovalMode.APPROVE_WRITES,
        )

    def tearDown(self) -> None:
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_agent_default_approval_mode_overrides_connection_default(self) -> None:
        self.agent.mcp_default_approval_mode = McpConnectionApprovalMode.APPROVE_ALL
        self.agent.save(update_fields=["mcp_default_approval_mode"])

        requirement = get_tool_approval_requirement(self.connection, "search_repositories", agent=self.agent)
        self.assertTrue(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("approval_mode"), McpConnectionApprovalMode.APPROVE_ALL)
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.READ)

    def test_agent_tool_setting_overrides_agent_default(self) -> None:
        self.agent.mcp_default_approval_mode = McpConnectionApprovalMode.APPROVE_ALL
        self.agent.save(update_fields=["mcp_default_approval_mode"])

        AgentMcpToolSetting.objects.create(
            agent_profile=self.agent,
            connection=self.connection,
            tool_name="search_repositories",
            approval_mode=McpConnectionApprovalMode.AUTO,
            operation_type=McpToolOperationType.READ,
        )

        requirement = get_tool_approval_requirement(self.connection, "search_repositories", agent=self.agent)
        self.assertFalse(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("approval_mode"), McpConnectionApprovalMode.AUTO)

