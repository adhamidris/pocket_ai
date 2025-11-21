import uuid
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase

from apps.services.ai_orchestrator import KnowledgeSearchService
from apps.services.knowledge_ingestion import KnowledgeIngestionService


class KnowledgeIngestionFormatTests(SimpleTestCase):
    def test_detect_format_handles_json_variants(self) -> None:
        file_detail = SimpleNamespace(filename="customers.jsonl", content_type="text/plain")
        self.assertEqual(KnowledgeIngestionService._detect_format(file_detail), "json")

        fallback_detail = SimpleNamespace(filename="noext", content_type="application/json")
        self.assertEqual(KnowledgeIngestionService._detect_format(fallback_detail), "json")


class KnowledgeSearchCacheTests(SimpleTestCase):
    @mock.patch("apps.services.ai_orchestrator.build_embedding_service")
    def test_query_cache_invalidation_bumps_version(self, build_embedding_service: mock.MagicMock) -> None:
        fake_embed = mock.Mock()
        fake_embed.model = "test-model"
        fake_embed.embed_text.return_value = [0.1, 0.2, 0.3]
        build_embedding_service.return_value = fake_embed

        cache.clear()
        service = KnowledgeSearchService()
        business = SimpleNamespace(id=uuid.uuid4())

        # First call populates cache
        _, diag_first = service._build_query_vector(business_profile=business, query_text="hello world")
        self.assertFalse(diag_first["vector_cache_hit"])
        self.assertTrue(fake_embed.embed_text.called)

        fake_embed.embed_text.reset_mock()
        _, diag_cached = service._build_query_vector(business_profile=business, query_text="hello world")
        self.assertTrue(diag_cached["vector_cache_hit"])
        fake_embed.embed_text.assert_not_called()

        # Invalidate and ensure we recompute
        KnowledgeSearchService.invalidate_query_cache(business.id)
        fake_embed.embed_text.reset_mock()
        _, diag_after_invalidate = service._build_query_vector(
            business_profile=business, query_text="hello world"
        )
        self.assertFalse(diag_after_invalidate["vector_cache_hit"])
        self.assertTrue(fake_embed.embed_text.called)
