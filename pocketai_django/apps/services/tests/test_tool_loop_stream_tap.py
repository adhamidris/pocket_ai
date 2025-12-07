from django.test import SimpleTestCase

from apps.services.llm_provider import StreamEvent
from apps.services.mcp.orchestrator import ToolLoopStreamTap


class ToolLoopStreamTapTests(SimpleTestCase):
    def test_abort_stops_future_deltas(self) -> None:
        emitted: list[str] = []
        aborted: list[str] = []

        tap = ToolLoopStreamTap(
            enabled=True,
            on_emit=emitted.append,
            on_abort=lambda: aborted.append("aborted"),
            conversation_id="conv-test",
            business_id="biz-test",
        )

        tap.emit_text("hello")
        tap.handle_event(StreamEvent(event_type="tool_call", tool_call={"function": {"name": "search_knowledge"}}))
        tap.emit_text("world")

        self.assertEqual(emitted, ["hello"])
        self.assertEqual(aborted, ["aborted"])
        self.assertTrue(tap.tool_detected)
