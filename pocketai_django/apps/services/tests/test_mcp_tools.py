from __future__ import annotations

from unittest import mock

from django.test import TestCase

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    RegistrationSession,
    User,
    IdentifierColumnMapping,
    IdentifierColumnStatus,
    IdentifierSchema,
    IdentifierSchemaSource,
    IdentifierSchemaStatus,
)
from apps.conversations.models import Conversation
from apps.services.mcp import tools
from apps.services.mcp.types import ToolExecutionContext


class McpReadDocumentHandlerTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch("apps.services.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()
        self.user = User.objects.create(email="mcp@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="MCP Bank",
            industry="banking",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="session-mcp",
        )
        self.page_synopsis = "Rates overview for personal loans."
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Loan Guide",
            ingestion_metadata={
                "structured_exports": {
                    "pages": [
                        {
                            "page_number": 1,
                            "synopsis": self.page_synopsis,
                            "headings": ["Rates"],
                        }
                    ]
                }
            },
        )
        self.chunk = KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=0,
            content="Full raw content for page one with several paragraphs.",
        )

    def tearDown(self) -> None:
        self.embed_patcher.stop()
        super().tearDown()

    def test_excerpt_mode_returns_page_synopsis_and_tracks_budget(self) -> None:
        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
            char_budget_per_minute=10000,
            minute_budget_reserver=lambda count: None,
        )
        payload = {"document_id": str(self.chunk.id), "mode": "excerpt"}
        result = tools._read_document_handler(payload, self.conversation, context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "excerpt")
        snippet = result["snippets"][0]
        self.assertEqual(snippet["page_mode"], "excerpt")
        self.assertEqual(snippet["read_state"], "summary")
        self.assertEqual(snippet["content"], self.page_synopsis)
        self.assertGreater(context.characters_used, 0)

    def test_full_page_downgraded_when_char_budget_low(self) -> None:
        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=120,
        )
        payload = {"document_id": str(self.chunk.id), "mode": "full_page"}
        result = tools._read_document_handler(payload, self.conversation, context)

        notice = result.get("throttle_notice")
        self.assertIsNotNone(notice)
        self.assertEqual(notice["reason"], "char_budget_low")
        self.assertEqual(result["mode"], "excerpt")
        snippet = result["snippets"][0]
        self.assertEqual(snippet["page_mode"], "excerpt")

    def test_read_document_requires_identifier_when_mapping_active(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="email",
            display_name="Email",
            status=IdentifierSchemaStatus.ACTIVE,
            source=IdentifierSchemaSource.USER,
            is_required=True,
        )
        IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=schema,
            upload=self.upload,
            column_name="Email",
            status=IdentifierColumnStatus.ACTIVE,
            source=IdentifierSchemaSource.USER,
        )
        context = ToolExecutionContext(
            max_chunk_reads_per_turn=3,
            max_chunk_pages_per_turn=3,
            char_budget_per_turn=5000,
        )
        payload = {"document_id": str(self.upload.id), "mode": "excerpt"}
        result = tools._read_document_handler(payload, self.conversation, context)

        self.assertEqual(result["status"], "identifier_required")
        self.assertEqual(result["snippets"], [])
        self.assertIn("email", result.get("required_identifiers", []))
        self.assertTrue(context.identifier_checks)

    def test_read_document_allows_when_identifier_present(self) -> None:
        schema = IdentifierSchema.objects.create(
            business_profile=self.business,
            key="email",
            display_name="Email",
            status=IdentifierSchemaStatus.ACTIVE,
            source=IdentifierSchemaSource.USER,
            is_required=True,
        )
        IdentifierColumnMapping.objects.create(
            business_profile=self.business,
            identifier=schema,
            upload=self.upload,
            column_name="Email",
            status=IdentifierColumnStatus.ACTIVE,
            source=IdentifierSchemaSource.USER,
        )
        self.conversation.metadata = {"customer_identifiers": {"email": "visitor@example.com"}}
        self.conversation.save(update_fields=["metadata"])
        context = ToolExecutionContext(
            max_chunk_reads_per_turn=3,
            max_chunk_pages_per_turn=3,
            char_budget_per_turn=5000,
        )
        payload = {"document_id": str(self.chunk.id), "mode": "excerpt"}
        result = tools._read_document_handler(payload, self.conversation, context)

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["snippets"])
        self.assertEqual(result["snippets"][0]["read_state"], "summary")


class McpSearchKnowledgeHandlerTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch("apps.services.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()
        self.user = User.objects.create(email="mcp-search@example.com", first_name="Searcher")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Search Co",
            industry="analytics",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="search-session",
        )

    def tearDown(self) -> None:
        self.embed_patcher.stop()
        super().tearDown()

    @mock.patch("apps.services.mcp.tools._knowledge_service")
    def test_search_knowledge_caps_extra_queries(self, service_factory_mock) -> None:
        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = tuple()
                self.status = "ok"
                self.diagnostics = {}

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult()
        service_factory_mock.return_value = service_mock
        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )
        primary_query = "sales units ازموراب 20 مجم 2 شريط سانسو هيماتنيك 28 قرص Retail Nasr Al-Deen"
        extra_queries = [
            "ازموراب 20 مجم 2 شريط",
            "سانسو هيماتنيك 28 قرص",
            "ازموراب 20 مجم 2 شريط",  # duplicate should be ignored
            "Retail Nasr Al-Deen",
            "Re Khatem Morsalin",
            "سانسو ومن 28 قرص",
            "ازموراب 40 مجم 14 كبسولة",
        ]
        payload = {
            "query": primary_query,
            "queries": extra_queries,
            "limit": 5,
        }

        tools._search_knowledge_handler(payload, self.conversation, context)

        queries_seen = [call.kwargs["query"] for call in service_mock.search.call_args_list]
        self.assertTrue(queries_seen)
        self.assertEqual(queries_seen[0], primary_query)
        self.assertLessEqual(len(queries_seen), 4)
