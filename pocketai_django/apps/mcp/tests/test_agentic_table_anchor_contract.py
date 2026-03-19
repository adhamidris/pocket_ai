from __future__ import annotations

import uuid
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts.models import (
    AgentProfile,
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.conversations.models import Conversation
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadPage,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
)
from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


@override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
class AgenticSearchTableRefTests(SimpleTestCase):
    def test_table_row_hits_remain_distinct_refs_without_promotion(self) -> None:
        table_id = str(uuid.uuid4())
        first_chunk_id = str(uuid.uuid4())
        second_chunk_id = str(uuid.uuid4())
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "id": "snippet-1",
                    "chunk_id": first_chunk_id,
                    "upload_id": str(uuid.uuid4()),
                    "title": "CIB-Loans-EN.pdf – chunk 11",
                    "public_label": "CIB-Loans-EN.pdf – chunk 11",
                    "entity_name": "Unsecured Personal Loans via Apply Online 1%",
                    "summary": "service: Unsecured Personal Loans via Apply Online 1%; fees_charges: 1%",
                    "content": "service: Unsecured Personal Loans via Apply Online 1%; fees_charges: 1%",
                    "is_table_chunk": True,
                    "search_stage": "table_row_expansion",
                    "confidence_score": 0.80,
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 10,
                        "table_title": "CIB-Loans-EN – Table 1",
                        "table_order_index": 1,
                        "table_total_rows": 22,
                        "table_column_count": 6,
                    },
                },
                {
                    "id": "snippet-2",
                    "chunk_id": second_chunk_id,
                    "upload_id": str(uuid.uuid4()),
                    "title": "CIB-Loans-EN.pdf – chunk 4",
                    "public_label": "CIB-Loans-EN.pdf – chunk 4",
                    "entity_name": "Assessment Fees",
                    "summary": "service: Assessment Fees; fees_charges: EGP 200 (Paid once)",
                    "content": "service: Assessment Fees; fees_charges: EGP 200 (Paid once)",
                    "is_table_chunk": True,
                    "search_stage": "table_row_expansion",
                    "confidence_score": 0.71,
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 3,
                        "table_title": "CIB-Loans-EN – Table 1",
                        "table_order_index": 1,
                        "table_total_rows": 22,
                        "table_column_count": 6,
                    },
                },
            ],
            "completeness": {"shown": 2, "total_found": 2},
        }

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=SimpleNamespace(id=uuid.uuid4(), business_profile_id=uuid.uuid4()),
            context=ToolExecutionContext(),
        )

        self.assertEqual(result["status"], "ok")
        refs = result.get("refs") or []
        self.assertEqual(len(refs), 2)
        self.assertEqual([ref.get("id") for ref in refs], [first_chunk_id, second_chunk_id])
        self.assertEqual([ref.get("kind") for ref in refs], ["table_row", "table_row"])
        first_ref = refs[0]
        second_ref = refs[1]
        self.assertIn("chunk 11", str(first_ref.get("label") or "").lower())
        self.assertIn("chunk 4", str(second_ref.get("label") or "").lower())
        self.assertEqual(first_ref.get("coverage_hint", {}).get("row_index"), 10)
        self.assertEqual(second_ref.get("coverage_hint", {}).get("row_index"), 3)
        self.assertNotIn("matched_row_index", first_ref.get("coverage_hint", {}))
        self.assertNotIn("anchor_row_indexes", first_ref.get("coverage_hint", {}))

    def test_text_chunk_hits_remain_distinct_refs_without_document_grouping(self) -> None:
        upload_id = str(uuid.uuid4())
        first_chunk_id = str(uuid.uuid4())
        second_chunk_id = str(uuid.uuid4())
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "id": "snippet-1",
                    "chunk_id": first_chunk_id,
                    "upload_id": upload_id,
                    "title": "Handbook chunk 4",
                    "public_label": "Handbook chunk 4",
                    "summary": "Personal loan assessment fees are charged once.",
                    "content": "Personal loan assessment fees are charged once.",
                    "search_stage": "semantic",
                    "confidence_score": 0.82,
                    "chunk_index": 4,
                    "page_number": 2,
                },
                {
                    "id": "snippet-2",
                    "chunk_id": second_chunk_id,
                    "upload_id": upload_id,
                    "title": "Handbook chunk 7",
                    "public_label": "Handbook chunk 7",
                    "summary": "Assessment fee details and supporting notes.",
                    "content": "Assessment fee details and supporting notes.",
                    "search_stage": "semantic",
                    "confidence_score": 0.79,
                    "chunk_index": 7,
                    "page_number": 3,
                },
            ],
            "completeness": {"shown": 2, "total_found": 2},
        }

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=SimpleNamespace(id=uuid.uuid4(), business_profile_id=uuid.uuid4()),
            context=ToolExecutionContext(),
        )

        refs = result.get("refs") or []
        self.assertEqual(len(refs), 2)
        self.assertEqual([ref.get("id") for ref in refs], [first_chunk_id, second_chunk_id])
        self.assertEqual([ref.get("kind") for ref in refs], ["text_anchor", "text_anchor"])
        self.assertEqual(refs[0].get("coverage_hint", {}).get("page"), 2)
        self.assertEqual(refs[1].get("coverage_hint", {}).get("page"), 3)

    def test_table_chunk_ref_is_suppressed_when_same_table_has_multiple_row_refs(self) -> None:
        table_id = str(uuid.uuid4())
        upload_id = str(uuid.uuid4())
        first_chunk_id = str(uuid.uuid4())
        second_chunk_id = str(uuid.uuid4())
        parent_chunk_id = str(uuid.uuid4())
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "id": "row-1",
                    "chunk_id": first_chunk_id,
                    "upload_id": upload_id,
                    "title": "chunk 7",
                    "public_label": "chunk 7",
                    "entity_name": "Administration Fees",
                    "summary": "service: Administration Fees; fees_charges: Secured; fees_charges: 1.75%",
                    "content": "service: Administration Fees; fees_charges: Secured; fees_charges: 1.75%",
                    "is_table_chunk": True,
                    "search_stage": "table_direct",
                    "confidence_score": 0.82,
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 7,
                        "table_total_rows": 22,
                        "table_column_count": 6,
                    },
                },
                {
                    "id": "row-2",
                    "chunk_id": second_chunk_id,
                    "upload_id": upload_id,
                    "title": "chunk 8",
                    "public_label": "chunk 8",
                    "entity_name": "Administration Fees",
                    "summary": "service: Administration Fees; fees_charges: Unsecured; fees_charges: 2.00%",
                    "content": "service: Administration Fees; fees_charges: Unsecured; fees_charges: 2.00%",
                    "is_table_chunk": True,
                    "search_stage": "table_direct",
                    "confidence_score": 0.8,
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 8,
                        "table_total_rows": 22,
                        "table_column_count": 6,
                    },
                },
                {
                    "id": "parent-1",
                    "chunk_id": parent_chunk_id,
                    "upload_id": upload_id,
                    "title": "chunk 24",
                    "public_label": "chunk 24",
                    "entity_name": "Administration Fees",
                    "summary": "service: Administration Fees on the total Loan Amount up to 8 years",
                    "content": "service: Administration Fees on the total Loan Amount up to 8 years",
                    "is_table_chunk": True,
                    "search_stage": "table_parent",
                    "confidence_score": 0.95,
                    "source_diagnostics": {
                        "table_id": table_id,
                        "table_total_rows": 22,
                        "table_column_count": 6,
                    },
                },
            ],
            "completeness": {"shown": 3, "total_found": 3},
        }

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=SimpleNamespace(id=uuid.uuid4(), business_profile_id=uuid.uuid4()),
            context=ToolExecutionContext(),
        )

        refs = result.get("refs") or []
        self.assertEqual([ref.get("id") for ref in refs], [first_chunk_id, second_chunk_id])
        self.assertEqual([ref.get("kind") for ref in refs], ["table_row", "table_row"])

    def test_batched_search_fusion_uses_global_rank_not_concat_order(self) -> None:
        runs = [
            {
                "query": "first query",
                "snippets": [
                    {
                        "id": "weak-first",
                        "chunk_id": "weak-first",
                        "upload_id": "upload-1",
                        "content": "weak first result",
                        "confidence_score": 0.35,
                    },
                    {
                        "id": "shared",
                        "chunk_id": "shared",
                        "upload_id": "upload-1",
                        "content": "shared result",
                        "confidence_score": 0.4,
                    },
                ],
            },
            {
                "query": "second query",
                "snippets": [
                    {
                        "id": "strong-second",
                        "chunk_id": "strong-second",
                        "upload_id": "upload-2",
                        "content": "strong later result",
                        "confidence_score": 0.97,
                    },
                    {
                        "id": "shared",
                        "chunk_id": "shared",
                        "upload_id": "upload-1",
                        "content": "shared result",
                        "confidence_score": 0.82,
                    },
                ],
            },
        ]

        fused, fusion = tools._fuse_batched_search_runs(runs, clip_limit=2)

        self.assertEqual(fusion, {"method": "rrf_dedupe", "runs": 2})
        self.assertEqual([snippet.get("chunk_id") for snippet in fused], ["shared", "strong-second"])

    def test_fusion_does_not_merge_distinct_chunks_with_identical_content(self) -> None:
        runs = [
            {
                "query": "first query",
                "snippets": [
                    {
                        "id": "cib-chunk",
                        "chunk_id": "cib-chunk",
                        "upload_id": "upload-cib",
                        "content": "International Delivery Shipment Fees",
                        "confidence_score": 0.95,
                    },
                    {
                        "id": "neighbor-a",
                        "chunk_id": "neighbor-a",
                        "upload_id": "upload-other",
                        "content": "International Delivery Shipment Fees",
                        "confidence_score": 0.70,
                    },
                ],
            },
            {
                "query": "second query",
                "snippets": [
                    {
                        "id": "neighbor-b",
                        "chunk_id": "neighbor-b",
                        "upload_id": "upload-other",
                        "content": "International Delivery Shipment Fees",
                        "confidence_score": 0.68,
                    },
                ],
            },
        ]

        fused, fusion = tools._fuse_batched_search_runs(runs, clip_limit=3)

        self.assertEqual(fusion, {"method": "rrf_dedupe", "runs": 2})
        self.assertEqual([snippet.get("chunk_id") for snippet in fused[:1]], ["cib-chunk"])
        self.assertEqual(
            {snippet.get("chunk_id") for snippet in fused},
            {"cib-chunk", "neighbor-a", "neighbor-b"},
        )

    def test_agentic_refs_are_sorted_by_confidence_before_emission(self) -> None:
        low_chunk_id = str(uuid.uuid4())
        high_chunk_id = str(uuid.uuid4())
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "id": "low",
                    "chunk_id": low_chunk_id,
                    "upload_id": str(uuid.uuid4()),
                    "title": "low chunk",
                    "public_label": "low chunk",
                    "summary": "lower confidence snippet",
                    "content": "lower confidence snippet",
                    "search_stage": "semantic",
                    "confidence_score": 0.31,
                },
                {
                    "id": "high",
                    "chunk_id": high_chunk_id,
                    "upload_id": str(uuid.uuid4()),
                    "title": "high chunk",
                    "public_label": "high chunk",
                    "summary": "higher confidence snippet",
                    "content": "higher confidence snippet",
                    "search_stage": "semantic",
                    "confidence_score": 0.92,
                },
            ],
            "completeness": {"shown": 2, "total_found": 2},
        }

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=SimpleNamespace(id=uuid.uuid4(), business_profile_id=uuid.uuid4()),
            context=ToolExecutionContext(),
        )

        refs = result.get("refs") or []
        self.assertEqual([ref.get("id") for ref in refs], [high_chunk_id, low_chunk_id])

    def test_exact_table_row_read_hint_is_sized_smaller_than_table_context_hint(self) -> None:
        table_id = str(uuid.uuid4())
        row_chunk_id = str(uuid.uuid4())
        table_chunk_id = str(uuid.uuid4())
        upload_id = str(uuid.uuid4())
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "id": "row-1",
                    "chunk_id": row_chunk_id,
                    "upload_id": upload_id,
                    "title": "Mortgage.pdf – chunk 8",
                    "public_label": "Mortgage.pdf – chunk 8",
                    "summary": "service_type: Reschedule fees; commission_fees: Reschedule fees 2% of the outstanding amount",
                    "content": "service_type: Reschedule fees; commission_fees: Reschedule fees 2% of the outstanding amount",
                    "is_table_chunk": True,
                    "search_stage": "table_direct",
                    "confidence_score": 0.95,
                    "source_diagnostics": {
                        "table_id": table_id,
                        "row_index": 7,
                        "table_total_rows": 8,
                        "table_column_count": 2,
                    },
                },
                {
                    "id": "table-1",
                    "chunk_id": table_chunk_id,
                    "upload_id": upload_id,
                    "title": "Mortgage.pdf – table preview",
                    "public_label": "Mortgage.pdf – table preview",
                    "summary": "Mortgage fees table preview",
                    "content": "Administrative Fee | Partial early settlement fees | Full early settlement fees | Buyout fees | Registration and mortgage fees & expenses | Late penalty fees | Reschedule fees",
                    "is_table_chunk": True,
                    "search_stage": "table_parent",
                    "confidence_score": 0.62,
                    "source_diagnostics": {
                        "table_id": table_id,
                        "table_total_rows": 8,
                        "table_column_count": 2,
                    },
                },
            ],
            "completeness": {"shown": 2, "total_found": 2},
        }

        result = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=SimpleNamespace(id=uuid.uuid4(), business_profile_id=uuid.uuid4()),
            context=ToolExecutionContext(),
        )

        refs = result.get("refs") or []
        by_id = {str(ref.get("id")): ref for ref in refs}
        row_hint = int(((by_id[row_chunk_id].get("read_hint") or {}).get("suggested_max_chars")) or 0)
        table_hint = int(((by_id[table_chunk_id].get("read_hint") or {}).get("suggested_max_chars")) or 0)
        self.assertGreater(row_hint, 0)
        self.assertGreater(table_hint, 0)
        self.assertLess(row_hint, table_hint)
        self.assertLess(row_hint, 900)


@override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
class AgenticReadTableAnchorMergeTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="anchor-merge@example.com", first_name="Anchor")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Anchor Merge Co",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Anchor Agent",
            role="Assistant",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="anchor-merge-session",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="CIB-Loans-EN.pdf",
            ingestion_metadata={"format": "pdf"},
        )
        with tenant_context(self.business.id):
            KnowledgeUploadPage.objects.create(
                upload=self.upload,
                page_number=1,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )
            self.table = KnowledgeUploadTable.objects.create(
                upload=self.upload,
                title="CIB-Loans-EN – Table 1",
                section_heading="Personal Loans",
                order_index=1,
                column_schema=["service", "fees_charges"],
                metadata={},
            )
            rows = [
                ("Segment/Product", "Prime", {"row_type": "header"}),
                ("Personal Loans", "", {"row_type": "section_header"}),
                ("Administration Fees above 12 years' loan tenors", "3.00%", {}),
                ("Assessment Fees", "EGP 200 (Paid once)", {}),
                ("Clearance Letter", "EGP 200", {}),
                ("Liability Letter Issuance Fees", "EGP 50", {}),
                ("Unsecured Personal Loans via Apply Online", "1%", {}),
            ]
            for row_index, (service, fee, row_metadata) in enumerate(rows):
                row = KnowledgeUploadTableRow.objects.create(
                    table=self.table,
                    row_index=row_index,
                    raw_text=f"{service} {fee}",
                    metadata=dict(row_metadata),
                )
                KnowledgeUploadTableCell.objects.create(
                    table=self.table,
                    row=row,
                    column_index=0,
                    column_key="service",
                    raw_text=service,
                )
                KnowledgeUploadTableCell.objects.create(
                    table=self.table,
                    row=row,
                    column_index=1,
                    column_key="fees_charges",
                    raw_text=fee,
                )
            self.assessment_row_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.upload,
                business_profile=self.business,
                chunk_index=4,
                content="[Table] CIB-Loans-EN – Table 1\n[Row] 3\nservice: Assessment Fees\nfees_charges: EGP 200 (Paid once)",
                token_count=20,
                metadata={
                    "is_table_chunk": True,
                    "table_chunk_role": "row",
                    "table_id": str(self.table.id),
                    "table_row_index": 3,
                },
            )
        self.context = ToolExecutionContext()
        self.context.table_row_anchor_manifests[str(self.table.id)] = {
            "ref_id": str(self.table.id),
            "table_id": str(self.table.id),
            "matched_row_index": 6,
            "anchors": [{"row_index": 6}, {"row_index": 3}],
            "estimated_rows": 7,
            "estimated_columns": 2,
        }

    def test_read_knowledge_merges_multiple_anchor_windows_for_promoted_table_ref(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": str(self.table.id)}],
                "max_chars": 4000,
            },
            conversation=self.conversation,
            context=self.context,
        )

        self.assertEqual(result.get("status"), "ok")
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        payload = evidence[0].get("payload") or {}
        self.assertEqual(payload.get("selection_mode"), "anchor_merge")
        rows = payload.get("rows") or []
        rendered_rows = {" | ".join(str(cell or "") for cell in row) for row in rows}
        self.assertTrue(any("Assessment Fees" in row and "EGP 200 (Paid once)" in row for row in rendered_rows))
        self.assertTrue(any("Unsecured Personal Loans via Apply Online" in row and "1%" in row for row in rendered_rows))

    @override_settings(MCP_READ_KNOWLEDGE_OVERFLOW_MARGIN=0)
    def test_table_paging_reports_more_available_without_truncated_status(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": str(self.table.id), "row_start": 0}],
                "max_chars": 320,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "ok")
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        entry = evidence[0]
        self.assertFalse(bool(entry.get("truncated")))
        self.assertFalse(bool(entry.get("complete")))
        self.assertTrue(bool(entry.get("more_rows_available")))
        payload = entry.get("payload") or {}
        self.assertIn("next_row_start", payload)
        read_entries = result.get("read") or []
        self.assertEqual(len(read_entries), 1)
        self.assertEqual(read_entries[0].get("status"), "more_available")
        self.assertIn("More rows available with row_start=", str(read_entries[0].get("hint") or ""))

    def test_row_chunk_ref_reads_exact_visible_row(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": str(self.assessment_row_chunk.id)}],
                "max_chars": 2000,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "ok")
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        payload = evidence[0].get("payload") or {}
        self.assertEqual(payload.get("selection_mode"), "row_ref")
        rows = payload.get("rows") or []
        self.assertEqual(len(rows), 1)
        rendered = " | ".join(str(cell or "") for cell in rows[0])
        self.assertIn("Assessment Fees", rendered)
        self.assertIn("EGP 200 (Paid once)", rendered)

    def test_row_chunk_ref_rejects_row_range_arguments(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": str(self.assessment_row_chunk.id), "row_start": 0, "row_limit": 10}],
                "max_chars": 2000,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "error")
        errors = result.get("errors") or []
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].get("error_code"), "row_ref_range_not_supported")
        self.assertIn(str(self.table.id), str(errors[0].get("hint") or ""))


@override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
class AgenticReadPackingTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="packing@example.com", first_name="Packing")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Packing Co",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Packing Agent",
            role="Assistant",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="packing-session",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Mortgage.pdf",
            ingestion_metadata={"format": "pdf"},
        )
        with tenant_context(self.business.id):
            KnowledgeUploadPage.objects.create(
                upload=self.upload,
                page_number=1,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )
            self.table = KnowledgeUploadTable.objects.create(
                upload=self.upload,
                title="Mortgage – Table 1",
                section_heading="Mortgage Fees",
                order_index=1,
                column_schema=["service_type", "commission_fees"],
                metadata={},
            )

            self.row_chunk_ids: list[str] = []
            rows = [
                (
                    "Administrative Fee (Paid once in advance) 2% of the loan amount",
                    "2% of the loan amount",
                ),
                (
                    "Partial early settlement fees 7% of the paid amount",
                    "7% of the paid amount",
                ),
                (
                    "Buyout fees",
                    "Buyout fees 10% of the outstanding balance",
                ),
            ]
            for row_index, (service, fee) in enumerate(rows):
                row = KnowledgeUploadTableRow.objects.create(
                    table=self.table,
                    row_index=row_index,
                    raw_text=f"{service} {fee}",
                    metadata={},
                )
                KnowledgeUploadTableCell.objects.create(
                    table=self.table,
                    row=row,
                    column_index=0,
                    column_key="service_type",
                    raw_text=service,
                )
                KnowledgeUploadTableCell.objects.create(
                    table=self.table,
                    row=row,
                    column_index=1,
                    column_key="commission_fees",
                    raw_text=fee,
                )
                chunk = KnowledgeUploadChunk.objects.create(
                    upload=self.upload,
                    business_profile=self.business,
                    chunk_index=row_index + 1,
                    content=f"[Table] Mortgage – Table 1\n[Row] {row_index}\nservice_type: {service}\ncommission_fees: {fee}",
                    token_count=24,
                    metadata={
                        "is_table_chunk": True,
                        "table_chunk_role": "row",
                        "table_id": str(self.table.id),
                        "table_row_index": row_index,
                    },
                )
                self.row_chunk_ids.append(str(chunk.id))

    def test_greedy_packing_reads_top_priority_rows_before_deferring_tail(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": ref_id} for ref_id in self.row_chunk_ids],
                "max_chars": 700,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "truncated")
        evidence = result.get("evidence") or []
        self.assertEqual([entry.get("id") for entry in evidence], self.row_chunk_ids[:2])
        deferred = result.get("deferred") or []
        self.assertEqual([entry.get("id") for entry in deferred], [self.row_chunk_ids[2]])
        self.assertEqual(deferred[0].get("reason"), "budget_too_small")

    def test_small_budget_near_miss_uses_overflow_buffer(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": self.row_chunk_ids[0]}],
                "max_chars": 300,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "ok")
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].get("id"), self.row_chunk_ids[0])
        self.assertGreater(int(result.get("total_chars") or 0), 300)

    @override_settings(MCP_READ_KNOWLEDGE_OVERFLOW_MARGIN=0)
    def test_budget_deferred_ref_can_retry_with_higher_max_chars_same_turn(self) -> None:
        context = ToolExecutionContext()

        first = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": self.row_chunk_ids[0]}],
                "max_chars": 300,
            },
            conversation=self.conversation,
            context=context,
        )

        self.assertEqual(first.get("status"), "error")
        self.assertEqual((first.get("deferred") or [])[0].get("id"), self.row_chunk_ids[0])

        second = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": self.row_chunk_ids[0]}],
                "max_chars": 1000,
            },
            conversation=self.conversation,
            context=context,
        )

        self.assertEqual(second.get("status"), "ok")
        evidence = second.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].get("id"), self.row_chunk_ids[0])
