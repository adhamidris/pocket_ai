from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import (
    AgentMcpToolSetting,
    BusinessProfile,
    McpConnection,
    McpConnectionApprovalMode,
    McpConnectionAuthType,
    McpConnectionStatus,
    McpToolOperationType,
    RegistrationSession,
)
from apps.accounts.models import AgentProfile
from apps.conversations.models import Conversation, ConversationToolApproval, ConversationToolApprovalStatus


User = get_user_model()


class ChatPortalToolApprovalTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="approver@example.com", password="changeme123", first_name="Approver")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Approval Corp",
            industry="Support",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Support Agent",
            status="active",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-approval",
        )
        self.connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="GitHub MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
        )

    def _create_approval(self, *, metadata: dict | None = None) -> ConversationToolApproval:
        return ConversationToolApproval.objects.create(
            conversation=self.conversation,
            connection=self.connection,
            tool_name="mcp_tool",
            remote_tool_name="create_issue",
            status=ConversationToolApprovalStatus.PENDING,
            tool_call_id="call_123",
            event_id="event_123",
            metadata=metadata or {},
        )

    def test_portal_tool_approval_approve(self) -> None:
        approval = self._create_approval()
        url = reverse("api:chat-portal-tools-approve")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "session_token": self.conversation.session_token,
                    "approval_id": str(approval.id),
                    "decision": "approve",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ConversationToolApprovalStatus.APPROVED)

    def test_portal_tool_approval_deny(self) -> None:
        approval = self._create_approval()
        url = reverse("api:chat-portal-tools-approve")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "session_token": self.conversation.session_token,
                    "approval_id": str(approval.id),
                    "decision": "deny",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        approval.refresh_from_db()
        self.assertEqual(approval.status, ConversationToolApprovalStatus.DENIED)

    @override_settings(PORTAL_ALLOW_MCP_TOOL_PREFERENCES=False)
    def test_portal_tool_approval_remember_saves_preference_when_authenticated(self) -> None:
        approval = self._create_approval(metadata={"operation_type": McpToolOperationType.READ})
        self.client.force_login(self.user)

        url = reverse("api:chat-portal-tools-approve")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "session_token": self.conversation.session_token,
                    "approval_id": str(approval.id),
                    "decision": "approve",
                    "remember": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload.get("preferenceSaved"))

        setting = AgentMcpToolSetting.objects.get(
            agent_profile=self.agent,
            connection=self.connection,
            tool_name="create_issue",
        )
        self.assertEqual(setting.approval_mode, McpConnectionApprovalMode.AUTO)
        self.assertEqual(setting.operation_type, McpToolOperationType.READ)

    @override_settings(PORTAL_ALLOW_MCP_TOOL_PREFERENCES=False)
    def test_portal_tool_approval_remember_ignored_for_anonymous(self) -> None:
        approval = self._create_approval(metadata={"operation_type": McpToolOperationType.READ})

        url = reverse("api:chat-portal-tools-approve")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "session_token": self.conversation.session_token,
                    "approval_id": str(approval.id),
                    "decision": "approve",
                    "remember": True,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload.get("preferenceSaved"))
        self.assertFalse(
            AgentMcpToolSetting.objects.filter(
                agent_profile=self.agent,
                connection=self.connection,
                tool_name="create_issue",
            ).exists()
        )
