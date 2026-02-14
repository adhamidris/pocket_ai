from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    AzureDocumentIntelligenceExtractor,
    KnowledgeIngestionService,
    TableCellPayload,
    TableRowPayload,
)
from apps.knowledge.table_scope_engine import (
    SCOPE_REASON_ABSTAIN,
    SCOPE_REASON_EDGE_COMPLETION,
    SCOPE_REASON_EXPLICIT_SPAN,
    SCOPE_REASON_REPEATED_VALUE_SPAN,
    SCOPE_REASON_SPARSE_EXPANSION,
)


class TableScopeEnginePhaseThreeTests(SimpleTestCase):
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

    def test_precedence_explicit_span_before_sparse_expansion(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"]
        rows = [
            self._make_row(0, schema),
            self._make_row(
                1,
                ["Traveler cheques", "USD", "", "1%", "1%", "1%", ""],
                spans={3: 3, 4: 3, 5: 3},
            ),
            self._make_row(2, ["Blank cheques", "USD", "", "", "EGP 10", "", ""]),
            self._make_row(3, ["MCDR", "USD", "", "", "0.5%", "", ""]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )

        row_one_meta = annotated[1].metadata
        self.assertEqual(row_one_meta.get("scope_reason"), SCOPE_REASON_EXPLICIT_SPAN)

    def test_precedence_repeated_value_span_before_sparse_expansion(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"]
        rows = [
            self._make_row(0, schema),
            self._make_row(1, ["Traveler cheques", "USD", "", "1%", "1%", "", ""]),
            self._make_row(2, ["Blank cheques", "USD", "", "", "EGP 10", "", ""]),
            self._make_row(3, ["MCDR", "USD", "", "", "0.5%", "", ""]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )

        row_one_meta = annotated[1].metadata
        self.assertEqual(row_one_meta.get("scope_reason"), SCOPE_REASON_REPEATED_VALUE_SPAN)
        self.assertEqual(
            row_one_meta.get("inferred_scope_columns"),
            ["prime", "plus", "wealth", "exclusive_wealth", "private"],
        )

    def test_explicit_span_edge_completion_extends_trailing_scope_dimension(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"]
        rows = [
            self._make_row(0, schema),
            self._make_row(
                1,
                ["Traveler cheques", "", "1% (Min USD 2)", "1% (Min USD 2)", "1% (Min USD 2)", "1% (Min USD 2)", ""],
                spans={2: 4, 3: 4, 4: 4, 5: 4},
            ),
            self._make_row(2, ["Blank cheques", "USD", "", "", "EGP 10", "", ""]),
            self._make_row(3, ["MCDR", "USD", "", "", "0.5%", "", ""]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )

        row_one_meta = annotated[1].metadata
        self.assertEqual(row_one_meta.get("scope_reason"), SCOPE_REASON_EDGE_COMPLETION)
        self.assertEqual(
            row_one_meta.get("inferred_scope_columns"),
            ["prime", "plus", "wealth", "exclusive_wealth", "private"],
        )

    def test_sparse_expansion_and_abstain_reasons_are_deterministic(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        sparse_schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"]
        sparse_rows = [
            self._make_row(0, sparse_schema),
            self._make_row(1, ["Service A", "USD", "", "", "10", "", ""]),
            self._make_row(2, ["Service B", "USD", "", "", "11", "", ""]),
            self._make_row(3, ["Service C", "USD", "", "", "12", "", ""]),
        ]
        sparse_annotated = extractor._annotate_row_applicability(
            table_rows=sparse_rows,
            column_schema=sparse_schema,
            header_rows={0},
        )
        self.assertEqual(sparse_annotated[1].metadata.get("scope_reason"), SCOPE_REASON_SPARSE_EXPANSION)

        dense_schema = ["service", "band_a", "band_b", "band_c", "band_d"]
        dense_rows = [
            self._make_row(0, dense_schema),
            self._make_row(1, ["S1", "10", "20", "30", "40"]),
            self._make_row(2, ["S2", "11", "21", "31", "41"]),
            self._make_row(3, ["S3", "12", "22", "32", "42"]),
            self._make_row(4, ["S4", "13", "23", "33", "43"]),
            self._make_row(5, ["S5", "", "", "55", ""]),
        ]
        dense_annotated = extractor._annotate_row_applicability(
            table_rows=dense_rows,
            column_schema=dense_schema,
            header_rows={0},
        )
        self.assertEqual(dense_annotated[5].metadata.get("scope_reason"), SCOPE_REASON_ABSTAIN)

    def test_scattered_rows_expand_over_scope_dimensions_only(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"]
        rows = [
            self._make_row(0, schema),
            self._make_row(1, ["Cashing cheques", "EGP", "", "", "Free", "", ""]),
            self._make_row(2, ["Payment instructions", "USD", "", "USD 2", "", "", ""]),
            self._make_row(3, ["Coupon settlement", "USD", "", "", "", "0.5%", ""]),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )

        for row_idx in (1, 2, 3):
            meta = annotated[row_idx].metadata
            self.assertEqual(meta.get("scope_reason"), SCOPE_REASON_SPARSE_EXPANSION)
            self.assertEqual(
                meta.get("inferred_scope_columns"),
                ["prime", "plus", "wealth", "exclusive_wealth", "private"],
            )

    def test_note_row_full_width_span_does_not_expand_scope_dimensions(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"]
        base_rows = [
            self._make_row(0, schema),
            self._make_row(1, ["Service A", "EGP", "", "", "Free", "", ""]),
            self._make_row(2, ["Service B", "USD", "", "USD 2", "", "", ""]),
        ]
        baseline_annotated = extractor._annotate_row_applicability(
            table_rows=base_rows,
            column_schema=schema,
            header_rows={0},
        )
        baseline_scope_dimensions = list(
            baseline_annotated[1].metadata.get("scope_dimension_columns") or []
        )

        rows_with_note = [
            *base_rows,
            self._make_row(
                3,
                ["*Note applies by account type", "", "", "", "", "", ""],
                spans={0: 7},
            ),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows_with_note,
            column_schema=schema,
            header_rows={0},
        )

        row_meta = annotated[1].metadata
        self.assertEqual(row_meta.get("scope_dimension_columns"), baseline_scope_dimensions)
        self.assertNotIn("service", row_meta.get("scope_dimension_columns") or [])

    def test_scope_contract_filters_non_scope_columns_when_scope_dimensions_exist(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        scope_contract = service._resolve_row_scope_contract(
            row_model_meta={
                "scope_dimension_columns": ["Prime", "Plus", "Wealth"],
                "inferred_scope_columns": ["Tariff", "Prime"],
                "scope_reason": "explicit_span",
                "scope_confidence": 0.9,
            },
            value_by_label={"Tariff": "USD", "Prime": "USD 2"},
            contextual_labels={"Service", "Tariff"},
            inferred_segment_labels=["Prime", "Plus", "Wealth"],
        )

        self.assertEqual(scope_contract.get("inferred_scope_columns"), ["Prime"])
        self.assertEqual(scope_contract.get("scope_reason"), SCOPE_REASON_EXPLICIT_SPAN)

    def test_sparse_right_edge_scope_dimension_is_retained(self) -> None:
        extractor = AzureDocumentIntelligenceExtractor(endpoint="https://example.test", key="secret")
        schema = ["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"]
        rows = [
            self._make_row(0, schema),
            self._make_row(1, ["Service A", "EGP", "10", "10", "10", "10", "Free"]),
            self._make_row(2, ["Service B", "EGP", "11", "11", "11", "11", "Free"]),
            self._make_row(3, ["Service C", "USD", "", "", "12", "", ""]),
            self._make_row(4, ["*Note text", "", "", "", "", "", ""], spans={0: 7}),
        ]

        annotated = extractor._annotate_row_applicability(
            table_rows=rows,
            column_schema=schema,
            header_rows={0},
        )

        scope_dimensions = annotated[1].metadata.get("scope_dimension_columns") or []
        self.assertIn("private", scope_dimensions)
        self.assertIn(
            "private",
            annotated[3].metadata.get("scope_dimension_columns") or [],
        )
