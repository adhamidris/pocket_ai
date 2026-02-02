from __future__ import annotations

from django.test import TestCase

from apps.conversations.models import ConversationToolApprovalStatus
from apps.mcp.orchestrator import McpOrchestratorService


class ToolApprovalBlockedPayloadTests(TestCase):
    def test_expired_approval_hint_guides_agentic_follow_up(self) -> None:
        payload = McpOrchestratorService._approval_blocked_payload(
            "initiate_phone_call",
            ConversationToolApprovalStatus.EXPIRED,
        )

        self.assertEqual(payload.get("status"), "blocked")
        self.assertEqual(payload.get("error_code"), "approval_timeout")

        hint = payload.get("hint")
        llm_hint = payload.get("llm_hint")
        self.assertIsInstance(hint, str)
        self.assertEqual(llm_hint, hint)

        normalized = hint.lower()
        self.assertIn("expired", normalized)
        self.assertIn("avoid repeating", normalized)
        self.assertNotIn("approve again", normalized)

