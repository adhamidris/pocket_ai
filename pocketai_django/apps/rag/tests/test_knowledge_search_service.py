from __future__ import annotations

import uuid
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

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
from apps.rag.ai_orchestrator import (
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
        self.assertEqual(arbitration["auto_arbitration_decision"], "clarification")
        self.assertTrue(arbitration["auto_arbitration_needs_clarification"])

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
            {"table_primary_unfiltered", "table_primary_filtered", "table_primary_strong"},
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
            mock.patch.object(service, "_snippet_rerank", side_effect=lambda snippets, **_: (tuple(snippets), 0)),
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
    def test_low_confidence_intent_returns_needs_clarification(self, _build_embeddings) -> None:
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
            mock.patch.object(service, "_chunk_hits") as chunk_hits,
        ):
            result = service.search(
                business_profile=self.business,
                query="show me what to do",
            )

        self.assertEqual(result.status, "needs_clarification")
        self.assertFalse(result.snippets)
        self.assertEqual(result.diagnostics.get("path"), "clarification")
        self.assertEqual(
            result.diagnostics.get("intent_clarification_question"),
            "Do you want one specific record or a full list?",
        )
        auto_contract = result.diagnostics.get("auto_decision_contract") or {}
        self.assertEqual(auto_contract.get("decision"), "clarification")
        self.assertTrue(auto_contract.get("needs_clarification"))
        self.assertEqual(auto_contract.get("table_score"), 0.0)
        self.assertEqual(auto_contract.get("text_score"), 0.0)
        self.assertEqual(auto_contract.get("margin"), 0.0)
        chunk_hits.assert_not_called()

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_ambiguous_auto_scores_return_needs_clarification_before_route(self, _build_embeddings) -> None:
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
            mock.patch.object(service, "_route_chunk_hits") as route_chunk_hits,
        ):
            result = service.search(
                business_profile=self.business,
                query="gold card details",
            )

        self.assertEqual(result.status, "needs_clarification")
        self.assertFalse(result.snippets)
        self.assertEqual(result.diagnostics.get("path"), "clarification")
        self.assertEqual(result.diagnostics.get("reason"), "auto_source_ambiguity")
        self.assertTrue(result.diagnostics.get("intent_requires_clarification"))
        self.assertIn(
            "table-only",
            str(result.diagnostics.get("intent_clarification_question") or "").lower(),
        )
        auto_contract = result.diagnostics.get("auto_decision_contract") or {}
        self.assertEqual(auto_contract.get("decision"), "clarification")
        self.assertTrue(auto_contract.get("needs_clarification"))
        route_chunk_hits.assert_not_called()

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_ambiguous_auto_scores_emit_dynamic_evidence_labels(self, _build_embeddings) -> None:
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
            "query_tokens": {"gold", "fee", "benefits"},
            "specific_tokens": {"gold", "fee"},
            "table_dominant": True,
            "table_upload_ratio": 0.7,
            "table_count": 2,
            "table_uploads": 1,
            "allow_generic": True,
            "tenant_lexicon_entity_terms_count": 0,
            "tenant_lexicon_attribute_terms_count": 0,
        }
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
        chunk_hits = (
            ChunkResult(
                chunk=table_chunk,
                source_stage="unit",
                rerank_score=0.74,
                lexical_score=0.62,
                diagnostics={"specific_match_tokens": ("gold", "fee")},
            ),
            ChunkResult(
                chunk=text_chunk,
                source_stage="unit",
                rerank_score=0.71,
                lexical_score=0.65,
            ),
        )
        with (
            mock.patch.object(service, "_table_query_context", return_value=table_context),
            mock.patch.object(service, "_business_has_tables", return_value=True),
            mock.patch.object(service, "search_by_alias", return_value=AliasSearchResult(tuple(), {})),
            mock.patch.object(service, "_chunk_hits", return_value=chunk_hits),
            mock.patch.object(
                service,
                "_score_auto_mode_candidates",
                return_value={
                    "auto_score_version": "v2",
                    "auto_score_sample_size": 2,
                    "auto_score_table_hits": 2,
                    "auto_score_text_hits": 2,
                    "auto_table_score": 0.66,
                    "auto_text_score": 0.6,
                    "auto_score_margin": 0.06,
                },
            ),
            mock.patch.object(service, "_route_chunk_hits") as route_chunk_hits,
        ):
            result = service.search(
                business_profile=self.business,
                query="gold card details and benefits",
            )

        self.assertEqual(result.status, "needs_clarification")
        self.assertIn("gold", str(result.diagnostics.get("intent_clarification_question") or "").lower())
        self.assertIn("benefits", str(result.diagnostics.get("intent_clarification_question") or "").lower())
        self.assertEqual(result.diagnostics.get("auto_arbitration_table_evidence_label"), "gold")
        self.assertIn(
            "gold card benefits",
            str(result.diagnostics.get("auto_arbitration_text_evidence_label") or "").lower(),
        )
        route_chunk_hits.assert_not_called()


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
            mock.patch("apps.rag.ai_orchestrator.QueryClassifier.classify", new=_fake_classify),
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
