from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import (
    BusinessProfile,
    McpConnectionApprovalMode,
    McpToolOperationType,
    RegistrationSession,
    User,
)
from apps.mcp.models import (
    McpConnection,
    McpConnectionToolSetting,
)
from apps.mcp.connectors import get_tool_approval_requirement
from core.tenancy import tenant_context


class McpToolApprovalInferenceTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-approval@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="MCP Bank",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
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

    def test_unknown_tool_infers_read_for_search_list_get(self) -> None:
        requirement = get_tool_approval_requirement(self.connection, "search_repositories")
        self.assertFalse(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.READ)

        requirement = get_tool_approval_requirement(self.connection, "list_releases")
        self.assertFalse(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.READ)

        requirement = get_tool_approval_requirement(self.connection, "get_me")
        self.assertFalse(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.READ)

    def test_unknown_tool_infers_write_for_create_update_delete(self) -> None:
        requirement = get_tool_approval_requirement(self.connection, "create_issue")
        self.assertTrue(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.WRITE)

        requirement = get_tool_approval_requirement(self.connection, "delete_branch")
        self.assertTrue(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.WRITE)

        requirement = get_tool_approval_requirement(self.connection, "update_file")
        self.assertTrue(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.WRITE)

    def test_explicit_tool_setting_overrides_inference(self) -> None:
        McpConnectionToolSetting.objects.create(
            connection=self.connection,
            tool_name="search_repositories",
            operation_type=McpToolOperationType.WRITE,
        )
        requirement = get_tool_approval_requirement(self.connection, "search_repositories")
        self.assertTrue(requirement.get("requires_approval"))
        self.assertEqual(requirement.get("operation_type"), McpToolOperationType.WRITE)

