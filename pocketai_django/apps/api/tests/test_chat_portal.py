from __future__ import annotations

from types import SimpleNamespace

from django.test import TestCase

from apps.api import chat_portal


class ChatPortalDebugSerializationTests(TestCase):
    def test_serialize_tool_trace_entry_includes_exact_llm_io(self) -> None:
        entry = {
            "tool": "search_knowledge",
            "status": "ok",
            "arguments": {"query": "fees", "limit": 5},
            "llm_request": {
                "tool": "search_knowledge",
                "arguments": {"query": "fees", "limit": 5, "queries": ["fees egp", "fees usd"]},
            },
            "llm_response": {
                "tool_call_id": "call_1",
                "content": '{"tool":"search_knowledge","status":"ok","refs":[{"id":"abc"}]}',
            },
        }

        payload = chat_portal._serialize_tool_trace_entry(entry)
        self.assertEqual(payload.get("tool"), "search_knowledge")
        self.assertIn("llm_request", payload)
        self.assertIn("llm_response", payload)
        self.assertEqual(payload["llm_request"]["arguments"]["query"], "fees")
        self.assertEqual(payload["llm_request"]["arguments"]["queries"], ["fees egp", "fees usd"])
        self.assertEqual(payload["llm_response"]["content"], entry["llm_response"]["content"])
        self.assertEqual(payload["llm_response"]["content_json"]["status"], "ok")
        self.assertEqual(payload["llm_response"]["content_json"]["refs"][0]["id"], "abc")

    def test_serialize_debug_tools_payload_keeps_llm_io_in_tool_trace(self) -> None:
        stream_context = SimpleNamespace(
            llm_usage=None,
            tool_trace=(),
            knowledge_payload=(),
            knowledge_reads=(),
            tool_context=SimpleNamespace(
                tool_trace=[
                    {
                        "tool": "read_knowledge",
                        "status": "truncated",
                        "arguments": {"refs": [{"id": "u1"}], "max_chars": 2000},
                        "llm_request": {"tool": "read_knowledge", "arguments": {"refs": [{"id": "u1"}]}},
                        "llm_response": {
                            "tool_call_id": "call_9",
                            "content": '{"tool":"read_knowledge","status":"truncated","evidence":[{"id":"u1"}]}',
                        },
                    }
                ],
                knowledge_results=[],
                knowledge_reads=[],
                search_history=[],
                coverage_ledger=[],
                prompt_budget_entries=[],
            ),
        )

        payload = chat_portal._serialize_debug_tools_payload(stream_context)
        self.assertIsInstance(payload, dict)
        self.assertIn("tool_trace", payload)
        self.assertEqual(len(payload["tool_trace"]), 1)
        trace = payload["tool_trace"][0]
        self.assertEqual(trace["tool"], "read_knowledge")
        self.assertEqual(trace["llm_request"]["tool"], "read_knowledge")
        self.assertEqual(trace["llm_response"]["tool_call_id"], "call_9")
        self.assertEqual(trace["llm_response"]["content_json"]["status"], "truncated")
