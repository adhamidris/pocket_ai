from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    AzureDocumentIntelligenceExtractor,
    KnowledgeIngestionService,
    TableCellPayload,
    TableRowPayload,
)
from apps.knowledge.table_scope_engine import SCOPE_REASON_EXPLICIT_SPAN


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
