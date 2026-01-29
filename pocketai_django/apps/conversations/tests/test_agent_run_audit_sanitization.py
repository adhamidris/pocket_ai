from __future__ import annotations

import json

from django.test import SimpleTestCase

from apps.conversations.agent_run_processing import sanitize_tool_event_for_audit


class AgentRunAuditSanitizationTests(SimpleTestCase):
    def test_sanitize_tool_event_redacts_input_values(self) -> None:
        event = {
            "event_id": "evt_1",
            "phase": "approval_requested",
            "status": "pending_approval",
            "tool_call_id": "call_1",
            "tool_name": "email_send_draft",
            "kind": "email",
            "input": {"to": ["alice@example.com"], "body_text": "Hello Alice"},
            "approval": {"id": "approval_1", "status": "pending", "reason": "Send email to alice@example.com"},
        }
        sanitized = sanitize_tool_event_for_audit(event)
        dump = json.dumps(sanitized)
        self.assertIn('"redacted": true', dump.lower())
        self.assertIn("to", dump)
        self.assertNotIn("alice@example.com", dump)
        self.assertNotIn("Hello Alice", dump)

    def test_sanitize_tool_event_drops_prompt_view(self) -> None:
        event = {
            "event_id": "evt_2",
            "phase": "finished",
            "status": "ok",
            "tool_call_id": "call_2",
            "tool_name": "mcp_call_tool",
            "kind": "mcp_remote",
            "output": {
                "tool": "mcp_call_tool",
                "status": "ok",
                "artifact_id": "artifact_1",
                "prompt_view": {"text": "super secret"},
            },
        }
        sanitized = sanitize_tool_event_for_audit(event)
        dump = json.dumps(sanitized)
        self.assertIn("artifact_1", dump)
        self.assertNotIn("prompt_view", dump)
        self.assertNotIn("super secret", dump)

