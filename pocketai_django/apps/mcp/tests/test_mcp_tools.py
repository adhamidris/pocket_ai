from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadTable,
)
from apps.conversations.models import AgentRun, AgentRunEvent, AgentRunEventType, AgentRunStatus, Conversation
from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


class McpSearchHintTests(TestCase):
    def test_clarification_status_prefers_diagnostics_question(self) -> None:
        hint = tools._search_hint(
            "needs_clarification",
            None,
            [],
            {"intent_clarification_question": "Which record should I retrieve?"},
        )
        self.assertEqual(hint, "Which record should I retrieve?")

    def test_broad_scope_clarification_uses_category_samples_when_question_missing(self) -> None:
        hint = tools._search_hint(
            "needs_clarification",
            None,
            [],
            {
                "reason": "broad_scope_ambiguity",
                "scope_clarification_categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                ],
            },
        )
        self.assertIn("online banking fees", str(hint).lower())
        self.assertIn("specific category", str(hint).lower())
        self.assertIn("all related fees", str(hint).lower())

    def test_broad_scope_clarification_uses_scope_summary_when_categories_missing(self) -> None:
        hint = tools._search_hint(
            "needs_clarification",
            None,
            [],
            {
                "reason": "broad_scope_ambiguity",
                "scope_summary": {
                    "category_counts": {
                        "online banking fees": 5,
                        "outgoing transfer fees": 4,
                        "statement fees": 3,
                    }
                },
            },
        )
        self.assertIn("online banking fees", str(hint).lower())
        self.assertIn("outgoing transfer fees", str(hint).lower())
        self.assertIn("all related fees", str(hint).lower())

    def test_broad_scope_clarification_uses_top_categories_when_present(self) -> None:
        hint = tools._search_hint(
            "needs_clarification",
            None,
            [],
            {
                "reason": "broad_scope_ambiguity",
                "top_categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                ],
            },
        )
        self.assertIn("online banking fees", str(hint).lower())
        self.assertIn("outgoing transfer fees", str(hint).lower())
        self.assertIn("all related fees", str(hint).lower())

    def test_not_found_hint_uses_no_result_reason_not_applicable(self) -> None:
        hint = tools._search_hint(
            "not_found",
            "table",
            [],
            {"no_result_reason": "not_applicable_to_segment"},
        )
        self.assertIn("requested segment", str(hint))

    def test_not_found_hint_uses_no_result_reason_insufficient_evidence(self) -> None:
        hint = tools._search_hint(
            "not_found",
            "table",
            [],
            {"no_result_reason": "insufficient_evidence"},
        )
        self.assertIn("not enough evidence", str(hint))

    def test_not_found_hint_uses_no_result_reason_not_found(self) -> None:
        hint = tools._search_hint(
            "not_found",
            "table",
            [],
            {"no_result_reason": "not_found"},
        )
        self.assertIn("No matching evidence", str(hint))

    def test_not_found_hint_uses_contract_no_result_reason(self) -> None:
        hint = tools._search_hint(
            "not_found",
            "table",
            [],
            {"auto_decision_contract": {"no_result_reason": "insufficient_evidence"}},
        )
        self.assertIn("not enough evidence", str(hint))

    def test_conflict_clarification_hint_without_explicit_question(self) -> None:
        hint = tools._search_hint(
            "needs_clarification",
            None,
            [],
            {
                "reason": "conflicting_evidence",
                "conflict_detected": True,
            },
        )
        self.assertIn("conflicting values", str(hint).lower())
        self.assertIn("narrow", str(hint).lower())


class McpScopeClarificationToolTests(SimpleTestCase):
    def test_present_scope_clarification_requires_pending_scope(self) -> None:
        context = ToolExecutionContext()

        result = tools._present_scope_clarification_handler(
            {},
            conversation=mock.Mock(),
            context=context,
        )

        self.assertEqual(result.get("tool"), "present_scope_clarification")
        self.assertEqual(result.get("status"), "error")
        self.assertEqual(result.get("error_code"), "no_pending_scope_clarification")

    @override_settings(MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=True)
    def test_present_scope_clarification_returns_mcq_payload(self) -> None:
        context = ToolExecutionContext()
        context.set_pending_scope_clarification(
            base_query="plus customer fees",
            categories=[
                "online banking fees",
                "outgoing transfer fees",
                "statement fees",
                "administrative fees",
            ],
            question="Do you want one specific category or all related fees?",
        )

        result = tools._present_scope_clarification_handler(
            {},
            conversation=mock.Mock(),
            context=context,
        )

        self.assertEqual(result.get("tool"), "present_scope_clarification")
        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("clarification_ui_mode"), "mcq")
        clarification = result.get("clarification") or {}
        self.assertEqual(clarification.get("mode"), "mcq")
        chips = clarification.get("chips") or []
        chip_ids = {str(item.get("id") or "") for item in chips if isinstance(item, dict)}
        self.assertIn("all_fees", chip_ids)
        self.assertIn("choose_categories", chip_ids)
        self.assertIn("Do you want one specific category", str(result.get("hint") or ""))


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

    def test_convert_to_agentic_search_response_preserves_clarification_payload(self) -> None:
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "needs_clarification",
            "snippets": [],
            "hint": "Do you want one specific category or all related fees?",
            "clarification": {
                "mode": "mcq",
                "categories": ["online banking fees", "outgoing transfer fees"],
                "top_categories": ["online banking fees", "outgoing transfer fees"],
                "chips": [
                    {"id": "all_fees", "label": "All fees", "query": "all"},
                    {"id": "choose_categories", "label": "Choose categories", "query": "what categories do you have?"},
                ],
            },
        }
        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=self.conversation,
            context=context,
        )

        clarification = result.get("clarification") or {}
        self.assertEqual(clarification.get("mode"), "mcq")
        self.assertEqual(
            clarification.get("categories"),
            ["online banking fees", "outgoing transfer fees"],
        )
        self.assertEqual(result.get("status"), "empty")

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_preserves_clarification_status_from_batched_runs(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Transfer Fees",
            summary="Outgoing transfer fees for plus segment",
            source="file",
            content="Outgoing transfers are free for plus segment.",
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
                self.diagnostics = {"path": "hybrid"}

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult()
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )
        cached_key = tools._search_cache_key(
            "plus customer fees",
            5,
            None,
            None,
            None,
        )
        context.search_cache[cached_key] = {
            "tool": "search_knowledge",
            "query": "plus customer fees",
            "limit_used": 5,
            "query_intent": "short",
            "intent_signal": {"intent": "short"},
            "status": "needs_clarification",
            "diagnostics": {"intent_clarification_question": "Do you want all plus fees or a specific fee category?"},
            "snippets": [],
        }

        result = tools._search_knowledge_handler(
            {
                "query": "plus customer fees",
                "queries": ["plus customer pricing fees"],
                "limit": 5,
            },
            self.conversation,
            context,
        )

        self.assertEqual(result.get("status"), "needs_clarification")
        self.assertEqual(
            result.get("hint"),
            "Do you want all plus fees or a specific fee category?",
        )
        diagnostics = result.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("final_status_source_index"), 0)
        self.assertEqual(diagnostics.get("final_status_source_query"), "plus customer fees")
        self.assertTrue(result.get("snippets"))
        self.assertEqual(service_mock.search.call_count, 1)

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        RAG_RETRIEVAL_CRITIQUE_ENABLED=False,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_conflicting_evidence_upgrades_to_clarification(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Conflicting fee rows",
            summary="Conflicting values detected for same segment/category",
            source="file",
            content="Value A vs Value B",
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
                self.diagnostics = {
                    "path": "hybrid",
                    "reason": "conflicting_evidence",
                    "conflict_detected": True,
                }

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult()
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )
        result = tools._search_knowledge_handler(
            {"query": "plus customer fees", "limit": 5},
            self.conversation,
            context,
        )

        self.assertEqual(result.get("status"), "needs_clarification")
        self.assertEqual(result.get("snippets"), [])
        diagnostics = result.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("reason"), "conflicting_evidence")
        self.assertTrue(bool(diagnostics.get("conflict_detected")))
        self.assertIn("conflicting values", str(result.get("hint") or "").lower())
        self.assertEqual(service_mock.search.call_count, 1)

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=False,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_broad_scope_clarification_stays_text_mode(self, service_factory_mock) -> None:
        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = tuple()
                self.status = "needs_clarification"
                self.diagnostics = {
                    "path": "clarification",
                    "reason": "broad_scope_ambiguity",
                    "intent_requires_clarification": True,
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                        "statement fees",
                        "administrative fees",
                    ],
                }

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult()
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        result = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )

        self.assertEqual(result.get("status"), "needs_clarification")
        hint = str(result.get("hint") or "").lower()
        self.assertIn("online banking fees", hint)
        self.assertIn("outgoing transfer fees", hint)
        self.assertIn("specific category", hint)
        self.assertIn("all related fees", hint)
        diagnostics = result.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("reason"), "broad_scope_ambiguity")
        self.assertEqual(diagnostics.get("clarification_ui_mode"), "text")
        self.assertEqual(result.get("snippets"), [])
        self.assertIsNotNone(context.pending_scope_clarification)

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=True,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_broad_scope_clarification_emits_mcq_payload(self, service_factory_mock) -> None:
        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = tuple()
                self.status = "needs_clarification"
                self.diagnostics = {
                    "path": "clarification",
                    "reason": "broad_scope_ambiguity",
                    "intent_requires_clarification": True,
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                        "statement fees",
                        "administrative fees",
                        "loan service fees",
                        "payment of invoices",
                    ],
                    "top_categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                        "statement fees",
                        "administrative fees",
                    ],
                }

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult()
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        result = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )

        self.assertEqual(result.get("status"), "needs_clarification")
        diagnostics = result.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("clarification_ui_mode"), "mcq")
        options = diagnostics.get("scope_clarification_options") or []
        self.assertTrue(options)
        option_ids = {str(item.get("id") or "") for item in options if isinstance(item, dict)}
        self.assertIn("all_fees", option_ids)
        self.assertIn("choose_categories", option_ids)
        self.assertTrue(any(str(option_id).startswith("category_") for option_id in option_ids))

        clarification = result.get("clarification") or {}
        self.assertEqual(clarification.get("mode"), "mcq")
        self.assertEqual(clarification.get("categories"), diagnostics.get("categories"))
        self.assertEqual(clarification.get("top_categories"), diagnostics.get("top_categories"))
        chips = clarification.get("chips") or []
        self.assertTrue(chips)
        chip_labels = {str(item.get("label") or "") for item in chips if isinstance(item, dict)}
        self.assertIn("All fees", chip_labels)
        self.assertIn("Choose categories", chip_labels)

        pending_scope = context.pending_scope_clarification or {}
        self.assertEqual(len(pending_scope.get("categories") or []), 6)

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=False,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_non_mcq_mode_strips_mcq_only_diagnostics(self, service_factory_mock) -> None:
        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = tuple()
                self.status = "needs_clarification"
                self.diagnostics = {
                    "path": "clarification",
                    "reason": "broad_scope_ambiguity",
                    "intent_requires_clarification": True,
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                    ],
                    "clarification_ui_mode": "mcq",
                    "mcq_options": [{"id": "all", "label": "All fees"}],
                    "clarification_actions": [{"id": "all"}],
                }

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult()
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        result = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )

        self.assertEqual(result.get("status"), "needs_clarification")
        diagnostics = result.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("clarification_ui_mode"), "text")
        self.assertNotIn("mcq_options", diagnostics)
        self.assertNotIn("mcq_actions", diagnostics)
        self.assertNotIn("clarification_actions", diagnostics)
        self.assertNotIn("scope_clarification_options", diagnostics)
        self.assertNotIn("scope_clarification_actions", diagnostics)
        self.assertNotIn("scope_clarification_more_query", diagnostics)

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_all_rewrites_query(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Transfer Fees",
            summary="Outgoing transfer fees for plus segment",
            source="file",
            content="Outgoing transfers are free for plus segment.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": ["loan service fees", "outgoing transfers", "wallet payments"],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "all", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertIsNone(context.pending_scope_clarification)
        self.assertEqual((context.scope_resolution or {}).get("mode"), "all")

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("what are the fees for plus customers?", second_query)
        self.assertIn("include all related fee categories", second_query)

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_all_alias_rewrites_query(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Transfer Fees",
            summary="Outgoing transfer fees for plus segment",
            source="file",
            content="Outgoing transfers are free for plus segment.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": ["loan service fees", "outgoing transfers", "wallet payments"],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "all_fees", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertIsNone(context.pending_scope_clarification)
        self.assertEqual((context.scope_resolution or {}).get("mode"), "all")

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("what are the fees for plus customers?", second_query)
        self.assertIn("include all related fee categories", second_query)

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=True,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_all_alias_rewrites_query_in_mcq_mode(
        self,
        service_factory_mock,
    ) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Grouped Fees",
            summary="All fee categories for plus segment",
            source="file",
            content="Grouped plus fee categories.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                        "statement fees",
                        "administrative fees",
                        "loan service fees",
                    ],
                    "top_categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                        "statement fees",
                        "administrative fees",
                    ],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        first_diagnostics = first.get("diagnostics") or {}
        self.assertEqual(first_diagnostics.get("clarification_ui_mode"), "mcq")
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "all_fees", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertIsNone(context.pending_scope_clarification)
        self.assertEqual((context.scope_resolution or {}).get("mode"), "all")

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("what are the fees for plus customers?", second_query)
        self.assertIn("include all related fee categories", second_query)

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_specific_category_rewrites_query(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Loan Fees",
            summary="Loan service fees for plus segment",
            source="file",
            content="Loan service fee is EGP 120 for plus segment.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": ["loan service fees", "outgoing transfers", "wallet payments"],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "loan service fees", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertIsNone(context.pending_scope_clarification)
        self.assertEqual((context.scope_resolution or {}).get("mode"), "specific")
        self.assertEqual((context.scope_resolution or {}).get("category"), "loan service fees")

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("what are the fees for plus customers?", second_query)
        self.assertIn("focus only on loan service fees", second_query)
        second_scope = second.get("scope_resolution") or {}
        self.assertEqual(second_scope.get("selection_mode"), "single")
        self.assertEqual(second_scope.get("categories"), ["loan service fees"])

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_explicit_category_intent_rewrites_query(
        self,
        service_factory_mock,
    ) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Transfer Fees",
            summary="Outgoing transfer fees for plus segment",
            source="file",
            content="Outgoing transfers are free for plus segment.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": ["loan service fees", "outgoing transfers", "wallet payments"],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "scope:category:outgoing transfers", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertIsNone(context.pending_scope_clarification)
        self.assertEqual((context.scope_resolution or {}).get("mode"), "specific")
        self.assertEqual((context.scope_resolution or {}).get("category"), "outgoing transfers")

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("what are the fees for plus customers?", second_query)
        self.assertIn("focus only on outgoing transfers", second_query)

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=True,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_top_category_click_rewrites_query_in_mcq_mode(
        self,
        service_factory_mock,
    ) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Transfer Fees",
            summary="Outgoing transfer fees for plus segment",
            source="file",
            content="Outgoing transfers are free for plus segment.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                        "statement fees",
                        "administrative fees",
                        "loan service fees",
                    ],
                    "top_categories": [
                        "online banking fees",
                        "outgoing transfer fees",
                        "statement fees",
                        "administrative fees",
                    ],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        first_diagnostics = first.get("diagnostics") or {}
        self.assertEqual(first_diagnostics.get("clarification_ui_mode"), "mcq")
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "outgoing transfer fees", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertIsNone(context.pending_scope_clarification)
        self.assertEqual((context.scope_resolution or {}).get("mode"), "specific")
        self.assertEqual((context.scope_resolution or {}).get("category"), "outgoing transfer fees")

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("what are the fees for plus customers?", second_query)
        self.assertIn("focus only on outgoing transfer fees", second_query)

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_multi_category_rewrites_query(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Grouped Fees",
            summary="Grouped fee details for selected categories",
            source="file",
            content="Grouped fee details.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": ["loan service fees", "outgoing transfers", "wallet payments"],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")

        second = tools._search_knowledge_handler(
            {"query": "loan service fees and outgoing transfers", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertEqual((context.scope_resolution or {}).get("mode"), "specific")
        categories = (context.scope_resolution or {}).get("categories") or []
        self.assertIn("loan service fees", categories)
        self.assertIn("outgoing transfers", categories)

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("focus only on these fee categories", second_query)
        self.assertIn("loan service fees", second_query)
        self.assertIn("outgoing transfers", second_query)

        second_scope = second.get("scope_resolution") or {}
        self.assertEqual(second_scope.get("selection_mode"), "multi")
        self.assertIn("loan service fees", second_scope.get("categories") or [])
        self.assertIn("outgoing transfers", second_scope.get("categories") or [])

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_variation_maps_to_known_category(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Transfer Fees",
            summary="Outgoing transfer fees for plus segment",
            source="file",
            content="Outgoing transfers are free for plus segment.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=1,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self, *, status: str, snippets: tuple[KnowledgeSnippet, ...], diagnostics: dict[str, object]) -> None:
                self.snippets = snippets
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                snippets=tuple(),
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": ["loan service fees", "outgoing transfers", "wallet payments"],
                },
            ),
            _DummySearchResult(
                status="ok",
                snippets=(snippet,),
                diagnostics={"path": "hybrid"},
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")

        second = tools._search_knowledge_handler(
            {"query": "transfer fee", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "ok")
        self.assertEqual((context.scope_resolution or {}).get("mode"), "specific")
        self.assertEqual((context.scope_resolution or {}).get("category"), "outgoing transfers")

        second_query = str(service_mock.search.call_args_list[1].kwargs.get("query") or "").lower()
        self.assertIn("focus only on outgoing transfers", second_query)

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_lists_categories_without_searching(self, service_factory_mock) -> None:
        class _DummySearchResult:
            def __init__(self, *, status: str, diagnostics: dict[str, object]) -> None:
                self.snippets = tuple()
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult(
            status="needs_clarification",
            diagnostics={
                "reason": "broad_scope_ambiguity",
                "intent_clarification_question": "Do you want one specific category or all related fees?",
                "scope_clarification_categories": [
                    "loan service fees",
                    "outgoing transfers",
                    "wallet payments",
                    "administrative fees",
                ],
            },
        )
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertIsNotNone(context.pending_scope_clarification)
        self.assertIsNone(context.scope_resolution)

        second = tools._search_knowledge_handler(
            {"query": "what categories do you have?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "needs_clarification")
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertIsNotNone(context.pending_scope_clarification)
        self.assertIsNone(context.scope_resolution)
        self.assertEqual(second.get("snippets"), [])

        diagnostics = second.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("scope_listed"), True)
        self.assertEqual(diagnostics.get("reason"), "broad_scope_ambiguity")
        self.assertEqual(diagnostics.get("clarification_ui_mode"), "text")
        self.assertEqual(diagnostics.get("scope_list_count"), 4)

        hint = str(second.get("hint") or "").lower()
        self.assertIn("available categories", hint)
        self.assertIn("loan service fees", hint)
        self.assertIn("outgoing transfers", hint)
        self.assertIn("reply with one category", hint)

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_lists_categories_for_choose_alias(self, service_factory_mock) -> None:
        class _DummySearchResult:
            def __init__(self, *, status: str, diagnostics: dict[str, object]) -> None:
                self.snippets = tuple()
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult(
            status="needs_clarification",
            diagnostics={
                "reason": "broad_scope_ambiguity",
                "intent_clarification_question": "Do you want one specific category or all related fees?",
                "scope_clarification_categories": [
                    "loan service fees",
                    "outgoing transfers",
                    "wallet payments",
                ],
            },
        )
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "choose_categories", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "needs_clarification")
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertIsNotNone(context.pending_scope_clarification)
        self.assertIsNone(context.scope_resolution)
        self.assertEqual(second.get("snippets"), [])

        diagnostics = second.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("scope_listed"), True)
        self.assertEqual(diagnostics.get("reason"), "broad_scope_ambiguity")
        self.assertEqual(diagnostics.get("clarification_ui_mode"), "text")

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=True,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_choose_categories_alias_lists_in_mcq_mode(
        self,
        service_factory_mock,
    ) -> None:
        class _DummySearchResult:
            def __init__(self, *, status: str, diagnostics: dict[str, object]) -> None:
                self.snippets = tuple()
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult(
            status="needs_clarification",
            diagnostics={
                "reason": "broad_scope_ambiguity",
                "intent_clarification_question": "Do you want one specific category or all related fees?",
                "categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                    "administrative fees",
                    "loan service fees",
                ],
                "top_categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                    "administrative fees",
                ],
            },
        )
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        first_diagnostics = first.get("diagnostics") or {}
        self.assertEqual(first_diagnostics.get("clarification_ui_mode"), "mcq")
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "choose_categories", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "needs_clarification")
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertIsNotNone(context.pending_scope_clarification)
        self.assertIsNone(context.scope_resolution)
        self.assertEqual(second.get("snippets"), [])

        diagnostics = second.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("scope_listed"), True)
        self.assertEqual(diagnostics.get("reason"), "broad_scope_ambiguity")
        self.assertEqual(diagnostics.get("clarification_ui_mode"), "text")
        self.assertEqual(diagnostics.get("scope_list_count"), 5)

        hint = str(second.get("hint") or "").lower()
        self.assertIn("available categories", hint)
        self.assertIn("online banking fees", hint)
        self.assertIn("loan service fees", hint)

    @override_settings(
        MCP_SEARCH_PAGINATION_ENABLED=False,
        MCP_NEW_CONTRACT_ENABLED=False,
        MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=False,
    )
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_scope_clarification_followup_ambiguity_retry_keeps_clarification_state(
        self,
        service_factory_mock,
    ) -> None:
        class _DummySearchResult:
            def __init__(self, *, status: str, diagnostics: dict[str, object]) -> None:
                self.snippets = tuple()
                self.status = status
                self.diagnostics = diagnostics

        service_mock = mock.Mock()
        service_mock.search.side_effect = [
            _DummySearchResult(
                status="needs_clarification",
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "Do you want one specific category or all related fees?",
                    "scope_clarification_categories": [
                        "loan service fees",
                        "outgoing transfers",
                        "wallet payments",
                    ],
                },
            ),
            _DummySearchResult(
                status="needs_clarification",
                diagnostics={
                    "reason": "broad_scope_ambiguity",
                    "intent_clarification_question": "I still need the fee category. Do you want one category or all?",
                    "scope_clarification_categories": [
                        "loan service fees",
                        "outgoing transfers",
                        "wallet payments",
                    ],
                },
            ),
        ]
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        first = tools._search_knowledge_handler(
            {"query": "what are the fees for plus customers?", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(first.get("status"), "needs_clarification")
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertIsNotNone(context.pending_scope_clarification)

        second = tools._search_knowledge_handler(
            {"query": "show list", "limit": 5},
            self.conversation,
            context,
        )
        self.assertEqual(second.get("status"), "needs_clarification")
        self.assertEqual(service_mock.search.call_count, 2)
        self.assertIsNotNone(context.pending_scope_clarification)

        diagnostics = second.get("diagnostics") or {}
        self.assertEqual(diagnostics.get("reason"), "broad_scope_ambiguity")
        self.assertEqual(diagnostics.get("clarification_ui_mode"), "text")
        self.assertIsNone(context.scope_resolution)
        self.assertEqual(second.get("snippets"), [])

    @override_settings(MCP_SEARCH_PAGINATION_ENABLED=False, MCP_NEW_CONTRACT_ENABLED=False)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_legacy_payload_stays_compatible_with_additive_diagnostics(self, service_factory_mock) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Legacy Compatible Fees",
            summary="Legacy payload should still expose snippet list",
            source="file",
            content="Plus segment fee is EGP 120.",
            upload_id=uuid.uuid4(),
            chunk_id=uuid.uuid4(),
            chunk_index=3,
            page_number=1,
            is_table_chunk=False,
            read_state="summary",
        )

        class _DummySearchResult:
            def __init__(self) -> None:
                self.snippets = (snippet,)
                self.status = "ok"
                self.diagnostics = {
                    "path": "hybrid",
                    "auto_decision_contract": {
                        "table_score": 0.8,
                        "text_score": 0.2,
                        "margin": 0.6,
                        "decision": "table",
                        "needs_clarification": False,
                        "scope_summary": {"total_matches": 7, "distinct_docs": 3},
                        "conflict_detected": False,
                        "no_result_reason": None,
                    },
                }

        service_mock = mock.Mock()
        service_mock.search.return_value = _DummySearchResult()
        service_factory_mock.return_value = service_mock

        context = ToolExecutionContext(
            max_chunk_reads_per_turn=5,
            max_chunk_pages_per_turn=5,
            char_budget_per_turn=5000,
        )

        result = tools._search_knowledge_handler(
            {"query": "plus customer fees", "limit": 5},
            self.conversation,
            context,
        )

        # Legacy schema still returned (no refs-based contract when MCP_NEW_CONTRACT_ENABLED=False)
        self.assertEqual(result.get("tool"), "search_knowledge")
        self.assertEqual(result.get("status"), "ok")
        self.assertIsInstance(result.get("snippets"), list)
        self.assertTrue(result.get("snippets"))
        self.assertNotIn("refs", result)
        diagnostics = result.get("diagnostics") or {}
        contract = diagnostics.get("auto_decision_contract") or {}
        self.assertIn("scope_summary", contract)
        self.assertIn("conflict_detected", contract)
        self.assertIn("no_result_reason", contract)

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
        self.assertIn(refs[0]["kind"], {"table_row", "table_chunk"})
        coverage = refs[0].get("coverage_hint") or {}
        self.assertEqual(
            coverage.get("row_index") if "row_index" in coverage else coverage.get("matched_row_index"),
            0,
        )

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_AGENTIC_TEXT_CHUNK_GROUPING_ENABLED=True,
        MCP_AGENTIC_TEXT_CHUNK_GROUP_THRESHOLD=2,
    )
    def test_agentic_search_groups_text_chunks_into_document_anchor(self) -> None:
        import uuid

        upload_id = str(uuid.uuid4())
        other_upload_id = str(uuid.uuid4())
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "is_table_chunk": False,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Remittance",
                    "summary": "chunk 4 summary",
                    "chunk_index": 4,
                    "page_number": 2,
                    "confidence_score": 0.44,
                    "search_stage": "content_fts",
                },
                {
                    "is_table_chunk": False,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Remittance",
                    "summary": "chunk 5 summary",
                    "chunk_index": 5,
                    "page_number": 2,
                    "confidence_score": 0.72,
                    "search_stage": "content_fts",
                },
                {
                    "is_table_chunk": False,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Remittance",
                    "summary": "chunk 6 summary",
                    "chunk_index": 6,
                    "page_number": 3,
                    "confidence_score": 0.61,
                    "search_stage": "content_fts",
                },
                {
                    "is_table_chunk": False,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": other_upload_id,
                    "title": "Other Doc",
                    "summary": "single chunk",
                    "chunk_index": 1,
                    "confidence_score": 0.2,
                    "search_stage": "content_fts",
                },
            ],
            "completeness": {"total_found": 4},
        }
        context = ToolExecutionContext(char_budget_per_turn=100_000)

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=self.conversation,
            context=context,
        )

        refs = result.get("refs") or []
        grouped_ref = next((ref for ref in refs if ref.get("id") == upload_id), None)
        self.assertIsNotNone(grouped_ref, refs)
        self.assertEqual(grouped_ref.get("kind"), "document_anchor")
        self.assertEqual(grouped_ref.get("type"), "text")
        self.assertIn("kind:document_context", grouped_ref.get("why") or [])
        coverage = grouped_ref.get("coverage_hint") or {}
        self.assertEqual(coverage.get("chunk_count"), 3)
        self.assertEqual(coverage.get("chunk_range"), [4, 6])
        self.assertEqual(coverage.get("chunk_indices"), [4, 5, 6])
        self.assertAlmostEqual(float(grouped_ref.get("score") or 0.0), 0.72, places=4)

        manifest = context.text_chunk_group_manifests.get(upload_id)
        self.assertIsInstance(manifest, dict)
        self.assertEqual(manifest.get("chunk_range"), [4, 6])

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_agentic_search_promotes_table_context_per_table_only(self) -> None:
        import uuid

        upload_id = str(uuid.uuid4())
        table_a = str(uuid.uuid4())
        table_b = str(uuid.uuid4())
        table_c = str(uuid.uuid4())

        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Table A row",
                    "summary": "row 0",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {"table_id": table_a, "row_index": 0, "table_total_rows": 6},
                },
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Table B row 0",
                    "summary": "row 0",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {"table_id": table_b, "row_index": 0, "table_total_rows": 20},
                },
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Table B row 1",
                    "summary": "row 1",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {"table_id": table_b, "row_index": 1, "table_total_rows": 20},
                },
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Table C row",
                    "summary": "row 0",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {"table_id": table_c, "row_index": 0, "table_total_rows": 10},
                },
            ],
            "completeness": {"total_found": 4},
        }

        result = tools._convert_to_agentic_search_response(legacy_payload, conversation=self.conversation)
        refs = result.get("refs") or []

        promoted_refs = [ref for ref in refs if ref.get("id") == table_b]
        self.assertEqual(len(promoted_refs), 1, refs)
        self.assertEqual(promoted_refs[0].get("kind"), "table_chunk")

        table_row_refs = [ref for ref in refs if ref.get("kind") == "table_row"]
        table_row_table_ids = {
            str((ref.get("coverage_hint") or {}).get("table_id") or "")
            for ref in table_row_refs
        }
        self.assertIn(table_a, table_row_table_ids)
        self.assertIn(table_c, table_row_table_ids)
        self.assertNotIn(table_b, table_row_table_ids)

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_agentic_search_caches_table_anchor_manifest_for_promoted_table_ref(self) -> None:
        import uuid

        upload_id = str(uuid.uuid4())
        table_id = str(uuid.uuid4())
        context = ToolExecutionContext(char_budget_per_turn=100_000)
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Fees row 19",
                    "summary": "row 19",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 19,
                        "table_total_rows": 23,
                        "table_column_count": 7,
                    },
                },
                {
                    "is_table_chunk": True,
                    "chunk_id": str(uuid.uuid4()),
                    "upload_id": upload_id,
                    "title": "Fees row 20",
                    "summary": "row 20",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 20,
                        "table_total_rows": 23,
                        "table_column_count": 7,
                    },
                },
            ],
            "completeness": {"total_found": 2},
        }

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=self.conversation,
            context=context,
        )
        refs = result.get("refs") or []
        self.assertEqual(len(refs), 1, refs)
        self.assertEqual(refs[0].get("id"), table_id)
        coverage = refs[0].get("coverage_hint") or {}
        self.assertEqual(coverage.get("matched_row_index"), 19)

        manifest = context.table_row_anchor_manifests.get(table_id)
        self.assertIsInstance(manifest, dict)
        self.assertEqual(manifest.get("table_id"), table_id)
        self.assertEqual(manifest.get("matched_row_index"), 19)
        self.assertEqual(manifest.get("estimated_rows"), 23)
        self.assertEqual(manifest.get("estimated_columns"), 7)

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_agentic_search_scales_table_read_hint_for_large_tables(self) -> None:
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
                    "title": "Large Fees Table",
                    "summary": "Issuance Fees row",
                    "search_stage": "table_direct",
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 0,
                        "table_total_rows": 53,
                    },
                }
            ],
            "completeness": {"total_found": 1},
        }

        result = tools._convert_to_agentic_search_response(legacy_payload, conversation=self.conversation)
        refs = result.get("refs") or []
        self.assertEqual(len(refs), 1, refs)
        suggested = int(((refs[0].get("read_hint") or {}).get("suggested_max_chars") or 0))
        self.assertGreaterEqual(suggested, 1800, refs[0])

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
        self.assertIn(second["status"], {"duplicate", "ok"})
        # Duplicate intents reuse prior results and do not consume search budget.
        self.assertEqual(context.searches_used, 1)
        # Second call should not execute a second backend search.
        self.assertEqual(service_mock.search.call_count, 1)
        self.assertEqual(second.get("refs"), first.get("refs"))

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_MAX_SEARCHES_PER_TURN=5,
        MCP_SEARCH_DUPLICATE_INTENT_ENABLED=True,
        MCP_SEARCH_RESULT_FINGERPRINT_DEDUP_ENABLED=True,
    )
    @mock.patch("apps.mcp.tools._portal_file_embedding_service", return_value=None)
    @mock.patch("apps.mcp.tools._knowledge_service")
    def test_search_knowledge_result_fingerprint_dedup_refunds_search_budget(
        self,
        service_factory_mock,
        _embedding_service_mock,
    ) -> None:
        import uuid
        from apps.rag.ai_orchestrator import KnowledgeSnippet

        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Credit Card Fees",
            summary="Issuance fee is EGP 450.",
            source="file",
            content="Issuance fee is EGP 450.",
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

        first = tools._search_knowledge_handler(
            {"query": "credit card issuance fees", "limit": 5},
            self.conversation,
            context,
        )
        second = tools._search_knowledge_handler(
            {"query": "credit card fees", "limit": 5},
            self.conversation,
            context,
        )

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second.get("status"), "duplicate")
        self.assertEqual(second.get("error_code"), "duplicate_results")
        self.assertEqual(second.get("refs"), first.get("refs"))
        # Search 2 executed but was refunded from per-turn budget because result set was identical.
        self.assertEqual(context.searches_used, 1)
        self.assertEqual(service_mock.search.call_count, 2)

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
        self.assertIsNotNone(run.execution_conversation_id)
        exec_conversation = Conversation.objects.get(id=run.execution_conversation_id)
        exec_meta = exec_conversation.metadata or {}
        self.assertEqual(str(exec_meta.get("source") or ""), "agent_run")
        self.assertEqual(str(exec_meta.get("anchor_conversation_id") or ""), str(self.conversation.id))

        queued = AgentRunEvent.objects.filter(run=run, sequence_index=1).first()
        self.assertIsNotNone(queued)
        assert queued is not None
        self.assertEqual(queued.event_type, AgentRunEventType.PROGRESS)
        self.assertEqual(queued.label, "Queued")


class McpContinueAgentRunToolTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-continue@example.com", first_name="Continue")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Continue Co",
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
            session_token="continue-session",
            metadata={"actor_user_id": str(self.user.id)},
        )
        self.execution = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="continue-exec-session",
            metadata={
                "source": "agent_run",
                "agent_run_id": "00000000-0000-0000-0000-000000000010",
                "anchor_conversation_id": str(self.conversation.id),
                "actor_user_id": str(self.user.id),
            },
        )

    def tearDown(self) -> None:
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_continue_agent_run_does_not_modify_tool_allowlist(self) -> None:
        run = AgentRun.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            conversation=self.conversation,
            execution_conversation=self.execution,
            created_by=self.user,
            title="Allowlist Test",
            status=AgentRunStatus.COMPLETED,
            run_spec_snapshot={"goal": "Find fees"},
        )

        result = tools.execute_tool(
            "continue_agent_run",
            {
                "run_id": str(run.id),
                "message": "Send the email with the findings.",
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )
        self.assertEqual(result["status"], "ok")

        run.refresh_from_db()
        self.assertIsNone(run.run_spec_snapshot.get("tool_allowlist"))


class McpFileContextConversationTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-files@example.com", first_name="Files")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Files Co",
            industry="ops",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="File Agent",
        )

    def tearDown(self) -> None:
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def test_agent_run_file_tools_route_to_anchor_conversation(self) -> None:
        anchor = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="anchor-files",
        )
        execution = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="execution-files",
            metadata={
                "source": "agent_run",
                "agent_run_id": "test",
                "anchor_conversation_id": str(anchor.id),
            },
        )

        resolved = tools._resolve_file_context_conversation(execution)
        self.assertEqual(resolved.id, anchor.id)
