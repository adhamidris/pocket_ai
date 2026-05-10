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
        self.assertIn("label", compact["refs"][0])
        self.assertEqual(compact["refs"][0]["read_hint"]["suggested_max_chars"], 15000)
        self.assertEqual(compact.get("total_found"), 1)
        self.assertTrue(compact.get("has_more"))
        self.assertEqual(compact.get("next_cursor"), "cursor-123")
        self.assertEqual(compact.get("read_budget_hint", {}).get("total_suggested_max_chars"), 15000)
        self.assertEqual(compact.get("completeness", {}).get("total_found"), 50)

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

    def test_search_knowledge_preserves_retrieval_observability(self) -> None:
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

        self.assertEqual(
            compact["retrieval_observability"]["query_scope"]["topic_scope"],
            "new_topic",
        )
        self.assertNotIn("enumeration", compact["retrieval_observability"])
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
        self.assertTrue(compact["evidence"][0]["payload"]["text"].startswith("\n\n"))
        self.assertEqual(len(compact["evidence"][0]["payload"]["text"]), 5002)
        self.assertEqual(compact["evidence"][0]["next_cursor"], "cursor-1")
        self.assertIn("deferred", compact)
        self.assertEqual(compact["deferred"][0]["id"], "chunk-2")
        self.assertEqual(compact["deferred"][0]["suggested_max_chars"], 12000)
        self.assertEqual(compact["read"][0]["artifact_id"], "00000000-0000-0000-0000-000000000001")

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

        message = json.dumps(payload, ensure_ascii=False)
        truncated = self.service._truncate_tool_message_for_prompt("read_knowledge", message)

        self.assertLessEqual(len(truncated), 500)
        parsed = json.loads(truncated)
        self.assertEqual(parsed.get("tool"), "read_knowledge")
        self.assertTrue(parsed.get("truncated"))
        self.assertTrue(parsed.get("prompt_compact"))
        self.assertIn("evidence", parsed)
        self.assertIn("payload", parsed["evidence"][0])
        self.assertIn("text", parsed["evidence"][0]["payload"])
        self.assertLess(len(parsed["evidence"][0]["payload"]["text"]), 2000)
