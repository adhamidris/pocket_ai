from __future__ import annotations

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.mcp import prompts
from apps.mcp.orchestrator import McpOrchestratorService


class _FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, *, tools=None, on_stream_delta=None, on_tool_call_start=None, response_format=None):
        self.calls += 1
        content = "I'll check the docs. The fee is $100 per year."
        if on_stream_delta:
            for chunk in ["I'll check the docs. ", "The fee is $100 per year."]:
                on_stream_delta(chunk)
        return {"message": {"role": "assistant", "content": content}}


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
        self.assertTrue(any("dropped_sentence" in entry for entry in logs.output))

    def test_prompts_include_no_narration_language(self) -> None:
        system_prompt = prompts.build_system_message(
            self.agent,
            business_name=self.business.name,
            business_industry=self.business.industry or "general services",
        )
        self.assertIn("Do not narrate internal steps", system_prompt)
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
