from __future__ import annotations

import uuid
from unittest import mock

from django.test import TestCase

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender
from apps.rag.ai_orchestrator import (
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

    def _snippet(
        self,
        *,
        confidence: float = 0.9,
        truncated_rows: int = 0,
        total_rows: int = 0,
        indexed_rows: int | None = None,
        partial_index: bool = False,
    ) -> KnowledgeSnippet:
        diagnostics: dict[str, int] = {}
        if truncated_rows:
            diagnostics["truncated_rows"] = truncated_rows
        if total_rows:
            diagnostics["table_total_rows"] = total_rows
        if indexed_rows is not None:
            diagnostics["table_indexed_rows"] = indexed_rows
        elif total_rows:
            diagnostics["table_indexed_rows"] = total_rows
        return KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Sheet",
            summary="Summary",
            source="sheet",
            content="",
            public_label="Sheet",
            structured_tables=tuple(),
            issues=tuple(),
            page_summaries=tuple(),
            read_state="summary",
            topic_hints=tuple(),
            is_pinned=False,
            upload_id=uuid.uuid4(),
            chunk_id=None,
            chunk_index=None,
            entity_type=None,
            entity_name=None,
            entity_business=None,
            is_table_chunk=False,
            aliases=tuple(),
            search_stage="hybrid",
            confidence_score=confidence,
            truncated=False,
            source_diagnostics=diagnostics,
            partial_index=partial_index,
            structured_table_count=0,
            issue_count=0,
            structured_table_hint=None,
        )

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

    def test_confidence_penalty_for_partial_tables(self) -> None:
        snippet = self._snippet(truncated_rows=400, total_rows=1000, indexed_rows=400)
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[snippet], diagnostics={"path": "hybrid"}))

        score, reason = orchestrator._compute_answer_confidence(
            (snippet,),
            knowledge_status="ok",
            knowledge_diagnostics={"path": "hybrid"},
        )

        self.assertIn("partial_index_penalty", reason)
        self.assertLess(score, 0.8)

    def test_confidence_penalty_for_ingestion_flags(self) -> None:
        snippet = self._snippet(total_rows=1000, indexed_rows=1000)
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[snippet], diagnostics={"path": "hybrid"}))
        knowledge_payload = [
            {
                "id": str(snippet.id),
                "issues": [{"issue_code": "table_truncated", "severity": "warning"}],
            }
        ]
        reads = [{"id": str(snippet.id)}]

        score, reason = orchestrator._compute_answer_confidence(
            (snippet,),
            knowledge_status="ok",
            knowledge_diagnostics={"path": "hybrid"},
            knowledge_payload=knowledge_payload,
            knowledge_reads=reads,
        )

        self.assertIn("truncation_penalty", reason)
        self.assertLess(score, 0.7)

    def test_confidence_bonus_for_fully_indexed_tables(self) -> None:
        snippet = self._snippet(total_rows=400, indexed_rows=400)
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[snippet], diagnostics={"path": "hybrid"}))

        score, reason = orchestrator._compute_answer_confidence(
            (snippet,),
            knowledge_status="ok",
            knowledge_diagnostics={"path": "hybrid"},
        )

        self.assertIn("full_index_bonus", reason)
        self.assertGreater(score, 0.7)

    def test_summarize_ingestion_truncation_prefers_table_language(self) -> None:
        snippet = self._snippet(truncated_rows=500, total_rows=2000, indexed_rows=500)
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[snippet]))
        payload = [
            {
                "id": str(snippet.id),
                "source_diagnostics": snippet.source_diagnostics,
                "partial_index": True,
            }
        ]
        reads = [{"id": str(snippet.id)}]

        notice = orchestrator._summarize_ingestion_truncation(payload, knowledge_reads=reads)
        self.assertIn("only indexes", notice.lower())

    def test_ingestion_notice_only_when_reads_present(self) -> None:
        snippet = self._snippet(truncated_rows=300, total_rows=800, indexed_rows=300)
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[snippet]))
        payload = [
            {
                "id": str(snippet.id),
                "source_diagnostics": snippet.source_diagnostics,
                "partial_index": True,
            }
        ]

        appended = orchestrator._maybe_append_ingestion_notice("Answer", payload, knowledge_reads=[{"id": str(snippet.id)}], knowledge_status="ok")
        self.assertNotEqual(appended, "Answer")

        untouched = orchestrator._maybe_append_ingestion_notice("Answer", payload, knowledge_reads=[], knowledge_status="ok")
        self.assertEqual(untouched, "Answer")

    def test_force_full_table_reads_for_total_queries(self) -> None:
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[]))
        upload_id = uuid.uuid4()
        chunk_id = uuid.uuid4()
        payload = [
            {
                "id": str(chunk_id),
                "upload_id": upload_id,
                "chunk_id": chunk_id,
                "status": "ready",
                "public_label": "Applied Jobs",
            }
        ]
        forced = orchestrator._forced_full_table_reads(
            query="how many jobs overall",
            knowledge_payload=payload,
            loaded_content_ids=set(),
        )
        self.assertIn(str(upload_id), forced)

    def test_force_full_table_reads_for_summary_snippet(self) -> None:
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[]))
        upload_id = uuid.uuid4()
        payload = [
            {
                "id": str(upload_id),
                "upload_id": upload_id,
                "status": "summary-only",
                "structuredTables": [{"title": "Jobs"}],
                "public_label": "Applied Jobs",
            }
        ]
        forced = orchestrator._forced_full_table_reads(
            query="total jobs",
            knowledge_payload=payload,
            loaded_content_ids=set(),
        )
        self.assertIn(str(upload_id), forced)

    def test_force_full_table_reads_respects_label_match(self) -> None:
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[]))
        upload_id = uuid.uuid4()
        chunk_id = uuid.uuid4()
        payload = [
            {
                "id": str(chunk_id),
                "upload_id": upload_id,
                "chunk_id": chunk_id,
                "status": "ready",
                "public_label": "Trips Ledger",
            }
        ]
        forced = orchestrator._forced_full_table_reads(
            query="how many jobs overall",
            knowledge_payload=payload,
            loaded_content_ids=set(),
        )
        self.assertEqual(forced, [])

    def test_force_full_table_reads_skips_when_not_needed(self) -> None:
        orchestrator = self._make_orchestrator(StubKnowledgeService(snippets=[]))
        upload_id = uuid.uuid4()
        payload = [
            {
                "id": str(upload_id),
                "upload_id": upload_id,
                "status": "ready",
                "structuredTables": [
                    {"title": "Jobs"},
                ],
            }
        ]
        forced = orchestrator._forced_full_table_reads(
            query="show jobs",
            knowledge_payload=payload,
            loaded_content_ids={str(upload_id)},
        )
        self.assertEqual(forced, [])
