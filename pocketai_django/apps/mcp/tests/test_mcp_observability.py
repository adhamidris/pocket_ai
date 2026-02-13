from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.mcp import prompts
from apps.mcp.orchestrator import McpOrchestratorService


class _FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

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
        content = "I'll check the docs. The fee is $100 per year."
        if on_stream_delta:
            for chunk in ["I'll check the docs. ", "The fee is $100 per year."]:
                on_stream_delta(chunk)
        return {"message": {"role": "assistant", "content": content}}


class _TailMismatchProvider:
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
        del messages, tools, on_reasoning_delta, on_tool_call_start, on_tool_call_delta, response_format, should_cancel
        final = (
            "Summary:\n"
            "- Applicable to: All customer segments (Prime, Plus, Wealth, Exclusive Wealth, Private)"
        )
        if on_stream_delta:
            on_stream_delta(
                "Summary:\n"
                "- Applicable to: All customer segments (Prime, Plus, Wealth, Exclusive Wealth, "
            )
        return {"message": {"role": "assistant", "content": final}}


class _ToolLoopFinalStreamProvider:
    def __init__(self) -> None:
        self.calls = 0

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
        del messages, tools, on_reasoning_delta, on_tool_call_delta, response_format, should_cancel
        self.calls += 1
        if self.calls == 1:
            tool_call = {
                "id": "call_search_1",
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "arguments": json.dumps(
                        {"queries": ["traveler cheques fee", "traveler cheques private fee"]},
                        ensure_ascii=False,
                    ),
                },
            }
            if on_tool_call_start:
                on_tool_call_start(tool_call)
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]}}
        final = "Final answer: 1% with minimum USD 2, applies to Prime/Plus/Wealth/Exclusive Wealth/Private."
        if on_stream_delta:
            on_stream_delta(final)
        return {"message": {"role": "assistant", "content": final}}


class McpObservabilityTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-observe@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Observability Bank",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Observer",
            role="AI Specialist",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="obs-session",
        )

    def test_orchestrator_logs_and_sanitizes(self) -> None:
        provider = _FakeProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        streamed: list[str] = []

        with self.assertLogs("apps.mcp.sanitizer", level="INFO") as logs:
            context = orchestrator.stream_turn(
                conversation=self.conversation,
                user_message="What's the annual fee?",
                on_response_text_delta=streamed.append,
            )

        self.assertEqual(context.response_text, "The fee is $100 per year.")
        self.assertEqual(list(context.streamed_chunks), ["The fee is $100 per year."])

        plan = orchestrator.finalize_turn(context)
        self.assertEqual(plan.response_text, "The fee is $100 per year.")
        sanitized_diag = plan.diagnostics.get("sanitized_sentences") or {}
        self.assertEqual(sanitized_diag.get("count"), 1)
        self.assertIn("I'll check the docs.", sanitized_diag.get("examples", []))
        self.assertTrue(any("DROPPED_SENTENCE" in entry for entry in logs.output))

    def test_prompts_include_no_narration_language(self) -> None:
        system_prompt = prompts.build_system_message(
            self.agent,
            business_name=self.business.name,
            business_industry=self.business.industry or "general services",
        )
        self.assertIn("Do not narrate internal steps", system_prompt)
        self.assertIn("per visitor message (user turn)", system_prompt)
        final_messages = prompts.build_final_answer_messages(
            conversation=self.conversation,
            user_message="Explain the fees.",
            tool_context_note="Tools executed: search_knowledge",
            coverage_ledger=(),
            tool_trace=(),
            assistant_draft={"content": "Draft reply"},
        )
        self.assertEqual(final_messages[0]["role"], "system")
        system_text = final_messages[0]["content"]
        self.assertIn("Tools have already been executed", system_text)
        self.assertIn("Do not narrate internal steps", system_text)
        user_payload = final_messages[1]["content"]
        self.assertIn("Latest user message:", user_payload)

    def test_stream_turn_reconciles_missing_stream_suffix(self) -> None:
        provider = _TailMismatchProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        streamed: list[str] = []

        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="Does this apply to private?",
            on_response_text_delta=streamed.append,
        )

        self.assertEqual(
            context.response_text,
            "Summary:\n- Applicable to: All customer segments (Prime, Plus, Wealth, Exclusive Wealth, Private)",
        )
        self.assertTrue(streamed)
        self.assertEqual("".join(streamed), context.response_text)
        self.assertEqual("".join(context.streamed_chunks), context.response_text)

    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_tool_loop_stream_does_not_replay_full_answer(self, execute_tool_mock) -> None:
        execute_tool_mock.return_value = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [],
        }
        provider = _ToolLoopFinalStreamProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        streamed: list[str] = []

        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="What is the traveler cheques fee?",
            on_response_text_delta=streamed.append,
        )

        self.assertEqual(provider.calls, 2)
        self.assertEqual(
            context.response_text,
            "Final answer: 1% with minimum USD 2, applies to Prime/Plus/Wealth/Exclusive Wealth/Private.",
        )
        self.assertEqual("".join(streamed), context.response_text)
        self.assertEqual("".join(context.streamed_chunks), context.response_text)
