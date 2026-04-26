from __future__ import annotations

import uuid
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.rag.ai_orchestrator import ChunkResult, KnowledgeSearchService
from apps.rag.evaluation.datasets import GOLDEN_SETS
from apps.rag.query_classifier import QueryClassifier, QueryIntent
from apps.rag.query_rewriter import ContextAwareQueryRewriter, RewriteContext


class AgenticRagRegressionEvals(SimpleTestCase):
    def test_problem_documents_are_registered_in_rag_golden_sets(self) -> None:
        fees_set = GOLDEN_SETS["fees-credit-cards"]
        account_set = GOLDEN_SETS["account-fees"]

        self.assertTrue(fees_set.fixtures[0].absolute_path.exists())
        self.assertTrue(account_set.fixtures[0].absolute_path.exists())
        self.assertIn(
            "fees_credit_cards_list_all_issuance",
            {query.query_id for query in fees_set.queries},
        )
        self.assertIn(
            "account_fees_opening_all",
            {query.query_id for query in account_set.queries},
        )

    def test_financial_list_queries_are_full_coverage_enumerations(self) -> None:
        classifier = QueryClassifier()

        queries = (
            "list me all credit cards and their issuance fees",
            "list me all account opening fees",
        )

        for query in queries:
            with self.subTest(query=query):
                classification = classifier.classify(query)
                self.assertEqual(classification.intent, QueryIntent.ENUMERATE)
                self.assertEqual(classification.scope, "all")
                self.assertTrue(classification.requires_full_coverage())
                self.assertTrue(classification.retrieval_hints.get("diversify_tables"))
                self.assertTrue(classification.retrieval_hints.get("include_all_tables"))

    def test_same_thread_topic_shift_does_not_rewrite_to_previous_document(self) -> None:
        rewriter = ContextAwareQueryRewriter(enabled=True, prefix_mode=True, min_confidence=0.5)
        context = RewriteContext(
            primary_document_title="Fees and Charges Credit Cards Eng_185",
            previous_queries=("list me all credit cards and their issuance fees",),
        )

        result = rewriter.rewrite("account opening fees", context)

        self.assertFalse(result.context_injected)
        self.assertEqual(result.rewritten_query, "account opening fees")
        self.assertEqual(result.rewrite_strategy, "topic_shift")
        self.assertEqual(result.reason, "topic_shift")

    def test_document_continuity_boost_requires_confirmed_followup_scope(self) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("account opening fees")
        primary_upload_id = uuid.uuid4()
        other_upload_id = uuid.uuid4()

        def _chunk(upload_id: uuid.UUID):
            return SimpleNamespace(
                id=uuid.uuid4(),
                upload_id=upload_id,
                upload=SimpleNamespace(display_name="Neutral Document", source_name="Neutral Document"),
                content="Opening fee information",
                metadata={"index_type": "text"},
            )

        def _candidates() -> list[ChunkResult]:
            return [
                ChunkResult(chunk=_chunk(other_upload_id), source_stage="hybrid", lexical_score=0.5),
                ChunkResult(chunk=_chunk(primary_upload_id), source_stage="hybrid", lexical_score=0.5),
            ]

        ranked_without_scope, _, _ = service._rerank_candidates(
            _candidates(),
            query_vector=None,
            traits=traits,
            table_context={"has_intent": False, "query_tokens": set(), "specific_tokens": set()},
            session_context={
                "primary_upload_id": str(primary_upload_id),
                "document_continuity_allowed": False,
            },
        )
        self.assertEqual(ranked_without_scope[0].chunk.upload_id, other_upload_id)
        primary_without_scope = next(
            hit for hit in ranked_without_scope if hit.chunk.upload_id == primary_upload_id
        )
        self.assertEqual(
            (primary_without_scope.diagnostics.get("score_breakdown") or {}).get("document_continuity_bonus"),
            0.0,
        )

        ranked_with_scope, _, _ = service._rerank_candidates(
            _candidates(),
            query_vector=None,
            traits=traits,
            table_context={"has_intent": False, "query_tokens": set(), "specific_tokens": set()},
            session_context={
                "primary_upload_id": str(primary_upload_id),
                "document_continuity_allowed": True,
            },
        )
        self.assertEqual(ranked_with_scope[0].chunk.upload_id, primary_upload_id)
        self.assertGreater(
            (ranked_with_scope[0].diagnostics.get("score_breakdown") or {}).get("document_continuity_bonus"),
            0.0,
        )
