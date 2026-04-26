from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

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
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
)
from apps.mcp import tools
from apps.mcp.orchestrator import McpOrchestratorService
from apps.mcp.types import ToolExecutionContext
from apps.rag.ai_orchestrator import KnowledgeSearchResult
from core.tenancy import tenant_context


@override_settings(
    MCP_NEW_CONTRACT_ENABLED=True,
    MCP_AGENTIC_READ_V2_ENABLED=True,
    MCP_AGENTIC_SEARCH_PREVIEWS_HYBRID_ENABLED=False,
)
class AgenticEnumerationEvidenceTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="enumeration@example.com", first_name="Enum")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Enumeration Bank",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Enumeration Agent",
            role="Assistant",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="enumeration-session",
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

    def _search_with_empty_retrieval(self, query: str) -> dict[str, object]:
        fake_service = SimpleNamespace(
            search=lambda **_kwargs: KnowledgeSearchResult(
                snippets=tuple(),
                status="not_found",
                diagnostics={"path": "test_empty"},
            )
        )
        with patch("apps.mcp.tools._knowledge_service", return_value=fake_service):
            return dict(
                tools.execute_tool(
                    "search_knowledge",
                    {"query": query, "limit": 12},
                    conversation=self.conversation,
                    context=ToolExecutionContext(),
                )
            )

    def _prefetched_payload(self, result: dict[str, object]) -> dict[str, object]:
        prefetched = result.get("prefetched_evidence")
        self.assertIsInstance(prefetched, list)
        self.assertTrue(prefetched)
        payload = prefetched[0].get("payload") if isinstance(prefetched[0], dict) else None
        self.assertIsInstance(payload, dict)
        return payload

    def test_credit_card_issuance_enumeration_prefetches_one_structured_packet(self) -> None:
        with tenant_context(self.business.id):
            upload = self._upload("Credit Card Fees.pdf")
            self._table(
                upload,
                title="Credit Card Fees - Table 1",
                columns=["card_type", "white", "classic", "gold"],
                rows=[
                    (["Card Type", "White", "Classic", "Gold"], {"row_type": "header"}),
                    (["Issuance and Renewal Fees", "EGP 500", "EGP 250", "EGP 300"], None),
                    (["Replacement Fees", "Free", "Free", "Free"], None),
                ],
            )
            self._table(
                upload,
                title="Credit Card Fees - Table 2",
                columns=["card_type", "platinum", "world"],
                rows=[
                    (["Card Type", "Platinum", "World"], {"row_type": "header"}),
                    (["Issuance and Renewal Fees", "EGP 700", "EGP 3,500"], None),
                ],
            )

        result = self._search_with_empty_retrieval("list me all credit cards and their issuance fees")

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("prefetched_read_status"), "complete")
        payload = self._prefetched_payload(result)
        self.assertEqual(payload.get("type"), "table_enumeration")
        self.assertEqual((payload.get("completeness") or {}).get("status"), "complete")
        rendered = {" | ".join(str(cell or "") for cell in row) for row in payload.get("rows") or []}
        self.assertTrue(any("white" in row and "EGP 500" in row for row in rendered))
        self.assertTrue(any("classic" in row and "EGP 250" in row for row in rendered))
        self.assertTrue(any("world" in row and "EGP 3,500" in row for row in rendered))

    def test_account_opening_enumeration_includes_merged_child_rows_and_inferred_headers(self) -> None:
        with tenant_context(self.business.id):
            upload = self._upload("Account Fees.pdf")
            self._table(
                upload,
                title="Main Account Fees",
                columns=["service", "column_2", "prime", "plus", "wealth"],
                rows=[
                    (["Service", "", "Prime", "Plus", "Wealth"], {"row_type": "header"}),
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
            self._table(
                upload,
                title="Bedaya Accounts",
                columns=["bedaya_accounts", "column_2", "column_3"],
                rows=[
                    (["", "Bedaya Saving EGP", "Bedaya Current USD"], None),
                    (["Account Opening Fees", "Free", "Free"], None),
                    (["Minimum Balance Fees", "Free", "Free"], None),
                ],
            )

        result = self._search_with_empty_retrieval("list me all account opening fees")

        self.assertEqual(result.get("status"), "ok")
        payload = self._prefetched_payload(result)
        rendered = {" | ".join(str(cell or "") for cell in row) for row in payload.get("rows") or []}
        self.assertTrue(any("prime" in row and "EGP 100" in row for row in rendered))
        self.assertTrue(any("WellSavers Account" in row and "wealth" in row and "EGP 1000" in row for row in rendered))
        self.assertTrue(any("Bedaya Saving EGP" in row and "Free" in row for row in rendered))
        self.assertFalse(any("Administrative Fees" in row for row in rendered))

    def test_compactor_preserves_prefetched_enumeration_rows_for_the_agent(self) -> None:
        payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "prefetched_evidence": [
                {
                    "id": "enumeration:test",
                    "title": "Enumeration Evidence: opening fee",
                    "type": "table",
                    "kind": "enumeration_table",
                    "complete": True,
                    "truncated": False,
                    "payload": {
                        "type": "table_enumeration",
                        "attribute": "opening fee",
                        "columns": ["document", "table", "attribute", "item", "value"],
                        "rows": [["Account Fees.pdf", "Main", "Account Opening Fees", "Prime", "EGP 100"]],
                        "row_count": 1,
                        "source_tables": [{"document": "Account Fees.pdf", "table": "Main", "matched_rows": 1}],
                        "completeness": {"status": "complete", "complete": True, "returned_items": 1},
                    },
                }
            ],
        }
        orchestrator = McpOrchestratorService(agent=self.agent, provider=None)

        compact = orchestrator._compact_tool_payload_for_prompt("search_knowledge", payload, max_rows=10)

        evidence = compact.get("prefetched_evidence")
        self.assertIsInstance(evidence, list)
        compact_payload = evidence[0].get("payload") if isinstance(evidence[0], dict) else None
        self.assertIsInstance(compact_payload, dict)
        self.assertEqual(compact_payload.get("rows"), [["Account Fees.pdf", "Main", "Account Opening Fees", "Prime", "EGP 100"]])
