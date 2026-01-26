from __future__ import annotations

import json
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.mcp.orchestrator import McpOrchestratorService


class AgenticPromptCompactionTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        agent = mock.Mock()
        agent.business_profile = mock.Mock(metadata={})
        self.service = McpOrchestratorService(agent=agent, provider=None)

    def test_search_knowledge_compacts_agentic_results(self) -> None:
        payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "refs": [
                {
                    "id": "chunk-1",
                    "document_id": "upload-1",
                    "label": "Annual fee — Fees",
                    "kind": "text_anchor",
                    "type": "text",  # legacy enum retained for routing
                    "source": "Guide.pdf",
                    "score": 0.91,
                    "char_estimate": 1200,
                    "read_hint": {"mode": "full_page", "page": 1, "suggested_max_chars": 15000},
                }
            ],
            "total_found": 1,
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "search_knowledge",
            payload,
            max_snippets=4,
            snippet_content_chars=600,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )

        self.assertIn("refs", compact)
        self.assertNotIn("snippets", compact)
        self.assertEqual(compact["refs"][0]["id"], "chunk-1")
        self.assertIn("label", compact["refs"][0])
        self.assertEqual(compact["refs"][0]["read_hint"]["suggested_max_chars"], 15000)

    def test_read_document_compacts_agentic_contents(self) -> None:
        payload = {
            "tool": "read_document",
            "status": "ok",
            "contents": [
                {
                    "id": "chunk-1",
                    "title": "Fees",
                    "type": "text",
                    # Leading newlines matter for cursor continuation (prepend_sep).
                    "content": "\n\n" + ("x" * 5000),
                    "truncated": False,
                    "next_cursor": "cursor-1",
                }
            ],
            "read": [
                {
                    "id": "chunk-1",
                    "status": "full",
                    "chars": 5002,
                    "next_cursor": "cursor-1",
                    "artifact_id": "00000000-0000-0000-0000-000000000001",
                    "prompt_view": {"text": "preview"},
                }
            ],
            "deferred": [
                {
                    "id": "chunk-2",
                    "chars": 11279,
                    "reason": "exceeds_budget",
                    "suggested_max_chars": 12000,
                    "hint": "Read separately with max_chars=12000.",
                }
            ],
            "total_chars": 5000,
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "read_document",
            payload,
            max_snippets=2,
            snippet_content_chars=400,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )

        self.assertIn("contents", compact)
        self.assertNotIn("snippets", compact)
        self.assertEqual(compact["contents"][0]["id"], "chunk-1")
        self.assertTrue(compact["contents"][0]["content"].startswith("\n\n"))
        self.assertEqual(len(compact["contents"][0]["content"]), 5002)
        self.assertEqual(compact["contents"][0]["next_cursor"], "cursor-1")
        self.assertIn("deferred", compact)
        self.assertEqual(compact["deferred"][0]["id"], "chunk-2")
        self.assertEqual(compact["deferred"][0]["suggested_max_chars"], 12000)
        self.assertEqual(compact["read"][0]["next_cursor"], "cursor-1")
        self.assertEqual(compact["read"][0]["artifact_id"], "00000000-0000-0000-0000-000000000001")
        self.assertIn("prompt_view", compact["read"][0])

    @override_settings(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=500)
    def test_tool_message_truncation_keeps_content_preview(self) -> None:
        payload = {
            "tool": "read_document",
            "status": "ok",
            "contents": [
                {
                    "id": "chunk-1",
                    "title": "Fees",
                    "type": "text",
                    "content": "x" * 2000,
                    "truncated": False,
                }
            ],
            "total_chars": 2000,
        }

        message = json.dumps(payload, ensure_ascii=False)
        truncated = self.service._truncate_tool_message_for_prompt("read_document", message)

        self.assertLessEqual(len(truncated), 500)
        parsed = json.loads(truncated)
        self.assertEqual(parsed.get("tool"), "read_document")
        self.assertTrue(parsed.get("truncated"))
        self.assertTrue(parsed.get("prompt_compact"))
        self.assertIn("contents", parsed)
        self.assertIn("content", parsed["contents"][0])
        self.assertLess(len(parsed["contents"][0]["content"]), 2000)
