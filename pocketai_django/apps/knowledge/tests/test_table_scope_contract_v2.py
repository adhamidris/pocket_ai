from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    AzureDocumentIntelligenceExtractor,
    KnowledgeIngestionService,
    TableCellPayload,
    TableRowPayload,
)
from apps.knowledge.tables.semantic.scope_engine import SCOPE_REASON_EXPLICIT_SPAN


class TableScopeContractV2Tests(SimpleTestCase):
    @staticmethod
    def _make_row(
        row_index: int,
        values: list[str],
        *,
        spans: dict[int, int] | None = None,
    ) -> TableRowPayload:
        cells: list[TableCellPayload] = []
        for col_idx, value in enumerate(values):
            metadata: dict[str, object] = {}
            if spans and col_idx in spans:
                metadata["column_span"] = spans[col_idx]
            cells.append(
                TableCellPayload(
                    row_index=row_index,
                    column_index=col_idx,
                    column_key=f"column_{col_idx + 1}",
                    raw_text=value,
                    metadata=metadata,
                )
            )
        return TableRowPayload(
            row_index=row_index,
            page_number=1,
            raw_text=" | ".join(values),
            metadata={"row_type": "header" if row_index == 0 else "data"},
            cells=cells,
        )

    def test_row_annotation_writes_v2_scope_contract_without_legacy_alias(self) -> None:
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
            self._make_row(
                1,
                [
                    "Traveler cheques",
                    "FX descriptor",
                    "",
                    "1% (Min USD 2)",
                    "1% (Min USD 2)",
                    "1% (Min USD 2)",
                    "",
                ],
                spans={3: 3, 4: 3, 5: 3},
            ),
            self._make_row(
                2,
                ["Blank Cheques", "Retail descriptor", "", "", "EGP 10", "", ""],
            ),
            self._make_row(
                3,
                ["MCDR", "Coupon descriptor", "", "", "0.5%", "", ""],
            ),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )
        meta = annotated[2].metadata

        self.assertEqual(meta.get("table_scope_contract_version"), "v2")
        self.assertTrue(str(meta.get("scope_reason") or "").strip())
        self.assertIsNotNone(meta.get("scope_confidence"))
        self.assertNotIn("applies_to_columns", meta)
        self.assertNotIn("applicability_mode", meta)
        self.assertNotIn("applicability_confidence", meta)
        self.assertTrue(meta.get("observed_value_columns"))
        self.assertTrue(meta.get("scope_dimension_columns"))

    def test_table_row_chunk_payload_emits_v2_only_scope_fields(self) -> None:
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
                self.row_index = 4
                self.metadata = {
                    "row_type": "data",
                    "table_scope_contract_version": "v2",
                    "observed_value_columns": ["Service", "Tariff", "Prime", "Plus", "Wealth"],
                    "qualifier_columns": ["Service", "Tariff"],
                    "scope_dimension_columns": ["Prime", "Plus", "Wealth"],
                    "inferred_scope_columns": ["Prime", "Plus", "Wealth"],
                    "scope_reason": "explicit_span",
                    "scope_confidence": 0.91,
                    "scope_value": "USD 2",
                }
                self.cells = _Manager(
                    [
                        _Cell("c-1", 0, "Payment instruction"),
                        _Cell("c-2", 1, "USD"),
                        _Cell("c-3", 2, "USD 2"),
                    ]
                )

        class _Table:
            def __init__(self):
                self.title = "Contract v2 table"
                self.order_index = 1
                self.section_heading = ""
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
            ],
            raw_schema=["service", "tariff", "prime", "plus", "wealth"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "table-v2"},
            max_rows=10,
        )

        self.assertEqual(len(payloads), 1)
        metadata = payloads[0]["metadata"]
        self.assertEqual(metadata.get("table_row_contract_version"), "v2")
        self.assertEqual(
            metadata.get("table_row_observed_value_columns"),
            ["Service", "Tariff", "Prime", "Plus", "Wealth"],
        )
        self.assertEqual(metadata.get("table_row_qualifier_columns"), ["Service", "Tariff"])
        self.assertEqual(metadata.get("table_row_scope_dimension_columns"), ["Prime", "Plus", "Wealth"])
        self.assertEqual(metadata.get("table_row_inferred_scope_columns"), ["Prime", "Plus", "Wealth"])
        self.assertEqual(metadata.get("table_row_scope_reason"), SCOPE_REASON_EXPLICIT_SPAN)
        self.assertEqual(metadata.get("table_row_scope_confidence"), 0.91)
        self.assertNotIn("table_row_applies_to_columns", metadata)
        self.assertNotIn("table_row_applicability_mode", metadata)
        self.assertNotIn("table_row_applicability_confidence", metadata)


class SectionHeaderClassificationTests(SimpleTestCase):
    """Tests for intra-table section header detection and suppression."""

    @staticmethod
    def _make_row(
        row_index: int,
        values: list[str],
        *,
        spans: dict[int, int] | None = None,
        row_type: str = "data",
    ) -> TableRowPayload:
        cells: list[TableCellPayload] = []
        for col_idx, value in enumerate(values):
            metadata: dict[str, object] = {}
            if spans and col_idx in spans:
                metadata["column_span"] = spans[col_idx]
            cells.append(
                TableCellPayload(
                    row_index=row_index,
                    column_index=col_idx,
                    column_key=f"column_{col_idx + 1}",
                    raw_text=value,
                    metadata=metadata,
                )
            )
        return TableRowPayload(
            row_index=row_index,
            page_number=1,
            raw_text=" | ".join(values),
            metadata={"row_type": "header" if row_index == 0 else row_type},
            cells=cells,
        )

    # ── Test A: column_span triggers section_header classification ──

    def test_section_header_row_classified_via_column_span(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test", key="secret",
        )
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive", "private"]
        header_text = "*Cash deposit with same day value date (upon customer request)"
        rows = [
            self._make_row(0, schema),
            self._make_row(
                1,
                [header_text, header_text, header_text, header_text,
                 header_text, header_text, header_text],
                spans={0: 7},
            ),
            self._make_row(
                2,
                ["Wire Transfer", "SWIFT", "", "EGP 50", "EGP 50", "Free", "Free"],
            ),
        ]
        annotated = extractor._annotate_row_applicability(
            table_rows=rows, column_schema=schema, header_rows={0},
        )
        self.assertEqual(annotated[1].metadata.get("row_type"), "section_header")
        # Data row should NOT be tagged as section_header
        self.assertNotEqual(annotated[2].metadata.get("row_type"), "section_header")

    # ── Test B: uniform text triggers section_header classification ──

    def test_section_header_row_classified_via_uniform_text(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(
            endpoint="https://example.test", key="secret",
        )
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive", "private"]
        header_text = "*Cash deposit with same day value date"
        rows = [
            self._make_row(0, schema),
            # No column_span — pdfplumber-style duplicate text
            self._make_row(
                1,
                [header_text, header_text, header_text, header_text,
                 header_text, header_text, header_text],
            ),
            self._make_row(
                2,
                ["Cheque Deposit", "Local", "", "Free", "Free", "Free", "Free"],
            ),
        ]
        annotated = extractor._annotate_row_applicability(
            table_rows=rows, column_schema=schema, header_rows={0},
        )
        self.assertEqual(annotated[1].metadata.get("row_type"), "section_header")
        self.assertNotEqual(annotated[2].metadata.get("row_type"), "section_header")

    # ── Test C: section_header rows skipped in chunking, subsection injected ──

    def test_section_header_not_chunked_as_data_row(self) -> None:
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
            def __init__(self, row_index, metadata, cells):
                self.row_index = row_index
                self.metadata = metadata
                self.cells = _Manager(cells)

        class _Table:
            def __init__(self, rows):
                self.title = "CIB Teller"
                self.order_index = 1
                self.section_heading = ""
                self.rows = _Manager(rows)

        header_text = "*Cash deposit same day"
        section_row = _Row(
            row_index=1,
            metadata={"row_type": "section_header"},
            cells=[
                _Cell("s-0", 0, header_text),
                _Cell("s-1", 1, header_text),
                _Cell("s-2", 2, header_text),
                _Cell("s-3", 3, header_text),
                _Cell("s-4", 4, header_text),
            ],
        )
        data_row = _Row(
            row_index=2,
            metadata={"row_type": "data"},
            cells=[
                _Cell("d-0", 0, "Wire Transfer"),
                _Cell("d-1", 1, "SWIFT"),
                _Cell("d-2", 2, "EGP 50"),
                _Cell("d-3", 3, "EGP 50"),
                _Cell("d-4", 4, "Free"),
            ],
        )
        table = _Table([section_row, data_row])
        service = KnowledgeIngestionService(enable_ocr=False)
        payloads = service._table_row_chunk_payloads(
            table=table,
            column_map=[
                ("Service", "service", 0),
                ("Tariff", "tariff", 1),
                ("Prime", "prime", 2),
                ("Plus", "plus", 3),
                ("Wealth", "wealth", 4),
            ],
            raw_schema=["service", "tariff", "prime", "plus", "wealth"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "t-1"},
            max_rows=50,
        )
        # Section header should NOT produce a chunk
        self.assertEqual(len(payloads), 1)
        text = payloads[0]["text"]
        self.assertIn("[SubSection] *Cash deposit same day", text)
        # The data row content should still appear
        self.assertIn("Service: Wire Transfer", text)

    # ── Test D: fallback uniform-value detection in chunking ──

    def test_fallback_uniform_value_detection_in_chunking(self) -> None:
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
            def __init__(self, row_index, metadata, cells):
                self.row_index = row_index
                self.metadata = metadata
                self.cells = _Manager(cells)

        class _Table:
            def __init__(self, rows):
                self.title = "Fee Table"
                self.order_index = 1
                self.section_heading = ""
                self.rows = _Manager(rows)

        # Row without section_header metadata but all identical values
        uniform_text = "Foreign Currency Transactions"
        untagged_section_row = _Row(
            row_index=1,
            metadata={"row_type": "data"},  # NOT tagged
            cells=[
                _Cell("u-0", 0, uniform_text),
                _Cell("u-1", 1, uniform_text),
                _Cell("u-2", 2, uniform_text),
                _Cell("u-3", 3, uniform_text),
            ],
        )
        data_row = _Row(
            row_index=2,
            metadata={"row_type": "data"},
            cells=[
                _Cell("d-0", 0, "FX Purchase"),
                _Cell("d-1", 1, "0.5%"),
                _Cell("d-2", 2, "0.3%"),
                _Cell("d-3", 3, "Free"),
            ],
        )
        table = _Table([untagged_section_row, data_row])
        service = KnowledgeIngestionService(enable_ocr=False)
        payloads = service._table_row_chunk_payloads(
            table=table,
            column_map=[
                ("Service", "service", 0),
                ("Tariff", "tariff", 1),
                ("Prime", "prime", 2),
                ("Plus", "plus", 3),
            ],
            raw_schema=["service", "tariff", "prime", "plus"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "t-2"},
            max_rows=50,
        )
        # Uniform-value row should be suppressed
        self.assertEqual(len(payloads), 1)
        text = payloads[0]["text"]
        self.assertIn("[SubSection] Foreign Currency Transactions", text)
        self.assertIn("Service: FX Purchase", text)

    def test_noisy_numeric_subsection_is_not_injected_into_row_chunks(self) -> None:
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
            def __init__(self, row_index, metadata, cells):
                self.row_index = row_index
                self.metadata = metadata
                self.cells = _Manager(cells)

        class _Table:
            def __init__(self, rows):
                self.title = "Loan fees"
                self.order_index = 1
                self.section_heading = ""
                self.rows = _Manager(rows)

        noisy_subsection = (
            "Secured 2.00% 1.75% 1.50% 1.25% 2.00% 2.00% 1.50% 1.75% "
            "1% up to 8 years loan tenor Liability Letter Issuance Fees EGP 50"
        )
        section_row = _Row(
            row_index=1,
            metadata={"row_type": "section_header"},
            cells=[
                _Cell("s-0", 0, noisy_subsection),
                _Cell("s-1", 1, noisy_subsection),
                _Cell("s-2", 2, noisy_subsection),
                _Cell("s-3", 3, noisy_subsection),
                _Cell("s-4", 4, noisy_subsection),
            ],
        )
        data_row = _Row(
            row_index=2,
            metadata={"row_type": "data"},
            cells=[
                _Cell("d-0", 0, "Clearance Letter"),
                _Cell("d-1", 1, "EGP 200"),
                _Cell("d-2", 2, "EGP 200"),
                _Cell("d-3", 3, "EGP 200"),
            ],
        )
        table = _Table([section_row, data_row])
        service = KnowledgeIngestionService(enable_ocr=False)
        payloads = service._table_row_chunk_payloads(
            table=table,
            column_map=[
                ("Service", "service", 0),
                ("Fees / Charges", "fees_charges", 1),
                ("Fees / Charges", "fees_charges_2", 2),
                ("Fees / Charges", "fees_charges_3", 3),
            ],
            raw_schema=["service", "fees_charges", "fees_charges_2", "fees_charges_3"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "t-3"},
            max_rows=50,
        )

        self.assertEqual(len(payloads), 1)
        text = payloads[0]["text"]
        self.assertNotIn("[SubSection]", text)
        self.assertIn("Service: Clearance Letter", text)


class TableRowSignalFilterTests(SimpleTestCase):
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
        def __init__(self, row_index, metadata, cells):
            self.row_index = row_index
            self.metadata = metadata
            self.cells = TableRowSignalFilterTests._Manager(cells)

    class _Table:
        def __init__(self, rows, metadata=None):
            self.id = "table-signal"
            self.title = "Signal Table"
            self.order_index = 1
            self.section_heading = ""
            self.metadata = metadata or {}
            self.rows = TableRowSignalFilterTests._Manager(rows)

    def _build_table(self):
        low_signal_row = self._Row(
            row_index=1,
            metadata={"row_type": "data"},
            cells=[
                self._Cell("r1-c1", 0, "Issuance"),
            ],
        )
        value_row = self._Row(
            row_index=2,
            metadata={"row_type": "data"},
            cells=[
                self._Cell("r2-c1", 0, "Stop Payment"),
                self._Cell("r2-c2", 1, "EGP"),
                self._Cell("r2-c3", 2, "EGP 25"),
                self._Cell("r2-c4", 3, "EGP 25"),
                self._Cell("r2-c5", 4, "EGP 25"),
            ],
        )
        return self._Table([low_signal_row, value_row])

    def _build_payloads(self):
        service = KnowledgeIngestionService(enable_ocr=False)
        return service._table_row_chunk_payloads(
            table=self._build_table(),
            column_map=[
                ("Service", "service", 0),
                ("Tariff", "tariff", 1),
                ("Prime", "prime", 2),
                ("Plus", "plus", 3),
                ("Wealth", "wealth", 4),
            ],
            raw_schema=["service", "tariff", "prime", "plus", "wealth"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "table-signal"},
            max_rows=50,
        )

    def test_row_signal_filter_suppresses_sparse_low_signal_rows(self) -> None:
        payloads = self._build_payloads()

        self.assertEqual(len(payloads), 1)
        metadata = payloads[0]["metadata"]
        self.assertEqual(metadata.get("table_row_index"), 2)
        self.assertTrue(metadata.get("table_row_signal_filter_enabled"))
        self.assertGreater(float(metadata.get("table_row_signal_score") or 0.0), 0.0)

    def test_row_signal_filter_keeps_meaningful_single_cell_rows_for_collapsed_native_tables(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        table = self._Table(
            [
                self._Row(
                    row_index=1,
                    metadata={"row_type": "data"},
                    cells=[self._Cell("r1-c1", 0, "Card Type Swype 12 Swype 36")],
                ),
                self._Row(
                    row_index=2,
                    metadata={"row_type": "data"},
                    cells=[self._Cell("r2-c1", 0, "Issuance and Renewal Fees Free Across all Card Types")],
                ),
                self._Row(
                    row_index=3,
                    metadata={"row_type": "data"},
                    cells=[self._Cell("r3-c1", 0, "Over Limit Fees EGP 150")],
                ),
            ],
            metadata={"promotion_class": "collapsed_uniform_value_matrix"},
        )

        payloads = service._table_row_chunk_payloads(
            table=table,
            column_map=[("column_1", "column_1", 0)],
            raw_schema=["column_1"],
            privacy_rules={},
            base_metadata={"is_table_chunk": True, "table_id": "table-signal"},
            max_rows=50,
        )

        self.assertEqual(len(payloads), 2)
        self.assertEqual([payload["metadata"].get("table_row_index") for payload in payloads], [2, 3])
        self.assertTrue(all(payload["metadata"].get("table_row_signal_override") for payload in payloads))
