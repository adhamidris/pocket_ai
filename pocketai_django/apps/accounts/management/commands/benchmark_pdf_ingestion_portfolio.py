from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.ingestion_benchmark import (
    build_pdf_portfolio_stem,
    capture_pdf_portfolio_snapshot,
    load_pdf_portfolio_expectations_from_json,
    resolve_output_dir,
    write_pdf_portfolio_files,
)


class Command(BaseCommand):
    help = "Evaluate a labeled PDF ingestion portfolio against expected table-count behavior."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--expectations-json",
            required=True,
            help="Path to the JSON expectations file.",
        )
        parser.add_argument(
            "--label",
            default="",
            help="Optional report label override.",
        )
        parser.add_argument(
            "--business-id",
            default="",
            help="Optional business_profile UUID to scope filename resolution.",
        )
        parser.add_argument(
            "--output-dir",
            default="docs/ingestion_snapshots",
            help="Output directory (absolute or relative to BASE_DIR).",
        )
        parser.add_argument(
            "--enforce",
            action="store_true",
            help="Fail the command if any benchmark entry is missing or out of expectation.",
        )

    def handle(self, *args, **options):
        expectations_json = str(options.get("expectations_json") or "").strip()
        if not expectations_json:
            raise CommandError("--expectations-json is required.")

        try:
            expectations = load_pdf_portfolio_expectations_from_json(expectations_json)
        except Exception as exc:
            raise CommandError(f"Could not load expectations: {exc}") from exc

        label = str(options.get("label") or expectations.get("label") or "").strip() or "pdf_portfolio_benchmark"
        output_dir = resolve_output_dir(options.get("output_dir"))
        business_id = str(options.get("business_id") or "").strip() or None

        report = capture_pdf_portfolio_snapshot(
            expectations=expectations,
            business_id=business_id,
            label=label,
        )
        stem = build_pdf_portfolio_stem(label)
        json_path, md_path = write_pdf_portfolio_files(report, output_dir=output_dir, stem=stem)

        summary = report.get("summary") or {}
        self.stdout.write(self.style.SUCCESS("PDF ingestion portfolio benchmark completed."))
        self.stdout.write(f"JSON: {json_path}")
        self.stdout.write(f"MD:   {md_path}")
        self.stdout.write(
            "passed={passed} total={total} failed={failed} missing={missing} pass_rate={rate}".format(
                passed=report.get("passed"),
                total=summary.get("total"),
                failed=summary.get("failed"),
                missing=summary.get("missing_uploads"),
                rate=summary.get("pass_rate"),
            )
        )

        failed_entries = list(report.get("failed_entries") or [])
        if failed_entries:
            for entry in failed_entries[:10]:
                self.stdout.write(
                    self.style.WARNING(
                        "{filename}: status={status} expected_mode={mode} actual_tables={actual} failed_checks={checks}".format(
                            filename=entry.get("filename"),
                            status=entry.get("status"),
                            mode=entry.get("expected_table_mode"),
                            actual=entry.get("actual_table_count"),
                            checks=",".join(entry.get("failed_checks") or []) or "none",
                        )
                    )
                )

        if options.get("enforce") and failed_entries:
            raise CommandError(
                f"PDF portfolio benchmark failed with {len(failed_entries)} failing entries. Report: {json_path}"
            )
