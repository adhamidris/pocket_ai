from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.ingestion_benchmark import compare_snapshots


class IngestionBenchmarkTests(SimpleTestCase):
    def test_compare_snapshots_aligns_single_table_rows_without_shared_table_ids(self) -> None:
        baseline = {
            "snapshot_label": "baseline",
            "upload_id": "u1",
            "row_chunks": [
                {
                    "table_row_index": 6,
                    "applies_to_columns": ["tariff", "wealth"],
                    "applicability_mode": "explicit_cells",
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_row_index": 7,
                    "applies_to_columns": ["tariff", "wealth"],
                    "applicability_mode": "explicit_cells",
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
                    "applies_to_columns": ["tariff", "wealth"],
                    "applicability_mode": "explicit_cells",
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_id": "table-uuid-1",
                    "table_row_index": 7,
                    "applies_to_columns": ["tariff", "wealth"],
                    "applicability_mode": "explicit_cells",
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
            "row_chunks": [{"table_row_index": 1, "applies_to_columns": ["a", "b"]}],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}]}],
        }
        candidate = {
            "snapshot_label": "candidate",
            "upload_id": "u2",
            "row_chunks": [{"table_row_index": 1, "applies_to_columns": ["a", "b"]}],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
        }

        report = compare_snapshots(baseline, candidate)
        self.assertEqual(report["baseline"]["table_count"], 1)
        self.assertEqual(report["candidate"]["table_count"], 1)
        self.assertEqual(report["baseline"]["table_data_row_count"], 1)
        self.assertEqual(report["candidate"]["table_data_row_count"], 2)
        self.assertEqual(report["deltas"]["table_data_row_count_delta"], 1)
