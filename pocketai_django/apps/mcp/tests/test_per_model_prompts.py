"""
Tests for per-model prompt templates, repeat-read detection, and MCP connection gating.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext
from apps.mcp.schemas.agentic_prompts import (
    DEEPSEEK_CHAT_SYSTEM_PROMPT,
    OPENAI_CHAT_SYSTEM_PROMPT,
    _select_template,
    build_model_specific_prompt,
)


# ---------------------------------------------------------------------------
# _select_template tests
# ---------------------------------------------------------------------------

class SelectTemplateTests(SimpleTestCase):
    def test_deepseek_chat(self) -> None:
        self.assertEqual(_select_template("deepseek-chat"), "deepseek_chat")

    def test_deepseek_reasoner(self) -> None:
        self.assertEqual(_select_template("deepseek-reasoner"), "deepseek_reasoner")

    def test_gpt_4o(self) -> None:
        self.assertEqual(_select_template("gpt-4o"), "openai_chat")

    def test_gpt_4o_mini(self) -> None:
        self.assertEqual(_select_template("gpt-4o-mini"), "openai_chat")

    def test_o3_mini(self) -> None:
        self.assertEqual(_select_template("o3-mini"), "openai_reasoning")

    def test_o1(self) -> None:
        self.assertEqual(_select_template("o1"), "openai_reasoning")

    def test_unknown_model(self) -> None:
        self.assertEqual(_select_template("unknown-model"), "default")

    def test_none_model(self) -> None:
        self.assertEqual(_select_template(None), "default")

    def test_empty_string(self) -> None:
        self.assertEqual(_select_template(""), "default")


# ---------------------------------------------------------------------------
# Template content assertions
# ---------------------------------------------------------------------------

class TemplateContentTests(SimpleTestCase):
    def test_deepseek_chat_contains_never_reread(self) -> None:
        self.assertIn("NEVER re-read", DEEPSEEK_CHAT_SYSTEM_PROMPT)

    def test_openai_chat_contains_search_before_answering(self) -> None:
        self.assertIn("Always search the knowledge base", OPENAI_CHAT_SYSTEM_PROMPT)

    def test_deepseek_chat_template_renders(self) -> None:
        agent = mock.Mock()
        agent.name = "TestBot"
        prompt = build_model_specific_prompt(
            agent,
            model_id="deepseek-chat",
            business_name="Acme Corp",
        )
        self.assertIn("TestBot", prompt)
        self.assertIn("Acme Corp", prompt)
        self.assertIn("NEVER re-read", prompt)
        self.assertIn("present_scope_clarification", prompt)
        self.assertIn("clarification_ui_mode=mcq", prompt)

    def test_default_template_renders(self) -> None:
        agent = mock.Mock()
        agent.name = "TestBot"
        prompt = build_model_specific_prompt(
            agent,
            model_id="unknown-model",
            business_name="Acme Corp",
        )
        self.assertIn("TestBot", prompt)
        self.assertIn("System Contract", prompt)
        self.assertIn("present_scope_clarification", prompt)
        self.assertIn("clarification_ui_mode=mcq", prompt)

    def test_default_template_prefers_read_after_search_for_factual_answers(self) -> None:
        agent = mock.Mock()
        agent.name = "TestBot"
        prompt = build_model_specific_prompt(
            agent,
            model_id="unknown-model",
            business_name="Acme Corp",
        )
        self.assertIn("For factual business questions, call `read_knowledge` once", prompt)
        self.assertIn("Use preview-only answers only for existence/navigation questions", prompt)

    def test_openai_chat_template_prefers_single_read_pass_for_factual_answers(self) -> None:
        agent = mock.Mock()
        agent.name = "TestBot"
        prompt = build_model_specific_prompt(
            agent,
            model_id="gpt-4o",
            business_name="Acme Corp",
        )
        self.assertIn("For factual business questions, call `read_knowledge` once", prompt)
        self.assertIn("present_scope_clarification", prompt)
        self.assertIn("clarification_ui_mode=mcq", prompt)

    @override_settings(MCP_SEARCH_MAX_QUERY_VARIANTS=1)
    def test_prompt_uses_single_variant_hint_when_limit_is_one(self) -> None:
        agent = mock.Mock()
        agent.name = "TestBot"
        prompt = build_model_specific_prompt(
            agent,
            model_id="unknown-model",
            business_name="Acme Corp",
        )
        self.assertIn("use up to 1 variant/sub-question.", prompt)

    @override_settings(MCP_SEARCH_MAX_QUERY_VARIANTS=3)
    def test_prompt_uses_configured_variant_limit_for_workflow_step(self) -> None:
        agent = mock.Mock()
        agent.name = "TestBot"
        prompt = build_model_specific_prompt(
            agent,
            model_id="deepseek-chat",
            business_name="Acme Corp",
        )
        self.assertIn("with up to 3 query variants.", prompt)

    @staticmethod
    def _search_queries_schema_description() -> str:
        tool_def = next(
            schema
            for schema in tools.get_tool_definitions()
            if schema.get("function", {}).get("name") == "search_knowledge"
        )
        return (
            tool_def.get("function", {})
            .get("parameters", {})
            .get("properties", {})
            .get("queries", {})
            .get("description", "")
        )

    @override_settings(MCP_SEARCH_MAX_QUERY_VARIANTS=1)
    def test_tool_schema_uses_single_variant_hint_when_limit_is_one(self) -> None:
        self.assertEqual(
            self._search_queries_schema_description(),
            "List of search queries. Use up to 1 short, specific variant.",
        )

    @override_settings(MCP_SEARCH_MAX_QUERY_VARIANTS=5)
    def test_tool_schema_uses_configured_variant_hint_when_limit_is_many(self) -> None:
        self.assertEqual(
            self._search_queries_schema_description(),
            "List of search queries. Use up to 5 short, specific variants.",
        )


# ---------------------------------------------------------------------------
# MCP connection gating in build_system_message
# ---------------------------------------------------------------------------

class McpConnectionGatingTests(SimpleTestCase):
    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_no_mcp_connections_omits_gateway_rules(self) -> None:
        from apps.mcp.prompts import build_system_message

        agent = mock.Mock()
        agent.name = "TestBot"
        agent.tone = "professional"
        agent.role = "Assistant"
        business_profile = mock.Mock()
        business_profile.name = "TestBiz"

        feature_mock = mock.Mock()
        feature_mock.rag_agentic_mode = True

        with mock.patch(
            "apps.mcp.prompts.FeatureFlagService.snapshot", return_value=feature_mock
        ):
            result = build_system_message(
                agent,
                business_name="TestBiz",
                business_profile=business_profile,
                has_mcp_connections=False,
            )
        self.assertNotIn("mcp_search_tools", result)

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_has_mcp_connections_includes_gateway_rules(self) -> None:
        from apps.mcp.prompts import build_system_message

        agent = mock.Mock()
        agent.name = "TestBot"
        agent.tone = "professional"
        agent.role = "Assistant"
        business_profile = mock.Mock()
        business_profile.name = "TestBiz"

        feature_mock = mock.Mock()
        feature_mock.rag_agentic_mode = True

        with mock.patch(
            "apps.mcp.prompts.FeatureFlagService.snapshot", return_value=feature_mock
        ):
            result = build_system_message(
                agent,
                business_name="TestBiz",
                business_profile=business_profile,
                has_mcp_connections=True,
            )
        self.assertIn("mcp_search_tools", result)


# ---------------------------------------------------------------------------
# Repeat-read detection tests
# ---------------------------------------------------------------------------

class RepeatReadDetectionTests(SimpleTestCase):
    @override_settings(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=12000, MCP_READ_DOCUMENT_MAX_CHARS_MARGIN=800)
    def test_repeat_read_same_ref_returns_already_read(self) -> None:
        """Re-reading the same ref ID without a cursor in the same turn should be blocked."""
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=50_000)
        ref_id = "00000000-0000-0000-0000-000000000001"

        # Simulate that this ref was already read this turn.
        context.read_ref_ids_this_turn.add(ref_id)

        result = tools._agentic_read_v2_handler(
            {"items": [{"id": ref_id}], "max_chars": 2000},
            conversation,
            context,
        )

        self.assertEqual(result["status"], "already_read")
        self.assertEqual(result["error_code"], "already_read")
        self.assertIn("already read", result.get("hint", "").lower())

    @override_settings(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=12000, MCP_READ_DOCUMENT_MAX_CHARS_MARGIN=800)
    def test_cursor_continuation_of_same_ref_succeeds(self) -> None:
        """A cursor-continuation read of the same ref should NOT be blocked."""
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=50_000)
        ref_id = "00000000-0000-0000-0000-000000000001"

        # Simulate that this ref was already read this turn.
        context.read_ref_ids_this_turn.add(ref_id)

        # With a cursor, the read should pass through repeat-read detection.
        # (It will fail later at cursor validation, which is fine — we're testing
        # that the repeat-read gate doesn't block it.)
        result = tools._agentic_read_v2_handler(
            {"items": [{"id": ref_id, "cursor": "some-cursor-token"}], "max_chars": 2000},
            conversation,
            context,
        )

        # Should NOT be "already_read" — it should proceed past the gate.
        self.assertNotEqual(result.get("status"), "already_read")

    @override_settings(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=12000, MCP_READ_DOCUMENT_MAX_CHARS_MARGIN=800)
    def test_mixed_refs_filters_already_read(self) -> None:
        """When some refs are already read and some are new, only new ones proceed."""
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=50_000)
        already_read_id = "00000000-0000-0000-0000-000000000001"
        new_id = "00000000-0000-0000-0000-000000000002"

        context.read_ref_ids_this_turn.add(already_read_id)

        # The new_id will fail at resolve_target (not found), but crucially the
        # already_read_id should be filtered out and the request should NOT return
        # "already_read" status.
        result = tools._agentic_read_v2_handler(
            {"items": [{"id": already_read_id}, {"id": new_id}], "max_chars": 2000},
            conversation,
            context,
        )

        self.assertNotEqual(result.get("status"), "already_read")
