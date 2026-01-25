from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.mcp import tools
from apps.mcp.types import ToolExecutionContext


class AgenticReadDocumentContractTests(SimpleTestCase):
    def test_read_document_agentic_wrapper_returns_contents_without_snippets(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)

        chunk_id = "00000000-0000-0000-0000-000000000001"

        fake_snippet = {
            "id": chunk_id,
            "chunk_id": chunk_id,
            "title": "Fees",
            "public_label": "Fees",
            "content": "Annual fee is 100 EGP.",
            "is_table_chunk": False,
        }

        with mock.patch.object(
            tools,
            "_read_document_handler",
            return_value={"tool": "read_document", "status": "ok", "snippets": [fake_snippet]},
        ):
            result = tools._read_document_agentic_wrapper(
                {"ids": [chunk_id], "max_chars": 2000},
                conversation,
                context,
            )

        self.assertEqual(result["tool"], "read_document")
        self.assertIn(result["status"], {"ok", "partial"})
        self.assertIn("contents", result)
        self.assertNotIn("snippets", result)
        self.assertTrue(result["contents"])

    def test_read_document_agentic_wrapper_rejects_max_chars_over_budget(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)
        chunk_id = "00000000-0000-0000-0000-000000000001"

        fake_service = SimpleNamespace(inline_char_limit_for_business=lambda business_profile: 12_000)

        with (
            mock.patch.object(
                tools.FeatureFlagService,
                "snapshot",
                return_value=SimpleNamespace(rag_agentic_mode=True),
            ),
            mock.patch.object(tools, "_knowledge_service", return_value=fake_service),
            mock.patch.object(tools, "_read_document_handler") as handler_mock,
        ):
            result = tools._read_document_agentic_wrapper(
                {"ids": [chunk_id], "max_chars": 50_000},
                conversation,
                context,
            )

        self.assertEqual(result["tool"], "read_document")
        self.assertEqual(result["status"], "constraint_error")
        self.assertEqual(result["error_code"], "max_chars_exceeded")
        self.assertEqual(result.get("max_chars_allowed"), 10_000)
        self.assertIn("max_chars", str(result.get("hint") or ""))
        handler_mock.assert_not_called()

    def test_read_document_defers_items_instead_of_truncating(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)

        # Use UUID-shaped ids (what search_knowledge returns in production).
        doc_big = "00000000-0000-0000-0000-0000000000aa"
        doc_mid = "00000000-0000-0000-0000-0000000000bb"
        doc_small = "00000000-0000-0000-0000-0000000000cc"

        fake_service = SimpleNamespace(inline_char_limit_for_business=lambda business_profile: 50_000)

        def _fake_estimator(doc_id, *args, **kwargs):
            if doc_id == doc_big:
                return 11279, None
            if doc_id == doc_mid:
                return 3200, None
            if doc_id == doc_small:
                return 2100, None
            return None, "not_found"

        def _fake_read(args, *_):
            doc_id = str(args.get("document_id") or "")
            if doc_id == doc_mid:
                content = "x" * 3200
            elif doc_id == doc_small:
                content = "y" * 2100
            else:
                content = "z" * 11279
            return {
                "tool": "read_document",
                "status": "ok",
                "snippets": [
                    {
                        "id": doc_id,
                        "chunk_id": doc_id,
                        "title": "Doc",
                        "public_label": "Doc",
                        "content": content,
                        "is_table_chunk": False,
                        "truncated": False,
                    }
                ],
            }

        with (
            mock.patch.object(tools, "_knowledge_service", return_value=fake_service),
            mock.patch.object(tools, "_estimate_agentic_read_chars_for_id", side_effect=_fake_estimator),
            mock.patch.object(tools, "_read_document_handler", side_effect=_fake_read) as handler_mock,
        ):
            result = tools._read_document_agentic_wrapper(
                {"ids": [doc_big, doc_mid, doc_small], "max_chars": 8000},
                conversation,
                context,
            )

        # Big doc doesn't fit; small+mid do.
        self.assertEqual(result["tool"], "read_document")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result.get("contents") or []), 2)
        self.assertEqual(len(result.get("deferred") or []), 1)
        self.assertEqual(result["deferred"][0]["id"], doc_big)
        self.assertNotIn("truncated_ids", result)

        # No per-item truncation: returned contents should be full strings.
        contents_by_id = {item["id"]: item for item in result["contents"]}
        self.assertEqual(len(contents_by_id[doc_mid]["content"]), 3200)
        self.assertEqual(len(contents_by_id[doc_small]["content"]), 2100)

        # Under the hood we should only read the items that fit.
        self.assertEqual(handler_mock.call_count, 2)

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_read_document_agentic_v2_rejects_legacy_ids(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)
        chunk_id = "00000000-0000-0000-0000-000000000001"

        with (
            mock.patch.object(
                tools.FeatureFlagService,
                "snapshot",
                return_value=SimpleNamespace(rag_agentic_mode=True),
            ),
            mock.patch.object(tools, "_agentic_read_v2_handler") as v2_mock,
            mock.patch.object(tools, "_agentic_batch_read_handler") as v1_mock,
        ):
            result = tools._read_document_agentic_wrapper(
                {"ids": [chunk_id], "max_chars": 2000},
                conversation,
                context,
            )

        self.assertEqual(result["tool"], "read_document")
        self.assertEqual(result["status"], "constraint_error")
        self.assertEqual(result["error_code"], "legacy_parameters_not_supported")
        self.assertIn("unsupported_fields", result)
        v2_mock.assert_not_called()
        v1_mock.assert_not_called()

    @override_settings(MCP_NEW_CONTRACT_ENABLED=True, MCP_AGENTIC_READ_V2_ENABLED=True)
    def test_read_document_agentic_v2_routes_items(self) -> None:
        conversation = SimpleNamespace(
            id="conv-1",
            business_profile_id="biz-1",
            business_profile=SimpleNamespace(metadata={}),
        )
        context = ToolExecutionContext(char_budget_per_turn=10_000)
        chunk_id = "00000000-0000-0000-0000-000000000001"

        fake_result = {"tool": "read_document", "status": "ok", "contents": [{"id": chunk_id, "content": "ok"}]}

        with (
            mock.patch.object(
                tools.FeatureFlagService,
                "snapshot",
                return_value=SimpleNamespace(rag_agentic_mode=True),
            ),
            mock.patch.object(tools, "_agentic_read_v2_handler", return_value=fake_result) as v2_mock,
        ):
            result = tools._read_document_agentic_wrapper(
                {"items": [{"id": chunk_id}], "max_chars": 2000},
                conversation,
                context,
            )

        self.assertEqual(result["tool"], "read_document")
        self.assertEqual(result["status"], "ok")
        v2_mock.assert_called_once()
