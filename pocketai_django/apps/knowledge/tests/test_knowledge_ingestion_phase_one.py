from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from apps.accounts.models import (
    BusinessProfile,
    KnowledgeBlockType,
    KnowledgeSourceType,
    KnowledgeStatus,
    RegistrationSession,
    User,
)
from apps.knowledge.models import (
    KnowledgeAlias,
    KnowledgeEntity,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadFile,
    KnowledgeUploadIssue,
    KnowledgeUploadTable,
    KnowledgeUploadTableRow,
    KnowledgeUploadText,
)
from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    PageBlockPayload,
    PageLayout,
    TablePayload,
    TableRowPayload,
)
from core.tenancy import tenant_context

try:
    from openpyxl import Workbook
except ImportError:
    Workbook = None


class KnowledgeIngestionAliasTests(TestCase):
    def test_collect_aliases_detects_short_identifiers(self):
        aliases, sources = KnowledgeIngestionService._collect_aliases_from_record(
            record={"slug": "trip-1"},
            flattened={"slug": "trip-1", "code": "A-9"},
            attributes={"slug": "trip-1"},
            entity_name="Trip 1",
        )
        self.assertIn("trip-1", aliases)
        self.assertTrue(any(source.startswith("record_slug") for source in sources))


class KnowledgeIngestionChunkingTests(SimpleTestCase):
    def test_chunk_text_overlap_does_not_split_words(self) -> None:
        # Regression: overlap should not start mid-token (e.g., "Free" -> "ee"), which creates
        # noisy fragments that pollute retrieval for tabular PDFs.
        text = "AAAAA Free\nBBBBB\nCCCCC"
        segments = KnowledgeIngestionService._chunk_text(text, chunk_chars=12, overlap=2)
        self.assertGreaterEqual(len(segments), 2)
        first_line = segments[1].splitlines()[0]
        self.assertNotEqual(first_line, "ee")
        self.assertTrue(segments[1].startswith("Free") or segments[1].startswith("BBBBB"))

    def test_numeric_signal_detection_is_domain_agnostic(self) -> None:
        self.assertTrue(
            KnowledgeIngestionService._has_numeric_table_signal("Latency P95 320 ms P99 870 ms")
        )
        self.assertTrue(
            KnowledgeIngestionService._has_numeric_table_signal("Seat capacity 120 units")
        )
        self.assertTrue(
            KnowledgeIngestionService._has_numeric_table_signal("Window 2026-02-08 14:30")
        )
        self.assertFalse(
            KnowledgeIngestionService._has_numeric_table_signal("General policy overview and notes")
        )

    def test_table_row_chunks_emit_applies_to_contract(self) -> None:
        class _Manager:
            def __init__(self, items):
                self._items = list(items)

            def all(self):
                return list(self._items)

        class _Cell:
            def __init__(self, cell_id: str, column_index: int, raw_text: str, column_key: str = ""):
                self.id = cell_id
                self.column_index = column_index
                self.column_key = column_key
                self.raw_text = raw_text

        class _Row:
            def __init__(self):
                self.row_index = 3
                self.metadata = {
                    "row_type": "data",
                    "applies_to_columns": ["Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"],
                    "applicability_mode": "inferred_sparse_expansion",
                    "applicability_confidence": 0.72,
                    "applicability_value": "1% (Min USD 2)",
                }
                self.cells = _Manager(
                    [
                        _Cell("c-1", 0, "Traveler cheques (sell) in foreign currency"),
                        _Cell("c-2", 4, "1% (Min USD 2)", "wealth"),
                    ]
                )

        class _Table:
            def __init__(self):
                self.title = "CIB Teller Fees"
                self.order_index = 1
                self.section_heading = "Over the counter"
                self.rows = _Manager([_Row()])

        service = KnowledgeIngestionService(enable_ocr=False)
        payloads = service._table_row_chunk_payloads(
            table=_Table(),
            column_map=[
                ("Service", "service", 0),
                ("Tariff", "tariff", 1),
                ("Prime", "prime", 2),
                ("Plus", "plus", 3),
                ("Wealth", "wealth", 4),
                ("Exclusive Wealth", "exclusive_wealth", 5),
                ("Private", "private", 6),
            ],
            raw_schema=["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "table-1"},
            max_rows=10,
        )

        self.assertEqual(len(payloads), 1)
        payload = payloads[0]
        text = str(payload.get("text") or "")
        metadata = payload.get("metadata") or {}

        self.assertIn("[Applies To] Prime, Plus, Wealth, Exclusive Wealth, Private", text)
        self.assertEqual(
            metadata.get("table_row_applies_to_columns"),
            ["Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"],
        )
        self.assertEqual(metadata.get("table_row_applicability_mode"), "inferred_sparse_expansion")
        self.assertEqual(metadata.get("table_row_fee_value"), "1% (Min USD 2)")

    def test_table_row_chunks_derive_scope_with_structural_context_columns(self) -> None:
        class _Manager:
            def __init__(self, items):
                self._items = list(items)

            def all(self):
                return list(self._items)

        class _Cell:
            def __init__(self, cell_id: str, column_index: int, raw_text: str, column_key: str = ""):
                self.id = cell_id
                self.column_index = column_index
                self.raw_text = raw_text
                self.column_key = column_key

        class _Row:
            def __init__(self, row_index: int, metadata: dict[str, object], cells: list[_Cell]):
                self.row_index = row_index
                self.metadata = metadata
                self.cells = _Manager(cells)

        class _Table:
            def __init__(self, rows: list[_Row]):
                self.title = "Generic matrix"
                self.order_index = 1
                self.section_heading = ""
                self.rows = _Manager(rows)

        header = _Row(
            0,
            {"row_type": "header"},
            [
                _Cell("h-1", 0, "Descriptor A"),
                _Cell("h-2", 1, "Descriptor B"),
                _Cell("h-3", 2, "Band 1"),
                _Cell("h-4", 3, "Band 2"),
                _Cell("h-5", 4, "Band 3"),
            ],
        )
        row_one = _Row(
            1,
            {"row_type": "data"},
            [
                _Cell("r1-1", 0, "Route settlement fees"),
                _Cell("r1-2", 1, "Category for local currency payouts"),
                _Cell("r1-3", 2, "1.5%"),
                _Cell("r1-4", 3, "1.5%"),
                _Cell("r1-5", 4, "1.5%"),
            ],
        )
        row_two = _Row(
            2,
            {"row_type": "data"},
            [
                _Cell("r2-1", 0, "Remote processing surcharge"),
                _Cell("r2-2", 1, "Category for foreign currency payouts"),
                _Cell("r2-4", 3, "2.0%"),
            ],
        )

        service = KnowledgeIngestionService(enable_ocr=False)
        payloads = service._table_row_chunk_payloads(
            table=_Table([header, row_one, row_two]),
            column_map=[
                ("Descriptor A", "descriptor_a", 0),
                ("Descriptor B", "descriptor_b", 1),
                ("Band 1", "band_1", 2),
                ("Band 2", "band_2", 3),
                ("Band 3", "band_3", 4),
            ],
            raw_schema=["descriptor_a", "descriptor_b", "band_1", "band_2", "band_3"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "table-2"},
            max_rows=10,
        )

        self.assertEqual(len(payloads), 2)
        row_two_payload = next(
            payload for payload in payloads if (payload.get("metadata") or {}).get("table_row_index") == 2
        )
        applies_to = (row_two_payload.get("metadata") or {}).get("table_row_applies_to_columns") or []
        self.assertIn("Band 2", applies_to)
        self.assertNotIn("Descriptor A", applies_to)
        self.assertNotIn("Descriptor B", applies_to)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_pdf_table_overlap_blocks_become_residual_segments_for_coverage(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        page = PageLayout(
            page_number=1,
            width=600.0,
            height=800.0,
            rotation=0,
            text_density=0.1,
            has_ocr_content=False,
            content_type="application/pdf",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text="Annual fee EGP 40",
                    bbox={"x0": 100.0, "y0": 100.0, "x1": 300.0, "y1": 140.0},
                    metadata={},
                ),
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=1,
                    text="General terms apply outside the fee table.",
                    bbox={"x0": 20.0, "y0": 720.0, "x1": 280.0, "y1": 760.0},
                    metadata={},
                ),
            ],
            metadata={},
        )
        table = TablePayload(
            order_index=1,
            title="Fees",
            section_heading="",
            page_number=1,
            bbox={"x0": 90.0, "y0": 90.0, "x1": 560.0, "y1": 610.0},
        )

        annotated_pages, diagnostics = service._annotate_pdf_blocks_with_table_overlap([page], [table])

        self.assertEqual(diagnostics.get("suppressed_text_blocks"), 1)
        self.assertEqual(diagnostics.get("overlapping_text_blocks"), 1)
        first_meta = annotated_pages[0].blocks[0].metadata
        second_meta = annotated_pages[0].blocks[1].metadata
        self.assertTrue(first_meta.get("table_overlap_candidate"))
        self.assertEqual(first_meta.get("table_overlap_candidate_reason"), "table_overlap")
        self.assertFalse(first_meta.get("suppress_text_chunk", False))
        self.assertFalse(second_meta.get("suppress_text_chunk", False))

        segments = service._build_text_segments_from_blocks(annotated_pages)
        combined = "\n".join(str(segment.get("text") or "") for segment in segments)
        self.assertIn("Annual fee EGP 40", combined)
        self.assertIn("General terms apply", combined)
        residual_segments = [seg for seg in segments if (seg.get("metadata") or {}).get("table_residual")]
        self.assertTrue(residual_segments)
        residual_text = "\n".join(str(segment.get("text") or "") for segment in residual_segments)
        self.assertIn("Annual fee EGP 40", residual_text)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_pdf_non_overlapping_blocks_remain_chunked(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        page = PageLayout(
            page_number=1,
            width=600.0,
            height=800.0,
            rotation=0,
            text_density=0.1,
            has_ocr_content=False,
            content_type="application/pdf",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text="Frequently asked questions for branch services.",
                    bbox={"x0": 25.0, "y0": 680.0, "x1": 420.0, "y1": 740.0},
                    metadata={},
                ),
            ],
            metadata={},
        )
        table = TablePayload(
            order_index=1,
            title="Fees",
            section_heading="",
            page_number=1,
            bbox={"x0": 90.0, "y0": 90.0, "x1": 560.0, "y1": 610.0},
        )

        annotated_pages, diagnostics = service._annotate_pdf_blocks_with_table_overlap([page], [table])
        self.assertEqual(diagnostics.get("suppressed_text_blocks"), 0)
        self.assertFalse(annotated_pages[0].blocks[0].metadata.get("suppress_text_chunk", False))

        segments = service._build_text_segments_from_blocks(annotated_pages)
        self.assertTrue(segments)
        self.assertIn("Frequently asked questions", segments[0].get("text") or "")

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_pdf_table_regions_merge_fragmented_boxes_without_merging_far_tables(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        page = PageLayout(
            page_number=1,
            width=600.0,
            height=800.0,
            rotation=0,
            text_density=0.1,
            has_ocr_content=False,
            content_type="application/pdf",
            blocks=[],
            metadata={},
        )
        tables = [
            TablePayload(
                order_index=1,
                title="Fees (fragment 1)",
                section_heading="",
                page_number=1,
                bbox={"x0": 48.0, "y0": 100.0, "x1": 560.0, "y1": 220.0},
            ),
            TablePayload(
                order_index=2,
                title="Fees (fragment 2)",
                section_heading="",
                page_number=1,
                bbox={"x0": 52.0, "y0": 224.0, "x1": 558.0, "y1": 360.0},
            ),
            TablePayload(
                order_index=3,
                title="Separate mini table",
                section_heading="",
                page_number=1,
                bbox={"x0": 60.0, "y0": 620.0, "x1": 260.0, "y1": 700.0},
            ),
        ]

        regions = service._table_regions_by_page(tables, pages=[page]).get(1, [])
        self.assertEqual(len(regions), 2)
        merged_top = regions[0]
        self.assertLessEqual(float(merged_top["x0"]), 48.0)
        self.assertGreaterEqual(float(merged_top["x1"]), 558.0)
        self.assertLessEqual(float(merged_top["y0"]), 100.0)
        self.assertGreaterEqual(float(merged_top["y1"]), 360.0)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_table_residual_blocks_are_tagged_as_fallback_segments(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        page = PageLayout(
            page_number=1,
            width=600.0,
            height=800.0,
            rotation=0,
            text_density=0.1,
            has_ocr_content=False,
            content_type="application/pdf",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text="Cash deposit with same day value date (T+3 customers) 0,3% minimum EGP 100",
                    # 2px below the table region to exercise near-region residual tagging.
                    bbox={"x0": 90.0, "y0": 612.0, "x1": 560.0, "y1": 640.0},
                    metadata={},
                ),
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=1,
                    text="General branch notes and disclaimers for customers.",
                    bbox={"x0": 40.0, "y0": 700.0, "x1": 460.0, "y1": 740.0},
                    metadata={},
                ),
            ],
            metadata={},
        )
        table = TablePayload(
            order_index=1,
            title="Fees",
            section_heading="",
            page_number=1,
            bbox={"x0": 80.0, "y0": 80.0, "x1": 570.0, "y1": 610.0},
        )

        annotated_pages, diagnostics = service._annotate_pdf_blocks_with_table_overlap([page], [table])
        self.assertEqual(diagnostics.get("table_residual_blocks"), 1)

        residual_meta = annotated_pages[0].blocks[0].metadata
        generic_meta = annotated_pages[0].blocks[1].metadata
        self.assertTrue(residual_meta.get("table_residual_candidate"))
        self.assertEqual(residual_meta.get("search_tier"), "fallback")
        self.assertFalse(residual_meta.get("suppress_text_chunk", False))
        self.assertFalse(generic_meta.get("table_residual_candidate", False))

        segments = service._build_text_segments_from_blocks(annotated_pages)
        residual_segments = [seg for seg in segments if (seg.get("metadata") or {}).get("table_residual")]
        self.assertTrue(residual_segments)
        self.assertEqual((residual_segments[0].get("metadata") or {}).get("search_tier"), "fallback")
        self.assertEqual((residual_segments[0].get("metadata") or {}).get("content_source"), "table_residual")

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_overlap_candidates_emit_residual_segments_per_block(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        page = PageLayout(
            page_number=1,
            width=600.0,
            height=800.0,
            rotation=0,
            text_density=0.1,
            has_ocr_content=False,
            content_type="application/pdf",
            blocks=[
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=0,
                    text="Cash deposit with same day value date 0,2% minimum EGP 100.",
                    bbox={"x0": 120.0, "y0": 120.0, "x1": 500.0, "y1": 150.0},
                    metadata={},
                ),
                PageBlockPayload(
                    block_type=KnowledgeBlockType.PARAGRAPH,
                    order_index=1,
                    text="In case of weekend submissions, one extra working day applies.",
                    bbox={"x0": 120.0, "y0": 170.0, "x1": 560.0, "y1": 210.0},
                    metadata={},
                ),
            ],
            metadata={},
        )
        table = TablePayload(
            order_index=1,
            title="Fees",
            section_heading="",
            page_number=1,
            bbox={"x0": 90.0, "y0": 90.0, "x1": 570.0, "y1": 610.0},
        )

        annotated_pages, _ = service._annotate_pdf_blocks_with_table_overlap([page], [table])
        segments = service._build_text_segments_from_blocks(annotated_pages)
        residual_segments = [seg for seg in segments if (seg.get("metadata") or {}).get("table_residual")]

        self.assertEqual(len(residual_segments), 2)
        self.assertTrue(
            all((seg.get("metadata") or {}).get("table_residual_granularity") == "block" for seg in residual_segments)
        )
        rendered = "\n".join(seg.get("text") or "" for seg in residual_segments)
        self.assertIn("same day value date", rendered)
        self.assertIn("extra working day applies", rendered)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_note_like_residual_is_not_dropped_by_row_subset_overlap(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        segment_payloads = [
            {
                "text": "[Table] Fees\n[Row] 18\nService: Cash deposit with same day value date\nTariff: 0,2% (With minimum EGP 100 or Equivalent and with no maximum)",
                "metadata": {
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_page_number": 1,
                    "table_row_index": 18,
                },
            },
            {
                "text": "In case cash deposits are made after 2:00 pm, Saturdays or public holidays, an extra working day will be counted to the applied value date according to account currency.",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "page_numbers": [1],
                    "table_overlap_ratio_max": 0.88,
                },
            },
        ]

        reconciled, stats = service._reconcile_table_residual_segments(segment_payloads)
        residual_reconciled = [
            payload for payload in reconciled if (payload.get("metadata") or {}).get("table_residual")
        ]

        self.assertEqual(stats.get("input_residual_segments"), 1)
        self.assertEqual(stats.get("dropped_equivalent_segments"), 0)
        self.assertEqual(stats.get("kept_residual_segments"), 1)
        self.assertEqual(len(residual_reconciled), 1)
        residual_meta = residual_reconciled[0].get("metadata") or {}
        self.assertEqual(residual_meta.get("table_residual_segment_kind"), "note_like")
        self.assertTrue(residual_meta.get("table_residual_equivalence_checked"))

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_cell_like_residual_segments_are_dropped(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        segment_payloads = [
            {
                "text": "[Table] Fees\n[Row] 1\nService: Cash Withdrawal/Deposit over the counter\nPrime: EGP 40",
                "metadata": {
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_page_number": 1,
                    "table_row_index": 1,
                },
            },
            {
                "text": "Free",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "page_numbers": [1],
                    "table_overlap_ratio_max": 0.9,
                },
            },
        ]

        reconciled, stats = service._reconcile_table_residual_segments(segment_payloads)
        residual_reconciled = [
            payload for payload in reconciled if (payload.get("metadata") or {}).get("table_residual")
        ]

        self.assertEqual(stats.get("input_residual_segments"), 1)
        self.assertEqual(stats.get("dropped_equivalent_segments"), 1)
        self.assertEqual(stats.get("dropped_equivalent_cell_like_segments"), 1)
        self.assertEqual(stats.get("kept_residual_segments"), 0)
        self.assertFalse(residual_reconciled)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_table_residual_reconciliation_dedupes_equivalent_rows_and_preserves_unique_segments(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        segment_payloads = [
            {
                "text": "[Table] Fees\n[Row] 1\nService: Cash deposit with same day value date\nTariff: 0,3% minimum EGP 100",
                "metadata": {
                    "is_table_chunk": True,
                    "index_type": "table",
                    "content_source": "table_row",
                    "table_page_number": 1,
                    "table_row_index": 1,
                },
            },
            {
                "text": "Cash deposit with same day value date Tariff 0,3% minimum EGP 100",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "page_numbers": [1],
                    "table_overlap_ratio_max": 0.18,
                },
            },
            {
                "text": "Cash deposit T+3 customers 0,3% minimum EGP 100 equivalent no max",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "page_numbers": [1],
                    "table_overlap_ratio_max": 0.11,
                },
            },
            {
                "text": "Cash deposit T+5 customers 0,5% minimum EGP 100 equivalent no max",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "page_numbers": [1],
                    "table_overlap_ratio_max": 0.27,
                },
            },
            {
                "text": "Cost per Bag (EGP 70,000) Fee EGP 14",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r1",
                    "page_numbers": [1],
                    "table_overlap_ratio_max": 0.22,
                },
            },
        ]

        reconciled, stats = service._reconcile_table_residual_segments(segment_payloads)
        residual_reconciled = [
            payload for payload in reconciled if (payload.get("metadata") or {}).get("table_residual")
        ]

        self.assertEqual(stats.get("input_residual_segments"), 4)
        self.assertEqual(stats.get("dropped_equivalent_segments"), 1)
        self.assertEqual(stats.get("dropped_cap_segments"), 0)
        self.assertEqual(stats.get("kept_residual_segments"), 3)
        self.assertEqual(len(residual_reconciled), 3)
        region_keys = {(payload.get("metadata") or {}).get("table_residual_region_key") for payload in residual_reconciled}
        self.assertEqual(region_keys, {"p1-r0", "p1-r1"})

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_canonicalize_payloads_projects_table_residuals_to_annotations(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        upload = SimpleNamespace(id=uuid.uuid4())
        segment_payloads = [
            {
                "text": "[Table] Fees\n[Columns] Service | Fee\n[Rows] 2 data rows",
                "metadata": {
                    "index_type": "table",
                    "is_table_chunk": True,
                    "content_source": "table_summary",
                    "table_chunk_role": "summary",
                    "table_id": "table-1",
                    "table_title": "Fees",
                    "table_order_index": 1,
                    "table_page_number": 1,
                    "search_tier": "primary",
                },
            },
            {
                "text": "[Table] Fees\n[Row] 1\nService: Cash deposit\nFee: EGP 14",
                "metadata": {
                    "index_type": "table",
                    "is_table_chunk": True,
                    "content_source": "table_row",
                    "table_chunk_role": "row",
                    "table_id": "table-1",
                    "table_row_index": 1,
                    "table_order_index": 1,
                    "table_page_number": 1,
                    "search_tier": "drill_down",
                },
            },
            {
                "text": "In case of cash deposits made after 2:00 pm, one extra working day applies.",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "table_overlap_ratio_max": 0.82,
                    "page_numbers": [1],
                },
            },
        ]

        canonicalized, stats = service._canonicalize_segment_payloads(
            upload=upload,
            segment_payloads=segment_payloads,
        )
        content_sources = [(payload.get("metadata") or {}).get("content_source") for payload in canonicalized]
        self.assertNotIn("table_residual", content_sources)
        self.assertIn("table_annotation", content_sources)

        annotation_payload = next(
            payload
            for payload in canonicalized
            if (payload.get("metadata") or {}).get("content_source") == "table_annotation"
        )
        annotation_meta = annotation_payload.get("metadata") or {}
        self.assertEqual(annotation_meta.get("canonical_chunk_kind"), "table_annotation")
        self.assertEqual(annotation_meta.get("table_id"), "table-1")
        self.assertEqual(annotation_meta.get("canonical_parent_anchor_id"), "table:table-1:summary")
        self.assertTrue(str(annotation_meta.get("canonical_anchor_id") or "").startswith("table:table-1:annotation:"))

        self.assertEqual((stats.get("kind_counts") or {}).get("table_annotation"), 1)
        projection_stats = stats.get("table_residual_projection") or {}
        self.assertEqual(projection_stats.get("input_residual_segments"), 1)
        self.assertEqual(projection_stats.get("table_annotation_chunks_created"), 1)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_canonicalize_payloads_assigns_narrative_anchor(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        upload = SimpleNamespace(id=uuid.uuid4())
        segment_payloads = [
            {
                "text": "General branch policy for customer service windows.",
                "metadata": {
                    "index_type": "text",
                    "content_source": "page_blocks",
                    "region_role": "text",
                    "page_numbers": [2],
                    "block_anchors": ["p2-b4"],
                },
            }
        ]

        canonicalized, _ = service._canonicalize_segment_payloads(
            upload=upload,
            segment_payloads=segment_payloads,
        )
        self.assertEqual(len(canonicalized), 1)
        metadata = canonicalized[0].get("metadata") or {}
        self.assertEqual(metadata.get("canonical_chunk_kind"), "narrative_paragraph")
        self.assertEqual(metadata.get("coverage_reason"), "layout_paragraph")
        self.assertTrue(str(metadata.get("canonical_anchor_id") or "").startswith("page:2:paragraph:"))

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_heading_like_table_residual_is_promoted_to_narrative(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        upload = SimpleNamespace(id=uuid.uuid4())
        segment_payloads = [
            {
                "text": "[Table] CIB-Teller EN – Table 1\n[Columns] Service | Tariff | Prime",
                "metadata": {
                    "index_type": "table",
                    "is_table_chunk": True,
                    "content_source": "table_summary",
                    "table_chunk_role": "summary",
                    "table_id": "table-main",
                    "table_title": "CIB-Teller EN – Table 1",
                    "table_order_index": 1,
                    "table_page_number": 1,
                },
            },
            {
                "text": "Over the counter in branch Fees & Charges",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "table_overlap_ratio_max": 0.91,
                    "page_numbers": [1],
                },
            },
        ]

        canonicalized, stats = service._canonicalize_segment_payloads(
            upload=upload,
            segment_payloads=segment_payloads,
        )
        narrative_payload = next(
            payload
            for payload in canonicalized
            if (payload.get("metadata") or {}).get("content_source") == "page_blocks"
        )
        narrative_meta = narrative_payload.get("metadata") or {}
        self.assertEqual(narrative_meta.get("canonical_chunk_kind"), "narrative_paragraph")
        self.assertEqual(narrative_meta.get("table_residual_projected"), "narrative")
        self.assertEqual(
            (stats.get("table_residual_projection") or {}).get("narrative_promoted_segments"),
            1,
        )

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    @override_settings(
        RAG_TABLE_ANNOTATION_MAX_CHARS=80,
        RAG_TABLE_ANNOTATION_MAX_PER_TABLE=1,
    )
    def test_annotation_projection_preserves_overflow_chunks_beyond_soft_limit(self, _build_embeddings) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        upload = SimpleNamespace(id=uuid.uuid4())
        segment_payloads = [
            {
                "text": "[Table] Fees\n[Columns] Service | Tariff",
                "metadata": {
                    "index_type": "table",
                    "is_table_chunk": True,
                    "content_source": "table_summary",
                    "table_chunk_role": "summary",
                    "table_id": "table-overflow",
                    "table_title": "Fees",
                    "table_order_index": 1,
                    "table_page_number": 1,
                },
            },
            {
                "text": "*In case cash deposits are made after 2:00 pm, one extra working day applies for value date.",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "table_overlap_ratio_max": 0.88,
                    "page_numbers": [1],
                },
            },
            {
                "text": "*For public holidays, settlement timing shifts according to account currency and cut-off windows.",
                "metadata": {
                    "index_type": "text",
                    "content_source": "table_residual",
                    "region_role": "table_residual",
                    "table_residual": True,
                    "table_residual_region_key": "p1-r0",
                    "table_overlap_ratio_max": 0.86,
                    "page_numbers": [1],
                },
            },
        ]

        canonicalized, stats = service._canonicalize_segment_payloads(
            upload=upload,
            segment_payloads=segment_payloads,
        )
        annotation_chunks = [
            payload
            for payload in canonicalized
            if (payload.get("metadata") or {}).get("content_source") == "table_annotation"
        ]
        self.assertGreaterEqual(len(annotation_chunks), 2)
        self.assertTrue(
            all((payload.get("metadata") or {}).get("table_annotation_soft_limit_exceeded") for payload in annotation_chunks)
        )
        projection_stats = stats.get("table_residual_projection") or {}
        self.assertEqual(projection_stats.get("soft_limit_exceeded_tables"), 1)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_auto_selection_overrides_fragmented_heuristic_when_non_heuristic_is_viable(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)

        heuristic_tables = [
            TablePayload(
                order_index=i + 1,
                title="EGP",
                section_heading="",
                page_number=1,
                bbox={"x0": 70.0 + (i * 5.0), "y0": 620.0 + (i * 40.0), "x1": 380.0, "y1": 650.0 + (i * 40.0)},
                rows=[
                    TableRowPayload(
                        row_index=1,
                        page_number=1,
                        raw_text=f"row-{i + 1}",
                        metadata={"row_type": "data"},
                    )
                ],
                metadata={"detected_via": "heuristic"},
            )
            for i in range(4)
        ]
        geometry_table = TablePayload(
            order_index=1,
            title="Over the counter in branch Fees & Charges",
            section_heading="",
            page_number=1,
            bbox={"x0": 0.0, "y0": 0.0, "x1": 1196.0, "y1": 989.0},
            rows=[
                TableRowPayload(
                    row_index=i + 1,
                    page_number=1,
                    raw_text=f"data-row-{i + 1}",
                    metadata={"row_type": "data"},
                )
                for i in range(19)
            ],
            metadata={"detected_via": "geometry"},
        )
        candidates = {
            "heuristic": heuristic_tables,
            "geometry": [geometry_table],
        }

        selected, tables, diag = service._select_table_candidates(candidates)

        self.assertEqual(selected, "geometry")
        self.assertEqual(len(tables), 1)
        self.assertTrue(diag.get("heuristic_override_applied"))
        self.assertEqual(diag.get("heuristic_override_from"), "heuristic")
        self.assertEqual(diag.get("heuristic_override_to"), "geometry")

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_auto_selection_keeps_heuristic_for_normal_multi_table_layout(
        self,
        _build_embeddings,
    ) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)

        heuristic_tables = []
        for i, title in enumerate(["Retail Fees", "Corporate Fees", "Loan Fees"]):
            heuristic_tables.append(
                TablePayload(
                    order_index=i + 1,
                    title=title,
                    section_heading="",
                    page_number=1,
                    bbox={
                        "x0": 40.0,
                        "y0": 80.0 + (i * 180.0),
                        "x1": 560.0,
                        "y1": 220.0 + (i * 180.0),
                    },
                    rows=[
                        TableRowPayload(
                            row_index=row_index + 1,
                            page_number=1,
                            raw_text=f"{title} row {row_index + 1}",
                            metadata={"row_type": "data"},
                        )
                        for row_index in range(3)
                    ],
                    metadata={"detected_via": "heuristic"},
                )
            )

        geometry_table = TablePayload(
            order_index=1,
            title="Document snapshot",
            section_heading="",
            page_number=1,
            bbox={"x0": 60.0, "y0": 60.0, "x1": 360.0, "y1": 180.0},
            rows=[
                TableRowPayload(
                    row_index=row_index + 1,
                    page_number=1,
                    raw_text=f"snapshot row {row_index + 1}",
                    metadata={"row_type": "data"},
                )
                for row_index in range(4)
            ],
            metadata={"detected_via": "geometry"},
        )

        candidates = {
            "heuristic": heuristic_tables,
            "geometry": [geometry_table],
        }
        selected, _, diag = service._select_table_candidates(candidates)

        self.assertEqual(selected, "heuristic")
        self.assertFalse(diag.get("heuristic_override_applied"))


class KnowledgeIngestionJsonTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root)
        self.override = override_settings(MEDIA_ROOT=self._media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create(email="ingest@example.com", first_name="Test")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Travel Co",
            industry="travel",
            metadata={"ingest_max_json_entities": 2},
        )

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_json_ingestion_persists_entities_and_aliases(self, _build_embeddings):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Trips",
        )
        storage_path = Path("uploads/sample.json")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "trips": [
                {"slug": "trip-1", "name": "Trip One", "code": "TR-ONE"},
                {"slug": "trip-2", "name": "Trip Two", "code": "TR-TWO"},
                {"slug": "trip-3", "name": "Trip Three", "code": "TR-THREE"},
            ]
        }
        target_path.write_text(json.dumps(payload), encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="sample.json",
            storage_path=str(storage_path),
            content_type="application/json",
            size_bytes=target_path.stat().st_size,
        )

        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)

        upload.refresh_from_db()
        self.assertEqual(KnowledgeEntity.objects.filter(upload=upload).count(), 2)
        alias_count = KnowledgeAlias.objects.filter(entity__upload=upload).count()
        self.assertGreater(alias_count, 0)
        self.assertEqual(upload.ingestion_metadata.get("alias_count"), alias_count)
        self.assertEqual(upload.ingestion_metadata.get("truncated_entities"), 2)


class KnowledgeIngestionTextTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root)
        self.override = override_settings(MEDIA_ROOT=self._media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create(email="text@example.com", first_name="Text")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Docs Co",
            industry="support",
        )

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_text_ingestion_persists_chunks(self, _build_embeddings) -> None:
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.TEXT,
            status=KnowledgeStatus.PENDING,
            display_name="Manual snippet",
        )
        KnowledgeUploadText.objects.create(
            upload=upload,
            content="Refund policy:\n- Refunds are allowed within 14 days of purchase.\n",
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        with tenant_context(self.business.id):
            extraction = service._extract_upload(upload)
            self.assertEqual(extraction.format_hint, "text")
            service._persist_extraction(upload, extraction)

            upload.refresh_from_db()
            self.assertEqual(upload.status, KnowledgeStatus.ACTIVE)
            self.assertGreater(int(upload.chunk_count or 0), 0)
            self.assertTrue(KnowledgeUploadChunk.objects.filter(upload=upload).exists())
            self.assertEqual(upload.ingestion_metadata.get("format"), "text")
            self.assertEqual(upload.ingestion_metadata.get("content_type"), "text/plain")


class KnowledgeIngestionSpreadsheetTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media_root)
        self.override = override_settings(MEDIA_ROOT=self._media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create(email="spreadsheet@example.com", first_name="Sheet")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Data Co",
            industry="finance",
        )

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_csv_ingestion_creates_tables(self, _build_embeddings):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans CSV",
        )
        storage_path = Path("uploads/plans.csv")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("plan,price\nBasic,10\nPro,25\n", encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.csv",
            storage_path=str(storage_path),
            content_type="text/csv",
            size_bytes=target_path.stat().st_size,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)
        upload.refresh_from_db()
        tables = KnowledgeUploadTable.objects.filter(upload=upload)
        self.assertEqual(tables.count(), 1)
        rows = KnowledgeUploadTableRow.objects.filter(table__upload=upload)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(tables.first().column_schema, ["plan", "price"])
        entities = KnowledgeEntity.objects.filter(upload=upload)
        self.assertEqual(entities.count(), 2)
        self.assertTrue(
            KnowledgeAlias.objects.filter(entity__upload=upload, alias_normalized="basic").exists()
        )
        table_stats = upload.ingestion_metadata.get("table_stats") or {}
        self.assertEqual(table_stats.get("total_rows"), 2)
        self.assertEqual(table_stats.get("indexed_rows"), 2)
        self.assertEqual(table_stats.get("row_cap"), 2)
        self.assertEqual(table_stats.get("row_tier"), "small")
        self.assertFalse(table_stats.get("partial_index"))
        self.assertFalse(KnowledgeUploadIssue.objects.filter(upload=upload).exists())

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_reingest_from_persisted_artifacts_rebuilds_canonical_chunk_metadata(self, _build_embeddings):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans CSV",
        )
        storage_path = Path("uploads/plans.csv")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("plan,price\nBasic,10\nPro,25\n", encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.csv",
            storage_path=str(storage_path),
            content_type="text/csv",
            size_bytes=target_path.stat().st_size,
        )

        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        with tenant_context(self.business.id):
            extraction = service._extract_upload(upload)
            service._persist_extraction(upload, extraction)
            service.reingest_from_persisted_artifacts(upload)
            upload.refresh_from_db()
            chunks = list(KnowledgeUploadChunk.objects.filter(upload=upload).order_by("chunk_index"))
        self.assertEqual(upload.chunk_count, len(chunks))
        self.assertTrue(chunks)
        self.assertTrue(all((chunk.metadata or {}).get("canonical_anchor_id") for chunk in chunks))
        kinds = {(chunk.metadata or {}).get("canonical_chunk_kind") for chunk in chunks}
        self.assertIn("table_row", kinds)
        self.assertIn("table_summary", kinds)

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_table_entities_persist_when_entity_chunking_disabled(self, _build_embeddings):
        self.business.metadata = {"features": {"entity_chunking": False}}
        self.business.save(update_fields=["metadata"])

        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans CSV",
        )
        storage_path = Path("uploads/plans.csv")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("plan,price\nBasic,10\nPro,25\n", encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.csv",
            storage_path=str(storage_path),
            content_type="text/csv",
            size_bytes=target_path.stat().st_size,
        )

        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)
        upload.refresh_from_db()

        self.assertGreater(KnowledgeEntity.objects.filter(upload=upload).count(), 0)
        self.assertGreater(KnowledgeAlias.objects.filter(entity__upload=upload).count(), 0)

    def test_detect_format_prefers_csv_over_text_content_type(self):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans CSV",
        )
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.csv",
            storage_path="uploads/plans.csv",
            content_type="text/csv",
            size_bytes=123,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        file_detail = upload.file_detail
        self.assertEqual(service._detect_format(file_detail), "csv")

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    @override_settings(
        TABLE_MAX_ROWS_DEFAULT=3,
        RAG_TABLE_SMALL_ROW_LIMIT=2,
        RAG_TABLE_LARGE_ROW_LIMIT=4,
        RAG_TABLE_MAX_HARD_CAP=5,
    )
    def test_large_csv_truncation_emits_issue(self, _build_embeddings):
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Large CSV",
        )
        storage_path = Path("uploads/large.csv")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        rows = ["plan,price"]
        for idx in range(1, 7):
            rows.append(f"Tier{idx},{idx * 10}")
        target_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="large.csv",
            storage_path=str(storage_path),
            content_type="text/csv",
            size_bytes=target_path.stat().st_size,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)
        upload.refresh_from_db()
        self.assertEqual(KnowledgeUploadTableRow.objects.filter(table__upload=upload).count(), 3)
        table_truncation = upload.ingestion_metadata.get("table_truncation") or {}
        self.assertEqual(table_truncation.get("truncated_rows"), 3)
        table_stats = upload.ingestion_metadata.get("table_stats") or {}
        self.assertTrue(table_stats.get("partial_index"))
        self.assertEqual(table_stats.get("row_cap"), 3)
        self.assertTrue(
            KnowledgeUploadIssue.objects.filter(upload=upload, issue_code="table_rows_truncated").exists()
        )
        self.assertEqual(table_stats.get("row_tier"), "large")
        issues = KnowledgeUploadIssue.objects.filter(upload=upload, issue_code="table_rows_truncated")
        self.assertTrue(issues.exists())

    @mock.patch("apps.knowledge.knowledge_ingestion.build_embedding_service", return_value=None)
    def test_xlsx_ingestion_creates_tables(self, _build_embeddings):
        if Workbook is None:
            self.skipTest("openpyxl not installed")
        upload = KnowledgeUpload.objects.create(
            business_profile=self.business,
            user=self.user,
            source_type=KnowledgeSourceType.FILE,
            status=KnowledgeStatus.PENDING,
            display_name="Plans XLSX",
        )
        storage_path = Path("uploads/plans.xlsx")
        target_path = Path(self._media_root) / storage_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Plans"
        sheet.append(["plan", "price"])
        sheet.append(["Starter", 5])
        sheet.append(["Enterprise", 99])
        workbook.save(target_path)
        KnowledgeUploadFile.objects.create(
            upload=upload,
            filename="plans.xlsx",
            storage_path=str(storage_path),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            size_bytes=target_path.stat().st_size,
        )
        service = KnowledgeIngestionService(media_root=Path(self._media_root))
        extraction = service._extract_upload(upload)
        service._persist_extraction(upload, extraction)
        upload.refresh_from_db()
        tables = KnowledgeUploadTable.objects.filter(upload=upload)
        self.assertEqual(tables.count(), 1)
        rows = KnowledgeUploadTableRow.objects.filter(table__upload=upload)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(tables.first().column_schema, ["plan", "price"])
        entities = KnowledgeEntity.objects.filter(upload=upload)
        self.assertEqual(entities.count(), 2)
        self.assertTrue(
            KnowledgeAlias.objects.filter(entity__upload=upload, alias_normalized="starter").exists()
        )
        table_stats = upload.ingestion_metadata.get("table_stats") or {}
        self.assertEqual(table_stats.get("total_rows"), 2)
        self.assertEqual(table_stats.get("indexed_rows"), 2)
        self.assertEqual(table_stats.get("row_cap"), 2)
        self.assertEqual(table_stats.get("row_tier"), "small")
        self.assertFalse(table_stats.get("partial_index"))


class DeriveTableTitleTests(SimpleTestCase):
    """Tests for KnowledgeIngestionService._derive_table_title."""

    @staticmethod
    def _make_table(
        title="",
        section_heading="",
        order_index=1,
        column_schema=None,
    ):
        from apps.knowledge.knowledge_ingestion import TablePayload

        return TablePayload(
            order_index=order_index,
            title=title,
            section_heading=section_heading,
            page_number=None,
            column_schema=column_schema or [],
        )

    @staticmethod
    def _make_upload(display_name="", source_name=""):
        from types import SimpleNamespace

        return SimpleNamespace(display_name=display_name, source_name=source_name)

    def test_non_generic_title_preserved(self):
        """Azure DI caption or real section heading is kept as-is."""
        table = self._make_table(title="Revenue by Region")
        upload = self._make_upload(display_name="report.pdf")
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "Revenue by Region")

    def test_generic_title_with_doc_name_and_section(self):
        table = self._make_table(title="Table 3", section_heading="Q4 Results")
        upload = self._make_upload(display_name="annual_report.pdf")
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "annual_report \u2013 Q4 Results")

    def test_generic_title_with_doc_name_only(self):
        table = self._make_table(title="Table 2", order_index=2)
        upload = self._make_upload(display_name="revenue.csv")
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "revenue \u2013 Table 2")

    def test_generic_title_with_section_only(self):
        table = self._make_table(title="Table 1", section_heading="Appendix A")
        upload = self._make_upload()
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "Appendix A")

    def test_generic_title_with_column_schema(self):
        table = self._make_table(
            title="Table 5",
            order_index=5,
            column_schema=["Name", "Price", "Quantity", "Total", "Tax"],
        )
        upload = self._make_upload()
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "Table 5 (Name, Price, Quantity, Total, ...)")

    def test_final_fallback(self):
        table = self._make_table(title="Table 9", order_index=9)
        upload = self._make_upload()
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "Table 9")

    def test_case_insensitive_generic_detection(self):
        table = self._make_table(title="table 7", order_index=7, section_heading="Sales")
        upload = self._make_upload()
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "Sales")

    def test_source_name_fallback(self):
        table = self._make_table(title="Table 1", order_index=1)
        upload = self._make_upload(source_name="budget_2024.xlsx")
        result = KnowledgeIngestionService._derive_table_title(table, upload)
        self.assertEqual(result, "budget_2024 \u2013 Table 1")
