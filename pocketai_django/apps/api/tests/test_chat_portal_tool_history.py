from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import (
    BusinessProfile,
    McpConnectionAuthType,
    McpConnectionStatus,
    RegistrationSession,
)
from apps.mcp.models import McpConnection
from apps.conversations.models import (
    Conversation,
    ConversationMessage,
    ConversationSender,
    ConversationToolApproval,
    ConversationToolApprovalStatus,
)


User = get_user_model()


class ChatPortalToolHistoryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email="history@example.com", password="changeme123", first_name="History")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="History Corp",
            industry="Support",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="session-history",
        )
        self.connection = McpConnection.objects.create(
            business_profile=self.business,
            created_by=self.user,
            name="GitHub MCP",
            server_url="https://example.com/mcp",
            status=McpConnectionStatus.ENABLED,
            auth_type=McpConnectionAuthType.NONE,
        )

    def test_tool_history_returns_approvals_and_tool_events(self) -> None:
        approval = ConversationToolApproval.objects.create(
            conversation=self.conversation,
            connection=self.connection,
            tool_name="mcp_tool",
            remote_tool_name="search_repositories",
            status=ConversationToolApprovalStatus.APPROVED,
            tool_call_id="call_123",
            event_id="event_123",
        )
        ConversationMessage.objects.create(
            conversation=self.conversation,
            sender=ConversationSender.AI,
            body="Tool call complete.",
            sent_at=timezone.now(),
            content_blocks=[
                {
                    "block_id": "blk_tool_123",
                    "type": "tool_use",
                    "created_at": timezone.now().isoformat(),
                    "payload": {
                        "event_id": "event_123",
                        "phase": "finished",
                        "status": "ok",
                        "tool_name": "mcp_tool",
                        "duration_ms": 250,
                        "remote": {"connection_name": "GitHub MCP", "remote_tool": "search_repositories"},
                    },
                }
            ],
        )

        url = reverse("api:chat-portal-tools-history")
        response = self.client.post(
            url,
            data=json.dumps({"session_token": self.conversation.session_token}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        history = payload.get("history") or {}
        approvals = history.get("approvals") or []
        tool_events = history.get("toolEvents") or []
        self.assertTrue(any(item.get("id") == str(approval.id) for item in approvals))
        self.assertTrue(any(item.get("event_id") == "event_123" for item in tool_events))
