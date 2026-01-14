from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext


class AgenticReadDocumentContractTests(SimpleTestCase):
    def test_read_document_agentic_wrapper_returns_contents_without_snippets(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)

        fake_snippet = {
            "id": "snippet-1",
            "chunk_id": "chunk-1",
            "title": "Fees",
            "public_label": "Fees",
            "content": "Annual fee is 100 EGP.",
            "is_table_chunk": False,
        }

        with mock.patch.object(
            tools,
            "_read_document_handler",
            return_value={"tool": "read_document", "status": "ok", "snippets": [fake_snippet]},
        ):
            result = tools._read_document_agentic_wrapper(
                {"ids": ["chunk-1"], "max_chars": 2000},
                conversation,
                context,
            )

        self.assertEqual(result["tool"], "read_document")
        self.assertIn(result["status"], {"ok", "partial"})
        self.assertIn("contents", result)
        self.assertNotIn("snippets", result)
        self.assertTrue(result["contents"])

