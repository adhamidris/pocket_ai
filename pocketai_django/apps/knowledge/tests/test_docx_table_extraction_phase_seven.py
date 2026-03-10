from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    DocxDocument,
    KnowledgeIngestionService,
    TablePayload,
    TableCellPayload,
    TableRowPayload,
)


class DocxTableExtractionTests(SimpleTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.service = KnowledgeIngestionService(enable_ocr=False)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_extracts_docx_tables_with_header_detection_and_heading_context(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Pricing Matrix:")
        table = doc.add_table(rows=3, cols=3)

        table.cell(0, 0).text = "Pricing"
        table.cell(0, 0).merge(table.cell(0, 1))
        table.cell(0, 2).text = "Tier"
        table.cell(1, 0).text = "Fee"
        table.cell(1, 1).text = "Tax"
        table.cell(1, 2).text = "Plan"
        table.cell(2, 0).text = "10"
        table.cell(2, 1).text = "1"
        table.cell(2, 2).text = "Gold"

        path = self.base / "pricing.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="pricing.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)
        self.assertEqual(len(tables), 1)

        payload = tables[0]
        self.assertEqual(payload.metadata.get("detected_via"), "docx:table_xml")
        self.assertEqual(payload.section_heading, "Pricing Matrix:")
        self.assertEqual(payload.title, "Pricing Matrix:")
        self.assertIn("pricing_fee", payload.column_schema)
        self.assertIn("pricing_tax", payload.column_schema)
        self.assertIn("tier_plan", payload.column_schema)
        self.assertEqual(payload.rows[0].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[1].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[2].metadata.get("row_type"), "data")

    def test_tracks_vertical_merge_regions(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        table = doc.add_table(rows=3, cols=2)
        table.cell(0, 0).text = "Category"
        table.cell(0, 1).text = "Value"
        table.cell(1, 0).text = "Local"
        table.cell(2, 0).text = "Local"
        table.cell(1, 0).merge(table.cell(2, 0))
        table.cell(1, 1).text = "100"
        table.cell(2, 1).text = "200"

        path = self.base / "vertical-merge.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="vertical-merge.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)
        payload = tables[0]

        anchor = payload.rows[1].cells[0]
        continuation = payload.rows[2].cells[0]

        self.assertEqual(anchor.metadata.get("row_span"), 2)
        self.assertTrue(anchor.metadata.get("merged_anchor"))
        self.assertEqual(continuation.metadata.get("row_span"), 2)
        self.assertFalse(continuation.metadata.get("merged_anchor"))
        self.assertEqual(continuation.metadata.get("merged_from"), {"row_index": 1, "column_index": 0})

    def test_compacts_blank_spacer_rows_and_recognizes_header_band(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Summary Results of Operations")
        table = doc.add_table(rows=6, cols=5)

        table.cell(0, 0).text = "(In millions)"
        table.cell(0, 2).text = "2025"
        table.cell(0, 4).text = "2024"
        table.cell(1, 0).text = ""
        table.cell(1, 1).text = ""
        table.cell(1, 2).text = ""
        table.cell(1, 3).text = ""
        table.cell(1, 4).text = ""
        table.cell(2, 0).text = "Year Ended June 30,"
        table.cell(2, 2).text = "2025"
        table.cell(2, 4).text = "2024"
        table.cell(3, 0).text = ""
        table.cell(3, 1).text = ""
        table.cell(3, 2).text = ""
        table.cell(3, 3).text = ""
        table.cell(3, 4).text = ""
        table.cell(4, 0).text = "Revenue"
        table.cell(4, 1).text = "$"
        table.cell(4, 2).text = "281,724"
        table.cell(4, 3).text = "$"
        table.cell(4, 4).text = "245,122"
        table.cell(5, 0).text = "Gross margin"
        table.cell(5, 2).text = "193,893"
        table.cell(5, 4).text = "171,008"

        path = self.base / "annual-like.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="annual-like.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)
        self.assertEqual(meta.get("blank_rows_compacted"), 2)

        payload = tables[0]
        self.assertEqual(len(payload.rows), 4)
        self.assertEqual(payload.metadata.get("blank_rows_compacted"), 2)
        self.assertEqual(payload.metadata.get("helper_columns_merged"), 2)
        self.assertEqual(len(payload.column_schema), 3)
        self.assertEqual(payload.column_schema, ["in_millions_year_ended_june_30", "2025", "2024"])
        self.assertEqual(payload.rows[0].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[1].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[0].cells[1].raw_text, "2025")
        self.assertEqual(payload.rows[0].cells[2].raw_text, "2024")
        self.assertEqual(payload.rows[1].cells[1].raw_text, "2025")
        self.assertEqual(payload.rows[1].cells[2].raw_text, "2024")
        self.assertEqual(payload.rows[2].metadata.get("row_type"), "data")
        self.assertEqual(payload.rows[2].cells[0].raw_text, "Revenue")
        self.assertEqual(payload.rows[2].cells[1].raw_text, "$281,724")
        self.assertEqual(payload.rows[2].cells[2].raw_text, "$245,122")
        self.assertFalse(any(not any((cell.raw_text or "").strip() for cell in row.cells) for row in payload.rows))

    def test_classifies_single_label_section_rows_in_docx_tables(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Segment Results")
        table = doc.add_table(rows=6, cols=4)

        table.cell(0, 0).text = "Metric"
        table.cell(0, 2).text = "2025"
        table.cell(0, 3).text = "2024"
        table.cell(1, 0).text = "Productivity and Business Processes"
        table.cell(2, 0).text = "Revenue"
        table.cell(2, 2).text = "120,810"
        table.cell(2, 3).text = "106,820"
        table.cell(3, 0).text = "Operating income"
        table.cell(3, 2).text = "69,773"
        table.cell(3, 3).text = "59,661"
        table.cell(4, 0).text = "Intelligent Cloud"
        table.cell(5, 0).text = "Revenue"
        table.cell(5, 2).text = "106,265"
        table.cell(5, 3).text = "87,464"

        path = self.base / "segment-section.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="segment-section.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)
        self.assertEqual(meta.get("section_rows_detected"), 2)

        payload = tables[0]
        row_types = {row.row_index: row.metadata.get("row_type") for row in payload.rows}
        self.assertEqual(row_types[0], "header")
        self.assertEqual(row_types[1], "section_header")
        self.assertEqual(row_types[4], "section_header")
        self.assertEqual(row_types[2], "data")
        self.assertEqual(row_types[5], "data")

    def test_collapses_split_negative_value_helper_columns(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Other income expense")
        table = doc.add_table(rows=3, cols=5)

        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "2025"
        table.cell(0, 3).text = "2024"
        table.cell(1, 0).text = "Interest expense"
        table.cell(1, 1).text = "(2,385"
        table.cell(1, 2).text = ")"
        table.cell(1, 3).text = "(2,935"
        table.cell(1, 4).text = ")"
        table.cell(2, 0).text = "Other, net"
        table.cell(2, 1).text = "(4,725"
        table.cell(2, 2).text = ")"
        table.cell(2, 3).text = "(1,319"
        table.cell(2, 4).text = ")"

        path = self.base / "negative-values.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="negative-values.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)

        payload = tables[0]
        self.assertEqual(len(payload.column_schema), 3)
        self.assertEqual(payload.metadata.get("helper_columns_merged"), 2)
        self.assertEqual(payload.rows[1].cells[1].raw_text, "(2,385)")
        self.assertEqual(payload.rows[1].cells[2].raw_text, "(2,935)")
        self.assertEqual(payload.rows[2].cells[1].raw_text, "(4,725)")
        self.assertEqual(payload.rows[2].cells[2].raw_text, "(1,319)")

    def test_deduplicates_repeated_header_phrases_after_helper_column_collapse(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Share Repurchases")
        table = doc.add_table(rows=4, cols=5)

        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "Amount"
        table.cell(0, 2).text = "Amount"
        table.cell(0, 3).text = "Amount"
        table.cell(0, 4).text = "Amount"
        table.cell(1, 1).text = "2025"
        table.cell(1, 2).text = "2025"
        table.cell(1, 3).text = "2024"
        table.cell(1, 4).text = "2024"
        table.cell(2, 0).text = "First Quarter"
        table.cell(2, 1).text = "$"
        table.cell(2, 2).text = "2,800"
        table.cell(2, 3).text = "$"
        table.cell(2, 4).text = "3,560"
        table.cell(3, 0).text = "Second Quarter"
        table.cell(3, 2).text = "3,500"
        table.cell(3, 4).text = "2,800"

        path = self.base / "duplicate-header-bands.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="duplicate-header-bands.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)

        payload = tables[0]
        self.assertEqual(payload.metadata.get("helper_columns_merged"), 2)
        self.assertEqual(payload.column_schema, ["metric", "amount_2025", "amount_2024"])
        self.assertEqual(payload.rows[0].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[1].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[0].cells[1].raw_text, "Amount")
        self.assertEqual(payload.rows[0].cells[2].raw_text, "Amount")
        self.assertEqual(payload.rows[1].cells[1].raw_text, "2025")
        self.assertEqual(payload.rows[1].cells[2].raw_text, "2024")
        self.assertEqual(payload.rows[2].cells[1].raw_text, "$2,800")
        self.assertEqual(payload.rows[2].cells[2].raw_text, "$3,560")

    def test_skips_docx_header_and_section_rows_from_table_entities(self) -> None:
        table = TablePayload(
            order_index=1,
            title="Docx Table",
            section_heading="Docx Table",
            page_number=1,
            column_schema=["metric", "value"],
            data_dictionary={},
            metadata={"detected_via": "docx:table_xml"},
            rows=[],
        )
        header_row = TableRowPayload(
            row_index=0,
            page_number=1,
            raw_text="Metric\tValue",
            metadata={"row_type": "header"},
            cells=[],
        )
        section_row = TableRowPayload(
            row_index=1,
            page_number=1,
            raw_text="Operating Expenses",
            metadata={"row_type": "section_header"},
            cells=[],
        )
        data_row = TableRowPayload(
            row_index=2,
            page_number=1,
            raw_text="Revenue\t281,724",
            metadata={"row_type": "data"},
            cells=[],
        )

        should_create_header, reason_header = self.service._should_create_table_row_entity(
            table=table,
            row=header_row,
            attributes={"metric": "Metric", "value": "Value"},
        )
        should_create_section, reason_section = self.service._should_create_table_row_entity(
            table=table,
            row=section_row,
            attributes={"metric": "Operating Expenses", "value": ""},
        )
        should_create_data, reason_data = self.service._should_create_table_row_entity(
            table=table,
            row=data_row,
            attributes={"metric": "Revenue", "value": "281,724"},
        )

        self.assertFalse(should_create_header)
        self.assertEqual(reason_header, "header_row")
        self.assertFalse(should_create_section)
        self.assertEqual(reason_section, "header_row")
        self.assertTrue(should_create_data)
        self.assertIsNone(reason_data)

    def test_normalizes_sparse_series_period_columns_into_header_schema(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Comparison")
        table = doc.add_table(rows=4, cols=7)

        table.cell(0, 1).text = "6/20"
        table.cell(0, 2).text = "6/20"
        table.cell(0, 3).text = "6/21"
        table.cell(0, 4).text = "6/21"
        table.cell(0, 5).text = "6/22"
        table.cell(0, 6).text = "6/22"
        table.cell(1, 0).text = "Microsoft Corporation"
        table.cell(1, 2).text = "100.00"
        table.cell(1, 4).text = "134.41"
        table.cell(1, 6).text = "128.48"
        table.cell(2, 0).text = "S&P 500"
        table.cell(2, 2).text = "100.00"
        table.cell(2, 4).text = "140.79"
        table.cell(2, 6).text = "125.85"
        table.cell(3, 0).text = "NASDAQ Computer"
        table.cell(3, 2).text = "100.00"
        table.cell(3, 4).text = "150.44"
        table.cell(3, 6).text = "117.59"

        path = self.base / "comparison-series.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="comparison-series.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)

        payload = tables[0]
        self.assertEqual(payload.rows[0].metadata.get("row_type"), "header")
        self.assertEqual(payload.column_schema[1:], ["6_20", "6_21", "6_22"])
        self.assertEqual([cell.raw_text for cell in payload.rows[1].cells], ["Microsoft Corporation", "100.00", "134.41", "128.48"])
        self.assertEqual([cell.raw_text for cell in payload.rows[2].cells], ["S&P 500", "100.00", "140.79", "125.85"])

    def test_sparse_series_normalizer_merges_duplicate_period_pairs(self) -> None:
        key_grid = [
            [1, 2, 3, 4, 5, 6, 7],
            [8, None, 9, None, 10, None, 11],
            [12, None, 13, None, 14, None, 15],
        ]
        text_by_key = {
            1: "",
            2: "6/20",
            3: "6/20",
            4: "6/21",
            5: "6/21",
            6: "6/22",
            7: "6/22",
            8: "Microsoft Corporation",
            9: "100.00",
            10: "134.41",
            11: "128.48",
            12: "S&P 500",
            13: "100.00",
            14: "140.79",
            15: "125.85",
        }
        span_by_key = {
            key: {
                "row_start": row_idx,
                "row_end": row_idx,
                "col_start": col_idx,
                "col_end": col_idx,
                "row_span": 1,
                "column_span": 1,
            }
            for row_idx, row in enumerate(key_grid)
            for col_idx, key in enumerate(row)
            if key is not None
        }

        new_grid, new_text_by_key, _, meta = self.service._docx_normalize_sparse_series_columns(
            key_grid,
            text_by_key,
            span_by_key,
        )

        self.assertEqual(meta.get("merged_columns"), 3)
        self.assertEqual(len(new_grid[0]), 4)
        self.assertEqual([new_text_by_key[key] if key is not None else "" for key in new_grid[0]], ["", "6/20", "6/21", "6/22"])
        self.assertEqual([new_text_by_key[key] if key is not None else "" for key in new_grid[1]], ["Microsoft Corporation", "100.00", "134.41", "128.48"])

    def test_promotes_single_period_row_into_header_band(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Unearned Revenue")
        table = doc.add_table(rows=4, cols=2)

        table.cell(0, 0).text = "(In millions)"
        table.cell(1, 0).text = "Year Ended June 30, 2025"
        table.cell(2, 0).text = "Balance, beginning of period"
        table.cell(2, 1).text = "$60,184"
        table.cell(3, 0).text = "Deferral of revenue"
        table.cell(3, 1).text = "186,957"

        path = self.base / "period-header.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="period-header.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)

        payload = tables[0]
        self.assertEqual(payload.rows[0].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[1].metadata.get("row_type"), "header")
        self.assertEqual(payload.rows[2].metadata.get("row_type"), "data")

    def test_classifies_mid_table_period_break_row_as_section_header(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Dividends")
        table = doc.add_table(rows=5, cols=5)

        table.cell(0, 0).text = "Declaration Date"
        table.cell(0, 1).text = "Record Date"
        table.cell(0, 2).text = "Payment Date"
        table.cell(0, 3).text = "Dividend Per Share"
        table.cell(0, 4).text = "Amount"
        table.cell(1, 0).text = "September 16, 2024"
        table.cell(1, 1).text = "November 21, 2024"
        table.cell(1, 2).text = "December 12, 2024"
        table.cell(1, 3).text = "$0.83"
        table.cell(1, 4).text = "$6,170"
        table.cell(2, 0).text = "Fiscal Year 2024"
        table.cell(3, 0).text = "September 19, 2023"
        table.cell(3, 1).text = "November 16, 2023"
        table.cell(3, 2).text = "December 14, 2023"
        table.cell(3, 3).text = "$0.75"
        table.cell(3, 4).text = "$5,574"
        table.cell(4, 0).text = "November 28, 2023"
        table.cell(4, 1).text = "February 15, 2024"
        table.cell(4, 2).text = "March 14, 2024"
        table.cell(4, 3).text = "$0.75"
        table.cell(4, 4).text = "$5,573"

        path = self.base / "period-break-section.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="period-break-section.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)

        payload = tables[0]
        self.assertEqual(payload.rows[2].metadata.get("row_type"), "section_header")
        self.assertEqual([cell.raw_text for cell in payload.rows[2].cells], ["Fiscal Year 2024", "", "", "", ""])

    def test_collapses_repeated_section_label_rows_into_single_visible_cell(self) -> None:
        if DocxDocument is None:
            self.skipTest("python-docx is not installed.")

        doc = DocxDocument()
        doc.add_paragraph("Segment Results")
        table = doc.add_table(rows=5, cols=4)

        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "2025"
        table.cell(0, 2).text = "2024"
        table.cell(0, 3).text = "2023"
        table.cell(1, 0).text = "Revenue"
        table.cell(1, 1).text = "120,810"
        table.cell(1, 2).text = "106,820"
        table.cell(1, 3).text = "95,421"
        table.cell(2, 0).text = "Intelligent Cloud"
        table.cell(2, 1).text = "Intelligent Cloud"
        table.cell(2, 2).text = "Intelligent Cloud"
        table.cell(2, 3).text = "Intelligent Cloud"
        table.cell(3, 0).text = "Revenue"
        table.cell(3, 1).text = "106,265"
        table.cell(3, 2).text = "87,464"
        table.cell(3, 3).text = "60,123"
        table.cell(4, 0).text = "Operating income"
        table.cell(4, 1).text = "44,589"
        table.cell(4, 2).text = "37,813"
        table.cell(4, 3).text = "31,002"

        path = self.base / "repeated-section-label.docx"
        doc.save(str(path))

        tables, issues, meta = self.service._extract_docx_table_candidates(path, filename="repeated-section-label.docx")
        self.assertFalse(issues)
        self.assertEqual(meta.get("table_count"), 1)

        payload = tables[0]
        self.assertEqual(payload.rows[2].metadata.get("row_type"), "section_header")
        self.assertEqual([cell.raw_text for cell in payload.rows[2].cells], ["Intelligent Cloud", "", "", ""])

    def test_column_misalignment_uses_full_header_band(self) -> None:
        table = TablePayload(
            order_index=1,
            title="Financial Table",
            section_heading="Financial Table",
            page_number=1,
            column_schema=["metric", "2025", "2025_value"],
            data_dictionary={},
            metadata={"detected_via": "docx:table_xml"},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    raw_text="(In millions)\t\t",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=0, column_index=0, column_key="metric", raw_text="(In millions)"),
                        TableCellPayload(row_index=0, column_index=1, column_key="2025", raw_text=""),
                        TableCellPayload(row_index=0, column_index=2, column_key="2025_value", raw_text=""),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    raw_text="Year Ended June 30,\t2025\t2025",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=1, column_index=0, column_key="metric", raw_text="Year Ended June 30,"),
                        TableCellPayload(row_index=1, column_index=1, column_key="2025", raw_text="2025"),
                        TableCellPayload(row_index=1, column_index=2, column_key="2025_value", raw_text="2025"),
                    ],
                ),
                TableRowPayload(
                    row_index=2,
                    page_number=1,
                    raw_text="Revenue\t$\t281,724",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=2, column_index=0, column_key="metric", raw_text="Revenue"),
                        TableCellPayload(row_index=2, column_index=1, column_key="2025", raw_text="$"),
                        TableCellPayload(row_index=2, column_index=2, column_key="2025_value", raw_text="281,724"),
                    ],
                ),
            ],
        )

        quality = self.service._assess_table_quality(table)
        self.assertFalse(quality["signals"].get("column_misalignment"))

    def test_refines_helper_duplicate_period_columns_in_schema(self) -> None:
        key_grid = [
            [1, 2, 3, 4, 5],
            [6, 7, 8, 9, 10],
            [11, 12, None, 13, 14],
        ]
        text_by_key = {
            1: "Risk Categories",
            2: "Hypothetical Change",
            3: "June 30, 2025",
            4: "June 30, 2025",
            5: "Impact",
            6: "Foreign currency – Revenue",
            7: "10% decrease in foreign exchange rates",
            8: "$",
            9: "(11,596)",
            10: "Earnings",
            11: "Credit",
            12: "100 basis point increase in credit spreads",
            13: "(436)",
            14: "Fair Value",
        }
        column_schema = [
            "risk_categories",
            "hypothetical_change",
            "june_30_2025",
            "june_30_2025",
            "impact",
        ]

        refined = self.service._docx_refine_grouped_column_schema(
            column_schema,
            key_grid,
            text_by_key,
            [0],
        )

        self.assertEqual(
            refined,
            ["risk_categories", "hypothetical_change", "helper_june_30_2025", "june_30_2025", "impact"],
        )

    def test_row_attributes_merge_helper_period_value_columns(self) -> None:
        row = TableRowPayload(
            row_index=0,
            page_number=1,
            raw_text="Revenue\t$\t120,810",
            metadata={"row_type": "data"},
            cells=[
                TableCellPayload(row_index=0, column_index=0, column_key="metric", raw_text="Revenue"),
                TableCellPayload(row_index=0, column_index=1, column_key="helper_2025", raw_text="$"),
                TableCellPayload(row_index=0, column_index=2, column_key="2025", raw_text="120,810"),
            ],
        )

        attributes = self.service._row_attributes_from_table(row, ["metric", "helper_2025", "2025"])

        self.assertEqual(attributes["metric"], "Revenue")
        self.assertEqual(attributes["2025"], "$120,810")
        self.assertEqual(attributes["helper_2025"], "")

    def test_misalignment_exempts_unlabeled_descriptor_column(self) -> None:
        table = TablePayload(
            order_index=1,
            title="Comparison Table",
            section_heading="Comparison Table",
            page_number=1,
            column_schema=["column_1", "6_20", "6_21"],
            data_dictionary={},
            metadata={"detected_via": "docx:table_xml"},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    raw_text="\t6/20\t6/21",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=0, column_index=0, column_key="column_1", raw_text=""),
                        TableCellPayload(row_index=0, column_index=1, column_key="6_20", raw_text="6/20"),
                        TableCellPayload(row_index=0, column_index=2, column_key="6_21", raw_text="6/21"),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    raw_text="Microsoft Corporation\t100.00\t134.41",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=1, column_index=0, column_key="column_1", raw_text="Microsoft Corporation"),
                        TableCellPayload(row_index=1, column_index=1, column_key="6_20", raw_text="100.00"),
                        TableCellPayload(row_index=1, column_index=2, column_key="6_21", raw_text="134.41"),
                    ],
                ),
                TableRowPayload(
                    row_index=2,
                    page_number=1,
                    raw_text="S&P 500\t100.00\t140.79",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=2, column_index=0, column_key="column_1", raw_text="S&P 500"),
                        TableCellPayload(row_index=2, column_index=1, column_key="6_20", raw_text="100.00"),
                        TableCellPayload(row_index=2, column_index=2, column_key="6_21", raw_text="140.79"),
                    ],
                ),
            ],
        )

        quality = self.service._assess_table_quality(table)
        self.assertFalse(quality["signals"].get("column_misalignment"))

    def test_misalignment_exempts_two_column_metric_value_table(self) -> None:
        table = TablePayload(
            order_index=1,
            title="Unearned Revenue",
            section_heading="Unearned Revenue",
            page_number=1,
            column_schema=["in_millions_year_ended_june_30_2025", "column_2"],
            data_dictionary={},
            metadata={"detected_via": "docx:table_xml"},
            rows=[
                TableRowPayload(
                    row_index=0,
                    page_number=1,
                    raw_text="(In millions)\t",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=0, column_index=0, column_key="in_millions_year_ended_june_30_2025", raw_text="(In millions)"),
                        TableCellPayload(row_index=0, column_index=1, column_key="column_2", raw_text=""),
                    ],
                ),
                TableRowPayload(
                    row_index=1,
                    page_number=1,
                    raw_text="Year Ended June 30, 2025\t",
                    metadata={"row_type": "header"},
                    cells=[
                        TableCellPayload(row_index=1, column_index=0, column_key="in_millions_year_ended_june_30_2025", raw_text="Year Ended June 30, 2025"),
                        TableCellPayload(row_index=1, column_index=1, column_key="column_2", raw_text=""),
                    ],
                ),
                TableRowPayload(
                    row_index=2,
                    page_number=1,
                    raw_text="Balance, beginning of period\t$60,184",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=2, column_index=0, column_key="in_millions_year_ended_june_30_2025", raw_text="Balance, beginning of period"),
                        TableCellPayload(row_index=2, column_index=1, column_key="column_2", raw_text="$60,184"),
                    ],
                ),
                TableRowPayload(
                    row_index=3,
                    page_number=1,
                    raw_text="Deferral of revenue\t186,957",
                    metadata={"row_type": "data"},
                    cells=[
                        TableCellPayload(row_index=3, column_index=0, column_key="in_millions_year_ended_june_30_2025", raw_text="Deferral of revenue"),
                        TableCellPayload(row_index=3, column_index=1, column_key="column_2", raw_text="186,957"),
                    ],
                ),
            ],
        )

        quality = self.service._assess_table_quality(table)
        self.assertFalse(quality["signals"].get("column_misalignment"))
