from __future__ import annotations

import uuid
from unittest import mock

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender
from apps.services.ai_orchestrator import (
    AiOrchestratorService,
    AliasSearchResult,
    KnowledgeSearchResult,
    KnowledgeSnippet,
    PromptBundle,
    QueryTraits,
)


class StubKnowledgeService:
    def __init__(
        self,
        *,
        snippets: list[KnowledgeSnippet],
        status: str = "ok",
        diagnostics: dict | None = None,
        alias_hits: tuple = tuple(),
    ) -> None:
        self.snippets = tuple(snippets)
        self.status = status
        self.diagnostics = diagnostics or {"path": "hybrid"}
        self.alias_hits = alias_hits
        self.alias_called = 0
        self.search_called = 0

    def analyze_query(self, query: str) -> QueryTraits:
        normalized = query.lower()
        tokens = tuple(normalized.split())
        return QueryTraits(
            original=query,
            normalized=normalized,
            tokens=tokens,
            alias_candidates=(normalized,),
            token_count=len(tokens),
            has_digits=any(ch.isdigit() for ch in query),
            has_dashes="-" in query,
            has_underscores="_" in query,
            is_identifier_like=any(ch.isdigit() for ch in query),
        )

    def search_by_alias(self, **kwargs) -> AliasSearchResult:
        self.alias_called += 1
        diagnostics = {"stage": "alias_exact"} if self.alias_hits else {"stage": "alias_none"}
        return AliasSearchResult(
            hits=self.alias_hits,
            diagnostics=diagnostics,
            short_circuit=bool(self.alias_hits),
        )

    def search(self, **kwargs) -> KnowledgeSearchResult:
        self.search_called += 1
        return KnowledgeSearchResult(
            snippets=self.snippets,
            status=self.status,
            diagnostics=self.diagnostics,
        )

    def load_chunk_contents(self, **kwargs):
        return tuple()

    def load_contents(self, **kwargs):
        return tuple()


class StubPromptBuilder:
    def build(self, *, conversation, knowledge_snippets, transcript, actions_catalog, knowledge_log):
        return PromptBundle(
            system_prompt="sys",
            user_prompt="user",
            transcript=[],
            knowledge_snippets=knowledge_snippets,
            actions_catalog=actions_catalog,
            agent_traits={"role": "AI"},
        )


class OrchestratorPhaseThreeTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create(email="phase3@example.com")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Phase3 Co",
            industry="travel",
        )
        self.agent = AgentProfile.objects.create(
            user=self.user,
            business_profile=self.business,
            name="Phase3 Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="test-session",
        )
        ConversationMessage.objects.create(
            conversation=self.conversation,
            sender=ConversationSender.CUSTOMER,
            body="Tell me about trip 101",
        )

    def _make_orchestrator(self, knowledge_service: StubKnowledgeService) -> AiOrchestratorService:
        orchestrator = AiOrchestratorService(agent=self.agent, provider=None)
        orchestrator.knowledge_service = knowledge_service
        orchestrator.prompt_builder = StubPromptBuilder()
        orchestrator._invoke_llm = mock.Mock(return_value=None)
        return orchestrator

    def test_identifier_query_records_tool_trace(self) -> None:
        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Trip 101",
            summary="Trip summary",
            source="file",
            content="Trip details",
            public_label="Trip 101",
            structured_tables=tuple(),
            issues=tuple(),
            page_summaries=tuple(),
            read_state="summary",
            topic_hints=tuple(),
            is_pinned=False,
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=0,
            search_stage="alias_exact",
            confidence_score=1.0,
        )
        knowledge_service = StubKnowledgeService(snippets=[snippet], diagnostics={"path": "alias_exact"})
        orchestrator = self._make_orchestrator(knowledge_service)

        plan = orchestrator.run_turn(conversation=self.conversation, user_message="TRIP-101")

        metadata = Conversation.objects.get(pk=self.conversation.pk).metadata
        trace = metadata.get("knowledge_trace") or []
        self.assertTrue(any(entry.get("tool") == "search_by_identifier" for entry in trace))
        self.assertEqual(plan.diagnostics.get("knowledge_status"), "ok")

    def test_not_found_response_mentions_absence(self) -> None:
        knowledge_service = StubKnowledgeService(snippets=[], status="not_found", diagnostics={"path": "not_found"})
        orchestrator = self._make_orchestrator(knowledge_service)

        plan = orchestrator.run_turn(conversation=self.conversation, user_message="missing-code")

        self.assertTrue(plan.response_text.lower().startswith("i couldn".lower()))
