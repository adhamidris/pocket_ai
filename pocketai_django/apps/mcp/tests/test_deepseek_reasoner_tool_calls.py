from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.llm.llm_provider import PromptGenerationError
from apps.mcp.orchestrator import McpOrchestratorService


class _DeepSeekReasonerToolLoopProvider:
    """
    Minimal provider that mimics DeepSeek thinking-mode tool-loop requirements.

    DeepSeek rejects tool-loop follow-up requests when assistant tool-call
    messages are missing `reasoning_content`.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.model = "deepseek-reasoner"
        self.requests: list[dict[str, object]] = []

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
        materialized = [dict(m) for m in messages]
        self.requests.append({"tools": bool(tools), "messages": materialized})

        if tools:
            for idx, msg in enumerate(materialized):
                if msg.get("role") == "assistant" and msg.get("tool_calls") and "reasoning_content" not in msg:
                    raise PromptGenerationError(
                        "DeepSeek tools error (400): "
                        + json.dumps(
                            {
                                "error": {
                                    "message": (
                                        "Missing `reasoning_content` field in the assistant message "
                                        f"at message index {idx}."
                                    )
                                }
                            }
                        )
                    )

        self.calls += 1
        if self.calls == 1:
            tool_call = {
                "id": "call_search_1",
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "arguments": json.dumps({"query": "github repos"}, ensure_ascii=False),
                },
            }
            if on_tool_call_start:
                on_tool_call_start(tool_call)
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "I should search first.",
                    "tool_calls": [tool_call],
                }
            }

        return {"message": {"role": "assistant", "content": "OK.", "reasoning_content": "Answer now."}}


class DeepSeekReasonerToolLoopTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="deepseek-tool-loop@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="DeepSeek Loop Bank",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="DeepSeek",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="deepseek-loop-session",
        )

    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_orchestrator_round_trips_reasoning_content_for_tool_calls(self, execute_tool_mock) -> None:
        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            self.assertEqual(name, "search_knowledge")
            self.assertIsNotNone(context)
            context.reserve_search()
            return {"tool": "search_knowledge", "status": "ok", "query": str(arguments.get("query") or ""), "snippets": []}

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _DeepSeekReasonerToolLoopProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="List my github repos",
        )

        tool_loop_requests = [entry for entry in provider.requests if entry.get("tools")]
        self.assertGreaterEqual(len(tool_loop_requests), 2)

        followup_messages = tool_loop_requests[1].get("messages") or []
        for msg in followup_messages:
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                continue
            if any(isinstance(call, dict) and call.get("id") == "call_search_1" for call in tool_calls):
                self.assertEqual(msg.get("reasoning_content"), "I should search first.")
                break
        else:
            self.fail("Expected follow-up DeepSeek tool-loop request to include the original assistant tool-call message.")
