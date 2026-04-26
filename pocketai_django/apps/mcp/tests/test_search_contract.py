from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.mcp.orchestrator import McpOrchestratorService


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
