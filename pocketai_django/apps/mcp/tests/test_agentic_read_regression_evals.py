from __future__ import annotations

from django.test import TestCase, override_settings

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
class AgenticReadRegressionEvals(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="read-regression@example.com", first_name="Read")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Read Regression Co",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Read Regression Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="read-regression-session",
        )
        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Account Fees.pdf",
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
                title="Account Fees - Table 1",
                section_heading="Accounts",
                order_index=1,
                column_schema=["service", "tariff", "prime", "plus"],
                metadata={},
            )
            rows = [
                ("Service", "Tariff", "Prime", "Plus", {"row_type": "header"}),
                (
                    "Account Opening Fees",
                    "Everyday Savers/Savers Account",
                    "EGP 100",
                    "EGP 100",
                    {},
                ),
                ("", "WellSavers Account", "N/A", "N/A", {}),
                ("Administrative Fees", "Everyday Savers/Savers Account", "EGP 120/Quarter", "EGP 120/Quarter", {}),
            ]
            self.table_rows: list[KnowledgeUploadTableRow] = []
            self.row_chunks: list[KnowledgeUploadChunk] = []
            for row_index, (service, tariff, prime, plus, row_metadata) in enumerate(rows):
                row = KnowledgeUploadTableRow.objects.create(
                    table=self.table,
                    row_index=row_index,
                    raw_text=f"{service} {tariff} {prime} {plus}",
                    metadata=dict(row_metadata),
                )
                self.table_rows.append(row)
                for column_index, (column_key, value) in enumerate(
                    (
                        ("service", service),
                        ("tariff", tariff),
                        ("prime", prime),
                        ("plus", plus),
                    )
                ):
                    KnowledgeUploadTableCell.objects.create(
                        table=self.table,
                        row=row,
                        column_index=column_index,
                        column_key=column_key,
                        raw_text=value,
                    )
                if row_index > 0:
                    chunk = KnowledgeUploadChunk.objects.create(
                        upload=self.upload,
                        business_profile=self.business,
                        chunk_index=row_index,
                        content=(
                            f"[Table] Account Fees - Table 1\n[Row] {row_index}\n"
                            f"service: {service}\ntariff: {tariff}\nprime: {prime}\nplus: {plus}"
                        ),
                        token_count=24,
                        metadata={
                            "is_table_chunk": True,
                            "table_chunk_role": "row",
                            "table_id": str(self.table.id),
                            "table_row_index": row_index,
                        },
                    )
                    self.row_chunks.append(chunk)

    def test_row_ref_with_range_arguments_auto_reads_parent_table(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": str(self.row_chunks[0].id), "row_start": 0, "row_limit": 10}],
                "max_chars": 4000,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "ok")
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        payload = evidence[0].get("payload") or {}
        self.assertEqual(payload.get("table_id"), str(self.table.id))
        self.assertEqual(payload.get("selection_mode"), "row_range")
        self.assertEqual(payload.get("upgraded_from_ref"), "table_row")
        rendered_rows = {" | ".join(str(cell or "") for cell in row) for row in payload.get("rows") or []}
        self.assertTrue(any("Account Opening Fees" in row and "EGP 100" in row for row in rendered_rows))
        self.assertTrue(any("Administrative Fees" in row and "EGP 120/Quarter" in row for row in rendered_rows))

    def test_table_row_model_ref_with_range_arguments_auto_reads_parent_table(self) -> None:
        result = tools.execute_tool(
            "read_knowledge",
            {
                "refs": [{"id": str(self.table_rows[1].id), "row_start": 0, "row_limit": 10}],
                "max_chars": 4000,
            },
            conversation=self.conversation,
            context=ToolExecutionContext(),
        )

        self.assertEqual(result.get("status"), "ok")
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1)
        payload = evidence[0].get("payload") or {}
        self.assertEqual(payload.get("table_id"), str(self.table.id))
        self.assertEqual(payload.get("selection_mode"), "row_range")
        self.assertEqual(payload.get("upgraded_from_ref"), "table_row")
        rendered_rows = {" | ".join(str(cell or "") for cell in row) for row in payload.get("rows") or []}
        self.assertTrue(any("Account Opening Fees" in row and "EGP 100" in row for row in rendered_rows))
        self.assertTrue(any("Administrative Fees" in row and "EGP 120/Quarter" in row for row in rendered_rows))
