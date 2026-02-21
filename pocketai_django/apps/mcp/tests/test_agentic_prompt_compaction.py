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

    def test_search_knowledge_compaction_preserves_scope_clarification_mcq_payload(self) -> None:
        payload = {
            "tool": "search_knowledge",
            "status": "needs_clarification",
            "hint": "I found multiple fee categories. Pick one or all.",
            "diagnostics": {
                "reason": "broad_scope_ambiguity",
                "clarification_ui_mode": "mcq",
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
                "scope_clarification_options": [
                    {"id": "all_fees", "label": "All fees", "query": "all", "kind": "action"},
                    {
                        "id": "choose_categories",
                        "label": "Choose categories",
                        "query": "what categories do you have?",
                        "kind": "action",
                        "expands": "categories",
                    },
                ],
            },
            "clarification": {
                "mode": "mcq",
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
                "chips": [
                    {"id": "all_fees", "label": "All fees", "query": "all"},
                    {"id": "choose_categories", "label": "Choose categories", "query": "what categories do you have?"},
                ],
                "all_query": "all",
                "choose_categories_query": "what categories do you have?",
            },
            "snippets": [],
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

        self.assertEqual(compact.get("status"), "needs_clarification")
        self.assertEqual(compact.get("clarification_ui_mode"), "mcq")
        self.assertIn("diagnostics", compact)
        self.assertEqual((compact.get("diagnostics") or {}).get("clarification_ui_mode"), "mcq")
        self.assertIn("scope_clarification_options", compact.get("diagnostics") or {})
        self.assertIn("clarification", compact)
        clarification = compact.get("clarification") or {}
        self.assertEqual(clarification.get("mode"), "mcq")
        self.assertTrue(clarification.get("chips"))
        self.assertEqual(clarification.get("all_query"), "all")
        self.assertEqual(clarification.get("choose_categories_query"), "what categories do you have?")
        self.assertTrue(compact.get("prompt_compact"))

    def test_present_scope_clarification_compaction_preserves_mcq_payload(self) -> None:
        payload = {
            "tool": "present_scope_clarification",
            "status": "ok",
            "query": "plus customer fees",
            "hint": "Please choose one category or all fees.",
            "diagnostics": {
                "reason": "broad_scope_ambiguity",
                "clarification_ui_mode": "mcq",
                "categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                    "administrative fees",
                ],
                "top_categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                ],
                "scope_clarification_options": [
                    {"id": "all_fees", "label": "All fees", "query": "all", "kind": "action"},
                    {"id": "choose_categories", "label": "Choose categories", "query": "what categories do you have?", "kind": "action"},
                ],
            },
            "clarification": {
                "mode": "mcq",
                "categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                    "administrative fees",
                ],
                "top_categories": [
                    "online banking fees",
                    "outgoing transfer fees",
                    "statement fees",
                ],
                "chips": [
                    {"id": "all_fees", "label": "All fees", "query": "all"},
                    {"id": "choose_categories", "label": "Choose categories", "query": "what categories do you have?"},
                ],
                "all_query": "all",
                "choose_categories_query": "what categories do you have?",
            },
        }

        compact = self.service._compact_tool_payload_for_prompt(
            "present_scope_clarification",
            payload,
            max_snippets=4,
            snippet_content_chars=600,
            max_rows=12,
            max_contributions=25,
            max_cells=12,
            max_cells_exact=60,
        )

        self.assertEqual(compact.get("status"), "ok")
        self.assertEqual(compact.get("clarification_ui_mode"), "mcq")
        self.assertEqual(compact.get("query"), "plus customer fees")
        self.assertIn("diagnostics", compact)
        self.assertIn("clarification", compact)
        clarification = compact.get("clarification") or {}
        self.assertEqual(clarification.get("mode"), "mcq")
        self.assertTrue(clarification.get("chips"))
        self.assertTrue(compact.get("prompt_compact"))

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
