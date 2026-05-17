from __future__ import annotations

import uuid
from contextlib import ExitStack
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.knowledge.models import (
    KnowledgeAlias,
    KnowledgeEntity,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
)
from apps.rag.knowledge_search import (
    AliasSearchResult,
    ChunkResult,
    KnowledgeSearchService,
    KnowledgeSnippet,
    QueryNormalizer,
)
from apps.rag.query_classifier import QueryClassification, QueryIntent
from core.tenancy import tenant_context


class QueryNormalizerTests(SimpleTestCase):
    def test_identifier_detection(self) -> None:
        traits = QueryNormalizer.normalize("Trip-101 deluxe package")
        self.assertTrue(traits.is_identifier_like)
        self.assertIn("trip-101", traits.alias_candidates)

    def test_alias_candidates_capture_multiword_phrases(self) -> None:
        traits = QueryNormalizer.normalize("Michael Page job opportunities")
        self.assertIn("michael-page", traits.alias_candidates)


class KnowledgeSearchServiceAutoDecisionContractTests(SimpleTestCase):
    def test_contract_defaults_to_undecided_without_route(self) -> None:
        contract = KnowledgeSearchService._derive_auto_decision_contract(
            route_diagnostics=None,
            requires_clarification=False,
        )
        self.assertEqual(contract["table_score"], 0.0)
        self.assertEqual(contract["text_score"], 0.0)
        self.assertEqual(contract["margin"], 0.0)
        self.assertEqual(contract["decision"], "undecided")
        self.assertFalse(contract["needs_clarification"])
        self.assertIsNone(contract["scope_summary"])
        self.assertEqual(contract["categories"], [])
        self.assertEqual(contract["top_categories"], [])
        self.assertIsNone(contract["clarification_ui_mode"])
        self.assertFalse(contract["conflict_detected"])
        self.assertIsNone(contract["no_result_reason"])


class KnowledgeSearchServiceRoutingTests(SimpleTestCase):
    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_route_chunk_hits_keeps_text_and_table_candidates_for_table_intent(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        table_chunk = mock.Mock(metadata={"index_type": "table", "is_table_chunk": True})
        text_chunk = mock.Mock(metadata={"index_type": "text"})

        routed, context_hits, diagnostics = service._route_chunk_hits(
            [
                ChunkResult(chunk=table_chunk, source_stage="hybrid", lexical_score=0.6),
                ChunkResult(chunk=text_chunk, source_stage="hybrid", lexical_score=0.6),
            ],
            table_intent=True,
            table_context={"modality_bias": "mixed"},
        )

        self.assertEqual(diagnostics.get("index_route"), "mixed_primary_table_biased")
        self.assertTrue(diagnostics.get("index_route_mixed"))
        self.assertEqual(diagnostics.get("index_route_table_hits"), 1)
        self.assertEqual(diagnostics.get("index_route_text_hits"), 1)
        self.assertEqual(len(routed), 2)
        self.assertTrue(any(bool((hit.chunk.metadata or {}).get("is_table_chunk")) for hit in routed))
        self.assertTrue(any(not bool((hit.chunk.metadata or {}).get("is_table_chunk")) for hit in routed))
        self.assertEqual(len(context_hits), 1)

    def test_contract_marks_clarification_when_required(self) -> None:
        contract = KnowledgeSearchService._derive_auto_decision_contract(
            route_diagnostics={"index_route": "text_first", "index_route_table_hits": 1, "index_route_text_hits": 3},
            requires_clarification=True,
        )
        self.assertEqual(contract["decision"], "clarification")
        self.assertTrue(contract["needs_clarification"])
        self.assertEqual(contract["table_score"], 1.0)
        self.assertEqual(contract["text_score"], 3.0)
        self.assertEqual(contract["margin"], 2.0)
        self.assertEqual(contract["clarification_ui_mode"], "text")

    def test_contract_uses_route_hit_counts_for_decision(self) -> None:
        contract = KnowledgeSearchService._derive_auto_decision_contract(
            route_diagnostics={"index_route": "table_specific_first", "index_route_table_hits": 4, "index_route_text_hits": 1},
            requires_clarification=False,
        )
        self.assertEqual(contract["decision"], "table")
        self.assertFalse(contract["needs_clarification"])
        self.assertEqual(contract["table_score"], 4.0)
        self.assertEqual(contract["text_score"], 1.0)
        self.assertEqual(contract["margin"], 3.0)

    def test_contract_marks_low_margin_scored_paths_as_blended(self) -> None:
        contract = KnowledgeSearchService._derive_auto_decision_contract(
            route_diagnostics={"index_route": "table_primary_filtered", "index_route_table_hits": 2, "index_route_text_hits": 2},
            scoring_diagnostics={"auto_table_score": 0.61, "auto_text_score": 0.58, "auto_score_margin": 0.03},
            requires_clarification=False,
        )
        self.assertEqual(contract["decision"], "blended")
        self.assertFalse(contract["needs_clarification"])
        self.assertEqual(contract["margin"], 0.03)

    def test_contract_prefers_scoring_diagnostics_when_available(self) -> None:
        contract = KnowledgeSearchService._derive_auto_decision_contract(
            route_diagnostics={"index_route": "table_specific_first", "index_route_table_hits": 4, "index_route_text_hits": 1},
            scoring_diagnostics={"auto_table_score": 0.21, "auto_text_score": 0.78, "auto_score_margin": 0.57},
            requires_clarification=False,
        )
        self.assertEqual(contract["decision"], "text")
        self.assertFalse(contract["needs_clarification"])
        self.assertEqual(contract["table_score"], 0.21)
        self.assertEqual(contract["text_score"], 0.78)
        self.assertEqual(contract["margin"], 0.57)

    def test_contract_emits_optional_phase1_diagnostics_keys(self) -> None:
        contract = KnowledgeSearchService._derive_auto_decision_contract(
            route_diagnostics={"index_route": "text_primary_filtered", "index_route_table_hits": 1, "index_route_text_hits": 2},
            scoring_diagnostics={"clarification_ui_mode": "text"},
            requires_clarification=False,
            scope_summary={
                "is_broad_scope": True,
                "distinct_docs": 7,
                "category_counts": {
                    "outgoing transfer fees": 5,
                    "loan service fees": 4,
                    "statement fees": 3,
                },
            },
            conflict_detected=True,
            no_result_reason="Insufficient_Evidence",
        )
        self.assertEqual(
            contract["scope_summary"],
            {
                "is_broad_scope": True,
                "distinct_docs": 7,
                "category_counts": {
                    "outgoing transfer fees": 5,
                    "loan service fees": 4,
                    "statement fees": 3,
                },
            },
        )
        self.assertEqual(
            contract["categories"],
            ["outgoing transfer fees", "loan service fees", "statement fees"],
        )
        self.assertEqual(
            contract["top_categories"],
            ["outgoing transfer fees", "loan service fees", "statement fees"],
        )
        self.assertEqual(contract["clarification_ui_mode"], "text")
        self.assertTrue(contract["conflict_detected"])
        self.assertEqual(contract["no_result_reason"], "insufficient_evidence")


class KnowledgeSearchServicePhaseSixSemanticsTests(SimpleTestCase):
    @staticmethod
    def _table_snippet(*, service_name: str, plus_value: str) -> KnowledgeSnippet:
        return KnowledgeSnippet(
            id=uuid.uuid4(),
            title=f"{service_name} table",
            summary=f"service: {service_name}; plus: {plus_value}",
            source="table_direct",
            content=f"[Table] {service_name}\nservice: {service_name}\nplus: {plus_value}\nprime: EGP 75",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=0,
            is_table_chunk=True,
            source_diagnostics={"table_title": service_name},
        )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_apply_phase6_semantics_sets_conflict_clarification(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("plus loan service fees")
        snippets = (
            self._table_snippet(service_name="Loan Service Fees", plus_value="EGP 120"),
            self._table_snippet(service_name="Loan Service Fees", plus_value="EGP 130"),
        )

        status, refined_snippets, diagnostics = service._apply_phase6_semantics(
            status="ok",
            snippets=snippets,
            diagnostics={"path": "hybrid"},
            traits=traits,
            table_context={"specific_tokens": {"plus"}, "matched_columns_specific": {"plus"}},
            table_blocked=False,
        )

        self.assertEqual(status, "ok")
        self.assertEqual(refined_snippets, snippets)
        self.assertTrue(bool(diagnostics.get("conflict_detected")))
        self.assertEqual(str(diagnostics.get("reason") or ""), "conflicting_evidence")
        contract = diagnostics.get("auto_decision_contract") or {}
        self.assertFalse(bool(contract.get("needs_clarification")))
        self.assertTrue(bool(contract.get("conflict_detected")))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_apply_phase6_semantics_sets_no_result_reason_not_applicable(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("plus premium segment fees")

        status, refined_snippets, diagnostics = service._apply_phase6_semantics(
            status="not_found",
            snippets=tuple(),
            diagnostics={"path": "hybrid", "table_reason": "specific_tokens_missing"},
            traits=traits,
            table_context={"specific_tokens": {"plus"}},
            table_blocked=True,
        )

        self.assertEqual(status, "not_found")
        self.assertFalse(refined_snippets)
        self.assertEqual(diagnostics.get("no_result_reason"), "not_applicable_to_segment")
        contract = diagnostics.get("auto_decision_contract") or {}
        self.assertEqual(contract.get("no_result_reason"), "not_applicable_to_segment")

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_apply_phase6_semantics_sets_no_result_reason_insufficient_evidence(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("plus customer fees")

        status, refined_snippets, diagnostics = service._apply_phase6_semantics(
            status="not_found",
            snippets=tuple(),
            diagnostics={"path": "hybrid", "chunk_candidate_count": 4},
            traits=traits,
            table_context={},
            table_blocked=False,
        )

        self.assertEqual(status, "not_found")
        self.assertFalse(refined_snippets)
        self.assertEqual(diagnostics.get("no_result_reason"), "insufficient_evidence")
        contract = diagnostics.get("auto_decision_contract") or {}
        self.assertEqual(contract.get("no_result_reason"), "insufficient_evidence")

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_apply_phase6_semantics_sets_no_result_reason_not_found(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("fees for unknown segment")

        status, refined_snippets, diagnostics = service._apply_phase6_semantics(
            status="not_found",
            snippets=tuple(),
            diagnostics={
                "path": "fallback",
                "chunk_candidate_count": 0,
                "chunk_candidate_count_raw": 0,
                "vector_candidates": 0,
                "fts_candidates": 0,
                "alias_hits": 0,
            },
            traits=traits,
            table_context={},
            table_blocked=False,
        )

        self.assertEqual(status, "not_found")
        self.assertFalse(refined_snippets)
        self.assertEqual(diagnostics.get("no_result_reason"), "not_found")
        contract = diagnostics.get("auto_decision_contract") or {}
        self.assertEqual(contract.get("no_result_reason"), "not_found")


class KnowledgeSearchServiceAutoScoringTests(SimpleTestCase):
    @staticmethod
    def _make_chunk(*, index_type: str, content: str, metadata: dict[str, object] | None = None):
        chunk = mock.Mock()
        payload = {"index_type": index_type}
        if index_type == "table":
            payload["is_table_chunk"] = True
        if metadata:
            payload.update(metadata)
        chunk.metadata = payload
        chunk.content = content
        return chunk

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_dual_path_scoring_prefers_table_when_specific_signals_are_strong(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        table_hit = ChunkResult(
            chunk=self._make_chunk(index_type="table", content="Gold card annual fee 199", metadata={"table_chunk_role": "row"}),
            source_stage="unit",
            rerank_score=0.68,
            lexical_score=0.52,
            diagnostics={
                "header_match": True,
                "specific_match": True,
                "specific_match_strong": True,
                "specific_match_ratio": 1.0,
            },
        )
        text_hit = ChunkResult(
            chunk=self._make_chunk(index_type="text", content="General card overview and narrative context"),
            source_stage="unit",
            rerank_score=0.61,
            lexical_score=0.32,
        )

        scores = service._score_auto_mode_candidates(
            [table_hit, text_hit],
            query_tokens=("gold", "card", "fee"),
            specific_tokens=("gold", "fee"),
        )

        self.assertEqual(scores["auto_score_version"], "v2")
        self.assertGreater(scores["auto_table_score"], scores["auto_text_score"])
        self.assertGreaterEqual(scores["auto_table_signal_strong_hits"], 1)
        self.assertGreaterEqual(scores["auto_table_signal_specific_hits"], 1)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_dual_path_scoring_prefers_text_when_semantic_signal_is_stronger(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        table_hit = ChunkResult(
            chunk=self._make_chunk(index_type="table", content="misc table row", metadata={"table_chunk_role": "parent"}),
            source_stage="unit",
            rerank_score=0.2,
            lexical_score=0.1,
        )
        text_hit = ChunkResult(
            chunk=self._make_chunk(
                index_type="text",
                content="Gold card benefits include airport lounge access, concierge support, and travel insurance coverage.",
            ),
            source_stage="unit",
            rerank_score=0.86,
            lexical_score=0.74,
        )

        scores = service._score_auto_mode_candidates(
            [table_hit, text_hit],
            query_tokens=("gold", "card", "benefits", "insurance"),
            specific_tokens=(),
        )

        self.assertGreater(scores["auto_text_score"], scores["auto_table_score"])
        self.assertGreater(scores["auto_text_semantic_overlap_avg"], 0.0)
        self.assertEqual(scores["auto_score_text_hits"], 1)


class KnowledgeSearchServiceRegressionContractTests(SimpleTestCase):
    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_public_confidence_ignores_recency_only_inflation(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        upload = mock.Mock()
        upload.metadata = {}
        upload.display_name = "Recent Lease"
        upload.source_name = "File Upload"
        upload.external_reference = ""
        upload.ingestion_metadata = {}
        upload.id = uuid.uuid4()
        upload.get_source_type_display.return_value = "File Upload"
        chunk = mock.Mock()
        chunk.id = uuid.uuid4()
        chunk.upload = upload
        chunk.chunk_index = 0
        chunk.content = "Lease terms and conditions"
        chunk.metadata = {"index_type": "text"}
        result = ChunkResult(
            chunk=chunk,
            source_stage="hybrid",
            lexical_score=0.12,
            recency_score=1.0,
        )

        snippet = service._chunk_to_snippet(chunk, result=result)

        self.assertEqual(snippet.confidence_score, 0.12)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_chunk_hits_reuses_free_text_rerank_output(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        business = mock.Mock()
        business.id = uuid.uuid4()
        upload = mock.Mock()
        upload.id = uuid.uuid4()
        chunk = mock.Mock()
        chunk.id = uuid.uuid4()
        chunk.upload = upload
        chunk.content = "Assessment Fees EGP 200 paid once"
        chunk.metadata = {"index_type": "text"}
        candidate = ChunkResult(
            chunk=chunk,
            source_stage="hybrid",
            lexical_score=0.8,
            rerank_score=0.7,
        )
        diagnostics: dict[str, object] = {}
        traits = service.analyze_query("assessment fee personal loan")
        hybrid = mock.Mock()
        hybrid.hits = (candidate,)
        hybrid.query_vector = None
        hybrid.diagnostics = {"rerank_duration_ms": 321}

        with (
            mock.patch.object(service, "_effective_chunk_cap", return_value=2),
            mock.patch.object(service, "search_free_text", return_value=hybrid),
            mock.patch.object(service, "_prioritize_token_hits", return_value=[candidate]),
            mock.patch.object(service, "_rerank_candidates") as rerank_mock,
            mock.patch.object(service, "_apply_vector_threshold", return_value=[candidate]),
            mock.patch.object(service, "_build_scope_summary_from_candidates", return_value={"total_matches": 1}),
            mock.patch.object(service, "_filler_tokens_for_business", return_value=()),
            mock.patch.object(service, "_mmr_select", return_value=[candidate]),
        ):
            hits = service._chunk_hits(
                business,
                traits=traits,
                limit=10,
                diagnostics=diagnostics,
                vector_ceiling=0.5,
                feature_state=mock.Mock(),
                table_context={},
            )

        self.assertEqual(hits, (candidate,))
        rerank_mock.assert_not_called()
        self.assertEqual(diagnostics.get("rerank_duration_ms"), 321)
        self.assertTrue(diagnostics.get("chunk_hits_rerank_reused"))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_chunk_hits_preserves_reranked_head_before_mmr(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        business = mock.Mock()
        business.id = uuid.uuid4()
        upload = mock.Mock()
        upload.id = uuid.uuid4()

        def _candidate(text: str, lexical: float, rerank: float) -> ChunkResult:
            chunk = mock.Mock()
            chunk.id = uuid.uuid4()
            chunk.upload = upload
            chunk.content = text
            chunk.metadata = {"index_type": "text"}
            return ChunkResult(
                chunk=chunk,
                source_stage="hybrid",
                lexical_score=lexical,
                rerank_score=rerank,
            )

        top = _candidate("International Delivery Shipment Fees USD 30", 0.9, 1.6)
        second = _candidate("International transfer fee details", 0.5, 0.8)
        third = _candidate("International ATM withdrawal fees", 0.4, 0.7)
        fourth = _candidate("International purchase limits", 0.3, 0.6)
        candidates = [top, second, third, fourth]
        diagnostics: dict[str, object] = {}
        traits = service.analyze_query("International Delivery Shipment Fees")
        hybrid = mock.Mock()
        hybrid.hits = tuple(candidates)
        hybrid.query_vector = [0.1, 0.2, 0.3]
        hybrid.diagnostics = {"rerank_duration_ms": 111}

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(service, "_effective_chunk_cap", return_value=2))
            stack.enter_context(mock.patch.object(service, "search_free_text", return_value=hybrid))
            stack.enter_context(mock.patch.object(service, "_prioritize_token_hits", return_value=candidates))
            stack.enter_context(mock.patch.object(service, "_apply_vector_threshold", return_value=candidates))
            stack.enter_context(
                mock.patch.object(
                    service,
                    "_build_scope_summary_from_candidates",
                    return_value={"total_matches": 4},
                )
            )
            stack.enter_context(mock.patch.object(service, "_filler_tokens_for_business", return_value=()))
            mmr_mock = stack.enter_context(mock.patch.object(service, "_mmr_select", return_value=[fourth]))
            hits = service._chunk_hits(
                business,
                traits=traits,
                limit=4,
                diagnostics=diagnostics,
                vector_ceiling=0.5,
                feature_state=mock.Mock(),
                table_context={"comprehensive_intent": False},
            )

        self.assertEqual(hits[0], top)
        self.assertEqual(hits[1], second)
        self.assertEqual(hits[2], third)
        self.assertEqual(hits[3], fourth)
        mmr_mock.assert_called_once()
        self.assertEqual(diagnostics.get("chunk_hits_mmr_preserve_head"), 3)
        self.assertTrue(diagnostics.get("chunk_hits_mmr_applied"))


class KnowledgeSearchServiceAutoArbitrationTests(SimpleTestCase):
    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_arbitration_selects_table_when_margin_is_clear(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        arbitration = service._arbitrate_auto_mode(
            scoring_diagnostics={
                "auto_table_score": 0.79,
                "auto_text_score": 0.34,
                "auto_score_table_hits": 3,
                "auto_score_text_hits": 2,
            },
            table_intent_hint=False,
        )
        self.assertEqual(arbitration["auto_arbitration_decision"], "table")
        self.assertFalse(arbitration["auto_arbitration_needs_clarification"])
        self.assertTrue(arbitration["auto_arbitration_table_intent"])

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_arbitration_selects_text_when_margin_is_clear(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        arbitration = service._arbitrate_auto_mode(
            scoring_diagnostics={
                "auto_table_score": 0.22,
                "auto_text_score": 0.71,
                "auto_score_table_hits": 2,
                "auto_score_text_hits": 3,
            },
            table_intent_hint=True,
        )
        self.assertEqual(arbitration["auto_arbitration_decision"], "text")
        self.assertFalse(arbitration["auto_arbitration_needs_clarification"])
        self.assertFalse(arbitration["auto_arbitration_table_intent"])

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_arbitration_marks_ambiguous_scores_for_clarification(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        arbitration = service._arbitrate_auto_mode(
            scoring_diagnostics={
                "auto_table_score": 0.61,
                "auto_text_score": 0.57,
                "auto_score_table_hits": 3,
                "auto_score_text_hits": 3,
            },
            table_intent_hint=True,
        )
        self.assertEqual(arbitration["auto_arbitration_decision"], "tie_fallback_to_hint")
        self.assertFalse(arbitration["auto_arbitration_needs_clarification"])
        self.assertTrue(arbitration["auto_arbitration_table_intent"])

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_dynamic_ambiguity_question_uses_table_and_text_evidence_labels(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        table_chunk = mock.Mock()
        table_chunk.metadata = {
            "index_type": "table",
            "is_table_chunk": True,
            "table_title": "Card fees",
            "row_label": "Gold card",
        }
        table_chunk.content = "[Table] Card fees\nplan: Gold card\nannual fee: 199"
        text_chunk = mock.Mock()
        text_chunk.metadata = {"index_type": "text", "section_heading": "Benefits"}
        text_chunk.content = "Gold card benefits include airport lounge access and cashback rewards."
        table_hit = ChunkResult(
            chunk=table_chunk,
            source_stage="unit",
            rerank_score=0.72,
            lexical_score=0.61,
            diagnostics={
                "specific_match_tokens": ("gold", "fee"),
                "header_match_tokens": ("annual", "fee"),
            },
        )
        text_hit = ChunkResult(
            chunk=text_chunk,
            source_stage="unit",
            rerank_score=0.7,
            lexical_score=0.64,
        )
        question, table_label, text_label = service._build_auto_ambiguity_clarification_question(
            hits=(table_hit, text_hit),
            query_tokens=("gold", "fee", "benefits"),
        )
        self.assertIn("table evidence around", question.lower())
        self.assertIn("text evidence around", question.lower())
        self.assertIn("both?", question.lower())
        self.assertEqual(table_label, "gold fee")
        self.assertIn("gold card benefits", text_label.lower())


class KnowledgeSearchServiceScopeSummaryTests(SimpleTestCase):
    @staticmethod
    def _make_chunk(
        *,
        index_type: str,
        content: str,
        upload_id: uuid.UUID | None = None,
        metadata: dict[str, object] | None = None,
    ):
        chunk = mock.Mock()
        payload = {"index_type": index_type}
        if index_type == "table":
            payload["is_table_chunk"] = True
        if metadata:
            payload.update(metadata)
        chunk.metadata = payload
        chunk.content = content
        chunk.upload_id = upload_id or uuid.uuid4()
        return chunk

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_scope_summary_detects_broad_fee_scope_before_clipping(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        doc_a = uuid.uuid4()
        doc_b = uuid.uuid4()
        doc_c = uuid.uuid4()
        hits = (
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="table",
                    content="service: outgoing transfers; plus: free",
                    upload_id=doc_a,
                ),
                source_stage="unit",
            ),
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="table",
                    content="service: outgoing transfers; plus: free",
                    upload_id=doc_b,
                ),
                source_stage="unit",
            ),
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="table",
                    content="service: loan service fees monthly; plus: egp 120",
                    upload_id=doc_b,
                ),
                source_stage="unit",
            ),
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="table",
                    content="service: payment of invoices; plus: customer applied fees",
                    upload_id=doc_c,
                ),
                source_stage="unit",
            ),
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="table",
                    content="service: minimum balance threshold; plus: egp 20,000",
                    upload_id=doc_c,
                ),
                source_stage="unit",
            ),
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="text",
                    content="section: remittance fees and transfer policies",
                    upload_id=doc_a,
                    metadata={"section_heading": "remittance fees"},
                ),
                source_stage="unit",
            ),
        )
        summary = service._build_scope_summary_from_candidates(
            hits,
            query_tokens=("what", "are", "the", "fees", "for", "plus", "customers"),
            filler_tokens={"what", "are", "the", "for"},
        )
        self.assertEqual(summary["total_matches"], 6)
        self.assertEqual(summary["distinct_docs"], 3)
        self.assertTrue(summary["is_broad_scope"])
        self.assertGreaterEqual(len(summary["category_counts"]), 3)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_scope_summary_marks_narrow_scope(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        doc_a = uuid.uuid4()
        hits = (
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="table",
                    content="service: outgoing transfers; plus: free",
                    upload_id=doc_a,
                ),
                source_stage="unit",
            ),
            ChunkResult(
                chunk=self._make_chunk(
                    index_type="table",
                    content="service: outgoing transfers; plus: free",
                    upload_id=doc_a,
                ),
                source_stage="unit",
            ),
        )
        summary = service._build_scope_summary_from_candidates(
            hits,
            query_tokens=("plus", "fees"),
            filler_tokens=set(),
        )
        self.assertEqual(summary["total_matches"], 2)
        self.assertEqual(summary["distinct_docs"], 1)
        self.assertFalse(summary["is_broad_scope"])
        self.assertGreaterEqual(summary["category_counts"].get("outgoing transfers", 0), 1)

    def test_scope_category_normalizer_strips_structural_noise(self) -> None:
        normalized = KnowledgeSearchService._normalize_scope_category_value(
            "cash, withdrawal, fees, from, international, atms",
            max_chars=96,
        )
        self.assertEqual(
            normalized,
            "cash withdrawal fees international atms",
        )

        normalized_table_suffix = KnowledgeSearchService._normalize_scope_category_value(
            "cheques-en - table 2",
            max_chars=96,
        )
        self.assertEqual(normalized_table_suffix, "cheques")

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_scope_categories_for_contract_returns_ranked_full_list(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        scope_summary = {
            "category_counts": {
                "statement fees": 3,
                "administrative fees": 2,
                "loan service fees": 5,
                "card issuance fees": 2,
                "outgoing transfers": 6,
                "minimum balance threshold fees": 4,
                "payment of invoices": 4,
                "returned cheque fees": 1,
                "customer service fees": 1,
                "collection fees": 1,
                "online banking fees": 1,
                "mobile wallet fees": 1,
                "trade bills fees": 1,
                "remittance fees": 1,
                "account closure fees": 1,
            },
        }

        categories, top_categories = service._scope_categories_for_contract(
            scope_summary=scope_summary,
        )

        self.assertEqual(len(categories), 15)
        self.assertEqual(
            categories[:7],
            (
                "outgoing transfers",
                "loan service fees",
                "minimum balance threshold fees",
                "payment of invoices",
                "statement fees",
                "administrative fees",
                "card issuance fees",
            ),
        )
        self.assertEqual(
            top_categories,
            categories[: service.scope_top_category_max],
        )


class KnowledgeSearchServiceAliasTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="searcher@example.com", first_name="Search")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Search Co",
            industry="travel",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Trips",
        )
        self.chunk = KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=0,
            content="Trip 101 includes premium lodging.",
            metadata={"entity_name": "Trip 101"},
        )
        entity = KnowledgeEntity.objects.create(
            business_profile=self.business,
            upload=self.upload,
            chunk_id=self.chunk.id,
            entity_type="trip",
            entity_name="Trip 101",
        )
        KnowledgeAlias.objects.create(
            business_profile=self.business,
            entity=entity,
            alias_raw="Trip-101",
            alias_normalized="trip-101",
            alias_search_vector="trip 101",
        )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_alias_short_circuit_returns_neighbor_snippet(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("TRIP-101")
        alias_result = service.search_by_alias(
            business_profile=self.business,
            traits=traits,
        )
        self.assertTrue(alias_result.short_circuit)
        result = service.search(
            business_profile=self.business,
            query="TRIP-101",
            traits=traits,
            alias_result=alias_result,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.snippets), 1)
        snippet = result.snippets[0]
        self.assertEqual(snippet.chunk_id, self.chunk.id)
        self.assertEqual(snippet.upload_id, self.upload.id)
        self.assertEqual(result.diagnostics.get("path"), "alias_exact")
        self.assertEqual(result.diagnostics.get("alias_stage"), "alias_exact")
        self.assertEqual(result.diagnostics.get("snippet_count"), len(result.snippets))
        self.assertEqual(result.diagnostics.get("token_count"), traits.token_count)
        self.assertTrue(result.diagnostics.get("identifier_like"))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_alias_fuzzy_hits_flow_into_hybrid(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        KnowledgeAlias.objects.filter(business_profile=self.business).update(
            alias_raw="grand-luxor",
            alias_normalized="grand-luxor",
            alias_search_vector="grand luxor",
        )
        traits = service.analyze_query("Grand Luxor")
        alias_result = service.search_by_alias(
            business_profile=self.business,
            traits=traits,
        )
        self.assertFalse(alias_result.short_circuit)
        self.assertGreaterEqual(len(alias_result.hits), 1)
        result = service.search(
            business_profile=self.business,
            query="Grand Luxor",
            traits=traits,
            alias_result=alias_result,
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.snippets)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_alias_search_handles_noisy_multiword_queries(self, _build_embeddings) -> None:
        chunk = KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=1,
            content="Finance role at Michael Page in Dubai.",
            metadata={"entity_name": "Michael Page"},
        )
        entity = KnowledgeEntity.objects.create(
            business_profile=self.business,
            upload=self.upload,
            chunk_id=chunk.id,
            entity_type="job",
            entity_name="Michael Page",
        )
        KnowledgeAlias.objects.create(
            business_profile=self.business,
            entity=entity,
            alias_raw="Michael Page",
            alias_normalized="michael-page",
            alias_search_vector="michael page",
        )

        service = KnowledgeSearchService()
        query = "Michael Page job opportunities in finance"
        traits = service.analyze_query(query)
        alias_result = service.search_by_alias(
            business_profile=self.business,
            traits=traits,
        )
        self.assertGreaterEqual(len(alias_result.hits), 1)
        result = service.search(
            business_profile=self.business,
            query=query,
            traits=traits,
            alias_result=alias_result,
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(any(snippet.chunk_id == chunk.id for snippet in result.snippets))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_not_found_status_when_no_chunks(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        empty_registration = RegistrationSession.objects.create(user=self.user)
        empty_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=empty_registration,
            name="Empty Co",
            industry="travel",
        )
        result = service.search(
            business_profile=empty_business,
            query="missing",
        )
        self.assertEqual(result.status, "not_found")
        self.assertFalse(result.snippets)


class KnowledgeSearchServiceTableTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="table@example.com", first_name="Table")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Ledger Co",
            industry="finance",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Pricing Sheet",
        )
        with tenant_context(self.business.id):
            self.table = KnowledgeUploadTable.objects.create(
                upload=self.upload,
                title="Card Pricing",
                section_heading="pricing",
                order_index=1,
                column_schema=["plan", "annual fee"],
                metadata={"sheet_name": "Plans"},
            )
            self.row = KnowledgeUploadTableRow.objects.create(
                table=self.table,
                row_index=1,
                raw_text="Gold plan annual fee 199",
                metadata={},
            )
            KnowledgeUploadTableCell.objects.create(
                table=self.table,
                row=self.row,
                column_index=0,
                column_key="plan",
                raw_text="Gold",
            )
            KnowledgeUploadTableCell.objects.create(
                table=self.table,
                row=self.row,
                column_index=1,
                column_key="annual fee",
                raw_text="$199",
            )
            self.row_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=0,
                content="[Table] Card Pricing\n[Row] 1\nplan: Gold\nannual fee: $199",
                metadata={
                    "is_table_chunk": True,
                    "table_id": str(self.table.id),
                    "table_chunk_role": "row",
                    "table_row_index": self.row.row_index,
                    "table_page_number": 1,
                    "index_type": "table",
                },
            )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_direct_path_returns_row_snippet(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        result = service.search(
            business_profile=self.business,
            query="What is the annual fee for Gold plan?",
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.snippets)
        snippet = result.snippets[0]
        self.assertIn(snippet.source, {"table_direct", "File Upload"})
        self.assertEqual(snippet.upload_id, self.upload.id)
        self.assertIn("Gold", snippet.summary)
        self.assertIn("plan: Gold", snippet.summary)
        self.assertIn("annual fee: $199", snippet.summary)
        self.assertEqual(snippet.summary.count("plan: Gold"), 1)
        self.assertEqual(snippet.summary.count("annual fee: $199"), 1)
        self.assertEqual(snippet.chunk_id, self.row_chunk.id)
        self.assertEqual(snippet.id, self.row_chunk.id)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_specific_fallback_keeps_table_hits_when_no_text_hits(self, _build_embeddings) -> None:
        # Add table chunks (as produced by ingestion schema chunking) so hybrid retrieval has candidates.
        KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=2,
            content="[Table] Card Pricing\nplan: Gold; annual fee: $199",
            metadata={
                "is_table_chunk": True,
                "is_table_preview": True,
                "table_id": str(self.table.id),
                "table_chunk_role": "preview",
                "index_type": "table",
            },
        )
        KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=3,
            content="[Table] Card Pricing\n[Row] 1\nplan: Gold\nannual fee: $199",
            metadata={
                "is_table_chunk": True,
                "is_table_preview": False,
                "table_id": str(self.table.id),
                "table_chunk_role": "row",
                "table_row_index": 1,
                "index_type": "table",
            },
        )

        service = KnowledgeSearchService()
        # "platinum" becomes a specific token that won't match any table header/row labels in this tenant.
        result = service.search(
            business_profile=self.business,
            query="What is the annual fee for Platinum plan?",
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.snippets)
        self.assertIn(
            result.diagnostics.get("index_route"),
            {"mixed_primary_table_biased", "mixed_primary_table_only"},
        )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_parallel_rrf_does_not_force_context_hits(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        with tenant_context(self.business.id):
            text_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=9,
                content="Gold plan annual fee details with full textual context.",
                metadata={"index_type": "text"},
            )
        table_hit = ChunkResult(chunk=self.row_chunk, source_stage="hybrid")
        text_hit = ChunkResult(chunk=text_chunk, source_stage="hybrid")
        vector_table_snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Vector Table",
            summary="table vector",
            source="hybrid",
            upload_id=self.upload.id,
            chunk_id=self.row_chunk.id,
            chunk_index=self.row_chunk.chunk_index,
            is_table_chunk=True,
            table_id=str(self.table.id),
        )
        table_direct_snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Table Direct",
            summary="table direct",
            source="table_direct",
            upload_id=self.upload.id,
            chunk_id=self.row_chunk.id,
            chunk_index=self.row_chunk.chunk_index,
            is_table_chunk=True,
            table_id=str(self.table.id),
        )
        table_context = {
            "has_intent": True,
            "comprehensive_intent": False,
            "query_classification": None,
            "matched_columns": set(),
            "matched_columns_query": set(),
            "matched_columns_tokens": set(),
            "matched_columns_specific": set(),
            "matched_row_labels": set(),
            "matched_keywords": set(),
            "numeric_intent": False,
            "available_columns": set(),
            "semantic_columns": set(),
            "matched_column_count": 0,
            "query_tokens": {"gold", "annual", "fee"},
            "specific_tokens": set(),
            "table_dominant": True,
            "table_upload_ratio": 1.0,
            "table_count": 1,
            "table_uploads": 1,
            "allow_generic": True,
        }
        captured: dict[str, list[KnowledgeSnippet]] = {}

        def _capture_rrf(*, vector_snippets, table_snippets, k=60):
            captured["vector"] = list(vector_snippets)
            captured["table"] = list(table_snippets)
            return list(vector_snippets) + list(table_snippets)

        with (
            mock.patch.object(service, "_table_query_context", return_value=table_context),
            mock.patch.object(service, "_business_has_tables", return_value=True),
            mock.patch.object(service, "search_by_alias", return_value=AliasSearchResult(tuple(), {})),
            mock.patch.object(service, "_chunk_hits", return_value=(table_hit, text_hit)),
            mock.patch.object(service, "_table_search_snippets", return_value=(table_direct_snippet,)),
            mock.patch.object(service, "_search_chunks", return_value=(vector_table_snippet,)),
            mock.patch.object(service, "_rrf_fusion_snippets", side_effect=_capture_rrf),
        ):
            result = service.search(
                business_profile=self.business,
                query="What is the annual fee for Gold plan?",
                limit=5,
            )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.diagnostics.get("path"), "parallel_rrf")
        self.assertIsNone(result.diagnostics.get("rrf_context_count"))
        self.assertTrue(captured.get("vector"))
        self.assertTrue(all(snippet.is_table_chunk for snippet in captured.get("vector", [])))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_parallel_rrf_requires_table_intent(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        with tenant_context(self.business.id):
            text_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=10,
                content="International Delivery Shipment Fees USD 30",
                metadata={"index_type": "text"},
            )
        text_hit = ChunkResult(chunk=text_chunk, source_stage="hybrid")
        text_snippet = KnowledgeSnippet(
            id=text_chunk.id,
            title="CIB-Customer Service-EN.pdf – chunk 0",
            summary="International Delivery Shipment Fees USD 30",
            source="File Upload",
            content="International Delivery Shipment Fees USD 30",
            upload_id=self.upload.id,
            chunk_id=text_chunk.id,
            chunk_index=text_chunk.chunk_index,
            is_table_chunk=False,
        )
        table_context = {
            "has_intent": False,
            "comprehensive_intent": False,
            "query_classification": None,
            "matched_columns": set(),
            "matched_columns_query": set(),
            "matched_columns_tokens": set(),
            "matched_columns_specific": set(),
            "matched_row_labels": set(),
            "matched_keywords": set(),
            "numeric_intent": False,
            "available_columns": set(),
            "semantic_columns": set(),
            "matched_column_count": 0,
            "query_tokens": {"international", "delivery", "shipment", "fees"},
            "specific_tokens": {"international", "delivery", "shipment"},
            "table_dominant": True,
            "table_upload_ratio": 1.0,
            "table_count": 1,
            "table_uploads": 1,
            "allow_generic": False,
        }

        with (
            mock.patch.object(service, "_table_query_context", return_value=table_context),
            mock.patch.object(service, "_business_has_tables", return_value=True),
            mock.patch.object(service, "search_by_alias", return_value=AliasSearchResult(tuple(), {})),
            mock.patch.object(service, "_chunk_hits", return_value=(text_hit,)),
            mock.patch.object(service, "_search_chunks", return_value=(text_snippet,)),
            mock.patch.object(service, "_table_search_snippets") as table_search_mock,
        ):
            result = service.search(
                business_profile=self.business,
                query="What are the International Delivery Shipment Fees?",
                limit=5,
            )

        self.assertEqual(result.status, "ok")
        self.assertNotEqual(result.diagnostics.get("path"), "parallel_rrf")
        table_search_mock.assert_not_called()


class KnowledgeSearchServiceRegressionTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="regression@example.com", first_name="Regression")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Regression Co",
            industry="multi",
        )
        self.service = KnowledgeSearchService()

    def _chunk(self, content: str, entity_name: str, entity_type: str = "generic") -> KnowledgeUploadChunk:
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name=entity_name,
        )
        return KnowledgeUploadChunk.objects.create(
            upload=upload,
            business_profile=self.business,
            chunk_index=0,
            content=content,
            metadata={"entity_name": entity_name, "entity_type": entity_type},
        )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_chatty_company_query_hits_hybrid(self, _build_embeddings) -> None:
        chunk = self._chunk("Opportunities at Michael Page across finance teams.", "Michael Page", "company")
        result = self.service.search(
            business_profile=self.business,
            query="tell me about Michael Page job opportunities in europe",
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(any(snippet.chunk_id == chunk.id for snippet in result.snippets))
        self.assertEqual(result.diagnostics.get("path"), "hybrid")

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_row_cap_honors_requested_limit(self, _build_embeddings) -> None:
        cap = self.service._table_row_result_cap_for_business(self.business, requested=10)
        self.assertGreaterEqual(cap, 10)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_row_cap_uses_business_override_as_floor(self, _build_embeddings) -> None:
        self.business.metadata = {
            "rag_overrides": {
                "table_results_limit": 25,
            }
        }
        self.business.save(update_fields=["metadata"])
        cap = self.service._table_row_result_cap_for_business(self.business, requested=10)
        self.assertEqual(cap, 25)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_base_queryset_keeps_chunks_with_missing_search_tier(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Tier Coverage",
        )
        with tenant_context(self.business.id):
            missing_tier_chunk = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=0,
                content="Plain text chunk with missing search_tier.",
                metadata={"index_type": "text"},
            )
            drill_down_chunk = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=1,
                content="Table drill row chunk.",
                metadata={"index_type": "table", "search_tier": "drill_down", "is_table_chunk": True},
            )
            primary_table_chunk = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=2,
                content="Primary table summary chunk.",
                metadata={"index_type": "table", "search_tier": "primary", "is_table_chunk": True},
            )

        with tenant_context(self.business.id):
            chunk_ids = set(service._base_chunk_queryset(self.business).values_list("id", flat=True))

        self.assertIn(missing_tier_chunk.id, chunk_ids)
        self.assertIn(primary_table_chunk.id, chunk_ids)
        self.assertNotIn(drill_down_chunk.id, chunk_ids)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_intent_not_triggered_by_generic_fee_word_without_table_signals(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("What is the fee for this?")
        with tenant_context(self.business.id):
            table_context = service._table_query_context(self.business, traits)
        self.assertFalse(table_context.get("has_intent"))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_chatty_plan_query_hits_hybrid(self, _build_embeddings) -> None:
        chunk = self._chunk("Gold Plan annual fee is $199 with bonus points.", "Gold Plan", "plan")
        result = self.service.search(
            business_profile=self.business,
            query="details on the Gold plan annual fee please",
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(any(snippet.chunk_id == chunk.id for snippet in result.snippets))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_chatty_clinic_query_hits_hybrid(self, _build_embeddings) -> None:
        chunk = self._chunk("Helio Health Clinic offers primary care and pediatrics.", "Helio Health Clinic", "clinic")
        result = self.service.search(
            business_profile=self.business,
            query="doctor availability at Helio Health Clinic please",
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(any(snippet.chunk_id == chunk.id for snippet in result.snippets))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_chatty_product_query_hits_hybrid(self, _build_embeddings) -> None:
        chunk = self._chunk("Nebula Card Metal has 5x bonus and lounge access.", "Nebula Card Metal", "product")
        result = self.service.search(
            business_profile=self.business,
            query="what's the bonus on nebula card metal card?",
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(any(snippet.chunk_id == chunk.id for snippet in result.snippets))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_fallback_runs_for_job_queries(self, _build_embeddings) -> None:
        job_registration = RegistrationSession.objects.create(user=self.user)
        job_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=job_registration,
            name="Jobs Co",
            industry="recruiting",
        )
        upload = KnowledgeUpload.objects.create(
            business_profile=job_business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Jobs Sheet",
        )
        table = KnowledgeUploadTable.objects.create(
            upload=upload,
            title="Job Listings",
            section_heading="jobs",
            order_index=1,
            column_schema=["company", "title", "location"],
            metadata={"sheet_name": "Jobs"},
        )
        row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=1,
            raw_text="Michael Page Senior PM London",
            metadata={},
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=0,
            column_key="company",
            raw_text="Michael Page",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=1,
            column_key="title",
            raw_text="Senior Product Manager",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=2,
            column_key="location",
            raw_text="London",
        )

        result = self.service.search(
            business_profile=job_business,
            query="Michael Page job opportunities",
        )
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.snippets)
        self.assertIn(result.diagnostics.get("path"), {"table_direct", "table_blended"})
        self.assertIn(
            result.diagnostics.get("table_reason"),
            {"fallback_no_chunk_candidates", "no_chunk_candidates"},
        )

    def test_expand_table_rows_propagates_parent_relevance_scores(self) -> None:
        with tenant_context(self.business.id):
            upload = KnowledgeUpload.objects.create(
                business_profile=self.business,
                user=self.user,
                source_type=KnowledgeSourceType.FILE,
                status=KnowledgeStatus.ACTIVE,
                display_name="Credit Fees",
            )
            table_id = "550e8400-e29b-41d4-a716-446655440999"
            parent_chunk = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=0,
                content="Table preview for credit card issuance fees",
                metadata={
                    "is_table_chunk": True,
                    "is_table_preview": True,
                    "table_chunk_role": "parent",
                    "table_id": table_id,
                },
            )
            KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=1,
                content="[Table] Credit Fees\n[Row] 0\nCard: Platinum\nIssuance Fee: EGP 700",
                metadata={
                    "is_table_chunk": True,
                    "table_chunk_role": "row",
                    "table_id": table_id,
                    "table_row_index": 0,
                },
            )

            parent_hit = ChunkResult(
                chunk=parent_chunk,
                source_stage="hybrid",
                lexical_score=0.38,
                alias_confidence=0.14,
                rerank_score=0.62,
            )

            expanded = self.service._expand_table_rows(
                self.business,
                [parent_hit],
                max_rows_per_table=10,
                query_tokens=("credit", "card", "issuance", "fees"),
            )
        self.assertTrue(expanded)
        self.assertGreater(expanded[0].lexical_score, 0.0)
        self.assertGreater(expanded[0].rerank_score, 0.0)

    def test_expand_table_rows_prefers_parent_shard_hint(self) -> None:
        with tenant_context(self.business.id):
            upload = KnowledgeUpload.objects.create(
                business_profile=self.business,
                user=self.user,
                source_type=KnowledgeSourceType.FILE,
                status=KnowledgeStatus.ACTIVE,
                display_name="Sharded Fees",
            )
            table_id = "11111111-2222-3333-4444-555555555555"
            parent_chunk = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=0,
                content="Table shard summary for issuance fees",
                metadata={
                    "is_table_chunk": True,
                    "is_table_preview": True,
                    "table_chunk_role": "summary",
                    "table_id": table_id,
                    "table_row_shard_index": 2,
                },
            )

            for idx, shard in enumerate([0, 0, 1, 1, 2, 2], start=1):
                KnowledgeUploadChunk.objects.create(
                    upload=upload,
                    business_profile=self.business,
                    chunk_index=idx,
                    content=f"[Row] {idx} shard={shard}",
                    metadata={
                        "is_table_chunk": True,
                        "table_chunk_role": "row",
                        "table_id": table_id,
                        "table_row_index": idx,
                        "table_row_shard_index": shard,
                    },
                )

            parent_hit = ChunkResult(
                chunk=parent_chunk,
                source_stage="hybrid",
                lexical_score=0.5,
                alias_confidence=0.2,
                rerank_score=0.7,
            )

            expanded = self.service._expand_table_rows(
                self.business,
                [parent_hit],
                max_rows_per_table=2,
                query_tokens=("issuance", "fees"),
            )

        self.assertEqual(len(expanded), 2)
        shards = {
            (hit.chunk.metadata or {}).get("table_row_shard_index")
            for hit in expanded
        }
        self.assertEqual(shards, {2})

    def test_expand_table_rows_can_complete_same_table_from_row_hit(self) -> None:
        with tenant_context(self.business.id):
            upload = KnowledgeUpload.objects.create(
                business_profile=self.business,
                user=self.user,
                source_type=KnowledgeSourceType.FILE,
                status=KnowledgeStatus.ACTIVE,
                display_name="Overdraft Fees",
            )
            table_id = "22222222-3333-4444-5555-666666666666"
            secured_row = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=0,
                content=(
                    "[Table] Overdraft Fees\n[Row] 0\n"
                    "Highest monthly debit balance for overdraft (Paid Monthly)\n"
                    "0.1% of highest closing debit balance for secured overdraft"
                ),
                metadata={
                    "is_table_chunk": True,
                    "table_chunk_role": "row",
                    "table_id": table_id,
                    "table_row_index": 0,
                },
            )
            unsecured_row = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=1,
                content=(
                    "[Table] Overdraft Fees\n[Row] 1\n"
                    "Highest monthly debit balance for overdraft (Paid Monthly)\n"
                    "0.15% of highest closing debit balance for revolving unsecured overdraft"
                ),
                metadata={
                    "is_table_chunk": True,
                    "table_chunk_role": "row",
                    "table_id": table_id,
                    "table_row_index": 1,
                },
            )

            row_hit = ChunkResult(
                chunk=secured_row,
                source_stage="table_direct",
                lexical_score=0.55,
                alias_confidence=0.1,
                rerank_score=0.74,
            )

            expanded = self.service._expand_table_rows(
                self.business,
                [row_hit],
                max_rows_per_table=10,
                query_tokens=("overdraft", "secured", "unsecured"),
            )

        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0].chunk_id, unsecured_row.id)
        self.assertEqual(expanded[0].diagnostics.get("expanded_from_stage"), "table_direct")

    def test_merge_expanded_table_hits_suppresses_parent_when_row_coverage_is_sufficient(self) -> None:
        with tenant_context(self.business.id):
            upload = KnowledgeUpload.objects.create(
                business_profile=self.business,
                user=self.user,
                source_type=KnowledgeSourceType.FILE,
                status=KnowledgeStatus.ACTIVE,
                display_name="Overdraft Fees",
            )
            table_id = "33333333-4444-5555-6666-777777777777"
            parent_chunk = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=0,
                content="Table preview for overdraft fee schedule",
                metadata={
                    "is_table_chunk": True,
                    "is_table_preview": True,
                    "table_chunk_role": "parent",
                    "table_id": table_id,
                },
            )
            secured_row = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=1,
                content="Highest monthly debit balance for overdraft 0.1% secured overdraft",
                metadata={
                    "is_table_chunk": True,
                    "table_chunk_role": "row",
                    "table_id": table_id,
                    "table_row_index": 0,
                },
            )
            unsecured_row = KnowledgeUploadChunk.objects.create(
                upload=upload,
                business_profile=self.business,
                chunk_index=2,
                content="Highest monthly debit balance for overdraft 0.15% revolving unsecured overdraft",
                metadata={
                    "is_table_chunk": True,
                    "table_chunk_role": "row",
                    "table_id": table_id,
                    "table_row_index": 1,
                },
            )

            merged_hits, merge_diagnostics = self.service._merge_expanded_table_hits(
                chunk_hits=(
                    ChunkResult(chunk=parent_chunk, source_stage="hybrid", rerank_score=0.8),
                    ChunkResult(chunk=secured_row, source_stage="table_direct", rerank_score=0.72),
                ),
                expanded_rows=(
                    ChunkResult(chunk=unsecured_row, source_stage="table_row_expansion", rerank_score=0.66),
                ),
                query_tokens=("overdraft", "secured", "unsecured"),
            )

        merged_ids = {hit.chunk_id for hit in merged_hits}
        self.assertIn(secured_row.id, merged_ids)
        self.assertIn(unsecured_row.id, merged_ids)
        self.assertNotIn(parent_chunk.id, merged_ids)
        self.assertEqual(merge_diagnostics["parent_chunks_suppressed"], 1)


class KnowledgeSearchServiceClarificationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="clarify@example.com", first_name="Clarify")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Clarify Co",
            industry="operations",
        )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_low_confidence_intent_does_not_block_with_needs_clarification(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        classification = QueryClassification(
            intent=QueryIntent.EXPLORATORY,
            confidence=0.28,
            reasoning="low-confidence fallback",
            retrieval_hints={},
            requires_clarification=True,
            clarification_question="Do you want one specific record or a full list?",
        )
        table_context = {
            "has_intent": False,
            "comprehensive_intent": False,
            "query_classification": classification,
            "intent_fallback_attempted": True,
            "intent_fallback_applied": False,
            "matched_columns": set(),
            "matched_columns_query": set(),
            "matched_columns_tokens": set(),
            "matched_columns_specific": set(),
            "matched_row_labels": set(),
            "matched_keywords": set(),
            "numeric_intent": False,
            "available_columns": set(),
            "semantic_columns": set(),
            "matched_column_count": 0,
            "query_tokens": set(),
            "specific_tokens": set(),
            "table_dominant": False,
            "table_upload_ratio": 0.0,
            "table_count": 0,
            "table_uploads": 0,
            "allow_generic": False,
            "tenant_lexicon_entity_terms_count": 0,
            "tenant_lexicon_attribute_terms_count": 0,
        }

        with (
            mock.patch.object(service, "_table_query_context", return_value=table_context),
            mock.patch.object(service, "_business_has_tables", return_value=False),
            mock.patch.object(service, "search_by_alias", return_value=AliasSearchResult(tuple(), {})),
            mock.patch.object(service, "_chunk_hits", return_value=tuple()) as chunk_hits,
        ):
            result = service.search(
                business_profile=self.business,
                query="show me what to do",
            )

        self.assertEqual(result.status, "not_found")
        self.assertFalse(result.snippets)
        self.assertTrue(bool(result.diagnostics.get("clarification_suggested")))
        self.assertEqual(result.diagnostics.get("clarification_reason"), "low_intent_confidence")
        self.assertEqual(result.diagnostics.get("clarification_question"), "Do you want one specific record or a full list?")
        auto_contract = result.diagnostics.get("auto_decision_contract") or {}
        self.assertFalse(auto_contract.get("needs_clarification"))
        self.assertEqual(auto_contract.get("table_score"), 0.0)
        self.assertEqual(auto_contract.get("text_score"), 0.0)
        self.assertEqual(auto_contract.get("margin"), 0.0)
        chunk_hits.assert_called()

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_ambiguous_auto_scores_do_not_block_with_needs_clarification(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        classification = QueryClassification(
            intent=QueryIntent.SPECIFIC_LOOKUP,
            confidence=0.83,
            reasoning="clear intent",
            retrieval_hints={},
            requires_clarification=False,
            clarification_question="",
        )
        table_context = {
            "has_intent": True,
            "comprehensive_intent": False,
            "query_classification": classification,
            "intent_fallback_attempted": False,
            "intent_fallback_applied": False,
            "matched_columns": {"fee"},
            "matched_columns_query": {"fee"},
            "matched_columns_tokens": {"fee"},
            "matched_columns_specific": {"fee"},
            "matched_row_labels": set(),
            "matched_keywords": set(),
            "numeric_intent": False,
            "available_columns": {"fee"},
            "semantic_columns": set(),
            "matched_column_count": 1,
            "query_tokens": {"gold", "fee"},
            "specific_tokens": {"gold", "fee"},
            "table_dominant": True,
            "table_upload_ratio": 0.7,
            "table_count": 2,
            "table_uploads": 1,
            "allow_generic": True,
            "tenant_lexicon_entity_terms_count": 0,
            "tenant_lexicon_attribute_terms_count": 0,
        }
        with (
            mock.patch.object(service, "_table_query_context", return_value=table_context),
            mock.patch.object(service, "_business_has_tables", return_value=True),
            mock.patch.object(service, "search_by_alias", return_value=AliasSearchResult(tuple(), {})),
            mock.patch.object(service, "_chunk_hits", return_value=tuple()),
            mock.patch.object(service, "_table_search_snippets", return_value=tuple()),
            mock.patch.object(
                service,
                "_score_auto_mode_candidates",
                return_value={
                    "auto_score_version": "v2",
                    "auto_score_sample_size": 2,
                    "auto_score_table_hits": 2,
                    "auto_score_text_hits": 2,
                    "auto_table_score": 0.64,
                    "auto_text_score": 0.59,
                    "auto_score_margin": 0.05,
                },
            ),
            mock.patch.object(
                service,
                "_route_chunk_hits",
                return_value=(tuple(), tuple(), {"index_route": "empty", "index_route_table_hits": 0, "index_route_text_hits": 0}),
            ) as route_chunk_hits,
        ):
            result = service.search(
                business_profile=self.business,
                query="gold card details",
            )

        self.assertNotEqual(result.status, "needs_clarification")
        self.assertFalse(result.snippets)
        self.assertNotEqual(result.diagnostics.get("auto_arbitration_decision"), "clarification")
        self.assertFalse(bool(result.diagnostics.get("auto_arbitration_needs_clarification")))
        auto_contract = result.diagnostics.get("auto_decision_contract") or {}
        self.assertFalse(auto_contract.get("needs_clarification"))
        route_chunk_hits.assert_called()

class KnowledgeSearchServicePhaseSixValidationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="phase6@example.com", first_name="Phase6")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Phase6 Ops",
            industry="operations",
        )
        self.other_registration = RegistrationSession.objects.create(user=self.user)
        self.other_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.other_registration,
            name="Phase6 Health",
            industry="healthcare",
        )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_context_uses_arabic_lexicon_for_aggregate_intent(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("ما إجمالي المبيعات؟")

        with (
            mock.patch.object(service, "_tenant_lexicon_tables_ready", return_value=True),
            mock.patch.object(
                service.tenant_lexicon_service,
                "get_snapshot",
                return_value={
                    "entity_terms": ["طلب"],
                    "attribute_terms": ["اجمالي المبيعات"],
                },
            ),
            mock.patch.object(service, "_table_query_tokens", return_value=({"اجمالي", "المبيعات"}, {"اجمالي", "المبيعات"})),
            mock.patch.object(service, "_table_columns_for_business", return_value={"اجمالي المبيعات"}),
            mock.patch.object(
                service,
                "_table_profile_for_business",
                return_value={
                    "table_uploads": 1,
                    "total_uploads": 1,
                    "table_upload_ratio": 1.0,
                    "table_count": 1,
                    "dominant": True,
                },
            ),
            mock.patch.object(service, "_table_row_label_tokens_for_business", return_value=set()),
        ):
            table_context = service._table_query_context(self.business, traits)

        classification = table_context.get("query_classification")
        self.assertIsNotNone(classification)
        self.assertEqual(classification.intent, QueryIntent.AGGREGATE)
        self.assertIn("اجمالي المبيعات", classification.attributes)
        self.assertEqual(table_context.get("tenant_lexicon_attribute_terms_count"), 1)
        self.assertFalse(table_context.get("requires_clarification"))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_context_passes_tenant_id_to_classifier(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("list all records")
        captured: dict[str, object] = {}

        def _fake_classify(_classifier_self, _query: str, context: dict | None = None):
            captured.update(context or {})
            return QueryClassification(
                intent=QueryIntent.EXPLORATORY,
                confidence=0.9,
                reasoning="captured-context",
            )

        with (
            mock.patch.object(service, "_tenant_lexicon_tables_ready", return_value=True),
            mock.patch.object(service.tenant_lexicon_service, "get_snapshot", return_value={}),
            mock.patch.object(service, "_table_query_tokens", return_value=(set(), set())),
            mock.patch.object(service, "_table_columns_for_business", return_value=set()),
            mock.patch.object(
                service,
                "_table_profile_for_business",
                return_value={
                    "table_uploads": 0,
                    "total_uploads": 0,
                    "table_upload_ratio": 0.0,
                    "table_count": 0,
                    "dominant": False,
                },
            ),
            mock.patch.object(service, "_table_row_label_tokens_for_business", return_value=set()),
            mock.patch("apps.rag.table_context.QueryClassifier.classify", new=_fake_classify),
        ):
            service._table_query_context(self.business, traits)

        self.assertEqual(captured.get("tenant_id"), str(self.business.id))

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_same_query_does_not_cross_tenant_bleed_lexicon_entity_names(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        query = "list all service requests"

        def _snapshot_for_tenant(*, business_profile, use_cache=True):
            if business_profile.id == self.business.id:
                return {
                    "entity_terms": ["service request"],
                    "attribute_terms": ["resolution time"],
                }
            return {
                "entity_terms": ["patient appointment"],
                "attribute_terms": ["visit duration"],
            }

        with (
            mock.patch.object(service, "_tenant_lexicon_tables_ready", return_value=True),
            mock.patch.object(service.tenant_lexicon_service, "get_snapshot", side_effect=_snapshot_for_tenant),
            mock.patch.object(service, "_table_query_tokens", return_value=({"list", "all", "service", "requests"}, {"service", "requests"})),
            mock.patch.object(service, "_table_columns_for_business", return_value=set()),
            mock.patch.object(
                service,
                "_table_profile_for_business",
                return_value={
                    "table_uploads": 0,
                    "total_uploads": 0,
                    "table_upload_ratio": 0.0,
                    "table_count": 0,
                    "dominant": False,
                },
            ),
            mock.patch.object(service, "_table_row_label_tokens_for_business", return_value=set()),
        ):
            context_a = service._table_query_context(self.business, service.analyze_query(query))
            context_b = service._table_query_context(self.other_business, service.analyze_query(query))

        class_a = context_a["query_classification"]
        class_b = context_b["query_classification"]
        self.assertIn("service request", [item.lower() for item in class_a.entity_names])
        self.assertNotIn("service request", [item.lower() for item in class_b.entity_names])

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_context_does_not_invoke_llm_intent_fallback(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("assessment fee personal loan")

        with (
            mock.patch.object(service, "_tenant_lexicon_tables_ready", return_value=True),
            mock.patch.object(service.tenant_lexicon_service, "get_snapshot", return_value={}),
            mock.patch.object(service, "_table_query_tokens", return_value=({"assessment", "fee", "loan"}, {"assessment", "fee"})),
            mock.patch.object(service, "_table_columns_for_business", return_value={"fees_charges"}),
            mock.patch.object(
                service,
                "_table_profile_for_business",
                return_value={
                    "table_uploads": 1,
                    "total_uploads": 1,
                    "table_upload_ratio": 1.0,
                    "table_count": 1,
                    "dominant": True,
                },
            ),
            mock.patch.object(service, "_table_row_label_tokens_for_business", return_value={"assessment fees"}),
            mock.patch(
                "apps.rag.table_context.QueryClassifier.classify",
                return_value=QueryClassification(
                    intent=QueryIntent.EXPLORATORY,
                    confidence=0.2,
                    reasoning="low confidence heuristic",
                ),
            ),
            mock.patch.object(service.intent_fallback_service, "classify") as fallback_mock,
        ):
            context = service._table_query_context(self.business, traits)

        fallback_mock.assert_not_called()
        self.assertFalse(context.get("intent_fallback_attempted"))
        self.assertFalse(context.get("intent_fallback_applied"))


class KnowledgeSearchServiceResidualRerankTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="residual@example.com", first_name="Residual")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Residual Co",
            industry="finance",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Fees",
        )


class KnowledgeSearchServiceTableContextGuardrailTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user = User.objects.create(email="table-guardrails@example.com", first_name="Guard")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Guardrail Co",
            industry="finance",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Guardrail Fees",
        )

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_context_ignores_single_char_columns_and_generic_row_labels_for_specific_lookup(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("International Delivery Shipment Fees", business_profile=self.business)

        with (
            mock.patch.object(service, "_tenant_lexicon_tables_ready", return_value=False),
            mock.patch.object(
                service,
                "_table_query_tokens",
                return_value=(
                    {"delivery", "fee", "international", "shipment"},
                    {"delivery", "international", "shipment"},
                ),
            ),
            mock.patch.object(service, "_table_columns_for_business", return_value={"m", "fees_charges", "types_of_services_fee"}),
            mock.patch.object(
                service,
                "_table_profile_for_business",
                return_value={
                    "table_uploads": 3,
                    "total_uploads": 6,
                    "table_upload_ratio": 0.5,
                    "table_count": 4,
                    "dominant": True,
                },
            ),
            mock.patch.object(service, "_table_row_label_tokens_for_business", return_value={"fee", "international"}),
            mock.patch(
                "apps.rag.table_context.QueryClassifier.classify",
                return_value=QueryClassification(
                    intent=QueryIntent.EXPLORATORY,
                    confidence=0.92,
                    reasoning="specific phrase lookup",
                ),
            ),
        ):
            table_context = service._table_query_context(self.business, traits)

        self.assertFalse(table_context["has_intent"])
        self.assertEqual(table_context["matched_columns_query"], set())
        self.assertEqual(table_context["matched_row_labels"], set())
        self.assertEqual(table_context["matched_columns_query_raw"], {"m"})
        self.assertEqual(table_context["matched_row_labels_raw"], {"fee", "international"})

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_context_keeps_generic_column_signal_for_broad_enumeration_when_tables_dominate(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("mortgage fees types", business_profile=self.business)

        with (
            mock.patch.object(service, "_tenant_lexicon_tables_ready", return_value=False),
            mock.patch.object(
                service,
                "_table_query_tokens",
                return_value=(
                    {"mortgage", "fee", "types"},
                    {"mortgage"},
                ),
            ),
            mock.patch.object(service, "_table_columns_for_business", return_value={"commission_fees"}),
            mock.patch.object(
                service,
                "_table_profile_for_business",
                return_value={
                    "table_uploads": 3,
                    "total_uploads": 4,
                    "table_upload_ratio": 0.75,
                    "table_count": 6,
                    "dominant": True,
                },
            ),
            mock.patch.object(service, "_table_row_label_tokens_for_business", return_value=set()),
            mock.patch(
                "apps.rag.table_context.QueryClassifier.classify",
                return_value=QueryClassification(
                    intent=QueryIntent.ENUMERATE,
                    confidence=0.88,
                    reasoning="broad fee listing",
                ),
            ),
        ):
            table_context = service._table_query_context(self.business, traits)

        self.assertTrue(table_context["has_intent"])
        self.assertTrue(table_context["generic_column_signal"])

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_context_does_not_treat_single_exploratory_row_label_as_table_intent(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("international delivery fees", business_profile=self.business)

        with (
            mock.patch.object(service, "_tenant_lexicon_tables_ready", return_value=False),
            mock.patch.object(
                service,
                "_table_query_tokens",
                return_value=(
                    {"delivery", "fee", "international"},
                    {"delivery", "international"},
                ),
            ),
            mock.patch.object(
                service,
                "_table_columns_for_business",
                return_value={"commission_fees", "fees_charges", "atm_withdrawal_fees"},
            ),
            mock.patch.object(
                service,
                "_table_profile_for_business",
                return_value={
                    "table_uploads": 5,
                    "total_uploads": 7,
                    "table_upload_ratio": 0.71,
                    "table_count": 8,
                    "dominant": True,
                },
            ),
            mock.patch.object(service, "_table_row_label_tokens_for_business", return_value={"fee", "international"}),
            mock.patch(
                "apps.rag.table_context.QueryClassifier.classify",
                return_value=QueryClassification(
                    intent=QueryIntent.EXPLORATORY,
                    confidence=0.5,
                    reasoning="broad mixed-corpus query",
                ),
            ),
        ):
            table_context = service._table_query_context(self.business, traits)

        self.assertEqual(table_context["matched_row_labels"], set())
        self.assertFalse(table_context["row_label_intent"])
        self.assertFalse(table_context["has_intent"])

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_residual_text_chunks_receive_stronger_penalty_under_table_intent(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("cash deposit same day value date")

        with tenant_context(self.business.id):
            residual_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=0,
                content="Cash deposit with same day value date T+3 0.3% minimum EGP 100",
                metadata={
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "search_tier": "fallback",
                },
            )
            clean_text_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=1,
                content="Fee schedule summary for branch over-the-counter services.",
                metadata={
                    "index_type": "text",
                    "content_source": "page_blocks",
                    "region_role": "text",
                },
            )

        def _candidates() -> list[ChunkResult]:
            return [
                ChunkResult(chunk=residual_chunk, source_stage="hybrid", lexical_score=0.6),
                ChunkResult(chunk=clean_text_chunk, source_stage="hybrid", lexical_score=0.6),
            ]

        neutral_ranked, _, _ = service._rerank_candidates(
            _candidates(),
            query_vector=None,
            traits=traits,
            table_context={"has_intent": False, "query_tokens": set(), "specific_tokens": set()},
        )
        neutral_residual = next(hit for hit in neutral_ranked if hit.chunk_id == residual_chunk.id)
        neutral_penalty = float(
            (neutral_residual.diagnostics.get("score_breakdown") or {}).get("table_residual_penalty") or 0.0
        )

        table_ranked, _, _ = service._rerank_candidates(
            _candidates(),
            query_vector=None,
            traits=traits,
            table_context={"has_intent": True, "query_tokens": set(traits.tokens), "specific_tokens": set()},
        )
        table_residual = next(hit for hit in table_ranked if hit.chunk_id == residual_chunk.id)
        table_penalty = float(
            (table_residual.diagnostics.get("score_breakdown") or {}).get("table_residual_penalty") or 0.0
        )

        self.assertGreater(table_penalty, neutral_penalty)
        self.assertLess(table_residual.rerank_score, neutral_residual.rerank_score)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_residual_rescue_applies_when_canonical_specific_match_is_missing(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("cash deposit same day value date")

        with tenant_context(self.business.id):
            canonical_table_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=2,
                content="[Table] Fees\n[Row] 1\nService: Cash withdrawal over the counter\nTariff: EGP 40",
                metadata={
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                },
            )
            residual_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=3,
                content="Cash deposit with same day value date 0.3% minimum EGP 100 no maximum",
                metadata={
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "search_tier": "fallback",
                },
            )

        ranked, _, rerank_diag = service._rerank_candidates(
            [
                ChunkResult(chunk=canonical_table_chunk, source_stage="hybrid", lexical_score=0.55),
                ChunkResult(chunk=residual_chunk, source_stage="hybrid", lexical_score=0.55),
            ],
            query_vector=None,
            traits=traits,
            table_context={
                "has_intent": True,
                "query_tokens": set(traits.tokens),
                "specific_tokens": {"same", "day", "value", "date"},
            },
        )
        residual_ranked = next(hit for hit in ranked if hit.chunk_id == residual_chunk.id)
        residual_breakdown = residual_ranked.diagnostics.get("score_breakdown") or {}

        self.assertTrue(rerank_diag.get("table_residual_rescue_applied"))
        self.assertEqual(rerank_diag.get("table_residual_rescue_count"), 1)
        self.assertEqual(rerank_diag.get("table_residual_rescue_reason"), "promoted")
        self.assertGreater(float(residual_breakdown.get("table_residual_rescue_bonus") or 0.0), 0.0)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_residual_rescue_skips_when_canonical_specific_match_exists(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("cash deposit same day value date")

        with tenant_context(self.business.id):
            canonical_table_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=4,
                content=(
                    "[Table] Fees\n[Row] 1\n"
                    "Service: Cash deposit with same day value date\n"
                    "Tariff: 0.3% minimum EGP 100"
                ),
                metadata={
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                },
            )
            residual_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=5,
                content="Cash deposit with same day value date 0.3% minimum EGP 100 no maximum",
                metadata={
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "search_tier": "fallback",
                },
            )

        ranked, _, rerank_diag = service._rerank_candidates(
            [
                ChunkResult(chunk=canonical_table_chunk, source_stage="hybrid", lexical_score=0.55),
                ChunkResult(chunk=residual_chunk, source_stage="hybrid", lexical_score=0.55),
            ],
            query_vector=None,
            traits=traits,
            table_context={
                "has_intent": True,
                "query_tokens": set(traits.tokens),
                "specific_tokens": {"same", "day", "value", "date"},
            },
        )
        residual_ranked = next(hit for hit in ranked if hit.chunk_id == residual_chunk.id)
        residual_breakdown = residual_ranked.diagnostics.get("score_breakdown") or {}

        self.assertFalse(rerank_diag.get("table_residual_rescue_applied"))
        self.assertEqual(rerank_diag.get("table_residual_rescue_count"), 0)
        self.assertEqual(
            rerank_diag.get("table_residual_rescue_reason"),
            "canonical_specific_match_present",
        )
        self.assertEqual(float(residual_breakdown.get("table_residual_rescue_bonus") or 0.0), 0.0)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_residual_rescue_applies_for_broad_enumerate_queries(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("list all credit cards and their issuance fees")

        with tenant_context(self.business.id):
            canonical_table_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=40,
                content="[Table] Fees\n[Row] 1\nProgram: EPP\nFee: 3.17%",
                metadata={
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_summary",
                    "table_chunk_role": "summary",
                },
            )
            supporting_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=41,
                content="Issuance and Renewal Fees World EGP 450 Heya Cards EGP 450 Titanium EGP 700",
                metadata={
                    "index_type": "text",
                    "content_source": "table_annotation",
                    "region_role": "table_annotation",
                    "table_annotation": True,
                    "search_tier": "supporting",
                },
            )

        ranked, _, rerank_diag = service._rerank_candidates(
            [
                ChunkResult(chunk=canonical_table_chunk, source_stage="hybrid", lexical_score=0.45),
                ChunkResult(chunk=supporting_chunk, source_stage="hybrid", lexical_score=0.7),
            ],
            query_vector=None,
            traits=traits,
            table_context={
                "has_intent": True,
                "query_tokens": set(traits.tokens),
                "specific_tokens": set(),
                "comprehensive_intent": True,
                "modality_bias": "mixed",
            },
        )

        supporting_ranked = next(hit for hit in ranked if hit.chunk_id == supporting_chunk.id)
        supporting_breakdown = supporting_ranked.diagnostics.get("score_breakdown") or {}

        self.assertTrue(rerank_diag.get("table_residual_rescue_applied"))
        self.assertEqual(rerank_diag.get("table_residual_rescue_count"), 1)
        self.assertEqual(rerank_diag.get("table_residual_rescue_reason"), "promoted")
        self.assertGreater(float(supporting_breakdown.get("table_residual_rescue_bonus") or 0.0), 0.0)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_structural_table_rows_are_penalized_for_specific_value_queries(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query(
            "secured personal loan administration fee plus segment"
        )

        with tenant_context(self.business.id):
            structural_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=6,
                content=(
                    "[Table] Loan fees\n[Row] 6\n"
                    "service: Administration Fees on the total Loan Amount up to 8 years\n"
                    "fees_charges: Segment/Product\n"
                    "Prime: Prime\n"
                    "Plus: Plus\n"
                    "Wealth: Wealth\n"
                    "Private: Private"
                ),
                metadata={
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "table_id": "table-structural",
                    "table_row_scope_dimension_columns": ["Prime", "Plus", "Wealth", "Private"],
                    "table_row_signal_numeric_value_count": 0,
                    "table_row_signal_value_keyword_count": 0,
                    "table_row_signal_has_fee_value": False,
                },
            )
            value_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=7,
                content=(
                    "[Table] Loan fees\n[Row] 7\n"
                    "service: Administration Fees on the total Loan Amount up to 8 years\n"
                    "fees_charges: Secured\n"
                    "Prime: 2.00%\n"
                    "Plus: 1.75%\n"
                    "Wealth: 1.50%\n"
                    "Private: 1.25%"
                ),
                metadata={
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "table_id": "table-structural",
                    "table_row_scope_dimension_columns": ["Prime", "Plus", "Wealth", "Private"],
                    "table_row_signal_numeric_value_count": 4,
                    "table_row_signal_value_keyword_count": 0,
                    "table_row_signal_has_fee_value": False,
                },
            )

        ranked, _, _ = service._rerank_candidates(
            [
                ChunkResult(chunk=structural_chunk, source_stage="hybrid", lexical_score=0.6),
                ChunkResult(chunk=value_chunk, source_stage="hybrid", lexical_score=0.6),
            ],
            query_vector=None,
            traits=traits,
            table_context={
                "has_intent": True,
                "numeric_intent": False,
                "query_tokens": set(traits.tokens),
                "specific_tokens": {"secured", "plus", "segment"},
            },
        )

        self.assertEqual(ranked[0].chunk_id, value_chunk.id)
        structural_ranked = next(hit for hit in ranked if hit.chunk_id == structural_chunk.id)
        structural_breakdown = structural_ranked.diagnostics.get("score_breakdown") or {}
        self.assertTrue(structural_ranked.diagnostics.get("structural_row"))
        self.assertGreater(float(structural_breakdown.get("table_structural_penalty") or 0.0), 0.0)
        self.assertEqual(float(structural_breakdown.get("table_header_bonus") or 0.0), 0.0)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_structural_table_rows_are_detected_from_content_when_scope_metadata_is_missing(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query(
            "secured personal loan administration fee plus segment"
        )

        with tenant_context(self.business.id):
            structural_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=8,
                content=(
                    "[Table] Loan fees\n[Row] 8\n"
                    "service: Service\n"
                    "prime: Prime\n"
                    "plus: Plus\n"
                    "wealth: Wealth\n"
                    "private: Private"
                ),
                metadata={
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "table_id": "table-structural-fallback",
                },
            )
            value_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=9,
                content=(
                    "[Table] Loan fees\n[Row] 9\n"
                    "service: Administration Fees on the total Loan Amount up to 8 years\n"
                    "fees_charges: Secured\n"
                    "Prime: 2.00%\n"
                    "Plus: 1.75%\n"
                    "Wealth: 1.50%\n"
                    "Private: 1.25%"
                ),
                metadata={
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "table_id": "table-structural-fallback",
                },
            )

        ranked, _, _ = service._rerank_candidates(
            [
                ChunkResult(chunk=structural_chunk, source_stage="hybrid", lexical_score=0.58),
                ChunkResult(chunk=value_chunk, source_stage="hybrid", lexical_score=0.58),
            ],
            query_vector=None,
            traits=traits,
            table_context={
                "has_intent": True,
                "numeric_intent": False,
                "query_tokens": set(traits.tokens),
                "specific_tokens": {"secured", "plus", "segment"},
            },
        )

        self.assertEqual(ranked[0].chunk_id, value_chunk.id)
        structural_ranked = next(hit for hit in ranked if hit.chunk_id == structural_chunk.id)
        structural_breakdown = structural_ranked.diagnostics.get("score_breakdown") or {}
        self.assertTrue(structural_ranked.diagnostics.get("structural_row"))
        self.assertGreater(int(structural_ranked.diagnostics.get("structural_pair_echo_count") or 0), 0)
        self.assertGreater(float(structural_breakdown.get("table_structural_penalty") or 0.0), 0.0)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_section_aware_reranking_prefers_matching_section_headings(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeSearchService()
        traits = service.analyze_query("what titles did he work and where")

        with tenant_context(self.business.id):
            summary_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=10,
                content="Adham Khaled Idris career details and role summary.",
                metadata={
                    "index_type": "text",
                    "content_source": "page_blocks",
                    "section_heading": "Professional Summary",
                    "section_headings": ["Professional Summary"],
                },
            )
            work_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=11,
                content="Adham Khaled Idris career details and role summary.",
                metadata={
                    "index_type": "text",
                    "content_source": "page_blocks",
                    "section_heading": "Work Experiences",
                    "section_headings": ["Work Experiences"],
                    "heading_path": ["Career", "Work Experiences"],
                },
            )

        candidates = [
            ChunkResult(chunk=summary_chunk, source_stage="hybrid", lexical_score=0.52),
            ChunkResult(chunk=work_chunk, source_stage="hybrid", lexical_score=0.52),
        ]

        neutral_ranked, _, _ = service._rerank_candidates(
            candidates,
            query_vector=None,
            traits=traits,
            table_context={"has_intent": False, "query_tokens": set(), "specific_tokens": set()},
        )
        self.assertEqual(neutral_ranked[0].chunk_id, summary_chunk.id)

        section_ranked, _, _ = service._rerank_candidates(
            candidates,
            query_vector=None,
            traits=traits,
            table_context={
                "has_intent": False,
                "query_tokens": set(),
                "specific_tokens": set(),
                "prefer_section_context": True,
                "section_focus_terms": ["titles", "work experience"],
            },
        )
        self.assertEqual(section_ranked[0].chunk_id, work_chunk.id)
        work_hit = next(hit for hit in section_ranked if hit.chunk_id == work_chunk.id)
        work_breakdown = work_hit.diagnostics.get("score_breakdown") or {}
        self.assertGreater(float(work_breakdown.get("section_context_boost") or 0.0), 0.0)
