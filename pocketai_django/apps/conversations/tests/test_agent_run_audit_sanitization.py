from __future__ import annotations

import json

from django.test import SimpleTestCase

from apps.conversations.agent_run_processing import sanitize_tool_event_for_audit


class AgentRunAuditSanitizationTests(SimpleTestCase):
    def test_sanitize_tool_event_keeps_safe_email_search_input(self) -> None:
        event = {
            "event_id": "evt_search",
            "phase": "finished",
            "status": "ok",
            "tool_call_id": "call_search",
            "tool_name": "email_search",
            "kind": "email",
            "input": {"query": "is:unread", "limit": 25},
            "output": {"status": "ok", "message_ids": ["msg_1"], "thread_ids": ["thread_1"], "result_count": 1},
        }

        sanitized = sanitize_tool_event_for_audit(event)

        self.assertEqual(sanitized["input"], {"query": "is:unread", "limit": 25})
        self.assertEqual(sanitized["output"]["message_ids"], ["msg_1"])

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

    def test_sanitize_tool_event_keeps_save_memory_result_metadata(self) -> None:
        event = {
            "event_id": "evt_memory",
            "phase": "finished",
            "status": "ok",
            "tool_call_id": "call_memory",
            "tool_name": "save_memory",
            "kind": "mcp_internal",
            "input": {"content": "Customer prefers yearly billing.", "scope": "agent"},
            "output": {
                "tool": "save_memory",
                "status": "ok",
                "memory_id": "memory_123",
                "review_required": False,
            },
        }

        sanitized = sanitize_tool_event_for_audit(event)
        dump = json.dumps(sanitized)

        self.assertEqual(sanitized["output"]["memory_id"], "memory_123")
        self.assertFalse(sanitized["output"]["review_required"])
        self.assertNotIn("Customer prefers yearly billing", dump)

    def test_sanitize_tool_event_compacts_list_tasks_output(self) -> None:
        event = {
            "event_id": "evt_tasks",
            "phase": "finished",
            "status": "ok",
            "tool_call_id": "call_tasks",
            "tool_name": "list_tasks",
            "kind": "mcp_internal",
            "input": {"status": "all", "limit": 20},
            "output": {
                "tool": "list_tasks",
                "status": "ok",
                "tasks": [
                    {
                        "id": "task_1",
                        "agent_id": "agent_1",
                        "name": "Sales Email Monitor & Report",
                        "description": "sales " * 500,
                        "status": "paused",
                        "visibility": "initiator",
                        "trigger_type": "schedule",
                        "trigger_config": {"cron": "0 * * * *", "type": "cron", "timezone": "Africa/Cairo"},
                        "source_config": {"secret": "do-not-show"},
                        "instructions": {"goal": "Monitor the inbox and report to adham@example.com"},
                        "last_triggered_at": "2026-05-17T03:13:40.709151+00:00",
                    }
                ],
            },
        }

        sanitized = sanitize_tool_event_for_audit(event)
        dump = json.dumps(sanitized)

        self.assertEqual(sanitized["input"], {"status": "all", "limit": 20})
        self.assertEqual(sanitized["output"]["tasks_count"], 1)
        self.assertEqual(sanitized["output"]["tasks"][0]["name"], "Sales Email Monitor & Report")
        self.assertEqual(sanitized["output"]["tasks"][0]["trigger_config"]["timezone"], "Africa/Cairo")
        self.assertNotIn("description", sanitized["output"]["tasks"][0])
        self.assertNotIn("source_config", dump)
        self.assertNotIn("adham@example.com", dump)

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
