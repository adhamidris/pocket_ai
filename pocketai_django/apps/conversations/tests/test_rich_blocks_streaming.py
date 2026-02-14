from __future__ import annotations

from django.test import SimpleTestCase

from apps.conversations.rich_blocks import RichBlockStreamBuilder


def _paragraph_content(blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "").strip().lower() != "paragraph":
            continue
        payload = block.get("payload")
        if not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if isinstance(content, list):
            return [node for node in content if isinstance(node, dict)]
    return []


def _inline_text(nodes: list[dict[str, object]]) -> str:
    out: list[str] = []
    for node in nodes:
        text = node.get("text")
        if isinstance(text, str):
            out.append(text)
    return "".join(out)


class RichBlockStreamingTests(SimpleTestCase):
    def test_streaming_preserves_bold_markdown_across_chunks(self) -> None:
        builder = RichBlockStreamBuilder()
        builder.feed_text("Based on the fee schedule, the fee for **Traveler")
        builder.feed_text(" cheques (sell) in foreign currency** is:")
        builder.feed_text("\n")
        builder.finalize()

        content = _paragraph_content(builder.snapshot())
        full_text = _inline_text(content)
        self.assertIn("Traveler cheques (sell) in foreign currency", full_text)
        self.assertNotIn("**", full_text)

        bold_text = _inline_text([node for node in content if node.get("marks") == ["bold"]])
        self.assertIn("Traveler cheques (sell) in foreign currency", bold_text)

    def test_finalize_lenient_parsing_for_unclosed_bold_marker(self) -> None:
        builder = RichBlockStreamBuilder()
        builder.feed_text("The fee is **1% with minimum USD 2")
        builder.finalize()

        content = _paragraph_content(builder.snapshot())
        full_text = _inline_text(content)
        self.assertIn("1% with minimum USD 2", full_text)
        self.assertNotIn("**", full_text)

        bold_text = _inline_text([node for node in content if node.get("marks") == ["bold"]])
        self.assertIn("1% with minimum USD 2", bold_text)
