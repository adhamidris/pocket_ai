from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import DocxDocument, KnowledgeIngestionService


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

