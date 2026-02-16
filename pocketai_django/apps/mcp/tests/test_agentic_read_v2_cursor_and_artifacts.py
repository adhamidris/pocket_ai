from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadPage,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    KnowledgeUploadTableCell,
)
from apps.conversations.models import Conversation
from apps.mcp import tools
from apps.mcp.models import McpToolOutputArtifact
from apps.mcp.tool_artifacts import store_local_tool_output_artifact
from apps.mcp.types import ToolExecutionContext
from core.tenancy import tenant_context


class AgenticReadV2CursorAndArtifactTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        tools._knowledge_service.cache_clear()  # type: ignore[attr-defined]
        self.embed_patcher = mock.patch("apps.rag.ai_orchestrator.build_embedding_service", return_value=None)
        self.embed_patcher.start()

        self.user = User.objects.create(email="mcp-read-v2@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="MCP V2 Bank",
            industry="banking",
        )
        self.tenant_scope = tenant_context(self.business.id)
        self.tenant_scope.__enter__()

        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            session_token="session-mcp-v2",
        )

        self.upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Big Doc",
            ingestion_metadata={"format": "pdf"},
        )

        self.chunk = KnowledgeUploadChunk.objects.create(
            upload=self.upload,
            business_profile=self.business,
            chunk_index=0,
            content="A" * 5000,
        )

    def tearDown(self) -> None:
        self.embed_patcher.stop()
        if hasattr(self, "tenant_scope"):
            self.tenant_scope.__exit__(None, None, None)
        super().tearDown()

    def _enable_agentic_mode(self):
        return mock.patch.object(
            tools.FeatureFlagService,
            "snapshot",
            return_value=SimpleNamespace(rag_agentic_mode=True),
        )

    @staticmethod
    def _resolve_signed_cursor(context: ToolExecutionContext, cursor_token: str) -> str:
        mapping = getattr(context, "read_cursor_handles", None)
        if isinstance(mapping, dict):
            resolved = mapping.get(str(cursor_token))
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()
        return str(cursor_token)

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_ref_reads_rows_without_model_field_errors(self) -> None:
        """
        Regression: table refs (KnowledgeUploadTable.id) must be readable via read_knowledge.

        This path previously crashed during queryset compilation when `.only()` referenced a
        non-existent KnowledgeUpload field (e.g., `upload__filename`).
        """

        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=1,
            title="Fees Table",
            column_schema=["card_type", "fee"],
        )
        # Avoid dataset heuristics: PDF uploads normally have pages in real ingestion.
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=1)
        # In production, ingestion annotates rows; mimic non-header rows.
        row = KnowledgeUploadTableRow.objects.create(table=table, row_index=0, metadata={"row_type": "body"})
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=0,
            column_key="card_type",
            raw_text="white",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=1,
            column_key="fee",
            raw_text="EGP 500",
        )

        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 2000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        self.assertTrue(result.get("evidence"), json.dumps(result, indent=2, default=str))
        item = result["evidence"][0]
        self.assertEqual(item["id"], str(table.id))
        self.assertEqual(item["type"], "table")
        self.assertEqual(item["kind"], "table_rows")
        self.assertEqual(item["payload"]["columns"], ["card_type", "fee"])
        self.assertEqual(item["payload"]["rows"], [["white", "EGP 500"]])

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_rows_apply_inferred_scope_to_effective_values(self) -> None:
        """Effective table rows should reflect inferred applicability for answer-time consistency."""

        fee_value = "1% (with Min USD 2 and no Max)"
        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=1,
            title="Traveler Cheques",
            column_schema=["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=1)
        row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=0,
            metadata={
                "row_type": "body",
                "table_scope_contract_version": "v2",
                "observed_value_columns": ["service", "prime", "plus", "wealth", "exclusive_wealth"],
                "qualifier_columns": ["service"],
                "scope_dimension_columns": ["prime", "plus", "wealth", "exclusive_wealth", "private"],
                "inferred_scope_columns": ["prime", "plus", "wealth", "exclusive_wealth", "private"],
                "scope_reason": "scope_edge_completion",
                "scope_confidence": 0.89,
                "scope_value": fee_value,
            },
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=0,
            column_key="service",
            raw_text="Traveler cheques (sell) in foreign currency",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=2,
            column_key="prime",
            raw_text=fee_value,
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=3,
            column_key="plus",
            raw_text=fee_value,
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=4,
            column_key="wealth",
            raw_text=fee_value,
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=5,
            column_key="exclusive_wealth",
            raw_text=fee_value,
        )

        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 4000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        self.assertTrue(result.get("evidence"), json.dumps(result, indent=2, default=str))
        payload = result["evidence"][0]["payload"]
        self.assertEqual(payload.get("row_value_mode"), "effective_scope_normalized")
        self.assertEqual(payload["rows"][0][6], fee_value)
        row_metadata = payload.get("row_metadata") or []
        self.assertEqual(len(row_metadata), 1)
        self.assertIn("private", row_metadata[0].get("effective_scope_overrides") or [])
        self.assertEqual(row_metadata[0].get("scope_reason"), "scope_edge_completion")

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_rows_do_not_fill_scope_for_abstain_reason(self) -> None:
        """Rows marked as scope_abstain should keep observed blanks in effective values."""

        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=1,
            title="Ambiguous Scope",
            column_schema=["service", "prime", "plus", "private"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=1)
        row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=0,
            metadata={
                "row_type": "body",
                "inferred_scope_columns": ["prime", "plus", "private"],
                "scope_reason": "scope_abstain",
                "scope_confidence": 0.58,
                "scope_value": "EGP 40",
            },
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=0,
            column_key="service",
            raw_text="Ambiguous sample",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=1,
            column_key="prime",
            raw_text="EGP 40",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=2,
            column_key="plus",
            raw_text="EGP 40",
        )

        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 2000},
                conversation=self.conversation,
                context=ctx,
            )

        payload = result["evidence"][0]["payload"]
        self.assertEqual(payload["rows"][0][3], "")
        row_metadata = payload.get("row_metadata") or []
        self.assertEqual(len(row_metadata), 1)
        self.assertNotIn("effective_scope_overrides", row_metadata[0])

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_row_ref_reads_single_row(self) -> None:
        """Row refs (KnowledgeUploadTableRow.id) should be readable and return a single row."""

        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=1,
            title="Fees Table",
            column_schema=["card_type", "fee"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=1)
        row = KnowledgeUploadTableRow.objects.create(table=table, row_index=0, metadata={"row_type": "body"})
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=0,
            column_key="card_type",
            raw_text="white",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=1,
            column_key="fee",
            raw_text="EGP 500",
        )

        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(row.id)}], "max_chars": 2000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        self.assertTrue(result.get("evidence"), json.dumps(result, indent=2, default=str))
        item = result["evidence"][0]
        self.assertEqual(item["id"], str(row.id))
        self.assertEqual(item["type"], "table")
        self.assertEqual(item["kind"], "table_rows")
        self.assertEqual(item["payload"]["columns"], ["card_type", "fee"])
        self.assertEqual(item["payload"]["rows"], [["white", "EGP 500"]])

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_grouped_row_refs_promote_to_single_table_context_read(self) -> None:
        """Multiple row refs for the same table should be consolidated into one broader table read."""

        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=1,
            title="Grouped Fees",
            column_schema=["service", "fee"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=1)
        rows = []
        for idx, (service, fee) in enumerate(
            (
                ("Account Opening", "EGP 100"),
                ("Outgoing Transfer", "0.2% min EGP 40"),
                ("Checkbook", "EGP 480"),
            ),
            start=0,
        ):
            row = KnowledgeUploadTableRow.objects.create(table=table, row_index=idx, metadata={"row_type": "body"})
            rows.append(row)
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=row,
                column_index=0,
                column_key="service",
                raw_text=service,
            )
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=row,
                column_index=1,
                column_key="fee",
                raw_text=fee,
            )

        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(rows[1].id)}, {"id": str(rows[2].id)}], "max_chars": 2000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1, json.dumps(result, indent=2, default=str))
        self.assertEqual(evidence[0]["id"], str(rows[1].id))
        payload = evidence[0]["payload"]
        self.assertEqual(payload["columns"], ["service", "fee"])
        self.assertGreaterEqual(int(payload.get("rows_shown") or 0), 2)
        read_entries = result.get("read") or []
        covered_entries = [entry for entry in read_entries if entry.get("status") == "covered"]
        self.assertEqual(len(covered_entries), 1, json.dumps(read_entries, indent=2, default=str))

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_promoted_table_ref_uses_anchor_manifest_on_first_read(self) -> None:
        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=2,
            title="Teller Fees",
            column_schema=["service", "fee"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=2)
        for idx in range(8):
            service = f"Service {idx}"
            fee = f"Fee {idx}"
            if idx == 6:
                service = "Cash deposit with same day value date"
                fee = "0.2% min EGP 100"
            row = KnowledgeUploadTableRow.objects.create(
                table=table,
                row_index=idx,
                metadata={"row_type": "body"},
            )
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=row,
                column_index=0,
                column_key="service",
                raw_text=service,
            )
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=row,
                column_index=1,
                column_key="fee",
                raw_text=fee,
            )

        context = ToolExecutionContext(char_budget_per_turn=100_000)
        legacy_payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "snippets": [
                {
                    "is_table_chunk": True,
                    "chunk_id": str(self.chunk.id),
                    "upload_id": str(self.upload.id),
                    "title": "Teller Fees row 6",
                    "summary": "row 6",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {
                        "table_id": str(table.id),
                        "row_index": 6,
                        "table_total_rows": 8,
                        "table_column_count": 2,
                    },
                },
                {
                    "is_table_chunk": True,
                    "chunk_id": str(self.chunk.id),
                    "upload_id": str(self.upload.id),
                    "title": "Teller Fees row 7",
                    "summary": "row 7",
                    "search_stage": "table_row_expansion",
                    "source_diagnostics": {
                        "table_id": str(table.id),
                        "row_index": 7,
                        "table_total_rows": 8,
                        "table_column_count": 2,
                    },
                },
            ],
            "completeness": {"total_found": 2},
        }
        search_out = tools._convert_to_agentic_search_response(
            legacy_payload,
            conversation=self.conversation,
            context=context,
        )
        refs = search_out.get("refs") or []
        self.assertEqual(len(refs), 1, refs)
        self.assertEqual(str(refs[0].get("id")), str(table.id))

        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 3000},
                conversation=self.conversation,
                context=context,
            )

        self.assertEqual(result.get("status"), "ok", json.dumps(result, indent=2, default=str))
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1, json.dumps(result, indent=2, default=str))
        item = evidence[0]
        payload = item.get("payload") or {}
        self.assertEqual(payload.get("row_offset"), 5)
        row_services = [str(row[0]) for row in (payload.get("rows") or []) if isinstance(row, list) and row]
        self.assertIn("Cash deposit with same day value date", row_services)
        semantic_links = payload.get("semantic_links") or []
        self.assertTrue(semantic_links, json.dumps(payload, indent=2, default=str))
        self.assertFalse(item.get("next_cursor"))

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_anchor_falls_back_to_row_zero_when_anchor_is_non_informative(self) -> None:
        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=3,
            title="Fallback Table",
            column_schema=["service", "fee"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=3)
        for idx in range(2):
            row = KnowledgeUploadTableRow.objects.create(
                table=table,
                row_index=idx,
                metadata={"row_type": "body"},
            )
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=row,
                column_index=0,
                column_key="service",
                raw_text=f"Service {idx}",
            )
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=row,
                column_index=1,
                column_key="fee",
                raw_text=f"Fee {idx}",
            )

        context = ToolExecutionContext(char_budget_per_turn=100_000)
        context.table_row_anchor_manifests[str(table.id)] = {
            "ref_id": str(table.id),
            "table_id": str(table.id),
            "matched_row_index": 99,
            "estimated_rows": 2,
            "estimated_columns": 2,
        }

        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 1500},
                conversation=self.conversation,
                context=context,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1, json.dumps(result, indent=2, default=str))
        payload = evidence[0].get("payload") or {}
        self.assertEqual(payload.get("row_offset"), 0)
        rows = payload.get("rows") or []
        self.assertTrue(rows, json.dumps(payload, indent=2, default=str))
        self.assertEqual(rows[0][0], "Service 0")

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_anchor_read_includes_trailing_section_note_as_context_row(self) -> None:
        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=4,
            title="Value Date Rules",
            column_schema=["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=4)

        value_row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=0,
            metadata={"row_type": "body"},
        )
        for idx, value in enumerate(
            [
                "Cash deposit with same day value date (T+5 customers)",
                "",
                "(With minimum EGP 100 or Equivalent and with no maximum) 0,5%",
                "(With minimum EGP 100 or Equivalent and with no maximum) 0,5%",
                "(With minimum EGP 100 or Equivalent and with no maximum) 0,5%",
                "(With minimum EGP 100 or Equivalent and with no maximum) 0,5%",
                "(With minimum EGP 100 or Equivalent and with no maximum) 0,5%",
            ]
        ):
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=value_row,
                column_index=idx,
                column_key=table.column_schema[idx],
                raw_text=value,
            )

        section_note = (
            "*In case of cash deposits made after 2:00 pm, Saturdays or public holidays: "
            "An extra working day will be counted to the applied value date according to the account type and currency"
        )
        note_row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=1,
            metadata={"row_type": "body"},
        )
        for idx, key in enumerate(table.column_schema):
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=note_row,
                column_index=idx,
                column_key=key,
                raw_text=section_note,
            )

        context = ToolExecutionContext(char_budget_per_turn=100_000)
        context.table_row_anchor_manifests[str(table.id)] = {
            "ref_id": str(table.id),
            "table_id": str(table.id),
            "matched_row_index": 1,
            "estimated_rows": 2,
            "estimated_columns": 7,
        }

        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 4500},
                conversation=self.conversation,
                context=context,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1, json.dumps(result, indent=2, default=str))
        payload = evidence[0].get("payload") or {}
        self.assertEqual(payload.get("row_offset"), 0)
        rows = payload.get("rows") or []
        self.assertEqual(len(rows), 1, json.dumps(payload, indent=2, default=str))
        self.assertEqual(rows[0][0], "Cash deposit with same day value date (T+5 customers)")

        context_rows = payload.get("context_rows") or []
        self.assertEqual(len(context_rows), 1, json.dumps(payload, indent=2, default=str))
        self.assertEqual(str(context_rows[0].get("text") or "").strip(), section_note)
        self.assertIn("after 2:00 pm", str(context_rows[0].get("text") or "").lower())

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_rows_skip_uniform_separator_rows_and_preserve_context_label(self) -> None:
        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=4,
            title="Section Separator Table",
            column_schema=["service", "tariff", "prime", "plus", "wealth"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=4)

        section_text = "*Cash deposit with same day value date (upon customer request)"
        separator_row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=0,
            metadata={"row_type": "body"},
        )
        for idx, column_key in enumerate(["service", "tariff", "prime", "plus", "wealth"]):
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=separator_row,
                column_index=idx,
                column_key=column_key,
                raw_text=section_text,
            )

        value_row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=1,
            metadata={"row_type": "body"},
        )
        row_values = [
            "Cash deposit with same day value date",
            "",
            "(With minimum EGP 100 or Equivalent 0,2% and with no maximum)",
            "(With minimum EGP 100 or Equivalent 0,2% and with no maximum)",
            "(With minimum EGP 100 or Equivalent 0,2% and with no maximum)",
        ]
        for idx, column_key in enumerate(["service", "tariff", "prime", "plus", "wealth"]):
            KnowledgeUploadTableCell.objects.create(
                table=table,
                row=value_row,
                column_index=idx,
                column_key=column_key,
                raw_text=row_values[idx],
            )

        context = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 5000},
                conversation=self.conversation,
                context=context,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1, json.dumps(result, indent=2, default=str))
        payload = evidence[0].get("payload") or {}
        rows = payload.get("rows") or []
        self.assertEqual(len(rows), 1, json.dumps(payload, indent=2, default=str))
        self.assertEqual(rows[0][0], "Cash deposit with same day value date")

        context_rows = payload.get("context_rows") or []
        self.assertEqual(len(context_rows), 1, json.dumps(payload, indent=2, default=str))
        self.assertEqual(context_rows[0].get("text"), section_text)

        row_metadata = payload.get("row_metadata") or []
        self.assertEqual(len(row_metadata), 1, json.dumps(payload, indent=2, default=str))
        self.assertEqual(row_metadata[0].get("contextual_service_label"), section_text)

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_row_budgeting_uses_serialized_payload_size(self) -> None:
        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=6,
            title="Budget Table",
            column_schema=["service", "fee"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=6)
        row = KnowledgeUploadTableRow.objects.create(
            table=table,
            row_index=0,
            metadata={
                "row_type": "body",
                "table_scope_contract_version": "v2",
                "observed_value_columns": [f"observed_column_{idx:03d}" for idx in range(180)],
                "qualifier_columns": [f"qualifier_column_{idx:03d}" for idx in range(160)],
                "scope_dimension_columns": [f"scope_dimension_{idx:03d}" for idx in range(160)],
                "inferred_scope_columns": ["fee"],
                "scope_reason": "scope_abstain",
                "scope_confidence": 0.51,
                "scope_value": "EGP 40",
            },
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=0,
            column_key="service",
            raw_text="Oversized metadata row",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=1,
            column_key="fee",
            raw_text="EGP 40",
        )

        context = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 700},
                conversation=self.conversation,
                context=context,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1, json.dumps(result, indent=2, default=str))
        item = evidence[0]
        payload = item.get("payload") or {}
        payload_chars = len(json.dumps(payload, ensure_ascii=False, default=str))
        self.assertLessEqual(payload_chars, 700)
        self.assertEqual(payload.get("rows"), [])
        self.assertTrue(item.get("next_cursor"))

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
    )
    def test_table_columns_are_deduped_when_schema_has_duplicates(self) -> None:
        """Duplicate column labels should be deduped for stable structured payloads."""

        table = KnowledgeUploadTable.objects.create(
            upload=self.upload,
            order_index=1,
            title="Duplicate Columns",
            column_schema=["prime", "prime", "plus"],
        )
        KnowledgeUploadPage.objects.create(upload=self.upload, page_number=1)
        row = KnowledgeUploadTableRow.objects.create(table=table, row_index=0, metadata={"row_type": "body"})
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=0,
            column_key="prime",
            raw_text="A",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=1,
            column_key="prime",
            raw_text="B",
        )
        KnowledgeUploadTableCell.objects.create(
            table=table,
            row=row,
            column_index=2,
            column_key="plus",
            raw_text="C",
        )

        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(table.id)}], "max_chars": 2000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        self.assertTrue(result.get("evidence"), json.dumps(result, indent=2, default=str))
        columns = result["evidence"][0]["payload"]["columns"]
        self.assertEqual(columns, ["prime", "prime_2", "plus"])

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
        MCP_AGENTIC_TEXT_CHUNK_GROUP_NEIGHBOR_CHUNKS=1,
        MCP_AGENTIC_TEXT_CHUNK_GROUP_MAX_WINDOW_CHUNKS=10,
    )
    def test_document_anchor_reads_grouped_window_and_marks_chunk_ref_covered(self) -> None:
        grouped_upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.ACTIVE,
            display_name="Remittance Guide",
            ingestion_metadata={"format": "pdf"},
        )
        grouped_chunks: list[KnowledgeUploadChunk] = []
        for idx in range(8):
            grouped_chunks.append(
                KnowledgeUploadChunk.objects.create(
                    upload=grouped_upload,
                    business_profile=self.business,
                    chunk_index=idx,
                    content=(f"chunk-{idx} " * 25).strip(),
                )
            )

        context = ToolExecutionContext(char_budget_per_turn=100_000)
        context.text_chunk_group_manifests[str(grouped_upload.id)] = {
            "upload_id": str(grouped_upload.id),
            "chunk_count": 3,
            "chunk_ids": [str(grouped_chunks[3].id), str(grouped_chunks[4].id), str(grouped_chunks[5].id)],
            "chunk_indices": [3, 4, 5],
            "chunk_range": [3, 5],
            "pages": [2, 3],
            "char_estimate": 2200,
        }

        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": str(grouped_upload.id)}, {"id": str(grouped_chunks[4].id)}], "max_chars": 5000},
                conversation=self.conversation,
                context=context,
            )

        self.assertIn(result["status"], {"ok", "truncated"}, json.dumps(result, indent=2, default=str))
        evidence = result.get("evidence") or []
        self.assertEqual(len(evidence), 1, json.dumps(result, indent=2, default=str))
        item = evidence[0]
        self.assertEqual(item["id"], str(grouped_upload.id))
        self.assertEqual(item["kind"], "document_context")
        text_payload = str(item["payload"].get("text") or "")
        self.assertIn("chunk-3", text_payload)
        self.assertIn("chunk-5", text_payload)
        self.assertNotIn("chunk-0", text_payload)

        coverage_hint = item.get("coverage_hint") or {}
        self.assertEqual(coverage_hint.get("chunk_range"), [3, 5])
        self.assertEqual(coverage_hint.get("window_range"), [2, 6])

        read_entries = result.get("read") or []
        covered_entries = [entry for entry in read_entries if entry.get("status") == "covered"]
        self.assertEqual(len(covered_entries), 1, json.dumps(read_entries, indent=2, default=str))
        self.assertEqual(str(covered_entries[0].get("id")), str(grouped_chunks[4].id))

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
        MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=25000,
        MCP_READ_DOCUMENT_MAX_CHARS_MARGIN=0,
    )
    def test_chunk_window_cursor_resumes_exactly(self) -> None:
        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        chunk_id = str(self.chunk.id)

        with self._enable_agentic_mode():
            first = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": chunk_id}], "max_chars": 1000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertEqual(first["tool"], "read_knowledge")
        self.assertIn(first["status"], {"ok", "truncated"})
        self.assertEqual(len(first["evidence"]), 1)
        first_item = first["evidence"][0]
        self.assertEqual(first_item["id"], chunk_id)
        self.assertEqual(len(first_item["payload"]["text"]), 1000)
        cursor_1 = first_item.get("next_cursor")
        self.assertIsInstance(cursor_1, str)
        self.assertTrue(cursor_1)
        self.assertTrue(str(cursor_1).startswith("c_"))

        signed_cursor_1 = self._resolve_signed_cursor(ctx, str(cursor_1))
        cursor_payload = tools._verify_agentic_read_cursor_v2(signed_cursor_1)  # type: ignore[attr-defined]
        self.assertEqual(cursor_payload.get("kind"), "chunk_window")
        self.assertEqual(int(cursor_payload.get("char_offset") or 0), 1000)

        with self._enable_agentic_mode():
            second = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": chunk_id, "cursor": cursor_1}], "max_chars": 1000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertEqual(len(second["evidence"]), 1)
        second_item = second["evidence"][0]
        self.assertEqual(len(second_item["payload"]["text"]), 1000)

        combined = first_item["payload"]["text"] + second_item["payload"]["text"]
        self.assertEqual(combined, ("A" * 2000))

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
        MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=25000,
        MCP_READ_DOCUMENT_MAX_CHARS_MARGIN=0,
    )
    def test_full_read_entries_without_control_hints_are_omitted_from_read_summary(self) -> None:
        """Balance prompt payloads: hide plain full read receipts, keep evidence unchanged."""

        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        chunk_id = str(self.chunk.id)

        with self._enable_agentic_mode():
            result = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": chunk_id}], "max_chars": 6000},
                conversation=self.conversation,
                context=ctx,
            )

        self.assertEqual(result["tool"], "read_knowledge")
        self.assertIn(result["status"], {"ok", "truncated"})
        self.assertEqual(len(result.get("evidence") or []), 1)
        self.assertNotIn("read", result, json.dumps(result, indent=2, default=str))

    @override_settings(
        MCP_NEW_CONTRACT_ENABLED=True,
        MCP_AGENTIC_READ_V2_ENABLED=True,
        MCP_TEXT_PII_REDACTION_ENABLED=False,
        MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=2100,
        MCP_READ_DOCUMENT_MAX_CHARS_MARGIN=0,
    )
    def test_artifact_cursor_pages_and_resumes_knowledge_cursor(self) -> None:
        ctx = ToolExecutionContext(char_budget_per_turn=100_000)
        chunk_id = str(self.chunk.id)

        with self._enable_agentic_mode():
            first = tools.execute_tool(
                "read_knowledge",
                {"refs": [{"id": chunk_id}], "max_chars": 2000},
                conversation=self.conversation,
                context=ctx,
            )

        # Tool output should stay under the prompt tool-output cap (no orchestrator truncation needed).
        self.assertLessEqual(len(json.dumps(first, ensure_ascii=False)), 2100)

        self.assertTrue(first.get("evidence"), json.dumps(first, indent=2, default=str))
        first_item = first["evidence"][0]
        preview_len = len(first_item["payload"]["text"])
        self.assertGreaterEqual(preview_len, 200)
        self.assertLess(preview_len, 2000)
        self.assertFalse(first_item.get("complete"))
        self.assertTrue(first_item.get("artifact_id"))

        first_trace = first["read"][0]
        self.assertEqual(first_trace["status"], "artifact")
        self.assertEqual(int(first_trace.get("chars") or 0), 2000)
        self.assertTrue(first_trace.get("artifact_id"))

        cursor_1 = first_item["next_cursor"]
        self.assertTrue(str(cursor_1).startswith("c_"))
        cursor_payload_1 = tools._verify_agentic_read_cursor_v2(self._resolve_signed_cursor(ctx, str(cursor_1)))  # type: ignore[attr-defined]
        self.assertEqual(cursor_payload_1.get("kind"), "artifact")
        self.assertEqual(int(cursor_payload_1.get("char_offset") or 0), preview_len)

        collected = first_item["payload"]["text"]
        next_cursor = cursor_1
        chunk_cursor = None
        # Follow artifact pages until the tool hands us back the underlying knowledge cursor.
        for _ in range(10):
            with self._enable_agentic_mode():
                page = tools.execute_tool(
                    "read_knowledge",
                    {"refs": [{"id": chunk_id, "cursor": next_cursor}], "max_chars": 2000},
                    conversation=self.conversation,
                    context=ctx,
                )

            self.assertLessEqual(len(json.dumps(page, ensure_ascii=False)), 2100)
            self.assertTrue(page.get("evidence"), json.dumps(page, indent=2, default=str))
            page_item = page["evidence"][0]
            collected += page_item["payload"]["text"]

            next_cursor = page_item.get("next_cursor")
            self.assertIsInstance(next_cursor, str)
            self.assertTrue(next_cursor)
            cursor_payload = tools._verify_agentic_read_cursor_v2(
                self._resolve_signed_cursor(ctx, str(next_cursor))
            )  # type: ignore[attr-defined]
            if cursor_payload.get("kind") == "chunk_window":
                chunk_cursor = next_cursor
                self.assertEqual(int(cursor_payload.get("char_offset") or 0), 2000)
                break
        self.assertIsNotNone(chunk_cursor)
        self.assertEqual(collected, ("A" * 2000))

        tail = ""
        cursor = chunk_cursor
        for _ in range(10):
            with self._enable_agentic_mode():
                out = tools.execute_tool(
                    "read_knowledge",
                    {"refs": [{"id": chunk_id, "cursor": cursor}], "max_chars": 1000},
                    conversation=self.conversation,
                    context=ctx,
                )
            self.assertLessEqual(len(json.dumps(out, ensure_ascii=False)), 2100)
            self.assertTrue(out.get("evidence"), json.dumps(out, indent=2, default=str))
            item = out["evidence"][0]
            tail += item["payload"]["text"]
            if len(tail) >= 1000:
                break
            cursor = item.get("next_cursor")
            self.assertIsInstance(cursor, str)
            self.assertTrue(cursor)

        self.assertGreaterEqual(len(tail), 1000)
        self.assertEqual(tail[:1000], ("A" * 1000))

    def test_store_local_tool_output_artifact_prunes_by_max_per_conversation(self) -> None:
        artifact_1 = store_local_tool_output_artifact(
            conversation=self.conversation,
            invoked_tool="read_document",
            request={"items": [{"id": str(self.chunk.id)}]},
            response={"text": "one"},
            status="ok",
            is_error=False,
            retention_days=365,
            max_per_conversation=1,
        )
        artifact_2 = store_local_tool_output_artifact(
            conversation=self.conversation,
            invoked_tool="read_document",
            request={"items": [{"id": str(self.chunk.id)}]},
            response={"text": "two"},
            status="ok",
            is_error=False,
            retention_days=365,
            max_per_conversation=1,
        )
        self.assertIsNotNone(artifact_1)
        self.assertIsNotNone(artifact_2)

        qs = McpToolOutputArtifact.objects.filter(conversation=self.conversation, invoked_tool="read_document").order_by("-created_at")
        self.assertEqual(qs.count(), 1)
        self.assertEqual(str(qs.first().id), artifact_2)

    def test_store_local_tool_output_artifact_prunes_by_retention_days(self) -> None:
        artifact_old = store_local_tool_output_artifact(
            conversation=self.conversation,
            invoked_tool="read_document",
            request={"items": [{"id": str(self.chunk.id)}]},
            response={"text": "old"},
            status="ok",
            is_error=False,
            retention_days=365,
            max_per_conversation=10,
        )
        self.assertIsNotNone(artifact_old)
        McpToolOutputArtifact.objects.filter(id=artifact_old).update(created_at=timezone.now() - timedelta(days=45))

        artifact_new = store_local_tool_output_artifact(
            conversation=self.conversation,
            invoked_tool="read_document",
            request={"items": [{"id": str(self.chunk.id)}]},
            response={"text": "new"},
            status="ok",
            is_error=False,
            retention_days=30,
            max_per_conversation=10,
        )
        self.assertIsNotNone(artifact_new)

        qs = McpToolOutputArtifact.objects.filter(conversation=self.conversation, invoked_tool="read_document").order_by("-created_at")
        self.assertEqual(qs.count(), 1)
        self.assertEqual(str(qs.first().id), artifact_new)
