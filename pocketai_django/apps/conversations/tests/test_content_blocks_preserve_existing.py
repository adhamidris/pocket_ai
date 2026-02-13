from __future__ import annotations

from django.test import SimpleTestCase

from apps.conversations.content_blocks import ensure_assistant_text_blocks


class EnsureAssistantTextBlocksPreserveExistingTests(SimpleTestCase):
    def test_preserves_existing_rich_blocks_even_if_body_is_plain(self) -> None:
        existing_blocks = [
            {
                "block_id": "blk_test_1",
                "type": "paragraph",
                "created_at": "2026-02-05T00:00:00Z",
                "payload": {"content": [{"text": "Bold title", "marks": ["bold"]}]},
            }
        ]

        # The body is a plain-text fallback and does not include markdown markers.
        out = ensure_assistant_text_blocks("Bold title", existing_blocks=existing_blocks)

        self.assertEqual(out, existing_blocks)
        self.assertEqual(out[0]["payload"]["content"][0].get("marks"), ["bold"])

    def test_force_regenerate_rebuilds_text_and_keeps_non_text_blocks(self) -> None:
        existing_blocks = [
            {
                "block_id": "blk_tool",
                "type": "tool_use",
                "created_at": "2026-02-05T00:00:00Z",
                "payload": {"tool_name": "search_knowledge"},
            },
            {
                "block_id": "blk_text",
                "type": "paragraph",
                "created_at": "2026-02-05T00:00:00Z",
                "payload": {"content": [{"text": "Old streamed text"}]},
            },
        ]

        out = ensure_assistant_text_blocks(
            "Final canonical text",
            existing_blocks=existing_blocks,
            force_regenerate_text=True,
        )

        self.assertEqual(out[0]["type"], "tool_use")
        self.assertEqual(out[0]["payload"]["tool_name"], "search_knowledge")
        self.assertTrue(any(block.get("type") == "paragraph" for block in out))
        paragraph = next(block for block in out if block.get("type") == "paragraph")
        content = paragraph.get("payload", {}).get("content", [])
        text = "".join(node.get("text", "") for node in content if isinstance(node, dict))
        self.assertIn("Final canonical text", text)
