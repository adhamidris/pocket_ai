import json
from typing import Iterable, List

from django.test import SimpleTestCase

from apps.services.llm_provider import StreamEvent, _consume_chat_completion_stream


class _FakeSseStream:
    """
    Minimal file-like object to simulate SSE responses for unit tests.
    """

    def __init__(self, payloads: Iterable[dict]) -> None:
        frames: List[bytes] = []
        for payload in payloads:
            frames.append(f"data: {json.dumps(payload)}\n".encode("utf-8"))
            frames.append(b"\n")
        frames.append(b"data: [DONE]\n")
        frames.append(b"\n")
        self._iterator = iter(frames)

    def readline(self) -> bytes:
        try:
            return next(self._iterator)
        except StopIteration:
            return b""


class StreamEventTests(SimpleTestCase):
    def test_text_events_are_emitted(self) -> None:
        payloads = [
            {
                "choices": [
                    {
                        "delta": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Hello world",
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "stop",
                        "message": {"content": "Hello world"},
                    }
                ]
            },
        ]
        stream = _FakeSseStream(payloads)
        events: list[StreamEvent] = []

        _consume_chat_completion_stream(stream, None, events.append)
        text_events = [evt for evt in events if evt.event_type == "text"]
        self.assertEqual(len(text_events), 1)
        self.assertEqual(text_events[0].text, "Hello world")
        finish_events = [evt for evt in events if evt.event_type == "finish"]
        self.assertTrue(finish_events)
        self.assertEqual(finish_events[0].finish_reason, "stop")

    def test_tool_call_events_are_emitted(self) -> None:
        payloads = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_123",
                                    "function": {"name": "search_knowledge", "arguments": '{"query": "fee"}'},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        ]
        stream = _FakeSseStream(payloads)
        events: list[StreamEvent] = []

        _consume_chat_completion_stream(stream, None, events.append)

        tool_events = [evt for evt in events if evt.event_type == "tool_call"]
        self.assertEqual(len(tool_events), 1)
        tool_event = tool_events[0]
        self.assertIsNotNone(tool_event.tool_call)
        self.assertEqual(tool_event.tool_call.get("function", {}).get("name"), "search_knowledge")
        self.assertEqual(tool_event.index, 0)
