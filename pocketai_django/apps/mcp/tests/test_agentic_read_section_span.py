from __future__ import annotations

from unittest import mock

from django.test import TestCase, override_settings

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.accounts.models import KnowledgeSourceType, KnowledgeStatus
from apps.accounts.feature_flags import FeatureState
from apps.conversations.models import Conversation
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadChunk, KnowledgeUploadPage, KnowledgeUploadPageBlock
from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


@override_settings(
    MCP_NEW_CONTRACT_ENABLED=True,
    MCP_AGENTIC_READ_V2_ENABLED=True,
)
class AgenticReadSectionSpanTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="section-span@example.com", first_name="Section")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Section Span Co",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Reader",
            role="Assistant",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="section-span-session",
        )

        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Adham Idris CV",
            ingestion_metadata={"format": "pdf"},
        )
        self.notes_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Unstructured Notes",
            ingestion_metadata={"format": "pdf"},
        )
        self.contract_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Vendor Agreement",
            ingestion_metadata={"format": "pdf"},
        )
        self.noisy_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Benefits Guide",
            ingestion_metadata={"format": "pdf"},
        )

        with tenant_context(self.business.id):
            cv_page1 = KnowledgeUploadPage.objects.create(
                upload=self.upload,
                page_number=1,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )
            cv_page2 = KnowledgeUploadPage.objects.create(
                upload=self.upload,
                page_number=2,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )

            page1_blocks = [
                "Adham Khaled Idris",
                "Full Stack Web Developer & AI Expert",
                "Professional Summary",
                "Former CIB SMEs Relationships Manager who later moved into technology.",
                "Work Experiences",
                "Portfolio - AUTOM8ED.space | August 2024 - Ongoing",
                "Built a full self-setup RAG SaaS designed for non-technical users across industries.",
            ]
            for order_index, text in enumerate(page1_blocks):
                KnowledgeUploadPageBlock.objects.create(
                    upload=self.upload,
                    page=cv_page1,
                    order_index=order_index,
                    text=text,
                )

            page2_blocks = [
                "Business Banking Relationship Manager | Commercial International Bank (CIB), Egypt | February 2022 - June 2023",
                "Managed 100+ BB clients and acted as their primary financial advisor.",
                "Senior Personal Banker - Retail Banking | Commercial International Bank (CIB), Egypt | June 2021 - February 2022",
                "Multiple top-ranked sales awards and accelerated promotion path.",
                "Business Competencies",
                "Client Portfolio Management | Credit Risk Assessment",
            ]
            for order_index, text in enumerate(page2_blocks):
                KnowledgeUploadPageBlock.objects.create(
                    upload=self.upload,
                    page=cv_page2,
                    order_index=order_index,
                    text=text,
                )

            self.cv_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=0,
                content="\n".join(page1_blocks),
                token_count=80,
                metadata={
                    "content_source": "page_blocks",
                    "page_numbers": [1],
                    "page_anchor": "p1",
                    "block_anchors": [f"p1-b{i}" for i in range(len(page1_blocks))],
                    "canonical_anchor_id": "page:1:paragraph:p1-b0",
                    "is_table_chunk": False,
                    "index_type": "text",
                },
            )

            notes_page = KnowledgeUploadPage.objects.create(
                upload=self.notes_upload,
                page_number=1,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )
            note_blocks = [
                "this paragraph starts in lowercase and should not be treated as a heading. "
                "it continues as ordinary body text without any structural boundary or heading marker. "
                "it keeps going with more narrative detail so the fallback path has plenty of text to page through.",
                "another sentence follows without any heading-like section boundary. "
                "it is intentionally long so the page-block fallback has to paginate this unstructured content. "
                "there is still no heading, no section marker, and no layout signal that should trigger section resolution.",
                "a third lowercase paragraph keeps the document unstructured and pushes the fallback path into truncation. "
                "it repeats the same plain prose style so the page-block cursor remains the only valid continuation mode.",
                "a fourth lowercase paragraph adds even more body text to guarantee truncation under the default overflow policy. "
                "this makes the fallback assertion stable even when the tool gets a little extra breathing room.",
            ]
            for order_index, text in enumerate(note_blocks):
                KnowledgeUploadPageBlock.objects.create(
                    upload=self.notes_upload,
                    page=notes_page,
                    order_index=order_index,
                    text=text,
                )

            self.notes_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.notes_upload,
                business_profile=self.business,
                chunk_index=0,
                content="\n".join(note_blocks),
                token_count=32,
                metadata={
                    "content_source": "page_blocks",
                    "page_numbers": [1],
                    "page_anchor": "p1",
                    "block_anchors": ["p1-b0", "p1-b1"],
                    "canonical_anchor_id": "page:1:paragraph:p1-b0",
                    "is_table_chunk": False,
                    "index_type": "text",
                },
            )

            contract_page1 = KnowledgeUploadPage.objects.create(
                upload=self.contract_upload,
                page_number=1,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )
            contract_page2 = KnowledgeUploadPage.objects.create(
                upload=self.contract_upload,
                page_number=2,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )
            contract_page1_blocks = [
                "Vendor Agreement",
                "General Overview",
                "This agreement governs service delivery and payment terms.",
                "Termination",
                "Either party may terminate for material breach upon written notice and failure to cure within 30 days.",
            ]
            contract_page2_blocks = [
                "The client may terminate immediately for fraud, data misuse, or repeated security violations.",
                "Upon termination, all unpaid invoices become immediately due and access credentials must be revoked.",
                "Governing Law",
                "This agreement is governed by the laws of Egypt.",
            ]
            for order_index, text in enumerate(contract_page1_blocks):
                KnowledgeUploadPageBlock.objects.create(
                    upload=self.contract_upload,
                    page=contract_page1,
                    order_index=order_index,
                    text=text,
                )
            for order_index, text in enumerate(contract_page2_blocks):
                KnowledgeUploadPageBlock.objects.create(
                    upload=self.contract_upload,
                    page=contract_page2,
                    order_index=order_index,
                    text=text,
                )
            self.contract_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.contract_upload,
                business_profile=self.business,
                chunk_index=0,
                content="\n".join(contract_page1_blocks),
                token_count=72,
                metadata={
                    "content_source": "page_blocks",
                    "page_numbers": [1],
                    "page_anchor": "p1",
                    "block_anchors": [f"p1-b{i}" for i in range(len(contract_page1_blocks))],
                    "canonical_anchor_id": "page:1:paragraph:p1-b0",
                    "is_table_chunk": False,
                    "index_type": "text",
                    "section_heading": "General Overview",
                    "section_headings": ["General Overview", "Termination"],
                },
            )

            self.noisy_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.noisy_upload,
                business_profile=self.business,
                chunk_index=0,
                content=(
                    "Benefits guide summary. Cafeteria schedule, parking access, health enrollment windows, "
                    "and office shuttle pickup times are described here."
                ),
                token_count=40,
                metadata={
                    "content_source": "page_blocks",
                    "page_numbers": [1],
                    "page_anchor": "p1",
                    "block_anchors": ["p1-b0"],
                    "canonical_anchor_id": "page:1:paragraph:p1-b0",
                    "is_table_chunk": False,
                    "index_type": "text",
                    "section_heading": "Benefits Overview",
                    "section_headings": ["Benefits Overview"],
                },
            )

    def _agentic_flags(self) -> FeatureState:
        return FeatureState(
            alias_lookup=True,
            entity_chunking=False,
            hybrid_search=True,
            rag_chunk_quality_filter=False,
            rag_chunk_dedupe=False,
            rag_alias_hygiene=False,
            rag_text_chunk_penalty=True,
            rag_shadow_ingestion=False,
            rag_shadow_retrieval=False,
            rag_eval_logging=False,
            rag_agentic_mode=True,
            mcp_gateway_mode=False,
            agent_workforce_v1=False,
            crm_v1=False,
        )

    def test_text_ref_uses_section_span_cursor_and_skips_earlier_summary(self) -> None:
        context = ToolExecutionContext()
        result = tools.execute_tool(
            "read_knowledge",
            {"refs": [{"id": str(self.cv_chunk.id)}], "max_chars": 180},
            conversation=self.conversation,
            context=context,
        )

        self.assertEqual(result.get("status"), "truncated")
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        entry = evidence[0]
        payload = entry.get("payload") or {}
        text = str(payload.get("text") or "")
        self.assertIn("Work Experiences", text)
        self.assertNotIn("Professional Summary", text)
        self.assertNotIn("Adham Khaled Idris", text)

        cursor_handle = str(entry.get("next_cursor") or "")
        self.assertTrue(cursor_handle)
        raw_cursor = context.read_cursor_handles.get(cursor_handle)
        self.assertIsInstance(raw_cursor, str)
        cursor_payload = tools._verify_agentic_read_cursor_v2(raw_cursor)  # type: ignore[arg-type]
        self.assertEqual(cursor_payload.get("kind"), "section_span")

    def test_section_span_continues_across_pages_until_next_major_heading(self) -> None:
        context = ToolExecutionContext()
        first = tools.execute_tool(
            "read_knowledge",
            {"refs": [{"id": str(self.cv_chunk.id)}], "max_chars": 180},
            conversation=self.conversation,
            context=context,
        )

        self.assertEqual(first.get("status"), "truncated")
        first_entry = (first.get("evidence") or [])[0]
        first_text = str((first_entry.get("payload") or {}).get("text") or "")
        cursor_handle = str(first_entry.get("next_cursor") or "")
        self.assertTrue(cursor_handle)

        second = tools.execute_tool(
            "read_knowledge",
            {"refs": [{"id": str(self.cv_chunk.id), "cursor": cursor_handle}], "max_chars": 500},
            conversation=self.conversation,
            context=context,
        )

        self.assertIn(second.get("status"), {"ok", "truncated"})
        second_entry = (second.get("evidence") or [])[0]
        second_text = str((second_entry.get("payload") or {}).get("text") or "")
        self.assertGreater(len(second_text), 50)
        combined_text = first_text + "\n" + second_text
        self.assertIn("Business Banking Relationship Manager", combined_text)
        self.assertIn("Business Banking Relationship Manager", first_text)
        self.assertIn("Senior Personal Banker - Retail Banking", combined_text)
        self.assertIn("Multiple top-ranked sales awards", second_text)
        self.assertNotIn("Business Competencies", second_text)

    def test_unstructured_text_falls_back_to_page_blocks_cursor(self) -> None:
        context = ToolExecutionContext()
        result = tools.execute_tool(
            "read_knowledge",
            {"refs": [{"id": str(self.notes_chunk.id)}], "max_chars": 200},
            conversation=self.conversation,
            context=context,
        )

        self.assertEqual(result.get("status"), "truncated")
        entry = (result.get("evidence") or [])[0]
        cursor_handle = str(entry.get("next_cursor") or "")
        self.assertTrue(cursor_handle)
        raw_cursor = context.read_cursor_handles.get(cursor_handle)
        self.assertIsInstance(raw_cursor, str)
        cursor_payload = tools._verify_agentic_read_cursor_v2(raw_cursor)  # type: ignore[arg-type]
        self.assertEqual(cursor_payload.get("kind"), "page_blocks")

    def test_search_replay_allows_repeat_queries_without_duplicate_trap(self) -> None:
        context = ToolExecutionContext()
        with mock.patch("apps.mcp.tools.FeatureFlagService.snapshot", return_value=self._agentic_flags()):
            first = tools.execute_tool(
                "search_knowledge",
                {"queries": ["Adham CV work titles positions"]},
                conversation=self.conversation,
                context=context,
            )
            second = tools.execute_tool(
                "search_knowledge",
                {"queries": ["Adham Idris work experience previous jobs positions roles"]},
                conversation=self.conversation,
                context=context,
            )

        self.assertEqual(first.get("tool"), "search_knowledge")
        self.assertEqual(second.get("tool"), "search_knowledge")
        self.assertNotEqual(first.get("status"), "duplicate")
        self.assertNotEqual(second.get("status"), "duplicate")
        self.assertNotEqual(first.get("error_code"), "duplicate_intent")
        self.assertNotEqual(second.get("error_code"), "duplicate_intent")
        self.assertEqual(context.searches_used, 2)
        self.assertTrue(first.get("refs"))
        self.assertTrue(second.get("refs"))

    def test_search_then_read_cv_work_history_surfaces_employment_section_in_noisy_corpus(self) -> None:
        context = ToolExecutionContext()
        with mock.patch("apps.mcp.tools.FeatureFlagService.snapshot", return_value=self._agentic_flags()):
            search_result = tools.execute_tool(
                "search_knowledge",
                {"queries": ["What titles did he work and where?"]},
                conversation=self.conversation,
                context=context,
            )

        self.assertEqual(search_result.get("status"), "ok")
        refs = search_result.get("refs") or []
        self.assertTrue(refs)
        self.assertGreaterEqual(len(refs), 1)
        top_labels = [str(ref.get("label") or "") for ref in refs[:4]]
        cv_ref = next((ref for ref in refs if "Adham Idris CV" in str(ref.get("label") or "")), None)
        self.assertIsNotNone(cv_ref, f"Expected CV ref in noisy result set, got {top_labels!r}")

        first_read = tools.execute_tool(
            "read_knowledge",
            {"refs": [{"id": str(self.cv_chunk.id)}], "max_chars": 180},
            conversation=self.conversation,
            context=context,
        )
        self.assertEqual(first_read.get("status"), "truncated")
        first_entry = (first_read.get("evidence") or [])[0]
        first_text = str((first_entry.get("payload") or {}).get("text") or "")
        self.assertIn("Work Experiences", first_text)

        cursor_handle = str(first_entry.get("next_cursor") or "")
        self.assertTrue(cursor_handle)
        second_read = tools.execute_tool(
            "read_knowledge",
            {"refs": [{"id": str(self.cv_chunk.id), "cursor": cursor_handle}], "max_chars": 500},
            conversation=self.conversation,
            context=context,
        )
        second_entry = (second_read.get("evidence") or [])[0]
        second_text = str((second_entry.get("payload") or {}).get("text") or "")
        combined = first_text + "\n" + second_text

        self.assertIn("Business Banking Relationship Manager", combined)
        self.assertIn("Senior Personal Banker - Retail Banking", combined)
        self.assertNotIn("Business Competencies", combined)

    def test_search_then_read_contract_query_stays_within_termination_section(self) -> None:
        context = ToolExecutionContext()
        with mock.patch("apps.mcp.tools.FeatureFlagService.snapshot", return_value=self._agentic_flags()):
            search_result = tools.execute_tool(
                "search_knowledge",
                {"queries": ["What are the termination rights?"]},
                conversation=self.conversation,
                context=context,
            )

        self.assertEqual(search_result.get("status"), "ok")
        refs = search_result.get("refs") or []
        self.assertTrue(refs)
        top_labels = [str(ref.get("label") or "") for ref in refs[:4]]
        contract_ref = next((ref for ref in refs if "Vendor Agreement" in str(ref.get("label") or "")), None)
        self.assertIsNotNone(contract_ref, f"Expected contract ref in noisy result set, got {top_labels!r}")

        first_read = tools.execute_tool(
            "read_knowledge",
            {"refs": [{"id": str(self.contract_chunk.id)}], "max_chars": 180},
            conversation=self.conversation,
            context=context,
        )
        self.assertIn(first_read.get("status"), {"ok", "truncated"})
        first_entry = (first_read.get("evidence") or [])[0]
        first_text = str((first_entry.get("payload") or {}).get("text") or "")
        self.assertIn("Termination", first_text)

        cursor_handle = str(first_entry.get("next_cursor") or "")
        second_text = ""
        if cursor_handle:
            second_read = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(self.contract_chunk.id), "cursor": cursor_handle}], "max_chars": 500},
                conversation=self.conversation,
                context=context,
            )
            second_entry = (second_read.get("evidence") or [])[0]
            second_text = str((second_entry.get("payload") or {}).get("text") or "")
        combined = first_text + "\n" + second_text

        self.assertIn("Either party may terminate for material breach", combined)
        self.assertIn("The client may terminate immediately for fraud", combined)
        self.assertIn("all unpaid invoices become immediately due", combined)
        self.assertNotIn("Governing Law", combined)
