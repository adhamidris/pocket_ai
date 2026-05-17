from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

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
from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)
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
from apps.rag.knowledge_search import KnowledgeSearchResult, KnowledgeSnippet
from core.tenancy import tenant_context


@override_settings(
    MCP_NEW_CONTRACT_ENABLED=True,
    MCP_AGENTIC_READ_V2_ENABLED=True,
    MCP_AGENTIC_SEARCH_PREVIEWS_HYBRID_ENABLED=False,
)
class AgenticRagProductionAcceptanceGate(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="rag-acceptance@example.com", first_name="Gate")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="RAG Acceptance Bank",
            industry="financial_services",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="RAG Acceptance Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="rag-acceptance-session",
        )
        self.credit_upload = self._upload("Fees and Charges Credit Cards Eng_185")
        self.account_upload = self._upload("CIB-Account-EN")

        with tenant_context(self.business.id):
            KnowledgeUploadPage.objects.create(
                upload=self.account_upload,
                page_number=1,
                width=612.0,
                height=792.0,
                content_type="application/pdf",
                metadata={},
            )
            self.account_table = self._table(
                self.account_upload,
                title="Account Fees - Table 1",
                columns=["service", "tariff", "prime", "plus", "wealth"],
                rows=[
                    (["Service", "Tariff", "Prime", "Plus", "Wealth"], {"row_type": "header"}),
                    (
                        [
                            "Account Opening Fees",
                            "Everyday Savers/Savers Account",
                            "EGP 100",
                            "EGP 100",
                            "Free",
                        ],
                        None,
                    ),
                    (["", "WellSavers Account", "N/A", "N/A", "EGP 1000"], None),
                    (
                        [
                            "Administrative Fees",
                            "Everyday Savers/Savers Account",
                            "EGP 120/Quarter",
                            "EGP 120/Quarter",
                            "EGP 100/Quarter",
                        ],
                        None,
                    ),
                ],
            )
            self.account_row_chunk = KnowledgeUploadChunk.objects.create(
                upload=self.account_upload,
                business_profile=self.business,
                chunk_index=1,
                content="[Table] Account Fees - Table 1 [Row] 1 Service: Account Opening Fees Prime: EGP 100",
                token_count=24,
                metadata={
                    "is_table_chunk": True,
                    "table_chunk_role": "row",
                    "table_id": str(self.account_table.id),
                    "table_row_index": 1,
                },
            )

    def _upload(self, name: str) -> KnowledgeUpload:
        return KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name=name,
            ingestion_metadata={"format": "pdf"},
        )

    def _table(
        self,
        upload: KnowledgeUpload,
        *,
        title: str,
        columns: list[str],
        rows: list[tuple[list[str], dict[str, object] | None]],
    ) -> KnowledgeUploadTable:
        table = KnowledgeUploadTable.objects.create(
            upload=upload,
            title=title,
            section_heading=title,
            order_index=1,
            column_schema=columns,
            metadata={},
        )
        for row_index, (values, metadata) in enumerate(rows):
            row = KnowledgeUploadTableRow.objects.create(
                table=table,
                row_index=row_index,
                raw_text=" ".join(values),
                metadata=dict(metadata or {}),
            )
            for column_index, value in enumerate(values):
                KnowledgeUploadTableCell.objects.create(
                    table=table,
                    row=row,
                    column_index=column_index,
                    column_key=columns[column_index] if column_index < len(columns) else f"column_{column_index + 1}",
                    raw_text=value,
                )
        return table

    def test_same_thread_topic_switch_does_not_lock_to_primary_document(self) -> None:
        captured: dict[str, object] = {}
        account_snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="CIB-Account-EN - chunk 1",
            summary="Account Opening Fees",
            source="File Upload",
            content="Account Opening Fees Prime EGP 100",
            upload_id=self.account_upload.id,
            chunk_id=self.account_row_chunk.id,
            chunk_index=1,
            search_stage="hybrid",
            confidence_score=0.9,
        )

        def _search(**kwargs):
            captured["query"] = kwargs.get("query")
            captured["session_context"] = dict(kwargs.get("session_context") or {})
            return KnowledgeSearchResult(
                snippets=(account_snippet,),
                status="ok",
                diagnostics={
                    "path": "hybrid",
                    "document_continuity_allowed": (kwargs.get("session_context") or {}).get(
                        "document_continuity_allowed"
                    ),
                    "document_continuity_reason": (kwargs.get("session_context") or {}).get(
                        "document_continuity_reason"
                    ),
                    "document_continuity_boosted_candidates": 0,
                },
            )

        context = ToolExecutionContext()
        context.track_document_read(str(self.credit_upload.id), title="Fees and Charges Credit Cards Eng_185")
        context.search_history.append({"query": "list me all credit cards and their issuance fees"})
        fake_service = SimpleNamespace(search=_search)

        with patch("apps.mcp.tools._knowledge_service", return_value=fake_service):
            result = tools.execute_tool(
                "search_knowledge",
                {"query": "account opening fees", "limit": 12},
                conversation=self.conversation,
                context=context,
            )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(captured.get("query"), "account opening fees")
        session_context = captured.get("session_context") or {}
        self.assertFalse(session_context.get("document_continuity_allowed"))
        observability = result.get("retrieval_observability") or {}
        self.assertEqual((observability.get("query_scope") or {}).get("topic_scope"), "not_evaluated")
        self.assertEqual((observability.get("query_scope") or {}).get("reason"), "document_continuity_removed")
        self.assertFalse((observability.get("continuity") or {}).get("allowed"))
        refs = result.get("refs") or []
        self.assertTrue(any(str(ref.get("document_id")) == str(self.account_upload.id) for ref in refs))

    def test_row_ref_continuation_auto_upgrades_to_parent_table(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": str(self.account_row_chunk.id), "row_start": 0, "row_limit": 10}],
                "max_chars": 4000,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "ok")
        self.assertNotEqual(result.get("error_code"), "row_ref_range_not_supported")
        evidence = result.get("evidence") or []
        self.assertTrue(evidence)
        payload = evidence[0].get("payload") or {}
        self.assertEqual(payload.get("table_id"), str(self.account_table.id))
        self.assertEqual(payload.get("selection_mode"), "row_range")
        self.assertEqual(payload.get("upgraded_from_ref"), "table_row")

    def test_search_does_not_backend_prefetch_enumeration_evidence(self) -> None:
        fake_service = SimpleNamespace(
            search=lambda **_kwargs: KnowledgeSearchResult(
                snippets=tuple(),
                status="not_found",
                diagnostics={"path": "acceptance_empty"},
            )
        )

        with patch("apps.mcp.tools._knowledge_service", return_value=fake_service):
            result = tools.execute_tool(
                "search_knowledge",
                {"query": "list me all account opening fees", "limit": 12},
                conversation=self.conversation,
                context=ToolExecutionContext(),
            )

        self.assertIn(result.get("status"), {"empty", "not_found"})
        self.assertNotIn("prefetched_evidence", result)
        self.assertNotIn("prefetched_read_status", result)
        completeness = result.get("completeness") or {}
        self.assertNotIn("enumeration_evidence", completeness)
        observability = result.get("retrieval_observability") or {}
        self.assertNotIn("enumeration", observability)


class AgenticRagIngestionAcceptanceGate(SimpleTestCase):
    @staticmethod
    def _make_row(row_index: int, values: list[str], *, row_type: str = "data") -> TableRowPayload:
        cells = [
            TableCellPayload(
                row_index=row_index,
                column_index=column_index,
                column_key=f"column_{column_index + 1}",
                raw_text=value,
                metadata={"row_span": 1, "column_span": 1},
            )
            for column_index, value in enumerate(values)
        ]
        return TableRowPayload(
            row_index=row_index,
            page_number=1,
            raw_text=" | ".join(values),
            metadata={"row_type": row_type},
            cells=cells,
        )

    def test_reingested_table_schema_preserves_headers_and_parent_labels(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        table = TablePayload(
            order_index=1,
            title="CIB-Account-EN - Table 2",
            section_heading="Bedaya Accounts",
            page_number=1,
            column_schema=["Bedaya Accounts", "column_2", "column_3"],
            rows=[
                self._make_row(0, ["", "Bedaya Saving EGP", "Bedaya Current USD"]),
                self._make_row(1, ["Account Opening Fees", "Free", "Free"]),
                self._make_row(2, ["Minimum Balance Fees", "Free", "Free"]),
            ],
        )

        processed, issues, meta = service._postprocess_tables([table])
        table_after = processed[0]
        header_row = next(row for row in table_after.rows if int(row.row_index) == 0)
        account_row = next(row for row in table_after.rows if int(row.row_index) == 1)

        self.assertEqual(
            table_after.column_schema,
            ["Bedaya Accounts", "Bedaya Saving EGP", "Bedaya Current USD"],
        )
        self.assertNotIn("column_2", table_after.column_schema)
        self.assertEqual(header_row.metadata.get("row_type"), "header")
        self.assertTrue(header_row.metadata.get("embedded_header_promoted"))
        self.assertEqual(account_row.cells[1].column_key, "Bedaya Saving EGP")
        self.assertEqual(account_row.cells[2].column_key, "Bedaya Current USD")
        self.assertEqual(meta.get("embedded_header_rows_promoted"), 1)
        self.assertTrue(any(issue.code == "table_embedded_header_promoted" for issue in issues))
