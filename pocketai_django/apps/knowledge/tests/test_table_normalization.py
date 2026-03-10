from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from apps.knowledge.table_normalization import (
    NormalizedSheet,
    SheetNormalizationDiagnostics,
    SpreadsheetRowInput,
    TableNormalizationPolicy,
    normalize_sheet_rows,
    resolve_normalization_policy,
    sheet_is_allowed,
    summarize_normalization,
)


class TableNormalizationTests(SimpleTestCase):
    def test_normalize_sheet_rows_cleans_tokens_and_drops_empty_rows(self) -> None:
        policy = TableNormalizationPolicy(
            enabled=True,
            null_tokens={"null"},
            drop_empty_columns=True,
            sheet_whitelist=set(),
            sheet_blacklist=set(),
            policy_version="v1",
        )
        normalized = normalize_sheet_rows(
            [
                ["Name", "Value"],
                ["Alice", "NULL"],
                ["", ""],
                ["Bob", "42"],
            ],
            sheet_name="Sheet1",
            policy=policy,
        )
        self.assertIsInstance(normalized, NormalizedSheet)
        self.assertEqual(normalized.column_schema, ["Name", "Value"])
        self.assertEqual(normalized.rows, [["Alice", ""], ["Bob", "42"]])
        self.assertEqual(normalized.diagnostics.rows_dropped, 1)
        self.assertEqual(normalized.diagnostics.columns_trimmed, 0)
        self.assertGreaterEqual(normalized.diagnostics.tokens_replaced, 1)

    def test_normalize_sheet_rows_trims_empty_columns(self) -> None:
        policy = TableNormalizationPolicy(
            enabled=True,
            null_tokens=set(),
            drop_empty_columns=True,
            sheet_whitelist=set(),
            sheet_blacklist=set(),
            policy_version="v1",
        )
        normalized = normalize_sheet_rows(
            [
                ["A", "", ""],
                ["1", "", "value"],
                ["2", "", ""],
            ],
            sheet_name="Sheet2",
            policy=policy,
        )
        self.assertEqual(normalized.column_schema, ["A", "column_2"])
        self.assertEqual(normalized.rows, [["1", "value"], ["2", ""]])
        self.assertEqual(normalized.diagnostics.columns_trimmed, 1)

    def test_normalize_sheet_rows_drops_hidden_zero_heavy_spreadsheet_rows(self) -> None:
        policy = TableNormalizationPolicy(
            enabled=True,
            null_tokens=set(),
            drop_empty_columns=True,
            sheet_whitelist=set(),
            sheet_blacklist=set(),
            policy_version="v1",
        )
        normalized = normalize_sheet_rows(
            [
                SpreadsheetRowInput(values=["Code", "Amount", "Match"], row_index=1, hidden=False),
                SpreadsheetRowInput(values=["E-1", 1250, 0], row_index=2, hidden=False),
                SpreadsheetRowInput(values=[0, 0, 0], row_index=3, hidden=True),
                SpreadsheetRowInput(values=[0, 0, 0], row_index=4, hidden=True),
            ],
            sheet_name="Equipment",
            policy=policy,
        )
        self.assertEqual(normalized.rows, [["E-1", "1250", "0"]])
        self.assertEqual(len(normalized.row_metadata), 1)
        self.assertEqual(normalized.row_metadata[0].source_row_index, 2)
        self.assertEqual(normalized.diagnostics.hidden_rows_dropped, 2)

    def test_normalize_sheet_rows_drops_scaffold_columns(self) -> None:
        policy = TableNormalizationPolicy(
            enabled=True,
            null_tokens=set(),
            drop_empty_columns=True,
            sheet_whitelist=set(),
            sheet_blacklist=set(),
            policy_version="v1",
        )
        normalized = normalize_sheet_rows(
            [
                SpreadsheetRowInput(
                    values=["Name", "SANDBOX AREA - rough work only"],
                    row_index=1,
                    hidden=False,
                ),
                SpreadsheetRowInput(values=["Alpha", ""], row_index=2, hidden=False),
                SpreadsheetRowInput(values=["Beta", "0"], row_index=3, hidden=False),
            ],
            sheet_name="Sheet1",
            policy=policy,
        )
        self.assertEqual(normalized.column_schema, ["Name"])
        self.assertEqual(normalized.rows, [["Alpha"], ["Beta"]])
        self.assertEqual(normalized.diagnostics.scaffold_columns_trimmed, 1)

    @override_settings(INGEST_NORMALIZE_TABLES=True)
    def test_policy_resolution_honors_upload_overrides(self) -> None:
        upload_meta = {"table_policy": {"enable_normalization": False, "drop_empty_columns": False}}
        business_meta = {"table_policy": {"null_tokens": ["CUSTOM"], "sheet_whitelist": ["Data"]}}
        upload = SimpleNamespace(
            metadata=upload_meta,
            business_profile=SimpleNamespace(metadata=business_meta),
        )
        policy = resolve_normalization_policy(upload)
        self.assertFalse(policy.enabled)
        self.assertFalse(policy.drop_empty_columns)
        self.assertIn("custom", policy.null_tokens)
        self.assertEqual(policy.sheet_whitelist, {"data"})

    def test_sheet_is_allowed_checks_lists(self) -> None:
        policy = TableNormalizationPolicy(
            enabled=True,
            null_tokens=set(),
            drop_empty_columns=True,
            sheet_whitelist={"primary"},
            sheet_blacklist={"secret"},
            policy_version="v1",
        )
        self.assertTrue(sheet_is_allowed("Primary", policy))
        self.assertFalse(sheet_is_allowed("Secret", policy))
        self.assertFalse(sheet_is_allowed("Other", policy))

    def test_summarize_normalization_aggregates_diagnostics(self) -> None:
        policy = TableNormalizationPolicy(
            enabled=True,
            null_tokens={"null"},
            drop_empty_columns=True,
            sheet_whitelist=set(),
            sheet_blacklist=set(),
            policy_version="v1",
        )
        diagnostics = [
            SheetNormalizationDiagnostics(sheet_name="One", rows_dropped=2, columns_trimmed=1, tokens_replaced=3),
            SheetNormalizationDiagnostics(sheet_name="Two", rows_dropped=0, columns_trimmed=2, tokens_replaced=0),
        ]
        diagnostics[1].skipped = True
        diagnostics[1].skip_reason = "empty"
        summary = summarize_normalization(policy, diagnostics)
        self.assertEqual(summary["rows_dropped"]["total"], 2)
        self.assertEqual(summary["columns_trimmed"]["total"], 3)
        self.assertEqual(summary["tokens_replaced"], 3)
        self.assertEqual(summary["empty_sheets_skipped"], ["Two"])

    def test_summarize_normalization_includes_spreadsheet_row_drop_buckets(self) -> None:
        policy = TableNormalizationPolicy(
            enabled=True,
            null_tokens={"null"},
            drop_empty_columns=True,
            sheet_whitelist=set(),
            sheet_blacklist=set(),
            policy_version="v1",
        )
        diagnostics = [
            SheetNormalizationDiagnostics(
                sheet_name="One",
                rows_dropped=4,
                hidden_rows_dropped=2,
                zero_heavy_rows_dropped=1,
                placeholder_rows_dropped=1,
                scaffold_columns_trimmed=1,
            )
        ]
        summary = summarize_normalization(policy, diagnostics)
        self.assertEqual(summary["hidden_rows_dropped"]["total"], 2)
        self.assertEqual(summary["zero_heavy_rows_dropped"]["total"], 1)
        self.assertEqual(summary["placeholder_rows_dropped"]["total"], 1)
        self.assertEqual(summary["scaffold_columns_trimmed"]["total"], 1)
