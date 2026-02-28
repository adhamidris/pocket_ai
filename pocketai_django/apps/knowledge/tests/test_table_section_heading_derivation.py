from __future__ import annotations

from django.test import SimpleTestCase

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.knowledge_ingestion import KnowledgeIngestionService, PageBlockPayload


class TableSectionHeadingDerivationTests(SimpleTestCase):
    def test_derives_heading_from_block_above_table(self) -> None:
        heading_block = PageBlockPayload(
            block_type=KnowledgeBlockType.PARAGRAPH,
            order_index=10,
            text="Fees & Charges",
            bbox={"x0": 100.0, "y0": 180.0, "x1": 500.0, "y1": 200.0},
            metadata={"region_role": "text"},
        )
        table_like_block = PageBlockPayload(
            block_type=KnowledgeBlockType.TABLE,
            order_index=11,
            text="Service\tTariff\tPrime\tPlus",
            bbox={"x0": 100.0, "y0": 205.0, "x1": 500.0, "y1": 235.0},
            metadata={"region_role": "table"},
        )

        heading, anchor = KnowledgeIngestionService._derive_table_section_heading_from_blocks(
            table_bbox={"x0": 100.0, "y0": 250.0, "x1": 500.0, "y1": 400.0},
            page_blocks=[heading_block, table_like_block],
            page_number=1,
            page_height=800.0,
        )

        self.assertEqual(heading, "Fees & Charges")
        self.assertEqual(anchor, "p1-b10")

    def test_skips_pii_like_heading_text(self) -> None:
        pii_block = PageBlockPayload(
            block_type=KnowledgeBlockType.PARAGRAPH,
            order_index=3,
            text="Contact: support@example.com",
            bbox={"x0": 100.0, "y0": 180.0, "x1": 500.0, "y1": 200.0},
            metadata={"region_role": "text"},
        )

        heading, anchor = KnowledgeIngestionService._derive_table_section_heading_from_blocks(
            table_bbox={"x0": 100.0, "y0": 250.0, "x1": 500.0, "y1": 400.0},
            page_blocks=[pii_block],
            page_number=1,
            page_height=800.0,
        )

        self.assertEqual(heading, "")
        self.assertIsNone(anchor)

    def test_row_chunks_do_not_include_derived_section_heading(self) -> None:
        class _Manager:
            def __init__(self, items):
                self._items = list(items)

            def all(self):
                return list(self._items)

        class _Cell:
            def __init__(self, column_index: int, raw_text: str):
                self.id = f"c-{column_index}"
                self.column_index = column_index
                self.column_key = ""
                self.raw_text = raw_text

        class _Row:
            def __init__(self):
                self.row_index = 1
                self.metadata = {"row_type": "data"}
                self.cells = _Manager([_Cell(0, "Service A"), _Cell(1, "EGP 10")])

        class _Table:
            def __init__(self):
                self.title = "Teller"
                self.order_index = 1
                self.section_heading = "Fees & Charges"
                self.metadata = {"derived_section_heading": "Fees & Charges"}
                self.rows = _Manager([_Row()])

        service = KnowledgeIngestionService(enable_ocr=False)
        payloads = service._table_row_chunk_payloads(
            table=_Table(),
            column_map=[("Service", "service", 0), ("Tariff", "tariff", 1)],
            raw_schema=["service", "tariff"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "table-1"},
            max_rows=10,
        )
        self.assertEqual(len(payloads), 1)
        text = str(payloads[0].get("text") or "")
        self.assertNotIn("[Section] Fees & Charges", text)
