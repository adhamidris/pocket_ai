from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.ingestion.benchmark import (
    build_comparison_stem,
    build_snapshot_stem,
    capture_upload_snapshot,
    compare_snapshots,
    evaluate_quality_gate,
    load_snapshot_from_json,
    parse_terms,
    quality_gate_thresholds,
    resolve_output_dir,
    write_comparison_files,
    write_snapshot_files,
)


class Command(BaseCommand):
    help = "Compare table-ingestion snapshots and emit deterministic JSON/Markdown deltas."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--baseline-json",
            required=True,
            help="Path to baseline snapshot JSON.",
        )
        parser.add_argument(
            "--candidate-json",
            default="",
            help="Path to candidate snapshot JSON. Optional when --candidate-upload-id is provided.",
        )
        parser.add_argument(
            "--candidate-upload-id",
            default="",
            help="Candidate upload UUID. If provided, snapshot is captured before comparison.",
        )
        parser.add_argument(
            "--candidate-label",
            default="table_ingestion_candidate",
            help="Label used when capturing candidate from upload ID.",
        )
        parser.add_argument(
            "--label",
            default="table_ingestion_comparison",
            help="Label used for output comparison filenames.",
        )
        parser.add_argument(
            "--output-dir",
            default="docs/ingestion_snapshots",
            help="Output directory (absolute or relative to BASE_DIR).",
        )
        parser.add_argument(
            "--term",
            action="append",
            default=[],
            help="Optional term to track in candidate capture mode (repeatable).",
        )
        parser.add_argument(
            "--focus-row-start",
            type=int,
            default=None,
            help="Optional inclusive lower bound for row-window comparison.",
        )
        parser.add_argument(
            "--focus-row-end",
            type=int,
            default=None,
            help="Optional inclusive upper bound for row-window comparison.",
        )
        parser.add_argument(
            "--enforce-quality-gates",
            action="store_true",
            help="Fail command when quality-gate thresholds or regression checks are violated.",
        )
        parser.add_argument(
            "--min-row-recall",
            type=float,
            default=None,
            help="Override minimum row recall threshold.",
        )
        parser.add_argument(
            "--min-row-order-stability",
            type=float,
            default=None,
            help="Override minimum row-order stability threshold.",
        )
        parser.add_argument(
            "--min-scope-f1",
            type=float,
            default=None,
            help="Override minimum scope F1 threshold.",
        )
        parser.add_argument(
            "--min-critical-value-coverage",
            type=float,
            default=None,
            help="Override minimum critical-value coverage threshold.",
        )
        parser.add_argument(
            "--min-scope-metadata-coverage",
            type=float,
            default=None,
            help="Override minimum scope metadata coverage threshold.",
        )

    def handle(self, *args, **options):
        baseline_json = str(options.get("baseline_json") or "").strip()
        candidate_json = str(options.get("candidate_json") or "").strip()
        candidate_upload_id = str(options.get("candidate_upload_id") or "").strip()
        candidate_label = str(options.get("candidate_label") or "").strip() or "table_ingestion_candidate"
        label = str(options.get("label") or "").strip() or "table_ingestion_comparison"
        focus_row_start = options.get("focus_row_start")
        focus_row_end = options.get("focus_row_end")
        terms = parse_terms(options.get("term") or [])
        output_dir = resolve_output_dir(options.get("output_dir"))
        thresholds = quality_gate_thresholds(
            min_row_recall=options.get("min_row_recall"),
            min_row_order_stability=options.get("min_row_order_stability"),
            min_scope_f1=options.get("min_scope_f1"),
            min_critical_value_coverage=options.get("min_critical_value_coverage"),
            min_scope_metadata_coverage=options.get("min_scope_metadata_coverage"),
        )
        enforce_quality_gates = bool(options.get("enforce_quality_gates"))

        if not baseline_json:
            raise CommandError("--baseline-json is required.")
        if not Path(baseline_json).exists():
            raise CommandError(f"Baseline snapshot file not found: {baseline_json}")
        if candidate_json and candidate_upload_id:
            raise CommandError("Specify only one of --candidate-json or --candidate-upload-id.")
        if not candidate_json and not candidate_upload_id:
            raise CommandError("Provide either --candidate-json or --candidate-upload-id.")

        baseline = load_snapshot_from_json(baseline_json)

        captured_candidate_json: Path | None = None
        captured_candidate_md: Path | None = None
        if candidate_upload_id:
            try:
                candidate = capture_upload_snapshot(
                    upload_id=candidate_upload_id,
                    snapshot_label=candidate_label,
                    terms=terms,
                    focus_row_start=focus_row_start,
                    focus_row_end=focus_row_end,
                )
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
            candidate_stem = build_snapshot_stem(candidate_label, candidate_upload_id)
            captured_candidate_json, captured_candidate_md = write_snapshot_files(
                candidate,
                output_dir=output_dir,
                stem=candidate_stem,
            )
        else:
            if not Path(candidate_json).exists():
                raise CommandError(f"Candidate snapshot file not found: {candidate_json}")
            candidate = load_snapshot_from_json(candidate_json)

        report = compare_snapshots(
            baseline,
            candidate,
            focus_row_start=focus_row_start,
            focus_row_end=focus_row_end,
            thresholds=thresholds,
        )
        quality_gate = evaluate_quality_gate(report, thresholds=thresholds)
        report["quality_gate"] = quality_gate
        comparison_stem = build_comparison_stem(label)
        report_json, report_md = write_comparison_files(report, output_dir=output_dir, stem=comparison_stem)

        deltas = report.get("deltas") or {}
        regressions = report.get("regressions") or []
        self.stdout.write(self.style.SUCCESS("Table-ingestion comparison completed."))
        if captured_candidate_json and captured_candidate_md:
            self.stdout.write(f"Candidate JSON: {captured_candidate_json}")
            self.stdout.write(f"Candidate MD:   {captured_candidate_md}")
        self.stdout.write(f"Comparison JSON: {report_json}")
        self.stdout.write(f"Comparison MD:   {report_md}")
        self.stdout.write(
            (
                "quality_gate_passed={passed} row_recall={row_recall} row_order_stability={row_order} "
                "scope_f1={scope_f1} critical_value_coverage={critical} scope_metadata_coverage={scope_metadata}"
            ).format(
                passed=quality_gate.get("passed"),
                row_recall=(quality_gate.get("metrics") or {}).get("row_recall"),
                row_order=(quality_gate.get("metrics") or {}).get("row_order_stability"),
                scope_f1=(quality_gate.get("metrics") or {}).get("scope_f1"),
                critical=(quality_gate.get("metrics") or {}).get("critical_value_coverage"),
                scope_metadata=(quality_gate.get("metrics") or {}).get("scope_metadata_coverage"),
            )
        )
        self.stdout.write(
            (
                "row_chunk_delta={row_delta} multi_scope_delta={multi_delta} ambiguous_delta={ambig_delta} "
                "coverage_delta={coverage_delta} residual_delta={residual_delta}"
            ).format(
                row_delta=deltas.get("row_chunk_count_delta"),
                multi_delta=deltas.get("multi_scope_rate_delta"),
                ambig_delta=deltas.get("ambiguous_scope_rate_delta"),
                coverage_delta=deltas.get("table_bbox_coverage_ratio_delta"),
                residual_delta=deltas.get("residual_text_ratio_delta"),
            )
        )
        if regressions:
            self.stdout.write(self.style.WARNING(f"Regressions: {', '.join(str(item) for item in regressions)}"))
        else:
            self.stdout.write(self.style.SUCCESS("Regressions: none"))
        if enforce_quality_gates and not quality_gate.get("passed"):
            failed_checks = ", ".join(str(item) for item in (quality_gate.get("failed_checks") or [])) or "none"
            regression_checks = ", ".join(str(item) for item in (quality_gate.get("regressions") or [])) or "none"
            raise CommandError(
                "Quality gate failed. "
                f"failed_checks={failed_checks}; regressions={regression_checks}; report={report_json}"
            )
