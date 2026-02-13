from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase


class CompareTableIngestionSnapshotsCommandTests(SimpleTestCase):
    def _write_snapshot(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _baseline_payload(self) -> dict:
        return {
            "snapshot_label": "baseline",
            "upload_id": "upload-baseline",
            "row_chunks": [
                {
                    "table_row_index": 6,
                    "applies_to_columns": ["tariff", "prime"],
                    "applicability_mode": "explicit_cells",
                    "table_row_applicability_confidence": 1.0,
                    "table_row_fee_value": "EGP",
                },
                {
                    "table_row_index": 7,
                    "applies_to_columns": ["tariff", "prime"],
                    "applicability_mode": "explicit_cells",
                    "table_row_applicability_confidence": 1.0,
                    "table_row_fee_value": "USD 5",
                },
            ],
            "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}, {"row_type": "data"}]}],
            "quality_metrics": {
                "table_bbox_coverage_ratio": 0.8,
                "residual_text_ratio": 0.1,
            },
        }

    def test_command_succeeds_when_quality_gate_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline_json = tmp_path / "baseline.json"
            candidate_json = tmp_path / "candidate.json"
            self._write_snapshot(baseline_json, self._baseline_payload())
            self._write_snapshot(candidate_json, self._baseline_payload() | {"snapshot_label": "candidate", "upload_id": "upload-candidate"})

            call_command(
                "compare_table_ingestion_snapshots",
                "--baseline-json",
                str(baseline_json),
                "--candidate-json",
                str(candidate_json),
                "--output-dir",
                str(tmp_path),
                "--label",
                "phase5_quality_gate_pass",
                "--enforce-quality-gates",
            )

            report_files = sorted(tmp_path.glob("*phase5*quality*gate*pass*.json"))
            self.assertTrue(report_files)

    def test_command_raises_when_quality_gate_fails(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline_json = tmp_path / "baseline.json"
            candidate_json = tmp_path / "candidate.json"
            baseline = self._baseline_payload()
            candidate = {
                "snapshot_label": "candidate",
                "upload_id": "upload-candidate",
                "row_chunks": [
                    {
                        "table_row_index": 7,
                        "applies_to_columns": ["tariff"],
                        "applicability_mode": "",
                        "table_row_applicability_confidence": None,
                        "table_row_fee_value": "USD 100",
                    }
                ],
                "tables": [{"rows": [{"row_type": "header"}, {"row_type": "data"}]}],
                "quality_metrics": {
                    "table_bbox_coverage_ratio": 0.4,
                    "residual_text_ratio": 0.6,
                },
            }
            self._write_snapshot(baseline_json, baseline)
            self._write_snapshot(candidate_json, candidate)

            with self.assertRaises(CommandError):
                call_command(
                    "compare_table_ingestion_snapshots",
                    "--baseline-json",
                    str(baseline_json),
                    "--candidate-json",
                    str(candidate_json),
                    "--output-dir",
                    str(tmp_path),
                    "--label",
                    "phase5_quality_gate_fail",
                    "--enforce-quality-gates",
                )
