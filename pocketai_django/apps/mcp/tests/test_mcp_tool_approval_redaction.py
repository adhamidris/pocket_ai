from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import BusinessProfile, McpConnection, RegistrationSession, User
from apps.conversations.models import Conversation, ConversationToolApproval, ConversationToolApprovalStatus
from apps.mcp.orchestrator import McpOrchestratorService
from apps.mcp.redaction import REDACTED_VALUE
from core.tenancy import tenant_context


class McpToolApprovalRedactionTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-approval-redact@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Redaction Bank",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="Postgres MCP",
            server_url="https://example.com/mcp",
            source_type="marketplace",
            marketplace_key="postgres",
            status="enabled",
            auth_type="none",
        )
        self.connection.credentials = {
            "setup_fields": {
                "connection_string": "postgresql://user:pass@host:5432/db",
                "store_url": "https://shop.example",
            }
        }
        self.connection.save(update_fields=["credentials_encrypted", "credentials_key_version", "credentials_last_rotated_at"])
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="session-redaction",
        )

    def tearDown(self) -> None:
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_approval_input_payload_redacts_setup_fields(self) -> None:
        service = McpOrchestratorService.__new__(McpOrchestratorService)
        approval = service._get_or_create_tool_approval(
            conversation=self.conversation,
            connection=self.connection,
            tool_name="mcp_tool",
            remote_tool_name="query",
            tool_call_id="call_1",
            tool_event_id="evt_1",
            arguments={
                "connection_string": "postgresql://user:pass@host:5432/db",
                "store_url": "https://shop.example",
                "query": "select 1",
                "token": "super-secret",
                "prompt_tokens": 12,
            },
            approval_requirement={"approval_mode": "approve_all", "operation_type": "write", "reason": "writes"},
        )

        self.assertEqual(approval.input_payload.get("connection_string"), REDACTED_VALUE)
        self.assertEqual(approval.input_payload.get("store_url"), REDACTED_VALUE)
        self.assertEqual(approval.input_payload.get("token"), REDACTED_VALUE)
        self.assertEqual(approval.input_payload.get("prompt_tokens"), 12)
        self.assertEqual(approval.input_payload.get("query"), "select 1")

    def test_maybe_request_tool_approval_requires_fresh_confirmation_per_call(self) -> None:
        ConversationToolApproval.objects.create(
            conversation=self.conversation,
            connection=self.connection,
            tool_name="mcp_tool",
            remote_tool_name="query",
            tool_call_id="call_existing",
            event_id="evt_existing",
            status=ConversationToolApprovalStatus.APPROVED,
            resolved_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=5),
            input_payload={"query": "select 1", "connection_string": REDACTED_VALUE},
            metadata={"approval_mode": "approve_all", "operation_type": "write", "reason": "writes"},
        )

        service = McpOrchestratorService.__new__(McpOrchestratorService)
        approved, approval, result = service._maybe_request_tool_approval(
            conversation=self.conversation,
            connection=self.connection,
            tool_name="mcp_tool",
            remote_tool_name="query",
            tool_call_id="call_new",
            tool_event_id="evt_new",
            arguments={"query": "select 1", "connection_string": "postgresql://secret"},
            approval_requirement={
                "requires_approval": True,
                "approval_mode": "approve_all",
                "operation_type": "write",
                "reason": "writes",
            },
            on_tool_event=None,
            wait_for_approval=False,
        )

        self.assertFalse(approved)
        self.assertIsNotNone(approval)
        self.assertEqual(getattr(approval, "status", None), ConversationToolApprovalStatus.PENDING)
        self.assertIsInstance(result, dict)
        self.assertEqual((result or {}).get("status"), "pending_approval")
        self.assertEqual(
            ConversationToolApproval.objects.filter(
                conversation=self.conversation,
                tool_name="mcp_tool",
                remote_tool_name="query",
                status=ConversationToolApprovalStatus.PENDING,
            ).count(),
            1,
        )

    def test_maybe_request_email_approval_requires_fresh_confirmation_per_call(self) -> None:
        ConversationToolApproval.objects.create(
            conversation=self.conversation,
            connection=None,
            tool_name="email_send_draft",
            remote_tool_name="",
            tool_call_id="email_existing",
            event_id="email_evt_existing",
            status=ConversationToolApprovalStatus.APPROVED,
            resolved_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=5),
            input_payload={"to": "ops@example.com", "subject": "Status", "body_text": REDACTED_VALUE},
            metadata={"approval_mode": "email_send", "operation_type": "write", "reason": "email_send"},
        )

        service = McpOrchestratorService.__new__(McpOrchestratorService)
        approved, approval, result = service._maybe_request_email_tool_approval(
            conversation=self.conversation,
            tool_name="email_send_draft",
            tool_call_id="email_new",
            tool_event_id="email_evt_new",
            arguments={"to": "ops@example.com", "subject": "Status", "body_text": "Confidential"},
            reason="email_send",
            on_tool_event=None,
            wait_for_approval=False,
        )

        self.assertFalse(approved)
        self.assertIsNotNone(approval)
        self.assertEqual(getattr(approval, "status", None), ConversationToolApprovalStatus.PENDING)
        self.assertIsInstance(result, dict)
        self.assertEqual((result or {}).get("status"), "pending_approval")
        self.assertEqual(
            ConversationToolApproval.objects.filter(
                conversation=self.conversation,
                tool_name="email_send_draft",
                remote_tool_name="",
                status=ConversationToolApprovalStatus.PENDING,
            ).count(),
            1,
        )
