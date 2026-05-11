from __future__ import annotations

import json
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.mcp.orchestrator import McpOrchestratorService


class AgenticPromptCompactionTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        agent = mock.Mock()
        agent.business_profile = mock.Mock(metadata={})
        self.service = McpOrchestratorService(agent=agent, provider=None)

    def test_search_knowledge_compacts_agentic_results(self) -> None:
        payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "refs": [
                {
                    "id": "chunk-1",
                    "document_id": "upload-1",
                    "label": "Annual fee — Fees",
                    "kind": "text_anchor",
                    "type": "text",  # legacy enum retained for routing
                    "source": "Guide.pdf",
                    "score": 0.91,
                    "char_estimate": 1200,
                    "read_hint": {"mode": "full_page", "page": 1, "suggested_max_chars": 15000},
                }
            ],
            "total_found": 1,
            "has_more": True,
            "next_cursor": "cursor-123",
            "read_budget_hint": {"total_suggested_max_chars": 15000, "max_chars_allowed": 25000},
            "completeness": {"shown": 1, "total_found": 50, "has_more": True},
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "search_knowledge",
            payload,
            max_snippets=4,
            snippet_content_chars=600,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )

        self.assertIn("refs", compact)
        self.assertNotIn("snippets", compact)
        self.assertEqual(compact["refs"][0]["id"], "chunk-1")
        self.assertNotIn("document_id", compact["refs"][0])
        self.assertNotIn("score", compact["refs"][0])
        self.assertNotIn("label", compact["refs"][0])
        self.assertNotIn("type", compact["refs"][0])
        self.assertNotIn("char_estimate", compact["refs"][0])
        self.assertEqual(compact["refs"][0]["document"], "Annual fee — Fees")
        self.assertEqual(compact["refs"][0]["read_chars"], 15000)
        self.assertNotIn("total_found", compact)
        self.assertNotIn("has_more", compact)
        self.assertNotIn("next_cursor", compact)
        self.assertEqual(compact.get("read_budget", {}).get("suggested_chars"), 15000)
        self.assertNotIn("completeness", compact)
        self.assertEqual(compact.get("pagination", {}).get("shown"), 1)
        self.assertEqual(compact.get("pagination", {}).get("total"), 50)
        self.assertEqual(compact.get("pagination", {}).get("next_cursor"), "cursor-123")
        self.assertNotIn("prompt_compact", compact)

    def test_search_knowledge_trace_summary_uses_requested_query(self) -> None:
        summary = self.service._tool_trace_output_summary(
            "search_knowledge",
            {
                "tool": "search_knowledge",
                "status": "ok",
                "query": "cheques",
                "refs": [],
            },
        )

        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertEqual(summary.get("requested_query"), "cheques")
        self.assertEqual(summary.get("effective_query"), "cheques")
        self.assertNotIn("scope_resolution", summary)

    def test_search_knowledge_keeps_retrieval_observability_out_of_prompt(self) -> None:
        observability = {
            "query_scope": {
                "followup_decision": "new_topic",
                "topic_scope": "new_topic",
                "strategy": "topic_shift",
                "reason": "topic_shift",
                "document_continuity_allowed": False,
            },
            "continuity": {
                "allowed": False,
                "reason": "topic_shift",
                "rag_received_context": True,
                "boosted_candidates": 0,
            },
            "table_coverage": {
                "tables_considered": 6,
                "tables_returned": 3,
                "coverage_diversification_applied": True,
            },
            "evidence_completeness": {
                "status": "complete",
                "shown": 8,
                "refs_total_found": 8,
                "has_more": False,
            },
        }
        payload = {
            "tool": "search_knowledge",
            "status": "ok",
            "refs": [{"id": "row-1", "label": "Account Opening Fees"}],
            "retrieval_observability": observability,
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "search_knowledge",
            payload,
            max_snippets=4,
            snippet_content_chars=600,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )
        summary = self.service._tool_trace_output_summary("search_knowledge", payload)

        self.assertNotIn("retrieval_observability", compact)
        assert summary is not None
        self.assertEqual(
            summary["retrieval_observability"]["table_coverage"]["tables_returned"],
            3,
        )

    def test_read_knowledge_compacts_agentic_evidence(self) -> None:
        payload = {
            "tool": "read_knowledge",
            "status": "ok",
            "evidence": [
                {
                    "id": "chunk-1",
                    "title": "Fees",
                    "type": "text",
                    "kind": "text_excerpt",
                    "payload": {"type": "text", "text": "\n\n" + ("x" * 5000)},
                    "truncated": False,
                    "next_cursor": "cursor-1",
                }
            ],
            "read": [
                {
                    "id": "chunk-1",
                    "status": "truncated",
                    "chars": 5002,
                    "artifact_id": "00000000-0000-0000-0000-000000000001",
                }
            ],
            "deferred": [
                {
                    "id": "chunk-2",
                    "chars": 11279,
                    "reason": "exceeds_budget",
                    "suggested_max_chars": 12000,
                    "hint": "Read separately with max_chars=12000.",
                }
            ],
            "total_chars": 5000,
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "read_knowledge",
            payload,
            max_snippets=2,
            snippet_content_chars=400,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )

        self.assertIn("evidence", compact)
        self.assertNotIn("snippets", compact)
        self.assertEqual(compact["evidence"][0]["id"], "chunk-1")
        self.assertNotIn("payload", compact["evidence"][0])
        self.assertEqual(compact["evidence"][0]["document"], "Fees")
        self.assertTrue(compact["evidence"][0]["text"].startswith("\n\n"))
        self.assertEqual(len(compact["evidence"][0]["text"]), 5002)
        self.assertEqual(compact["evidence"][0]["next_cursor"], "cursor-1")
        self.assertNotIn("total_chars", compact)
        self.assertNotIn("max_chars", compact)
        self.assertIn("deferred", compact)
        self.assertEqual(compact["deferred"][0]["id"], "chunk-2")
        self.assertEqual(compact["deferred"][0]["suggested_max_chars"], 12000)
        self.assertEqual(compact["read"][0]["artifact_id"], "00000000-0000-0000-0000-000000000001")

    def test_read_knowledge_hides_parent_paging_for_exact_table_row_prompt_payload(self) -> None:
        payload = {
            "tool": "read_knowledge",
            "status": "ok",
            "budget": {
                "searches_used": 1,
                "searches_remaining": 1,
                "reads_used": 1,
                "reads_remaining": 9,
                "chars_used": 3676,
                "chars_budget": 200000,
            },
            "evidence": [
                {
                    "id": "row-1",
                    "title": "Fees and Charges Credit Cards Eng_185 - Table 1",
                    "type": "table",
                    "kind": "table_rows",
                    "chars": 348,
                    "complete": True,
                    "truncated": False,
                    "more_rows_available": True,
                    "payload": {
                        "type": "table",
                        "table_id": "table-1",
                        "columns": ["card_type", "white", "classic"],
                        "rows": [["Issuance and Renewal Fees", "EGP 500", "EGP 250"]],
                        "row_offset": 0,
                        "rows_shown": 1,
                        "total_rows": 18,
                        "selection_mode": "row_ref",
                        "next_row_start": 1,
                    },
                }
            ],
            "total_chars": 348,
            "max_chars": 8000,
            "max_chars_allowed": 20000,
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "read_knowledge",
            payload,
            max_snippets=2,
            snippet_content_chars=400,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )

        entry = compact["evidence"][0]
        self.assertEqual(entry["id"], "row-1")
        self.assertEqual(entry["document"], "Fees and Charges Credit Cards Eng_185 - Table 1")
        self.assertEqual(entry["kind"], "table_rows")
        self.assertEqual(entry["columns"], ["card_type", "white", "classic"])
        self.assertEqual(entry["rows"], [["Issuance and Renewal Fees", "EGP 500", "EGP 250"]])
        self.assertNotIn("payload", entry)
        self.assertNotIn("table_id", entry)
        self.assertNotIn("selection_mode", entry)
        self.assertNotIn("row_offset", entry)
        self.assertNotIn("rows_shown", entry)
        self.assertNotIn("total_rows", entry)
        self.assertNotIn("next_row_start", entry)
        self.assertNotIn("more_rows_available", entry)
        self.assertNotIn("budget", compact)

    def test_read_knowledge_keeps_table_range_paging_for_prompt_payload(self) -> None:
        payload = {
            "tool": "read_knowledge",
            "status": "ok",
            "evidence": [
                {
                    "id": "table-1",
                    "title": "Fees Table",
                    "type": "table",
                    "kind": "table_rows",
                    "complete": True,
                    "truncated": False,
                    "more_rows_available": True,
                    "payload": {
                        "type": "table",
                        "table_id": "table-1",
                        "columns": ["service", "fee"],
                        "rows": [["A", "EGP 10"], ["B", "EGP 20"]],
                        "row_offset": 0,
                        "rows_shown": 2,
                        "total_rows": 10,
                        "selection_mode": "row_range",
                        "next_row_start": 2,
                    },
                }
            ],
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "read_knowledge",
            payload,
            max_snippets=2,
            snippet_content_chars=400,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )

        entry = compact["evidence"][0]
        self.assertEqual(entry["columns"], ["service", "fee"])
        self.assertEqual(entry["rows"], [["A", "EGP 10"], ["B", "EGP 20"]])
        self.assertEqual(entry["row_offset"], 0)
        self.assertEqual(entry["rows_shown"], 2)
        self.assertEqual(entry["total_rows"], 10)
        self.assertEqual(entry["next_row_start"], 2)
        self.assertTrue(entry["more_rows_available"])
        self.assertNotIn("payload", entry)

    @override_settings(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS=500)
    def test_tool_message_truncation_keeps_content_preview(self) -> None:
        payload = {
            "tool": "read_knowledge",
            "status": "ok",
            "evidence": [
                {
                    "id": "chunk-1",
                    "title": "Fees",
                    "type": "text",
                    "kind": "text_excerpt",
                    "payload": {"type": "text", "text": "x" * 2000},
                    "truncated": False,
                }
            ],
            "total_chars": 2000,
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "read_knowledge",
            payload,
            max_snippets=2,
            snippet_content_chars=400,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )
        message = json.dumps(compact, ensure_ascii=False)
        truncated = self.service._truncate_tool_message_for_prompt("read_knowledge", message)

        self.assertLessEqual(len(truncated), 500)
        parsed = json.loads(truncated)
        self.assertEqual(parsed.get("tool"), "read_knowledge")
        self.assertTrue(parsed.get("truncated"))
        self.assertTrue(parsed.get("prompt_compact"))
        self.assertIn("evidence", parsed)
        self.assertIn("text", parsed["evidence"][0])
        self.assertLess(len(parsed["evidence"][0]["text"]), 2000)
