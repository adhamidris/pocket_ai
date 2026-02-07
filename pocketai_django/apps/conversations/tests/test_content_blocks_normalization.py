from __future__ import annotations

from django.test import SimpleTestCase

from apps.conversations.content_blocks import (
    ensure_assistant_text_blocks,
    normalize_assistant_content_blocks,
)


def _inline_text(block: dict) -> str:
    payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}
    nodes = payload.get("content")
    if not isinstance(nodes, list):
        return ""
    out: list[str] = []
    for node in nodes:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                out.append(text)
    return "".join(out)


class ContentBlockNormalizationTests(SimpleTestCase):
    def test_normalize_assistant_content_blocks_splits_embedded_lists(self) -> None:
        blocks = [
            {
                "block_id": "blk_p",
                "type": "paragraph",
                "created_at": "2026-02-07T00:00:00Z",
                "payload": {"content": [{"text": "Based on evidence."}]},
            },
            {
                "block_id": "blk_tool",
                "type": "tool_use",
                "created_at": "2026-02-07T00:00:01Z",
                "payload": {"event_id": "evt_1", "tool_name": "read_knowledge"},
            },
            {
                "block_id": "blk_h",
                "type": "heading",
                "created_at": "2026-02-07T00:00:02Z",
                "payload": {
                    "level": 2,
                    "content": [
                        {
                            "text": "Fees Stated in EGP 1. Account Opening Fees: EGP 100 2. Outgoing Transfers: 0.2%"
                        }
                    ],
                },
            },
        ]

        normalized = normalize_assistant_content_blocks(blocks)
        self.assertTrue(normalized)
        self.assertEqual(normalized[1]["type"], "tool_use")
        heading_blocks = [block for block in normalized if block.get("type") == "heading"]
        self.assertTrue(heading_blocks)
        heading_text = _inline_text(heading_blocks[0])
        self.assertNotIn("1.", heading_text)
        list_items = [block for block in normalized if block.get("type") == "list_item"]
        self.assertGreaterEqual(len(list_items), 2)

    def test_ensure_assistant_text_blocks_repairs_malformed_rich_blocks_without_body(self) -> None:
        existing_blocks = [
            {
                "block_id": "blk_h",
                "type": "heading",
                "created_at": "2026-02-07T00:00:00Z",
                "payload": {
                    "level": 2,
                    "content": [
                        {"text": "Fees Stated in USD 1. Transfer Fees 2. Account Opening Fees"}
                    ],
                },
            }
        ]

        out = ensure_assistant_text_blocks("", existing_blocks=existing_blocks)
        self.assertTrue(out)
        list_items = [block for block in out if block.get("type") == "list_item"]
        self.assertGreaterEqual(len(list_items), 2)
