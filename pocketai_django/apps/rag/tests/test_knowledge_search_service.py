from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeAlias,
    KnowledgeEntity,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
    RegistrationSession,
    User,
)
from apps.rag.ai_orchestrator import ChunkResult, KnowledgeSearchService, QueryNormalizer
from core.tenancy import tenant_context


class QueryNormalizerTests(SimpleTestCase):
    def test_identifier_detection(self) -> None:
        traits = QueryNormalizer.normalize("Trip-101 deluxe package")
        self.assertTrue(traits.is_identifier_like)
        self.assertIn("trip-101", traits.alias_candidates)

    def test_alias_candidates_capture_multiword_phrases(self) -> None:
        traits = QueryNormalizer.normalize("Michael Page job opportunities")
        self.assertIn("michael-page", traits.alias_candidates)


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
        self.assertEqual(snippet.source, "table_direct")
        self.assertEqual(snippet.upload_id, self.upload.id)
        self.assertIn("Gold", snippet.summary)
        self.assertEqual(snippet.chunk_id, self.row_chunk.id)
        self.assertEqual(snippet.id, self.row_chunk.id)

    @mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
    def test_table_specific_fallback_keeps_table_hits_when_no_text_hits(self, _build_embeddings) -> None:
        # Add table chunks (as produced by ingestion schema chunking) so hybrid retrieval has candidates.
        KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=0,
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
            chunk_index=1,
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
        self.assertEqual(result.diagnostics.get("index_route"), "table_specific_fallback_table")


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
