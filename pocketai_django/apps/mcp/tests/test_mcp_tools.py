from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
    RegistrationSession,
    User,
    IdentifierColumnMapping,
    IdentifierColumnStatus,
    IdentifierSchema,
    IdentifierSchemaSource,
    IdentifierSchemaStatus,
)
from apps.conversations.models import AgentRun, AgentRunEvent, AgentRunEventType, AgentRunStatus, Conversation
from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


class McpReadDocumentHandlerTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()
        self.user = User.objects.create(email="mcp@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="MCP Bank",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
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
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
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


class McpReadKnowledgeRoutingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()
        self.user = User.objects.create(email="mcp-read-knowledge@example.com", first_name="Reader")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Docs Co",
            industry="docs",
            metadata={FEATURE_FLAG_METADATA_KEY: {"rag_agentic_mode": False}},
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="read-knowledge-session",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Card Fees PDF",
            ingestion_metadata={
                "format": "pdf",
                "structured_exports": {
                    "pages": [
                        {
                            "page_number": 1,
                            "synopsis": "Fee table overview.",
                            "headings": ["Fees"],
                        }
                    ]
                },
            },
        )
        self.table_chunk = KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=0,
            content="Fee\tAmount\nForeign transaction\t3%",
            metadata={"is_table_chunk": True, "strategy": "table_extract", "is_table_preview": True},
        )

    def tearDown(self) -> None:
        self.embed_patcher.stop()
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_read_knowledge_forces_text_for_pdf_table_chunk_even_with_table_args(self) -> None:
        context = ToolExecutionContext(
            max_chunk_reads_per_turn=3,
            max_chunk_pages_per_turn=3,
            char_budget_per_turn=5000,
        )
        payload = {
            "document_id": str(self.table_chunk.id),
            "intent": "table",
            "table": {"query": "foreign transaction fee"},
        }
        result = tools._read_knowledge_handler(payload, self.conversation, context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["engine"], "text_page")
        self.assertEqual(result["diagnostics"]["engine_tool"], "read_document")
        evidence = result.get("evidence") or {}
        self.assertTrue(evidence.get("snippets"))


class McpListTablesHandlerDatasetOnlyTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()
        self.user = User.objects.create(email="mcp-list-tables@example.com", first_name="Lister")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Tables Co",
            industry="tables",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="list-tables-session",
        )
        self.pdf_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Fees PDF",
            ingestion_metadata={"format": "pdf"},
        )
        KnowledgeUploadTable.objects.create(upload=self.pdf_upload, order_index=1, title="Fees Table")
        self.csv_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Fees CSV",
            ingestion_metadata={"format": "csv"},
        )
        KnowledgeUploadTable.objects.create(upload=self.csv_upload, order_index=1, title="Fees Dataset")

    def tearDown(self) -> None:
        self.embed_patcher.stop()
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_list_tables_excludes_pdf_uploads(self) -> None:
        context = ToolExecutionContext()
        result = tools._list_tables_handler({}, self.conversation, context)

        self.assertEqual(result["status"], "ok")
        upload_ids = {entry.get("upload_id") for entry in result.get("results", [])}
        self.assertIn(str(self.csv_upload.id), upload_ids)
        self.assertNotIn(str(self.pdf_upload.id), upload_ids)


class McpSearchKnowledgeHandlerTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()
        self.user = User.objects.create(email="mcp-search@example.com", first_name="Searcher")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Search Co",
            industry="analytics",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="search-session",
        )

    def tearDown(self) -> None:
        self.embed_patcher.stop()
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    @mock.patch("apps.mcp.tools._knowledge_service")
    @override_settings(MCP_SEARCH_MAX_QUERY_VARIANTS=4)
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

    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_uses_server_default_limit_when_omitted(self, service_factory_mock) -> None:
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
        payload = {"queries": ["credit card issuance fees"]}

        tools._search_knowledge_handler(payload, self.conversation, context)

        self.assertEqual(service_mock.search.call_count, 1)
        used_limit = service_mock.search.call_args.kwargs.get("limit")
        # Regression: omitting `limit` must not result in limit=None (unbounded).
        self.assertIsNotNone(used_limit)

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_agentic_search_dedup_preserves_row_index_zero(self) -> None:
        """
        Regression: row_index=0 is valid and must not be dropped by truthy coalescing.

        If we lose 0, anchor dedup falls back to chunk ids and can emit duplicates.
        """

        import uuid

        table_id = str(uuid.uuid4())
        upload_id = str(uuid.uuid4())

        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Fees Table",
                    "summary": "row 0",
                    "search_stage": "table_direct",
                    "source_diagnostics": {"table_id": table_id, "row_index": 0},
                },
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Fees Table (dup)",
                    "summary": "row 0 duplicate via another stage",
                    "search_stage": "content_fts",
                    "source_diagnostics": {"table_id": table_id, "row_index": 0},
                },
            ],
            "completeness": {"total_found": 2},
        }

        result = tools._convert_to_agentic_search_response(legacy_payload, conversation=self.conversation)
        self.assertEqual(result["status"], "ok")
        refs = result.get("refs") or []
        self.assertEqual(len(refs), 1, refs)
        self.assertEqual(refs[0]["kind"], "table_row")
        coverage = refs[0].get("coverage_hint") or {}
        self.assertEqual(coverage.get("row_index"), 0)

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_MAX_SEARCHES_PER_TURN=5)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_semantic_dedup_reuses_results_and_does_not_consume_budget(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Fees",
            summary="Annual fee overview",
            source="file",
            content="Annual fee is 100 EGP.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = (snippet,)
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
        payload = {"query": "credit card fees", "limit": 5}

        first = tools._search_knowledge_handler(payload, self.conversation, context)
        second = tools._search_knowledge_handler(payload, self.conversation, context)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "duplicate")
        # Duplicate intents reuse prior results and do not consume search budget.
        self.assertEqual(context.searches_used, 1)
        # Second call should not execute a second backend search.
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertEqual(second.get("refs"), first.get("refs"))

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_SEARCH_PAGINATION_ENABLED=True)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_cursor_pages_and_excludes_seen_results(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        self.business.metadata = {FEATURE_FLAG_METADATA_KEY: {"rag_agentic_mode": True}}
        self.business.save(update_fields=["metadata"])

        snippets = []
        for idx in range(4):
            snippets.append(
                KnowledgeSnippet(
                    id=uuid.uuid4(),
                    title=f"Chunk {idx}",
                    summary=f"Summary {idx}",
                    source="file",
                    content=f"Content {idx}",
                    upload_id=uuid.uuid4(),
                    chunk_id=uuid.uuid4(),
                    chunk_index=idx,
                    page_number=1,
                    is_table_chunk=False,
                    read_state="summary",
                )
            )

        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = tuple(snippets)
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

        first = tools._search_knowledge_handler({"query": "fees", "limit": 2}, self.conversation, context)
        self.assertEqual(first["status"], "ok")
        self.assertTrue(first.get("has_more"))
        self.assertIsInstance(first.get("next_cursor"), str)

        refs_first = first.get("refs") or []
        self.assertEqual(len(refs_first), 2)
        ids_first = {ref.get("id") for ref in refs_first}

        second = tools._search_knowledge_handler(
            {"cursor": first["next_cursor"], "limit": 2}, self.conversation, context
        )
        self.assertEqual(second["status"], "ok")
        self.assertFalse(second.get("has_more", True))

        refs_second = second.get("refs") or []
        self.assertEqual(len(refs_second), 2)
        ids_second = {ref.get("id") for ref in refs_second}

        # Page 2 must not repeat page 1.
        self.assertTrue(ids_first.isdisjoint(ids_second))
        # Cursor paging should not trigger a second backend search.
        self.assertEqual(service_mock.search.call_count, 1)

    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_read_hint_uses_page_from_metadata_not_chunk_index(self, service_factory_mock) -> None:
        """Integration test: read_hint.page should use page_number from snippet, not chunk_index + 1."""
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet
        
        # Create a real KnowledgeSnippet with page_number=3 and chunk_index=7
        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Test Chunk",
            summary="Test summary",
            source="file",
            content="Test content",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=7,  # Old bug would make page=8
            page_number=3,  # Should use this instead
            is_table_chunk=True,
            read_state="summary",
        )
        
        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = (snippet,)
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
        payload = {"query": "test query", "limit": 5}
        
        result = tools._search_knowledge_handler(payload, self.conversation, context)
        
        self.assertEqual(result["status"], "ok")
        refs = result.get("refs", [])
        self.assertTrue(refs, "Should have at least one ref")
        
        read_hint = refs[0].get("read_hint", {})
        coverage_hint = refs[0].get("coverage_hint", {})
        # V2 read contract hides legacy read knobs, so page/offset live in coverage_hint instead.
        if "page" in read_hint:
            self.assertEqual(read_hint["page"], 3, "page should be 3 from page_number, not 8 (chunk_index + 1)")
            self.assertNotIn("offset", read_hint, "Should not have offset when page is present")
        else:
            self.assertIn("page", coverage_hint, "coverage_hint.page should be present when page_number is set")
            self.assertEqual(coverage_hint["page"], 3)

    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_read_hint_uses_offset_when_no_page_number(self, service_factory_mock) -> None:
        """Integration test: read_hint should use offset (not page) when no page_number."""
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet
        
        # Create a real KnowledgeSnippet with NO page_number
        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Test Chunk No Page",
            summary="Test summary",
            source="file",
            content="Test content",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=7,
            page_number=None,  # No page number
            is_table_chunk=False,
            read_state="summary",
        )
        
        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = (snippet,)
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
        payload = {"query": "test query", "limit": 5}
        
        result = tools._search_knowledge_handler(payload, self.conversation, context)
        
        self.assertEqual(result["status"], "ok")
        refs = result.get("refs", [])
        self.assertTrue(refs)
        
        read_hint = refs[0].get("read_hint", {})
        coverage_hint = refs[0].get("coverage_hint", {})
        # V2 read contract hides legacy read knobs; validate offset is preserved via coverage_hint.
        if "offset" in read_hint:
            self.assertNotIn("page", read_hint, "Should NOT have page when no page_number")
            self.assertEqual(read_hint["offset"], 7)
        else:
            self.assertNotIn("page", coverage_hint, "coverage_hint should NOT have page when no page_number")
            self.assertIn("offset", coverage_hint, "coverage_hint.offset should be present when no page_number")
            self.assertEqual(coverage_hint["offset"], 7)

    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_always_returns_completeness(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet_one = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Result One",
            summary="Summary one",
            source="file",
            content="Content one",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )
        snippet_two = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Result Two",
            summary="Summary two",
            source="file",
            content="Content two",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=2,
            page_number=2,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = (snippet_one, snippet_two)
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
        payload = {"query": "credit cards", "limit": 5}

        result = tools._search_knowledge_handler(payload, self.conversation, context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result.get("refs", [])), 2)
        self.assertIn("completeness", result)
        completeness = result["completeness"]
        self.assertEqual(completeness["shown"], 2)
        self.assertEqual(completeness["total_found"], 2)
        self.assertEqual(completeness["already_seen"], 0)

    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_does_not_filter_seen_items(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        chunk_id_one = uuid.uuid4()
        chunk_id_two = uuid.uuid4()
        snippet_one = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Result One",
            summary="Summary one",
            source="file",
            content="Content one",
            upload_id=uuid.uuid4(),
            chunk_id=chunk_id_one,
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )
        snippet_two = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Result Two",
            summary="Summary two",
            source="file",
            content="Content two",
            upload_id=uuid.uuid4(),
            chunk_id=chunk_id_two,
            chunk_index=2,
            page_number=2,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = (snippet_one, snippet_two)
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
        context.seen_chunk_ids = {str(chunk_id_one), str(chunk_id_two)}
        payload = {"query": "credit cards", "limit": 5}

        result = tools._search_knowledge_handler(payload, self.conversation, context)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result.get("refs", [])), 2)
        completeness = result["completeness"]
        self.assertEqual(completeness["shown"], 2)
        self.assertEqual(completeness["already_seen"], 2)
        self.assertTrue(completeness.get("all_previously_shown"))


@override_settings(SUB_AGENTS_V1_GLOBAL_OVERRIDE=None)
class McpCreateAgentRunToolTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-runs@example.com", first_name="Runner")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Runs Co",
            industry="ops",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": True}},
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Ops Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="runs-session",
            metadata={"actor_user_id": str(self.user.id)},
        )

    def tearDown(self) -> None:
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_create_agent_run_rejects_when_feature_disabled(self) -> None:
        self.business.metadata = {FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": False}}
        self.business.save(update_fields=["metadata"])
        result = tools.execute_tool(
            "create_agent_run",
            {"goal": "Do the thing"},
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "feature_disabled")

    def test_create_agent_run_rejects_without_actor_user_id(self) -> None:
        self.conversation.metadata = {}
        self.conversation.save(update_fields=["metadata"])
        result = tools.execute_tool(
            "create_agent_run",
            {"goal": "Do the thing"},
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "missing_actor_user")

    def test_create_agent_run_rejects_nested_run_conversation(self) -> None:
        self.conversation.metadata = {"source": "agent_run", "actor_user_id": str(self.user.id)}
        self.conversation.save(update_fields=["metadata"])
        result = tools.execute_tool(
            "create_agent_run",
            {"goal": "Do the thing"},
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], "nested_runs_forbidden")

    def test_create_agent_run_creates_run_and_queued_event(self) -> None:
        result = tools.execute_tool(
            "create_agent_run",
            {
                "goal": "Summarize yesterday sales and draft an email",
                "title": "Daily Sales Summary",
                "success_criteria": ["Email draft created", "Summary includes totals"],
                "constraints": {"timeout_seconds": 120},
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        self.assertEqual(result["status"], "ok")
        run_id = result.get("run_id")
        self.assertIsNotNone(run_id)

        run = AgentRun.objects.get(id=run_id)
        self.assertEqual(run.conversation_id, self.conversation.id)
        self.assertEqual(run.created_by_id, self.user.id)
        self.assertEqual(run.status, AgentRunStatus.QUEUED)
        self.assertEqual(run.title, "Daily Sales Summary")

        queued = AgentRunEvent.objects.filter(run=run, sequence_index=1).first()
        self.assertIsNotNone(queued)
        assert queued is not None
        self.assertEqual(queued.event_type, AgentRunEventType.PROGRESS)
        self.assertEqual(queued.label, "Queued")
