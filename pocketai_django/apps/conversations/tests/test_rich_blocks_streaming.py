from __future__ import annotations

from django.test import SimpleTestCase

from apps.conversations.rich_blocks import RichBlockStreamBuilder, rich_blocks_from_text


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
    def test_chunk_boundary_newline_closes_paragraph_immediately(self) -> None:
        builder = RichBlockStreamBuilder()

        first_events = builder.feed_text("Administrative Fee - Paid once in advance (2% of the loan amount)\n")

        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in first_events],
            ["block_start", "block_delta", "block_end"],
        )
        self.assertEqual(builder.active_paragraph_id, None)

        second_events = builder.feed_text("Partial Early Settlement Fees - 7% of the paid amount")

        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in second_events],
            ["block_start", "block_delta"],
        )

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

    def test_streaming_promotes_markdown_table_to_table_block(self) -> None:
        builder = RichBlockStreamBuilder()

        first_events = builder.feed_text("| Account Type | Fee | Maximum Cap |\n")
        self.assertEqual(first_events, [])

        second_events = builder.feed_text("| --- | --- | --- |\n")
        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in second_events],
            ["block_start"],
        )

        third_events = builder.feed_text("| Plus | EGP 20 per paper | Max EGP 1,000 |\n")
        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in third_events],
            ["block_delta"],
        )

        blocks = builder.snapshot()
        self.assertEqual(len(blocks), 1)
        table = blocks[0]
        self.assertEqual(table["type"], "table")
        payload = table["payload"]
        self.assertEqual([col["label"] for col in payload["columns"]], ["Account Type", "Fee", "Maximum Cap"])
        self.assertEqual(payload["rows"], [{"cells": ["Plus", "EGP 20 per paper", "Max EGP 1,000"]}])

    def test_rich_blocks_from_text_promotes_markdown_table(self) -> None:
        blocks = rich_blocks_from_text(
            "\n".join(
                [
                    "Copy of Document Fees:",
                    "| Account Type | Fee per Paper | Maximum Cap |",
                    "| --- | --- | --- |",
                    "| Plus | EGP 20 per paper | Max EGP 1,000 |",
                    "| Private | EGP 5 per paper | Max EGP 250 |",
                ]
            )
        )

        self.assertEqual([block["type"] for block in blocks], ["paragraph", "table"])
        table = blocks[1]
        payload = table["payload"]
        self.assertEqual(len(payload["columns"]), 3)
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["rows"][1]["cells"], ["Private", "EGP 5 per paper", "Max EGP 250"])

    def test_streaming_partial_table_header_does_not_leak_paragraph(self) -> None:
        builder = RichBlockStreamBuilder()

        first_events = builder.feed_text("| Segment")
        self.assertEqual(first_events, [])

        second_events = builder.feed_text(" | Fee per Paper | Maximum Cap |\n")
        self.assertEqual(second_events, [])

        third_events = builder.feed_text("| --- | --- | --- |\n")
        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in third_events],
            ["block_start"],
        )

        fourth_events = builder.feed_text("| Plus | EGP 20 per paper | Max EGP 1,000 |\n")
        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in fourth_events],
            ["block_delta"],
        )

        blocks = builder.snapshot()
        self.assertEqual([block["type"] for block in blocks], ["table"])
        payload = blocks[0]["payload"]
        self.assertEqual([col["label"] for col in payload["columns"]], ["Segment", "Fee per Paper", "Maximum Cap"])
        self.assertEqual(payload["rows"], [{"cells": ["Plus", "EGP 20 per paper", "Max EGP 1,000"]}])

    def test_streaming_partial_table_row_updates_live_cells(self) -> None:
        builder = RichBlockStreamBuilder()

        builder.feed_text("| Segment | Fee per Paper | Maximum Cap |\n")
        builder.feed_text("| --- | --- | --- |\n")

        first_partial = builder.feed_text("| Plus")
        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in first_partial],
            ["block_delta"],
        )
        first_ops = first_partial[0]["payload"]["ops"]
        self.assertEqual(len(first_ops), 1)
        self.assertEqual(first_ops[0]["op"], "set_table_cell_text")
        self.assertEqual(first_ops[0]["row_index"], 0)
        self.assertEqual(first_ops[0]["cell_index"], 0)
        self.assertEqual(first_ops[0]["text"], "Plus")

        second_partial = builder.feed_text(" | EGP 20")
        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in second_partial],
            ["block_delta"],
        )
        second_ops = second_partial[0]["payload"]["ops"]
        self.assertEqual(len(second_ops), 1)
        self.assertEqual(second_ops[0]["op"], "set_table_cell_text")
        self.assertEqual(second_ops[0]["row_index"], 0)
        self.assertEqual(second_ops[0]["cell_index"], 1)
        self.assertEqual(second_ops[0]["text"], "EGP 20")

        final_partial = builder.feed_text(" per paper | Max EGP 1,000 |\n")
        self.assertEqual(
            [str(event.get("type") or "").strip().lower() for event in final_partial],
            ["block_delta"],
        )
        final_ops = final_partial[0]["payload"]["ops"]
        self.assertEqual(
            final_ops,
            [
                {"op": "set_table_cell_text", "row_index": 0, "cell_index": 1, "text": "EGP 20 per paper"},
                {"op": "set_table_cell_text", "row_index": 0, "cell_index": 2, "text": "Max EGP 1,000"},
            ],
        )

        blocks = builder.snapshot()
        self.assertEqual([block["type"] for block in blocks], ["table"])
        payload = blocks[0]["payload"]
        self.assertEqual(
            payload["rows"],
            [{"cells": ["Plus", "EGP 20 per paper", "Max EGP 1,000"]}],
        )
