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
from apps.services.ai_orchestrator import KnowledgeSearchService, QueryNormalizer


class QueryNormalizerTests(SimpleTestCase):
    def test_identifier_detection(self) -> None:
        traits = QueryNormalizer.normalize("Trip-101 deluxe package")
        self.assertTrue(traits.is_identifier_like)
        self.assertIn("trip-101", traits.alias_candidates)


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
            chunk=self.chunk,
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

    @mock.patch("apps.services.ai_orchestrator.build_embedding_service", return_value=None)
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

    @mock.patch("apps.services.ai_orchestrator.build_embedding_service", return_value=None)
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

    @mock.patch("apps.services.ai_orchestrator.build_embedding_service", return_value=None)
    def test_not_found_status_when_no_chunks(self, _build_embeddings) -> None:
        service = KnowledgeSearchService()
        empty_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
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

    @mock.patch("apps.services.ai_orchestrator.build_embedding_service", return_value=None)
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
