from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.mcp import tools as mcp_tools
from apps.mcp.orchestrator import McpOrchestratorService
from apps.mcp.types import ToolExecutionContext


class _SearchTwiceProvider:
    """
    Fake provider that attempts to call search_knowledge twice in the same user turn.

    The MCP orchestrator should execute both searches when budget allows.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.model = "deepseek-chat"

    def chat(
        self,
        messages,
        *,
        tools=None,
        on_stream_delta=None,
        on_reasoning_delta=None,
        on_tool_call_start=None,
        on_tool_call_delta=None,
        response_format=None,
        should_cancel=None,
    ):
        self.calls += 1

        if self.calls in {1, 2}:
            tool_call = {
                "id": f"call_search_{self.calls}",
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "arguments": json.dumps(
                        {"queries": ["credit card fees", "credit card fees charges costs"]},
                        ensure_ascii=False,
                    ),
                },
            }
            if on_tool_call_start:
                on_tool_call_start(tool_call)
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]}}

        content = "Annual fee example: 100 EGP. Tell me your card type if you want exact fees."
        return {"message": {"role": "assistant", "content": content}}


class _SingleAnswerProvider:
    def __init__(self, content: str = "Cheque fees are available in the selected category.") -> None:
        self.calls = 0
        self.content = content
        self.model = "deepseek-chat"

    def chat(
        self,
        messages,
        *,
        tools=None,
        on_stream_delta=None,
        on_reasoning_delta=None,
        on_tool_call_start=None,
        on_tool_call_delta=None,
        response_format=None,
        should_cancel=None,
    ):
        del messages, tools, on_stream_delta, on_reasoning_delta, on_tool_call_start, on_tool_call_delta, response_format, should_cancel
        self.calls += 1
        return {"message": {"role": "assistant", "content": self.content}}


class McpSearchContractTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-search-contract@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Search Contract Bank",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Searcher",
            role="AI Specialist",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="search-contract-session",
        )

    @override_settings(MCP_MAX_SEARCHES_PER_TURN=1)
    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_search_knowledge_budget_blocks_second_call(self, execute_tool_mock) -> None:
        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            self.assertEqual(name, "search_knowledge")
            self.assertIsNotNone(context)
            context.reserve_search()
            return {
                "tool": "search_knowledge",
                "status": "ok",
                "query": str((arguments.get("queries") or [""])[0]),
                "snippets": [
                    {
                        "id": "snippet-1",
                        "title": "Fees",
                        "public_label": "Fees",
                        "content": "Annual fee example: 100 EGP",
                        "read_state": "summary",
                        "read_required": False,
                        "search_stage": "hybrid",
                        "chunk_id": "chunk-1",
                        "upload_id": "upload-1",
                        "is_table_chunk": False,
                    }
                ],
            }

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _SearchTwiceProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="Tell me more about credit card fees",
        )

        self.assertEqual(provider.calls, 3)
        self.assertIsNotNone(context.tool_context)
        self.assertEqual(context.tool_context.searches_used, 1)

        execute_tool_mock.assert_called_once()

        search_traces = [t for t in context.tool_trace if t.get("tool") == "search_knowledge"]
        self.assertEqual(len(search_traces), 2)
        self.assertEqual(sum(1 for t in search_traces if t.get("origin") == "live"), 1)
        self.assertEqual(sum(1 for t in search_traces if t.get("origin") == "policy"), 1)
        policy_trace = next(t for t in search_traces if t.get("origin") == "policy")
        self.assertIn("read_knowledge", str(policy_trace.get("hint") or ""))
        self.assertIn("budget_guidance", policy_trace.get("result_keys") or [])
        output_summary = policy_trace.get("output_summary") or {}
        self.assertEqual((output_summary.get("budget") or {}).get("searches_remaining"), 0)
        self.assertEqual((output_summary.get("budget_guidance") or {}).get("reason"), "orchestrator_policy")

        self.assertNotIn("search limit", context.response_text.lower())
        self.assertNotIn("budget", context.response_text.lower())

    @override_settings(MCP_MAX_SEARCHES_PER_TURN=2)
    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_search_knowledge_allows_second_call_when_budget_allows(self, execute_tool_mock) -> None:
        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            self.assertEqual(name, "search_knowledge")
            self.assertIsNotNone(context)
            context.reserve_search()
            return {
                "tool": "search_knowledge",
                "status": "ok",
                "query": str((arguments.get("queries") or [""])[0]),
                "snippets": [
                    {
                        "id": f"snippet-{context.searches_used}",
                        "title": "Fees",
                        "public_label": "Fees",
                        "content": "Annual fee example: 100 EGP",
                        "read_state": "summary",
                        "read_required": False,
                        "search_stage": "hybrid",
                        "chunk_id": f"chunk-{context.searches_used}",
                        "upload_id": "upload-1",
                        "is_table_chunk": False,
                    }
                ],
            }

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _SearchTwiceProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="Tell me more about credit card fees",
        )

        self.assertEqual(provider.calls, 3)
        self.assertIsNotNone(context.tool_context)
        self.assertEqual(context.tool_context.searches_used, 2)

        self.assertEqual(execute_tool_mock.call_count, 2)

        search_traces = [t for t in context.tool_trace if t.get("tool") == "search_knowledge"]
        self.assertEqual(len(search_traces), 2)
        self.assertEqual(sum(1 for t in search_traces if t.get("origin") == "live"), 2)
        self.assertEqual(sum(1 for t in search_traces if t.get("origin") == "policy"), 0)

    @override_settings(MCP_MAX_SEARCHES_PER_TURN=1)
    def test_search_budget_exceeded_tool_payload_guides_recovery(self) -> None:
        context = ToolExecutionContext()
        context.set_recent_search_refs(
            [
                {
                    "id": str(uuid.uuid4()),
                    "label": "CIB Account Fees - opening fee row",
                    "kind": "table_row",
                    "type": "table",
                }
            ]
        )
        context.reserve_search()

        result = mcp_tools.execute_tool(
            "search_knowledge",
            {"queries": ["account opening fees"]},
            conversation=self.conversation,
            context=context,
        )

        self.assertEqual(result.get("status"), "blocked")
        self.assertEqual(result.get("error_code"), "search_budget_exceeded")
        self.assertIn("read_knowledge", str(result.get("hint") or ""))
        guidance = result.get("budget_guidance") or {}
        self.assertEqual(guidance.get("reason"), "per_turn_limit")
        self.assertEqual(guidance.get("available_refs_count"), 1)
        actions = [entry.get("action") for entry in guidance.get("next_actions") or []]
        self.assertIn("read_existing_refs", actions)
        self.assertIn("answer_from_available_evidence", actions)
        self.assertIn("ask_clarification", actions)
        self.assertEqual((result.get("budget") or {}).get("searches_remaining"), 0)
        self.assertEqual(
            (result.get("budget") or {}).get("next_action"),
            "read_existing_refs_or_answer_or_ask_clarification",
        )

    @override_settings(MCP_MAX_SEARCHES_PER_TURN=2, MCP_NEW_CONTRACT_ENABLED=True)
    @patch("apps.mcp.tools._portal_file_embedding_service")
    @patch("apps.mcp.tools._knowledge_service")
    @patch("apps.mcp.tools.FeatureFlagService.snapshot")
    def test_equivalent_repeated_search_returns_soft_guidance(
        self,
        feature_snapshot_mock,
        knowledge_service_mock,
        embedding_service_mock,
    ) -> None:
        feature_snapshot_mock.return_value = SimpleNamespace(rag_agentic_mode=False)
        embedding_service_mock.return_value = SimpleNamespace(embed_text=lambda _text: [1.0, 0.0])
        knowledge_service_mock.return_value = SimpleNamespace(
            search=lambda **_kwargs: SimpleNamespace(snippets=tuple(), status="not_found", diagnostics={})
        )
        context = ToolExecutionContext()
        context.set_recent_search_refs(
            [{"id": str(uuid.uuid4()), "label": "CIB Account Fees - account opening row"}]
        )
        context.search_history.append(
            {
                "intent": "account opening fees",
                "embedding": [1.0, 0.0],
                "response": {
                    "tool": "search_knowledge",
                    "status": "ok",
                    "refs": [{"id": str(uuid.uuid4()), "label": "prior account fee ref"}],
                },
            }
        )

        result = mcp_tools.execute_tool(
            "search_knowledge",
            {"queries": ["current account opening charges"]},
            conversation=self.conversation,
            context=context,
        )

        self.assertEqual(result.get("tool"), "search_knowledge")
        self.assertNotEqual(result.get("status"), "duplicate")
        diagnostics = result.get("diagnostics") or {}
        self.assertGreaterEqual(diagnostics.get("duplicate_intent_similarity"), 0.85)
        repeat_guidance = result.get("search_repeat_guidance") or {}
        self.assertEqual(repeat_guidance.get("reason"), "repeated_equivalent_search")
        self.assertIn("read", str(repeat_guidance.get("message") or "").lower())
        self.assertEqual(repeat_guidance.get("available_refs_count"), 1)
