from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext


class AgenticReadKnowledgeContractTests(SimpleTestCase):
    def test_read_knowledge_agentic_wrapper_routes_refs_to_v2(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)

        ref_id = "00000000-0000-0000-0000-000000000001"
        fake_result = {
            "tool": "read_knowledge",
            "status": "ok",
            "evidence": [{"id": ref_id, "type": "text", "payload": {"type": "text", "text": "ok"}}],
        }

        with mock.patch.object(tools, "_agentic_read_v2_handler", return_value=fake_result) as v2_mock:
            result = tools._read_knowledge_agentic_wrapper(
                {"refs": [{"id": ref_id}], "max_chars": 2000},
                conversation,
                context,
            )

        self.assertEqual(result["tool"], "read_knowledge")
        self.assertEqual(result["status"], "ok")
        v2_mock.assert_called_once()
        called_args = v2_mock.call_args.args[0]
        self.assertEqual(called_args["items"], [{"id": ref_id}])
        self.assertEqual(called_args["max_chars"], 2000)

    def test_read_knowledge_agentic_wrapper_rejects_unsupported_parameters(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)

        ref_id = "00000000-0000-0000-0000-000000000001"
        result = tools._read_knowledge_agentic_wrapper(
            {"refs": [{"id": ref_id}], "max_chars": 2000, "page": 1},
            conversation,
            context,
        )
        self.assertEqual(result["tool"], "read_knowledge")
        self.assertEqual(result["status"], "constraint_error")
        self.assertEqual(result["error_code"], "unsupported_parameters")
        self.assertIn("page", result.get("unsupported_fields") or [])

    @override_settings(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=1000, MCP_READ_DOCUMENT_MAX_CHARS_MARGIN=0)
    def test_agentic_read_v2_rejects_max_chars_over_budget(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)

        ref_id = "00000000-0000-0000-0000-000000000001"
        result = tools._agentic_read_v2_handler(
            {"items": [{"id": ref_id}], "max_chars": 50_000},
            conversation,
            context,
        )

        self.assertEqual(result["tool"], "read_knowledge")
        self.assertEqual(result["status"], "constraint_error")
        self.assertEqual(result["error_code"], "max_chars_exceeded")
        self.assertEqual(result.get("max_chars_allowed"), 1000)
