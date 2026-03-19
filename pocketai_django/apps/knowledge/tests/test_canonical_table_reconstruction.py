from __future__ import annotations

from django.test import SimpleTestCase

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.canonical_table_reconstruction import CanonicalTableReconstructor
from apps.knowledge.knowledge_ingestion import (
    PageBlockPayload,
    PageLayout,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)


class CanonicalTableReconstructionTests(SimpleTestCase):
    def test_reconstructs_pseudo_table_from_aligned_blocks(self) -> None:
        blocks = [
            PageBlockPayload(
                block_type=KnowledgeBlockType.HEADING,
                order_index=1,
                text="Payroll Disbursement Pricing",
                bbox={"x0": 40.0, "y0": 60.0, "x1": 560.0, "y1": 80.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=2,
                text="From 1 to 100",
                bbox={"x0": 60.0, "y0": 120.0, "x1": 240.0, "y1": 140.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=3,
                text="EGP 7",
                bbox={"x0": 420.0, "y0": 120.0, "x1": 520.0, "y1": 140.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=4,
                text="From 101 to 500",
                bbox={"x0": 60.0, "y0": 150.0, "x1": 260.0, "y1": 170.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=5,
                text="EGP 6",
                bbox={"x0": 420.0, "y0": 150.0, "x1": 520.0, "y1": 170.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=6,
                text="From 501 to 1000",
                bbox={"x0": 60.0, "y0": 180.0, "x1": 270.0, "y1": 200.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=7,
                text="EGP 5",
                bbox={"x0": 420.0, "y0": 180.0, "x1": 520.0, "y1": 200.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=8,
                text="1000+",
                bbox={"x0": 60.0, "y0": 210.0, "x1": 180.0, "y1": 230.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=9,
                text="EGP 4",
                bbox={"x0": 420.0, "y0": 210.0, "x1": 520.0, "y1": 230.0},
                metadata={},
            ),
        ]
        pages = [
            PageLayout(
                page_number=1,
                width=600.0,
                height=800.0,
                rotation=0,
                text_density=0.01,
                has_ocr_content=False,
                content_type="application/pdf",
                blocks=blocks,
                metadata={},
            )
        ]

        recon = CanonicalTableReconstructor(
            PageLayout=PageLayout,
            PageBlockPayload=PageBlockPayload,
            TablePayload=TablePayload,
            TableRowPayload=TableRowPayload,
            TableCellPayload=TableCellPayload,
        )
        out_pages, out_tables, meta, _issues = recon.run(pages=pages, tables=[])

        self.assertEqual(meta.reconstructed_tables, 1)
        self.assertEqual(meta.reconstructed_rows, 4)
        self.assertEqual(len(out_tables), 1)
        table = out_tables[0]
        self.assertEqual(table.column_schema, ["Description", "Value"])
        self.assertEqual(len(table.rows), 4)
        self.assertEqual(table.rows[0].cells[0].raw_text, "From 1 to 100")
        self.assertEqual(table.rows[0].cells[1].raw_text, "EGP 7")
        self.assertEqual(table.rows[-1].cells[0].raw_text, "1000+")
        self.assertEqual(table.rows[-1].cells[1].raw_text, "EGP 4")

        # Used blocks should be marked as consumed.
        consumed = [
            b
            for b in out_pages[0].blocks
            if isinstance(b.metadata, dict) and b.metadata.get("canonical_consumed_by_table")
        ]
        self.assertGreaterEqual(len(consumed), 8)

    def test_promotes_three_block_rows_when_middle_is_placeholder(self) -> None:
        blocks = [
            PageBlockPayload(
                block_type=KnowledgeBlockType.HEADING,
                order_index=1,
                text="Payroll Disbursement Pricing",
                bbox={"x0": 40.0, "y0": 60.0, "x1": 560.0, "y1": 80.0},
                metadata={},
            ),
            # Row 1: label + placeholder + numeric value.
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=2,
                text="From 1 to 100 transactions",
                bbox={"x0": 60.0, "y0": 120.0, "x1": 240.0, "y1": 140.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=3,
                text="No fees",
                bbox={"x0": 290.0, "y0": 120.0, "x1": 360.0, "y1": 140.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.HEADING,
                order_index=4,
                text="EGP 7",
                bbox={"x0": 420.0, "y0": 120.0, "x1": 520.0, "y1": 140.0},
                metadata={},
            ),
            # Row 2
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=5,
                text="From 101 to 500 transactions",
                bbox={"x0": 60.0, "y0": 150.0, "x1": 270.0, "y1": 170.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=6,
                text="No fees",
                bbox={"x0": 290.0, "y0": 150.0, "x1": 360.0, "y1": 170.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.HEADING,
                order_index=7,
                text="EGP 6",
                bbox={"x0": 420.0, "y0": 150.0, "x1": 520.0, "y1": 170.0},
                metadata={},
            ),
            # Row 3
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=8,
                text="From 501 to 1000 transactions",
                bbox={"x0": 60.0, "y0": 180.0, "x1": 280.0, "y1": 200.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=9,
                text="No fees",
                bbox={"x0": 290.0, "y0": 180.0, "x1": 360.0, "y1": 200.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.HEADING,
                order_index=10,
                text="EGP 5",
                bbox={"x0": 420.0, "y0": 180.0, "x1": 520.0, "y1": 200.0},
                metadata={},
            ),
            # Row 4
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=11,
                text="1000+ transactions",
                bbox={"x0": 60.0, "y0": 210.0, "x1": 200.0, "y1": 230.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=12,
                text="No fees",
                bbox={"x0": 290.0, "y0": 210.0, "x1": 360.0, "y1": 230.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.HEADING,
                order_index=13,
                text="EGP 4",
                bbox={"x0": 420.0, "y0": 210.0, "x1": 520.0, "y1": 230.0},
                metadata={},
            ),
        ]
        pages = [
            PageLayout(
                page_number=1,
                width=600.0,
                height=800.0,
                rotation=0,
                text_density=0.01,
                has_ocr_content=False,
                content_type="application/pdf",
                blocks=blocks,
                metadata={},
            )
        ]

        recon = CanonicalTableReconstructor(
            PageLayout=PageLayout,
            PageBlockPayload=PageBlockPayload,
            TablePayload=TablePayload,
            TableRowPayload=TableRowPayload,
            TableCellPayload=TableCellPayload,
        )
        out_pages, out_tables, meta, _issues = recon.run(pages=pages, tables=[])

        self.assertEqual(meta.reconstructed_tables, 1)
        self.assertEqual(meta.reconstructed_rows, 4)
        self.assertEqual(len(out_tables), 1)
        table = out_tables[0]
        self.assertEqual(table.rows[0].cells[0].raw_text, "From 1 to 100 transactions")
        self.assertEqual(table.rows[0].cells[1].raw_text, "EGP 7")
        self.assertEqual(table.rows[-1].cells[0].raw_text, "1000+ transactions")
        self.assertEqual(table.rows[-1].cells[1].raw_text, "EGP 4")

        consumed = [
            b
            for b in out_pages[0].blocks
            if isinstance(b.metadata, dict) and b.metadata.get("canonical_consumed_by_table")
        ]
        # At minimum the label/value blocks (8) should be consumed; placeholders may remain as text.
        self.assertGreaterEqual(len(consumed), 8)

    def test_attaches_residual_block_into_cell(self) -> None:
        table = TablePayload(
            order_index=1,
            title="Cheques",
            section_heading="",
            page_number=1,
            bbox={"x0": 50.0, "y0": 100.0, "x1": 550.0, "y1": 300.0},
            column_schema=["Service", "Fee"],
            metadata={"detected_via": "geometry"},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    bbox={"x0": 50.0, "y0": 140.0, "x1": 550.0, "y1": 160.0},
                    raw_text="",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(
                            row_index=0,
                            column_index=0,
                            column_key="Service",
                            raw_text="LCY Checks Outside CBE Clearing House",
                            bbox={"x0": 60.0, "y0": 140.0, "x1": 280.0, "y1": 160.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=1,
                            column_key="Fee",
                            raw_text="0.2% Min EGP 20 / Max EGP 400",
                            bbox={"x0": 300.0, "y0": 140.0, "x1": 540.0, "y1": 160.0},
                        ),
                    ],
                )
            ],
        )

        residual_block = PageBlockPayload(
            block_type=KnowledgeBlockType.PARAGRAPH,
            order_index=10,
            text="0.2% Min EGP 20 / Max EGP 400 + Correspondent Fees + Courier Fees",
            bbox={"x0": 300.0, "y0": 140.0, "x1": 540.0, "y1": 160.0},
            metadata={"table_residual": True},
        )
        pages = [
            PageLayout(
                page_number=1,
                width=600.0,
                height=800.0,
                rotation=0,
                text_density=0.01,
                has_ocr_content=False,
                content_type="application/pdf",
                blocks=[residual_block],
                metadata={},
            )
        ]

        recon = CanonicalTableReconstructor(
            PageLayout=PageLayout,
            PageBlockPayload=PageBlockPayload,
            TablePayload=TablePayload,
            TableRowPayload=TableRowPayload,
            TableCellPayload=TableCellPayload,
        )
        out_pages, out_tables, meta, _issues = recon.run(pages=pages, tables=[table])

        self.assertEqual(meta.attached_blocks, 1)
        self.assertEqual(meta.attached_cells, 1)
        updated = out_tables[0].rows[0].cells[1].raw_text
        self.assertIn("Correspondent Fees", updated)
        self.assertTrue(out_pages[0].blocks[0].metadata.get("canonical_consumed_by_table"))

    def test_does_not_force_row_wide_block_into_single_cell(self) -> None:
        table = TablePayload(
            order_index=1,
            title="Customer Service Fees",
            section_heading="",
            page_number=1,
            bbox={"x0": 50.0, "y0": 100.0, "x1": 650.0, "y1": 260.0},
            column_schema=["Service", "Tariff", "Prime", "Plus"],
            metadata={"detected_via": "azure_di"},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    bbox={"x0": 50.0, "y0": 140.0, "x1": 650.0, "y1": 180.0},
                    raw_text="",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(
                            row_index=0,
                            column_index=0,
                            column_key="Service",
                            raw_text="Standing Instruction for External transfer",
                            bbox={"x0": 60.0, "y0": 140.0, "x1": 220.0, "y1": 180.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=1,
                            column_key="Tariff",
                            raw_text="EGP 50 Subscription",
                            bbox={"x0": 220.0, "y0": 140.0, "x1": 360.0, "y1": 180.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=2,
                            column_key="Prime",
                            raw_text="EGP 40 per transaction",
                            bbox={"x0": 360.0, "y0": 140.0, "x1": 500.0, "y1": 180.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=3,
                            column_key="Plus",
                            raw_text="EGP 10 per transaction",
                            bbox={"x0": 500.0, "y0": 140.0, "x1": 640.0, "y1": 180.0},
                        ),
                    ],
                )
            ],
        )

        row_wide_block = PageBlockPayload(
            block_type=KnowledgeBlockType.PARAGRAPH,
            order_index=10,
            text=(
                "Standing Instruction for External transfer\n"
                "EGP 50 Subscription\n"
                "EGP 40 per transaction\n"
                "EGP 10 per transaction"
            ),
            bbox={"x0": 70.0, "y0": 144.0, "x1": 620.0, "y1": 176.0},
            metadata={},
        )
        pages = [
            PageLayout(
                page_number=1,
                width=700.0,
                height=800.0,
                rotation=0,
                text_density=0.01,
                has_ocr_content=False,
                content_type="application/pdf",
                blocks=[row_wide_block],
                metadata={},
            )
        ]

        recon = CanonicalTableReconstructor(
            PageLayout=PageLayout,
            PageBlockPayload=PageBlockPayload,
            TablePayload=TablePayload,
            TableRowPayload=TableRowPayload,
            TableCellPayload=TableCellPayload,
        )
        out_pages, out_tables, meta, _issues = recon.run(pages=pages, tables=[table])

        self.assertEqual(meta.attached_blocks, 0)
        self.assertEqual(meta.attached_cells, 0)
        updated_cells = out_tables[0].rows[0].cells
        self.assertEqual(updated_cells[0].raw_text, "Standing Instruction for External transfer")
        self.assertEqual(updated_cells[1].raw_text, "EGP 50 Subscription")
        self.assertEqual(updated_cells[2].raw_text, "EGP 40 per transaction")
        self.assertEqual(updated_cells[3].raw_text, "EGP 10 per transaction")
        self.assertFalse(out_pages[0].blocks[0].metadata.get("canonical_consumed_by_table"))

    def test_does_not_force_multi_value_block_into_single_value_cell(self) -> None:
        table = TablePayload(
            order_index=1,
            title="Customer Service Fees",
            section_heading="",
            page_number=1,
            bbox={"x0": 50.0, "y0": 100.0, "x1": 700.0, "y1": 260.0},
            column_schema=["Service", "Tariff", "Prime", "Plus", "Wealth"],
            metadata={"detected_via": "azure_di"},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    bbox={"x0": 50.0, "y0": 140.0, "x1": 700.0, "y1": 180.0},
                    raw_text="",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(
                            row_index=0,
                            column_index=0,
                            column_key="Service",
                            raw_text="Standing Instruction for Internal transfer",
                            bbox={"x0": 60.0, "y0": 140.0, "x1": 240.0, "y1": 180.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=1,
                            column_key="Tariff",
                            raw_text="EGP 50 Subscription",
                            bbox={"x0": 240.0, "y0": 140.0, "x1": 360.0, "y1": 180.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=2,
                            column_key="Prime",
                            raw_text="EGP 50 Subscription",
                            bbox={"x0": 360.0, "y0": 140.0, "x1": 470.0, "y1": 180.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=3,
                            column_key="Plus",
                            raw_text="EGP 10 per transaction",
                            bbox={"x0": 470.0, "y0": 140.0, "x1": 580.0, "y1": 180.0},
                        ),
                        TableCellPayload(
                            row_index=0,
                            column_index=4,
                            column_key="Wealth",
                            raw_text="Free",
                            bbox={"x0": 580.0, "y0": 140.0, "x1": 680.0, "y1": 180.0},
                        ),
                    ],
                )
            ],
        )

        row_wide_values = PageBlockPayload(
            block_type=KnowledgeBlockType.PARAGRAPH,
            order_index=11,
            text=(
                "EGP 50 Subscription\n"
                "EGP 10 per transaction\n"
                "Free\n"
                "Free\n"
                "Free"
            ),
            bbox={"x0": 250.0, "y0": 144.0, "x1": 670.0, "y1": 176.0},
            metadata={"table_residual": True},
        )
        pages = [
            PageLayout(
                page_number=1,
                width=700.0,
                height=800.0,
                rotation=0,
                text_density=0.01,
                has_ocr_content=False,
                content_type="application/pdf",
                blocks=[row_wide_values],
                metadata={},
            )
        ]

        recon = CanonicalTableReconstructor(
            PageLayout=PageLayout,
            PageBlockPayload=PageBlockPayload,
            TablePayload=TablePayload,
            TableRowPayload=TableRowPayload,
            TableCellPayload=TableCellPayload,
        )
        out_pages, out_tables, meta, _issues = recon.run(pages=pages, tables=[table])

        self.assertEqual(meta.attached_blocks, 0)
        self.assertEqual(meta.attached_cells, 0)
        updated_cells = out_tables[0].rows[0].cells
        self.assertEqual(updated_cells[1].raw_text, "EGP 50 Subscription")
        self.assertEqual(updated_cells[2].raw_text, "EGP 50 Subscription")
        self.assertEqual(updated_cells[3].raw_text, "EGP 10 per transaction")
        self.assertEqual(updated_cells[4].raw_text, "Free")
        self.assertFalse(out_pages[0].blocks[0].metadata.get("canonical_consumed_by_table"))

    def test_does_not_promote_noisy_three_block_layout(self) -> None:
        blocks = [
            PageBlockPayload(
                block_type=KnowledgeBlockType.HEADING,
                order_index=1,
                text="Project Milestones",
                bbox={"x0": 40.0, "y0": 60.0, "x1": 320.0, "y1": 80.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=2,
                text="Phase One",
                bbox={"x0": 60.0, "y0": 120.0, "x1": 180.0, "y1": 140.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=3,
                text="Completed during pilot",
                bbox={"x0": 230.0, "y0": 120.0, "x1": 390.0, "y1": 140.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=4,
                text="2023",
                bbox={"x0": 470.0, "y0": 120.0, "x1": 520.0, "y1": 140.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=5,
                text="Phase Two",
                bbox={"x0": 60.0, "y0": 150.0, "x1": 180.0, "y1": 170.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=6,
                text="Completed during rollout",
                bbox={"x0": 230.0, "y0": 150.0, "x1": 405.0, "y1": 170.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=7,
                text="2024",
                bbox={"x0": 470.0, "y0": 150.0, "x1": 520.0, "y1": 170.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=8,
                text="Phase Three",
                bbox={"x0": 60.0, "y0": 180.0, "x1": 190.0, "y1": 200.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=9,
                text="Completed during launch",
                bbox={"x0": 230.0, "y0": 180.0, "x1": 395.0, "y1": 200.0},
                metadata={},
            ),
            PageBlockPayload(
                block_type=KnowledgeBlockType.PARAGRAPH,
                order_index=10,
                text="2025",
                bbox={"x0": 470.0, "y0": 180.0, "x1": 520.0, "y1": 200.0},
                metadata={},
            ),
        ]
        pages = [
            PageLayout(
                page_number=1,
                width=600.0,
                height=800.0,
                rotation=0,
                text_density=0.01,
                has_ocr_content=False,
                content_type="application/pdf",
                blocks=blocks,
                metadata={},
            )
        ]

        recon = CanonicalTableReconstructor(
            PageLayout=PageLayout,
            PageBlockPayload=PageBlockPayload,
            TablePayload=TablePayload,
            TableRowPayload=TableRowPayload,
            TableCellPayload=TableCellPayload,
        )
        out_pages, out_tables, meta, _issues = recon.run(pages=pages, tables=[])

        self.assertEqual(meta.reconstructed_tables, 0)
        self.assertEqual(len(out_tables), 0)
        consumed = [
            b
            for b in out_pages[0].blocks
            if isinstance(b.metadata, dict) and b.metadata.get("canonical_consumed_by_table")
        ]
        self.assertEqual(consumed, [])
