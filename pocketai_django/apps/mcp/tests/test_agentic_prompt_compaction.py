from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

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
            "results": [
                {
                    "id": "chunk-1",
                    "read_id": "chunk-1",
                    "document_id": "upload-1",
                    "title": "Fees",
                    "type": "text",
                    "source": "Guide.pdf",
                    "preview": "Annual fee is listed in this document.",
                    "char_estimate": 1200,
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

        self.assertIn("results", compact)
        self.assertNotIn("snippets", compact)
        self.assertEqual(compact["results"][0]["id"], "chunk-1")
        self.assertIn("preview", compact["results"][0])

    def test_read_document_compacts_agentic_contents(self) -> None:
        payload = {
            "tool": "read_document",
            "status": "ok",
            "contents": [
                {
                    "id": "chunk-1",
                    "title": "Fees",
                    "type": "text",
                    "content": "x" * 5000,
                    "truncated": False,
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
        self.assertLessEqual(len(compact["contents"][0]["content"]), 400)

