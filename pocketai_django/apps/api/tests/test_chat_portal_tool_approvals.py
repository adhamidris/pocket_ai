from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
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
from apps.conversations.models import AgentRun, AgentRunStatus, Conversation, ConversationToolApproval, ConversationToolApprovalStatus


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
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": True}},
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


class ChatPortalRunApprovalTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="runner@example.com", password="changeme123", first_name="Runner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Run Approval Corp",
            industry="Ops",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Ops Agent",
            status="active",
        )
        self.anchor = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-run-approval",
            metadata={"actor_user_id": str(self.user.id)},
        )
        self.execution = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-run-exec",
            metadata={
                "source": "agent_run",
                "anchor_conversation_id": str(self.anchor.id),
                "agent_run_id": "00000000-0000-0000-0000-000000000000",
                "actor_user_id": str(self.user.id),
            },
        )
        self.approval = ConversationToolApproval.objects.create(
            conversation=self.execution,
            connection=None,
            tool_name="email_send_draft",
            remote_tool_name="",
            status=ConversationToolApprovalStatus.PENDING,
            tool_call_id="",
            event_id="",
            input_payload={"draft_id": "draft_123", "email_account_id": "00000000-0000-0000-0000-000000000000"},
            metadata={"reason": "draft_plus_approval_default"},
        )
        self.run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            conversation=self.anchor,
            execution_conversation=self.execution,
            created_by=self.user,
            title="Email something",
            status=AgentRunStatus.WAITING_APPROVAL,
            run_spec_snapshot={"goal": "Email something"},
            metadata={
                "pending_approval_id": str(self.approval.id),
            },
        )

    def test_portal_agent_run_approval_approve(self) -> None:
        url = reverse("api:chat-portal-runs-approval")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "session_token": self.anchor.session_token,
                    "run_id": str(self.run.id),
                    "approval_id": str(self.approval.id),
                    "decision": "approve",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.status, ConversationToolApprovalStatus.APPROVED)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AgentRunStatus.QUEUED)
        self.assertNotIn("pending_approval_id", self.run.metadata)
        self.assertNotIn("resume", self.run.metadata)

    def test_portal_agent_run_approval_deny(self) -> None:
        url = reverse("api:chat-portal-runs-approval")
        response = self.client.post(
            url,
            data=json.dumps(
                {
                    "session_token": self.anchor.session_token,
                    "run_id": str(self.run.id),
                    "approval_id": str(self.approval.id),
                    "decision": "deny",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.status, ConversationToolApprovalStatus.DENIED)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AgentRunStatus.CANCELLED)
        self.assertNotIn("pending_approval_id", self.run.metadata)


class ChatPortalPendingToolExecutionTests(TestCase):
    """Tests for executing pending tools after approval."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(email="pending-tool@example.com", password="changeme123", first_name="Tester")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Pending Tool Corp",
            industry="Tech",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": True}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Test Agent",
            status="active",
        )
        self.anchor = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-pending-tool",
            metadata={"actor_user_id": str(self.user.id)},
        )
        self.execution = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-pending-exec",
            metadata={
                "source": "agent_run",
                "anchor_conversation_id": str(self.anchor.id),
                "agent_run_id": "00000000-0000-0000-0000-000000000001",
                "actor_user_id": str(self.user.id),
            },
        )
        self.approval = ConversationToolApproval.objects.create(
            conversation=self.execution,
            connection=None,
            tool_name="email_send_draft",
            remote_tool_name="",
            status=ConversationToolApprovalStatus.PENDING,
            tool_call_id="call_test_123",
            event_id="evt_test_123",
            input_payload={"draft_id": "draft_123", "email_account_id": "00000000-0000-0000-0000-000000000000"},
            metadata={"reason": "draft_plus_approval_default"},
        )
        # Create run WITH pending_tool_call in metadata
        self.run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            conversation=self.anchor,
            execution_conversation=self.execution,
            created_by=self.user,
            title="Email something with pending tool",
            status=AgentRunStatus.WAITING_APPROVAL,
            run_spec_snapshot={"goal": "Email something"},
            metadata={
                "pending_approval_id": str(self.approval.id),
                "pending_tool_call": {
                    "tool_name": "email_send_draft",
                    "tool_call_id": "call_test_123",
                    "arguments": {
                        "draft_id": "draft_123",
                        "email_account_id": "00000000-0000-0000-0000-000000000000",
                    },
                    "approval_id": str(self.approval.id),
                    "connection_id": None,
                    "remote_tool_name": "",
                    "event_id": "evt_test_123",
                },
            },
        )

    def test_pending_tool_call_preserved_after_approval(self) -> None:
        """Verify pending_tool_call stays in metadata after approval."""
        url = reverse("api:chat-portal-runs-approval")
        response = self.client.post(
            url,
            data=json.dumps({
                "session_token": self.anchor.session_token,
                "run_id": str(self.run.id),
                "approval_id": str(self.approval.id),
                "decision": "approve",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()

        # pending_approval_id should be removed
        self.assertNotIn("pending_approval_id", self.run.metadata)

        # pending_tool_call should be preserved for worker
        self.assertIn("pending_tool_call", self.run.metadata)
        self.assertEqual(
            self.run.metadata["pending_tool_call"]["tool_name"],
            "email_send_draft"
        )
        self.assertEqual(
            self.run.metadata["pending_tool_call"]["tool_call_id"],
            "call_test_123"
        )

    def test_pending_tool_call_cleared_on_deny(self) -> None:
        """Verify pending_tool_call is preserved on deny (run is cancelled anyway)."""
        url = reverse("api:chat-portal-runs-approval")
        response = self.client.post(
            url,
            data=json.dumps({
                "session_token": self.anchor.session_token,
                "run_id": str(self.run.id),
                "approval_id": str(self.approval.id),
                "decision": "deny",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.run.refresh_from_db()

        # Run should be cancelled
        self.assertEqual(self.run.status, AgentRunStatus.CANCELLED)

        # pending_approval_id should be removed
        self.assertNotIn("pending_approval_id", self.run.metadata)

        # pending_tool_call is still there (run is cancelled, no cleanup needed)
        self.assertIn("pending_tool_call", self.run.metadata)
