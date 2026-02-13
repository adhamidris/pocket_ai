from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)


class TablePostprocessContinuationTests(SimpleTestCase):
    @staticmethod
    def _make_row(row_index: int, values: list[str], *, row_type: str = "data") -> TableRowPayload:
        cells: list[TableCellPayload] = []
        for col_idx, value in enumerate(values):
            cells.append(
                TableCellPayload(
                    row_index=row_index,
                    column_index=col_idx,
                    column_key=f"column_{col_idx + 1}",
                    raw_text=value,
                    metadata={"row_span": 1, "column_span": 1},
                )
            )
        return TableRowPayload(
            row_index=row_index,
            page_number=1,
            raw_text=" | ".join(values),
            metadata={"row_type": row_type},
            cells=cells,
        )

    @staticmethod
    def _cell_text(row: TableRowPayload, index: int) -> str:
        for cell in row.cells:
            if int(cell.column_index) == index:
                return str(cell.raw_text or "")
        return ""

    def _table(self, rows: list[TableRowPayload]) -> TablePayload:
        return TablePayload(
            order_index=1,
            title="Fees",
            section_heading="",
            page_number=1,
            column_schema=["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"],
            rows=rows,
        )

    def test_postprocess_stitches_descriptor_continuation_across_adjacent_rows(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        rows = [
            self._make_row(0, ["Service", "Tariff", "Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"], row_type="header"),
            self._make_row(1, ["Payment of bank cheques issued by Al Fardan", "EGP", "", "", "Free", "", ""]),
            self._make_row(2, ["Company in the United Arab Emirates", "USD", "", "", "USD 5", "", ""]),
            self._make_row(3, ["Cashing bank cheques issued by Al Rajhi Company in Saudi Arabia", "EGP", "", "", "Free", "", ""]),
        ]
        table = self._table(rows)

        processed, issues, meta = service._postprocess_tables([table])

        self.assertEqual(len(processed), 1)
        stitched_table = processed[0]
        row_one = next(row for row in stitched_table.rows if row.row_index == 1)
        row_two = next(row for row in stitched_table.rows if row.row_index == 2)
        combined = "Payment of bank cheques issued by Al Fardan Company in the United Arab Emirates"
        self.assertEqual(self._cell_text(row_one, 0), combined)
        self.assertEqual(self._cell_text(row_two, 0), combined)
        self.assertEqual(meta.get("row_continuation_stitched_pairs"), 1)
        self.assertTrue(any(issue.code == "table_row_continuation_stitched" for issue in issues))

    def test_postprocess_does_not_stitch_unrelated_adjacent_rows(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        rows = [
            self._make_row(0, ["Service", "Tariff", "Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"], row_type="header"),
            self._make_row(1, ["Alpha service", "EGP", "", "", "Free", "", ""]),
            self._make_row(2, ["Beta service", "USD", "", "", "USD 5", "", ""]),
        ]
        table = self._table(rows)

        processed, issues, meta = service._postprocess_tables([table])

        self.assertEqual(len(processed), 1)
        table_after = processed[0]
        row_one = next(row for row in table_after.rows if row.row_index == 1)
        row_two = next(row for row in table_after.rows if row.row_index == 2)
        self.assertEqual(self._cell_text(row_one, 0), "Alpha service")
        self.assertEqual(self._cell_text(row_two, 0), "Beta service")
        self.assertEqual(meta.get("row_continuation_stitched_pairs"), 0)
        self.assertFalse(any(issue.code == "table_row_continuation_stitched" for issue in issues))

    def test_postprocess_stitches_split_scope_value_fragments_within_row(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        rows = [
            self._make_row(0, ["Service", "Tariff", "Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"], row_type="header"),
            self._make_row(
                1,
                [
                    "Cash deposit with same day value date",
                    "",
                    "(With minimum",
                    "(With minimum",
                    "EGP 100 or Equivalent 0,2%",
                    "and with no maximum)",
                    "",
                ],
            ),
            self._make_row(
                2,
                [
                    "Cash deposit with same day value date (T+3 customers)",
                    "",
                    "0,3%",
                    "0,3%",
                    "0,3%",
                    "0,3%",
                    "0,3%",
                ],
            ),
        ]
        table = self._table(rows)

        processed, _issues, meta = service._postprocess_tables([table])

        row = next(r for r in processed[0].rows if r.row_index == 1)
        prime = self._cell_text(row, 2)
        plus = self._cell_text(row, 3)
        wealth = self._cell_text(row, 4)
        exclusive = self._cell_text(row, 5)

        self.assertIn("with minimum", prime.lower())
        self.assertIn("egp 100", prime.lower())
        self.assertIn("no maximum", prime.lower())
        self.assertEqual(prime, plus)
        self.assertEqual(prime, wealth)
        self.assertEqual(prime, exclusive)
        self.assertGreaterEqual(int(meta.get("value_fragment_stitched_cells") or 0), 3)

    def test_postprocess_does_not_stitch_distinct_scope_values(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        rows = [
            self._make_row(0, ["Service", "Tariff", "Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"], row_type="header"),
            self._make_row(
                1,
                [
                    "Service with distinct values",
                    "",
                    "10%",
                    "20%",
                    "30%",
                    "40%",
                    "",
                ],
            ),
        ]
        table = self._table(rows)

        processed, _issues, meta = service._postprocess_tables([table])

        row = next(r for r in processed[0].rows if r.row_index == 1)
        self.assertEqual(self._cell_text(row, 2), "10%")
        self.assertEqual(self._cell_text(row, 3), "20%")
        self.assertEqual(self._cell_text(row, 4), "30%")
        self.assertEqual(self._cell_text(row, 5), "40%")
        self.assertEqual(int(meta.get("value_fragment_stitched_cells") or 0), 0)
