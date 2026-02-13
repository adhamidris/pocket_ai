from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.ingestion_benchmark import compare_snapshots, evaluate_quality_gate, quality_gate_thresholds


class IngestionBenchmarkTests(SimpleTestCase):
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
