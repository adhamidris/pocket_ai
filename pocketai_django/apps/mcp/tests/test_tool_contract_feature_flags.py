from __future__ import annotations

from django.test import TestCase

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.mcp.orchestrator import McpOrchestratorService


class _ToolRecordingProvider:
    def __init__(self) -> None:
        self.tool_name_sets: list[set[str]] = []

    def chat(self, messages, *, tools=None, on_stream_delta=None, on_tool_call_start=None, response_format=None):
        if tools is not None:
            names: set[str] = set()
            for tool_def in tools:
                name = (tool_def or {}).get("function", {}).get("name")
                if isinstance(name, str) and name:
                    names.add(name)
            self.tool_name_sets.append(names)
        return {"message": {"role": "assistant", "content": "Ok."}}


class McpToolContractFeatureFlagTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-tool-contract@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)

    def _build_conversation(self, *, rag_agentic_mode: bool) -> Conversation:
        business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Tool Contract Co",
            industry="software",
            metadata={FEATURE_FLAG_METADATA_KEY: {"rag_agentic_mode": rag_agentic_mode}},
        )
        agent = AgentProfile.objects.create(
            business_profile=business,
            user=self.user,
            name="Tooler",
            role="AI Specialist",
        )
        return Conversation.objects.create(
            business_profile=business,
            agent_profile=agent,
            session_token=f"tool-contract-session-{int(rag_agentic_mode)}",
        )

    def test_agentic_mode_advertises_minimal_tools(self) -> None:
        conversation = self._build_conversation(rag_agentic_mode=True)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=conversation.agent_profile, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="What credit cards do you offer?")

        self.assertTrue(provider.tool_name_sets, "Provider never received tool definitions.")
        advertised = provider.tool_name_sets[0]
        self.assertEqual(advertised, {"search_knowledge", "read_document"})

    def test_non_agentic_mode_advertises_full_tool_catalog(self) -> None:
        conversation = self._build_conversation(rag_agentic_mode=False)
        provider = _ToolRecordingProvider()
        orchestrator = McpOrchestratorService(agent=conversation.agent_profile, provider=provider)

        orchestrator.stream_turn(conversation=conversation, user_message="What credit cards do you offer?")

        self.assertTrue(provider.tool_name_sets, "Provider never received tool definitions.")
        advertised = provider.tool_name_sets[0]
        self.assertIn("search_knowledge", advertised)
        self.assertIn("read_document", advertised)
        self.assertIn("create_case", advertised)
        self.assertIn("query_dataset", advertised)
