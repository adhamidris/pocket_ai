from __future__ import annotations

import io
import json

from django.test import SimpleTestCase

from apps.llm.llm_provider import _consume_chat_completion_stream


class DeepSeekReasoningContentStreamingTests(SimpleTestCase):
    def test_stream_assembles_reasoning_content_for_tool_calls(self) -> None:
        events = [
            {
                "model": "deepseek-reasoner",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "reasoning_content": "Need to call a tool."},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "model": "deepseek-reasoner",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "search_knowledge", "arguments": "{}"},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "model": "deepseek-reasoner",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
            },
        ]
        sse_bytes = b"".join([b"data: " + json.dumps(event).encode("utf-8") + b"\n\n" for event in events])
        sse_bytes += b"data: [DONE]\n\n"

        payload = _consume_chat_completion_stream(io.BytesIO(sse_bytes), on_stream_delta=None)
        message = (payload.get("choices") or [{}])[0].get("message") or {}

        self.assertEqual(message.get("role"), "assistant")
        self.assertIn("tool_calls", message)
        self.assertEqual((message.get("tool_calls") or [{}])[0].get("function", {}).get("name"), "search_knowledge")
        self.assertEqual(message.get("reasoning_content"), "Need to call a tool.")

