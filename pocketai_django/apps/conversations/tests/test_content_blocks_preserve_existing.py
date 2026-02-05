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

