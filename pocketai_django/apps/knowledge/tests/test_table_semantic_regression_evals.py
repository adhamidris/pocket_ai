from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
)


class TableSemanticRegressionEvals(SimpleTestCase):
    @staticmethod
    def _make_row(
        row_index: int,
        values: list[str],
        *,
        row_type: str = "data",
        row_spans: dict[int, int] | None = None,
    ) -> TableRowPayload:
        row_spans = row_spans or {}
        cells = [
            TableCellPayload(
                row_index=row_index,
                column_index=column_index,
                column_key=f"column_{column_index + 1}",
                raw_text=value,
                metadata={"row_span": int(row_spans.get(column_index, 1)), "column_span": 1},
            )
            for column_index, value in enumerate(values)
        ]
        return TableRowPayload(
            row_index=row_index,
            page_number=1,
            raw_text=" | ".join(values),
            metadata={"row_type": row_type},
            cells=cells,
        )

    @staticmethod
    def _cell_text(row: TableRowPayload, column_index: int) -> str:
        for cell in row.cells:
            if int(cell.column_index) == column_index:
                return str(cell.raw_text or "")
        return ""

    def test_merged_parent_service_label_is_carried_down_to_child_rows(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        table = TablePayload(
            order_index=1,
            title="Account Fees",
            section_heading="Accounts Fees and Charges",
            page_number=1,
            column_schema=["service", "tariff", "prime", "plus", "wealth", "exclusive_wealth", "private"],
            rows=[
                self._make_row(
                    0,
                    ["Service", "Tariff", "Prime", "Plus", "Wealth", "Exclusive Wealth", "Private"],
                    row_type="header",
                ),
                self._make_row(
                    1,
                    [
                        "Account Opening Fees",
                        "EGP Everyday Savers/Savers Account, Classic Current Account",
                        "EGP 100",
                        "EGP 100",
                        "Free",
                        "Free",
                        "Free",
                    ],
                    row_spans={0: 2},
                ),
                self._make_row(
                    2,
                    [
                        "",
                        "EGP WellSavers Account Wealth/Exclusive Wealth segments)",
                        "N/A",
                        "N/A",
                        "EGP 1000",
                        "EGP 1000",
                        "EGP 1000",
                    ],
                ),
            ],
        )

        processed, _issues, meta = service._postprocess_tables([table])
        account_table = processed[0]
        child_row = next(row for row in account_table.rows if int(row.row_index) == 2)

        self.assertEqual(self._cell_text(child_row, 0), "Account Opening Fees")
        self.assertEqual(child_row.metadata.get("merged_parent_labels_carried_down"), 1)
        self.assertEqual(meta.get("merged_parent_label_cells_carried_down"), 1)
        self.assertTrue(any(issue.code == "table_merged_parent_labels_carried_down" for issue in _issues))

    def test_embedded_header_row_promotes_real_schema_and_is_not_data(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        table = TablePayload(
            order_index=2,
            title="Table 2",
            section_heading="",
            page_number=1,
            column_schema=["Bedaya Accounts", "column_2", "column_3"],
            rows=[
                self._make_row(0, ["", "Bedaya Saving EGP", "Bedaya Current USD"]),
                self._make_row(1, ["Account Opening Fees", "Free", "Free"]),
                self._make_row(2, ["Minimum Balance Fees", "Free", "Free"]),
            ],
        )

        processed, issues, meta = service._postprocess_tables([table])
        table_after = processed[0]
        header_row = next(row for row in table_after.rows if int(row.row_index) == 0)
        account_row = next(row for row in table_after.rows if int(row.row_index) == 1)

        self.assertEqual(
            table_after.column_schema,
            ["Bedaya Accounts", "Bedaya Saving EGP", "Bedaya Current USD"],
        )
        self.assertEqual(table_after.section_heading, "Bedaya Accounts")
        self.assertEqual(header_row.metadata.get("row_type"), "header")
        self.assertTrue(header_row.metadata.get("embedded_header_promoted"))
        self.assertEqual(account_row.cells[1].column_key, "Bedaya Saving EGP")
        self.assertEqual(account_row.cells[2].column_key, "Bedaya Current USD")
        self.assertEqual(meta.get("embedded_header_rows_promoted"), 1)
        self.assertTrue(any(issue.code == "table_embedded_header_promoted" for issue in issues))

    def test_table_quality_exposes_semantic_repair_signals(self) -> None:
        service = KnowledgeIngestionService(enable_ocr=False)
        table = TablePayload(
            order_index=3,
            title="Table 3",
            section_heading="",
            page_number=1,
            column_schema=["Service", "column_2", "prime", "plus"],
            rows=[
                self._make_row(0, ["", "Tariff", "Prime", "Plus"]),
                self._make_row(1, ["Opening Fees", "Standard", "EGP 100", "EGP 100"], row_spans={0: 2}),
                self._make_row(2, ["", "Premium", "Free", "Free"]),
            ],
        )

        processed, _issues, _meta = service._postprocess_tables([table])
        quality = service._assess_table_quality(processed[0])
        signals = quality.get("signals") or {}

        self.assertEqual(signals.get("embedded_header_rows_promoted"), 1)
        self.assertEqual(signals.get("merged_parent_label_cells_carried_down"), 1)
