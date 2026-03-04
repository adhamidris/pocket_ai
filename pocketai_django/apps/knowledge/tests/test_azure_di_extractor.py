from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import AzureDocumentIntelligenceExtractor
from apps.knowledge.knowledge_ingestion import KnowledgeIngestionService
from apps.knowledge.knowledge_ingestion import TableCellPayload, TableRowPayload
from apps.knowledge.knowledge_ingestion import TablePayload


def _mock_response(
    *,
    status_code: int,
    payload: dict | None = None,
    headers: dict | None = None,
    text: str = "",
):
    response = mock.Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.text = text
    if payload is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload
    return response


class AzureDocumentIntelligenceExtractorTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        handle.write(b"%PDF-1.4\n% phase-one retry test\n")
        handle.flush()
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        self.path = Path(handle.name)

    @mock.patch("apps.knowledge.knowledge_ingestion.time.sleep")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.get")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.post")
    def test_submit_retries_on_throttle_with_retry_after(
        self,
        mock_post,
        mock_get,
        mock_sleep,
    ) -> None:
        mock_post.side_effect = [
            _mock_response(
                status_code=429,
                headers={"retry-after": "2"},
                text="throttled",
            ),
            _mock_response(
                status_code=202,
                headers={"operation-location": "https://example.test/op/123"},
            ),
        ]
        mock_get.return_value = _mock_response(
            status_code=200,
            payload={"status": "succeeded", "analyzeResult": {"pages": [], "tables": []}},
        )

        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test",
            key="secret",
            request_max_attempts=3,
            poll_request_max_attempts=2,
            retry_backoff_base_seconds=0.1,
            retry_backoff_max_seconds=0.2,
            max_retry_after_seconds=5.0,
            poll_interval_seconds=2.0,
            max_polls=5,
        )

        analyze_result, issues, meta = extractor._analyze_document(self.path)

        self.assertEqual(analyze_result, {"pages": [], "tables": []})
        self.assertEqual(issues, [])
        self.assertEqual(meta.get("status"), "succeeded")
        self.assertEqual(meta.get("request_attempts"), 2)

        retry_events = meta.get("retry_events") or []
        self.assertTrue(retry_events)
        first_retry = retry_events[0]
        self.assertEqual(first_retry.get("phase"), "submit")
        self.assertEqual(first_retry.get("reason"), "submit_http_retry")
        self.assertEqual(first_retry.get("status_code"), 429)
        self.assertGreaterEqual(float(first_retry.get("delay_s") or 0.0), 2.0)
        self.assertTrue(mock_sleep.called)

    @mock.patch("apps.knowledge.knowledge_ingestion.time.sleep")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.get")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.post")
    def test_poll_timeout_is_classified(self, mock_post, mock_get, _mock_sleep) -> None:
        mock_post.return_value = _mock_response(
            status_code=202,
            headers={"operation-location": "https://example.test/op/456"},
        )
        mock_get.side_effect = [
            requests.Timeout("poll timeout #1"),
            requests.Timeout("poll timeout #2"),
        ]

        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test",
            key="secret",
            request_max_attempts=1,
            poll_request_max_attempts=2,
            retry_backoff_base_seconds=0.1,
            retry_backoff_max_seconds=0.2,
            poll_interval_seconds=2.0,
            max_polls=3,
        )

        analyze_result, issues, meta = extractor._analyze_document(self.path)

        self.assertIsNone(analyze_result)
        self.assertTrue(issues)
        self.assertEqual(issues[0].code, "azure_di_poll_failed")
        self.assertEqual(meta.get("status"), "timeout")
        self.assertEqual(meta.get("failure_class"), "timeout")
        self.assertEqual(meta.get("failure_stage"), "poll")
        self.assertEqual(meta.get("failure_reason"), "poll_exception")

    @mock.patch("apps.knowledge.knowledge_ingestion.time.sleep")
    @mock.patch("apps.knowledge.knowledge_ingestion.requests.post")
    def test_submit_hard_failure_is_classified(self, mock_post, mock_sleep) -> None:
        mock_post.return_value = _mock_response(
            status_code=400,
            text="bad request",
            payload={"error": {"code": "InvalidRequest"}},
        )

        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test",
            key="secret",
            request_max_attempts=3,
        )
        analyze_result, issues, meta = extractor._analyze_document(self.path)

        self.assertIsNone(analyze_result)
        self.assertTrue(issues)
        self.assertEqual(issues[0].code, "azure_di_request_error")
        self.assertEqual(meta.get("status"), "failed")
        self.assertEqual(meta.get("failure_class"), "hard_failure")
        self.assertEqual(meta.get("failure_stage"), "submit")
        self.assertEqual(meta.get("failure_reason"), "request_http_error")
        self.assertEqual(meta.get("request_attempts"), 1)
        self.assertFalse(mock_sleep.called)

    def test_extract_tables_normalizes_dict_caption_to_string(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        analyze_result = {
            "pages": [],
            "tables": [
                {
                    "caption": {"content": "Account Fees Table"},
                    "rowCount": 1,
                    "columnCount": 1,
                    "cells": [
                        {
                            "rowIndex": 0,
                            "columnIndex": 0,
                            "content": "Service",
                            "kind": "columnHeader",
                        }
                    ],
                }
            ],
        }

        with mock.patch.object(
            extractor,
            "_analyze_document",
            return_value=(analyze_result, [], {"status": "succeeded"}),
        ):
            tables, issues, meta = extractor.extract_tables(self.path)

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0].title, "Account Fees Table")
        self.assertEqual(issues, [])
        self.assertEqual(meta.get("table_count"), 1)

    def test_extract_tables_does_not_overwrite_value_cells_with_spanning_labels(self) -> None:
        """
        Regression: Azure DI can emit broad-span label cells (e.g. "Annual Fees")
        whose spans overlap value columns. If we write them after the values, we
        lose the numeric/value evidence and the table becomes unreadable.
        """
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        analyze_result = {
            "pages": [],
            "tables": [
                {
                    "caption": "Fees Table",
                    "rowCount": 2,
                    "columnCount": 2,
                    "cells": [
                        # Value cell first (row 1, col 1)
                        {
                            "rowIndex": 1,
                            "columnIndex": 1,
                            "rowSpan": 1,
                            "columnSpan": 1,
                            "content": "EGP 200",
                            "kind": "",
                        },
                        # Spanning label cell later (row 1, spans both columns)
                        {
                            "rowIndex": 1,
                            "columnIndex": 0,
                            "rowSpan": 1,
                            "columnSpan": 2,
                            "content": "Annual Fees",
                            "kind": "",
                        },
                    ],
                }
            ],
        }

        with mock.patch.object(
            extractor,
            "_analyze_document",
            return_value=(analyze_result, [], {"status": "succeeded"}),
        ):
            tables, issues, meta = extractor.extract_tables(self.path)

        self.assertEqual(issues, [])
        self.assertEqual(meta.get("table_count"), 1)
        self.assertEqual(len(tables), 1)
        self.assertEqual(len(tables[0].rows), 2)

        # The per-column value must survive the spanning label write.
        self.assertEqual(tables[0].rows[1].cells[1].raw_text, "EGP 200")

    def test_derive_table_title_handles_mapping_title_without_crashing(self) -> None:
        table_payload = TablePayload(
            order_index=1,
            title={"content": "Bedaya Accounts"},
            section_heading="",
            page_number=1,
        )
        upload = mock.Mock(display_name="CIB-Account-EN.pdf", source_name="CIB-Account-EN.pdf")

        title = KnowledgeIngestionService._derive_table_title(table_payload, upload)
        self.assertEqual(title, "Bedaya Accounts")


class AzureDocumentIntelligenceApplicabilityTests(SimpleTestCase):
    def _make_row(self, row_index: int, values: list[str], *, spans: dict[int, int] | None = None) -> TableRowPayload:
        spans = spans or {}
        cells: list[TableCellPayload] = []
        for col_idx, value in enumerate(values):
            cells.append(
                TableCellPayload(
                    row_index=row_index,
                    column_index=col_idx,
                    column_key=f"column_{col_idx + 1}",
                    raw_text=value,
                    metadata={"column_span": spans.get(col_idx, 1)},
                )
            )
        return TableRowPayload(
            row_index=row_index,
            page_number=1,
            raw_text=" | ".join(values),
            metadata={"row_type": "header" if row_index == 0 else "data"},
            cells=cells,
        )

    def test_explicit_span_is_preserved(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = [
            "descriptor_a",
            "descriptor_b",
            "segment_1",
            "segment_2",
            "segment_3",
            "segment_4",
            "segment_5",
        ]
        rows = [
            self._make_row(0, schema),
            self._make_row(
                1,
                [
                    "Traveler cheques",
                    "FX settlement descriptor",
                    "",
                    "1% (Min USD 2)",
                    "1% (Min USD 2)",
                    "1% (Min USD 2)",
                    "",
                ],
                spans={3: 3, 4: 3, 5: 3},
            ),
            self._make_row(2, ["Blank Cheques", "Retail service descriptor", "", "", "EGP 10", "", ""]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )
        meta = annotated[1].metadata
        self.assertIn(
            meta.get("applicability_mode"),
            {"explicit_cells", "inferred_span_extension"},
        )
        self.assertEqual(
            meta.get("applies_to_columns"),
            ["segment_1", "segment_2", "segment_3", "segment_4", "segment_5"],
        )

    def test_sparse_expansion_infers_multi_column_scope(self) -> None:
        """When >=40% of data rows have exactly one non-empty segment cell,
        each such row should expand to all segment columns (sparse-row pattern)."""
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = [
            "descriptor_a",
            "descriptor_b",
            "segment_1",
            "segment_2",
            "segment_3",
            "segment_4",
            "segment_5",
        ]
        rows = [
            self._make_row(0, schema),
            self._make_row(
                1,
                [
                    "Traveler cheques",
                    "FX settlement descriptor",
                    "",
                    "1% (Min USD 2)",
                    "1% (Min USD 2)",
                    "1% (Min USD 2)",
                    "",
                ],
                spans={3: 3, 4: 3, 5: 3},
            ),
            self._make_row(2, ["Blank Cheques", "Retail service descriptor", "", "", "EGP 10", "", ""]),
            self._make_row(3, ["MCDR", "Coupon settlement descriptor", "", "", "0.5%", "", ""]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )
        meta = annotated[2].metadata
        self.assertEqual(meta.get("applicability_mode"), "inferred_sparse_expansion")
        self.assertEqual(
            meta.get("applies_to_columns"),
            ["segment_1", "segment_2", "segment_3", "segment_4", "segment_5"],
        )

    def test_scattered_placement_triggers_sparse_expansion(self) -> None:
        """When Azure DI scatters single values across different columns
        (no dominant column), sparse-row expansion should still fire."""
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = [
            "service",
            "tariff",
            "prime",
            "plus",
            "wealth",
            "exclusive_wealth",
            "private",
        ]
        rows = [
            self._make_row(0, schema),
            # Row 1: value lands in 'wealth' (col 4)
            self._make_row(1, [
                "Cashing cheques issued by Al Fardan Company",
                "Local currency descriptor for retail banking",
                "", "", "Free", "", "",
            ]),
            # Row 2: value lands in 'plus' (col 3) — scattered!
            self._make_row(2, [
                "Payment of bank cheques issued by Al Rajhi",
                "Foreign currency descriptor for settlement",
                "", "USD 2", "", "", "",
            ]),
            # Row 3: value lands in 'exclusive_wealth' (col 5) — scattered!
            self._make_row(3, [
                "Cashing Sharing coupons through MCDR representative",
                "Coupon processing descriptor for clearance",
                "", "", "", "0.5%", "",
            ]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )
        # All three sparse rows should expand to all segment columns
        for row_idx in (1, 2, 3):
            meta = annotated[row_idx].metadata
            self.assertEqual(
                meta.get("applicability_mode"),
                "inferred_sparse_expansion",
                f"Row {row_idx} should be inferred_sparse_expansion",
            )
            self.assertEqual(
                meta.get("applies_to_columns"),
                ["prime", "plus", "wealth", "exclusive_wealth", "private"],
                f"Row {row_idx} should apply to all segments",
            )

    def test_dense_table_does_not_trigger_sparse_expansion(self) -> None:
        """A table where most rows have per-column values should NOT expand
        single-value rows — they may genuinely apply to one column only."""
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = ["service", "band_a", "band_b", "band_c", "band_d"]
        rows = [
            self._make_row(0, schema),
            self._make_row(1, ["Service A", "10", "20", "30", "40"]),
            self._make_row(2, ["Service B", "15", "25", "35", "45"]),
            self._make_row(3, ["Service C", "12", "22", "32", "42"]),
            self._make_row(4, ["Service D", "8", "18", "28", "38"]),
            # One row with single value — should NOT expand because table is mostly dense
            self._make_row(5, ["Service E", "", "", "50", ""]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )
        meta = annotated[5].metadata
        # sparse_fraction = 1/5 = 0.2 < 0.4, so no expansion
        self.assertEqual(meta.get("applicability_mode"), "explicit_cells")
        self.assertEqual(meta.get("applies_to_columns"), ["band_c"])


class AzureDocumentIntelligenceGeometricReconciliationTests(SimpleTestCase):
    def test_geometric_reconciliation_detects_spanning_cell(self) -> None:
        """A data cell whose bbox spans 5 header columns should get
        column_span updated and its value duplicated across the grid."""
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")

        # 7 columns: 2 context + 5 segment.  Each header cell is ~80px wide.
        col_count = 7
        row_count = 2
        grid = [["" for _ in range(col_count)] for _ in range(row_count)]
        cell_lookup: dict[tuple[int, int], dict] = {}

        # Header row (row 0) — each column has a distinct x-range.
        headers = ["Service", "Tariff", "Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"]
        for c, label in enumerate(headers):
            x0 = 50.0 + c * 80.0
            x1 = x0 + 75.0
            grid[0][c] = label
            cell_lookup[(0, c)] = {
                "row_span": 1,
                "column_span": 1,
                "kind": "columnheader",
                "confidence": 0.99,
                "regions": [{"pageNumber": 1, "polygon": [x0, 100, x1, 100, x1, 120, x0, 120]}],
            }

        # Data row (row 1) — Azure DI placed value only in column 4 (Wealth)
        # but the bbox physically spans columns 2-6 (Prime through Private).
        grid[1][0] = "Traveler cheques"
        cell_lookup[(1, 0)] = {
            "row_span": 1, "column_span": 1, "kind": "content", "confidence": 0.95,
            "regions": [{"pageNumber": 1, "polygon": [50, 130, 125, 130, 125, 150, 50, 150]}],
        }
        grid[1][1] = ""
        cell_lookup[(1, 1)] = {
            "row_span": 1, "column_span": 1, "kind": "content", "confidence": 0.95,
            "regions": [{"pageNumber": 1, "polygon": [130, 130, 205, 130, 205, 150, 130, 150]}],
        }
        # Column 4 (Wealth) has the value, but its bbox spans from Prime (col 2) to Private (col 6)
        wide_x0 = 50.0 + 2 * 80.0  # Start of Prime column
        wide_x1 = 50.0 + 7 * 80.0  # End of Private column
        grid[1][4] = "1% (Min USD 2)"
        cell_lookup[(1, 4)] = {
            "row_span": 1, "column_span": 1, "kind": "content", "confidence": 0.95,
            "regions": [{"pageNumber": 1, "polygon": [wide_x0, 130, wide_x1, 130, wide_x1, 150, wide_x0, 150]}],
        }
        # Columns 2,3,5,6 are empty in the grid
        for c in (2, 3, 5, 6):
            grid[1][c] = ""

        extractor._reconcile_spans_from_geometry(
            grid=grid,
            cell_lookup=cell_lookup,
            header_rows={0},
            row_count=row_count,
            col_count=col_count,
            page_unit_scale=None,
        )

        # After reconciliation, value should be duplicated across cols 2-6
        for c in (2, 3, 4, 5, 6):
            self.assertEqual(grid[1][c], "1% (Min USD 2)", f"Column {c} should have the duplicated value")
            self.assertEqual(cell_lookup[(1, c)].get("column_span"), 5, f"Column {c} should have span=5")
            self.assertTrue(cell_lookup[(1, c)].get("geometric_span_reconciled"))

        # Context columns should be untouched
        self.assertEqual(grid[1][0], "Traveler cheques")
        self.assertEqual(grid[1][1], "")

    def test_geometric_reconciliation_skips_already_spanned_cells(self) -> None:
        """Cells that already have column_span > 1 should not be re-processed."""
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")

        col_count = 4
        row_count = 2
        grid = [["" for _ in range(col_count)] for _ in range(row_count)]
        cell_lookup: dict[tuple[int, int], dict] = {}

        for c in range(col_count):
            x0 = c * 100.0
            x1 = x0 + 95.0
            grid[0][c] = f"H{c}"
            cell_lookup[(0, c)] = {
                "row_span": 1, "column_span": 1, "kind": "columnheader", "confidence": 0.99,
                "regions": [{"pageNumber": 1, "polygon": [x0, 0, x1, 0, x1, 20, x0, 20]}],
            }

        # Data cell with explicit column_span=3 already set by Azure DI
        grid[1][1] = "Shared value"
        cell_lookup[(1, 1)] = {
            "row_span": 1, "column_span": 3, "kind": "content", "confidence": 0.9,
            "regions": [{"pageNumber": 1, "polygon": [100, 30, 395, 30, 395, 50, 100, 50]}],
        }

        extractor._reconcile_spans_from_geometry(
            grid=grid,
            cell_lookup=cell_lookup,
            header_rows={0},
            row_count=row_count,
            col_count=col_count,
            page_unit_scale=None,
        )

        # Should NOT touch the cell — it already has a span
        self.assertEqual(cell_lookup[(1, 1)].get("column_span"), 3)
        self.assertFalse(cell_lookup[(1, 1)].get("geometric_span_reconciled"))
