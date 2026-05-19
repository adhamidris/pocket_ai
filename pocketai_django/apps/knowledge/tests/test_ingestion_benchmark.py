from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.ingestion.benchmark import (
    compare_snapshots,
    evaluate_pdf_portfolio_snapshot,
    evaluate_quality_gate,
    normalize_pdf_portfolio_expectations,
    quality_gate_thresholds,
)


class IngestionBenchmarkTests(SimpleTestCase):
    def test_normalize_pdf_portfolio_expectations_resolves_none_some_and_range(self) -> None:
        payload = {
            "label": "pdf_portfolio_v1",
            "entries": [
                {"filename": "text-first.pdf", "expected_table_mode": "none"},
                {"filename": "matrix.pdf", "expected_table_mode": "some"},
                {"filename": "tariff.pdf", "expected_table_count": 3},
            ],
        }

        normalized = normalize_pdf_portfolio_expectations(payload)

        self.assertEqual(normalized["label"], "pdf_portfolio_v1")
        self.assertEqual(normalized["entries"][0]["min_table_count"], 0)
        self.assertEqual(normalized["entries"][0]["max_table_count"], 0)
        self.assertEqual(normalized["entries"][1]["min_table_count"], 1)
        self.assertIsNone(normalized["entries"][1]["max_table_count"])
        self.assertEqual(normalized["entries"][2]["min_table_count"], 3)
        self.assertEqual(normalized["entries"][2]["max_table_count"], 3)

    def test_evaluate_pdf_portfolio_snapshot_flags_missing_and_out_of_range_entries(self) -> None:
        snapshot = {
            "label": "pdf_portfolio_v1",
            "entries": [
                {
                    "filename": "text-first.pdf",
                    "expected_table_mode": "none",
                    "min_table_count": 0,
                    "max_table_count": 0,
                    "upload_found": True,
                    "actual_table_count": 0,
                },
                {
                    "filename": "matrix.pdf",
                    "expected_table_mode": "range",
                    "min_table_count": 2,
                    "max_table_count": 4,
                    "upload_found": True,
                    "actual_table_count": 1,
                },
                {
                    "filename": "missing.pdf",
                    "expected_table_mode": "none",
                    "min_table_count": 0,
                    "max_table_count": 0,
                    "upload_found": False,
                    "actual_table_count": None,
                },
            ],
        }

        report = evaluate_pdf_portfolio_snapshot(snapshot)

        self.assertFalse(report["passed"])
        self.assertEqual(report["summary"]["total"], 3)
        self.assertEqual(report["summary"]["passed"], 1)
        self.assertEqual(report["summary"]["failed"], 2)
        self.assertEqual(report["summary"]["missing_uploads"], 1)
        self.assertEqual(report["entries"][1]["failed_checks"], ["too_few_tables"])
        self.assertEqual(report["entries"][2]["status"], "missing_upload")

    def test_compare_snapshots_aligns_single_table_rows_without_shared_table_ids(self) -> None:
        baseline = {
            "snapshot_label": "baseline",
            "upload_id": "u1",
            "row_chunks": [
                {
                    "table_row_index": 6,
                    "inferred_scope_columns": ["tariff", "wealth"],
                    "scope_reason": "explicit_cells",
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_row_index": 7,
                    "inferred_scope_columns": ["tariff", "wealth"],
                    "scope_reason": "explicit_cells",
                    "table_row_fee_value": "USD",
                },
            ],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
        }
        candidate = {
            "snapshot_label": "candidate",
            "upload_id": "u2",
            "row_chunks": [
                {
                    "table_id": "table-uuid-1",
                    "table_row_index": 6,
                    "inferred_scope_columns": ["tariff", "wealth"],
                    "scope_reason": "explicit_cells",
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_id": "table-uuid-1",
                    "table_row_index": 7,
                    "inferred_scope_columns": ["tariff", "wealth"],
                    "scope_reason": "explicit_cells",
                    "table_row_fee_value": "USD",
                },
            ],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
        }

        report = compare_snapshots(baseline, candidate, focus_row_start=6, focus_row_end=7)
        self.assertEqual(report["changed_rows"], [])
        self.assertEqual(report["deltas"]["row_chunk_count_delta"], 0)
        self.assertEqual(report["deltas"]["table_count_delta"], 0)
        self.assertEqual(report["deltas"]["table_data_row_count_delta"], 0)

    def test_compare_snapshots_derives_counts_when_top_level_values_missing(self) -> None:
        baseline = {
            "snapshot_label": "baseline",
            "upload_id": "u1",
            "row_chunks": [{"table_row_index": 1, "inferred_scope_columns": ["a", "b"]}],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}]}],
        }
        candidate = {
            "snapshot_label": "candidate",
            "upload_id": "u2",
            "row_chunks": [{"table_row_index": 1, "inferred_scope_columns": ["a", "b"]}],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
        }

        report = compare_snapshots(baseline, candidate)
        self.assertEqual(report["baseline"]["table_count"], 1)
        self.assertEqual(report["candidate"]["table_count"], 1)
        self.assertEqual(report["baseline"]["table_data_row_count"], 1)
        self.assertEqual(report["candidate"]["table_data_row_count"], 2)
        self.assertEqual(report["deltas"]["table_data_row_count_delta"], 1)

    def test_compare_snapshots_includes_quality_gate_metrics_and_passes_when_aligned(self) -> None:
        baseline = {
            "snapshot_label": "baseline",
            "upload_id": "u1",
            "row_chunks": [
                {
                    "table_row_index": 6,
                    "inferred_scope_columns": ["tariff", "prime"],
                    "scope_reason": "explicit_cells",
                    "table_row_scope_confidence": 1.0,
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_row_index": 7,
                    "inferred_scope_columns": ["tariff", "prime"],
                    "scope_reason": "explicit_cells",
                    "table_row_scope_confidence": 1.0,
                    "table_row_fee_value": "USD 5",
                },
            ],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
        }
        candidate = {
            "snapshot_label": "candidate",
            "upload_id": "u2",
            "row_chunks": [
                {
                    "table_id": "table-1",
                    "table_row_index": 6,
                    "inferred_scope_columns": ["tariff", "prime"],
                    "scope_reason": "explicit_cells",
                    "table_row_scope_confidence": 0.9,
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_id": "table-1",
                    "table_row_index": 7,
                    "inferred_scope_columns": ["tariff", "prime"],
                    "scope_reason": "explicit_cells",
                    "table_row_scope_confidence": 0.9,
                    "table_row_fee_value": "USD 5",
                },
            ],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
        }

        report = compare_snapshots(baseline, candidate)
        metrics = report.get("quality_gate_metrics") or {}
        gate = report.get("quality_gate") or {}

        self.assertEqual(metrics.get("row_recall"), 1.0)
        self.assertEqual(metrics.get("row_order_stability"), 1.0)
        self.assertEqual(metrics.get("scope_f1"), 1.0)
        self.assertEqual(metrics.get("critical_value_coverage"), 1.0)
        self.assertEqual(metrics.get("scope_metadata_coverage"), 1.0)
        self.assertTrue(gate.get("passed"))
        self.assertEqual(gate.get("failed_checks"), [])

    def test_quality_gate_fails_for_scope_and_coverage_regressions(self) -> None:
        baseline = {
            "snapshot_label": "baseline",
            "upload_id": "u1",
            "row_chunks": [
                {
                    "table_row_index": 1,
                    "inferred_scope_columns": ["tariff", "prime"],
                    "scope_reason": "explicit_cells",
                    "table_row_scope_confidence": 1.0,
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_row_index": 2,
                    "inferred_scope_columns": ["tariff", "plus"],
                    "scope_reason": "explicit_cells",
                    "table_row_scope_confidence": 1.0,
                    "table_row_fee_value": "USD 2",
                },
            ],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
        }
        candidate = {
            "snapshot_label": "candidate",
            "upload_id": "u2",
            "row_chunks": [
                {
                    "table_row_index": 2,
                    "inferred_scope_columns": ["tariff"],
                    "scope_reason": "",
                    "table_row_scope_confidence": None,
                    "table_row_fee_value": "USD 99",
                },
            ],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}]}],
            "quality_metrics": {"table_bbox_coverage_ratio": 0.1, "residual_text_ratio": 0.9},
        }

        report = compare_snapshots(baseline, candidate)
        gate = evaluate_quality_gate(report)

        self.assertFalse(gate.get("passed"))
        self.assertIn("row_recall", gate.get("failed_checks") or [])
        self.assertIn("scope_f1", gate.get("failed_checks") or [])
        self.assertIn("critical_value_coverage", gate.get("failed_checks") or [])
        self.assertIn("scope_metadata_coverage", gate.get("failed_checks") or [])

    def test_quality_gate_threshold_override(self) -> None:
        report = {
            "quality_gate_metrics": {
                "row_recall": 1.0,
                "row_order_stability": 1.0,
                "scope_f1": 1.0,
                "critical_value_coverage": 1.0,
                "scope_metadata_coverage": 0.5,
            },
            "regressions": [],
        }
        default_gate = evaluate_quality_gate(report)
        self.assertFalse(default_gate.get("passed"))

        relaxed = quality_gate_thresholds(min_scope_metadata_coverage=0.5)
        relaxed_gate = evaluate_quality_gate(report, thresholds=relaxed)
        self.assertTrue(relaxed_gate.get("passed"))
