from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.knowledge.ingestion_benchmark import (
    build_snapshot_stem,
    capture_upload_snapshot,
    parse_terms,
    resolve_output_dir,
    write_snapshot_files,
)


class Command(BaseCommand):
    help = "Capture a reproducible table-ingestion snapshot for a specific upload ID."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--upload-id", required=True, help="KnowledgeUpload UUID.")
        parser.add_argument(
            "--label",
            default="table_ingestion_snapshot",
            help="Human-readable label used in snapshot metadata and output filenames.",
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
            help="Optional term to track in chunk hits (repeatable).",
        )
        parser.add_argument(
            "--focus-row-start",
            type=int,
            default=None,
            help="Optional inclusive lower bound for focused row window.",
        )
        parser.add_argument(
            "--focus-row-end",
            type=int,
            default=None,
            help="Optional inclusive upper bound for focused row window.",
        )

    def handle(self, *args, **options):
        upload_id = str(options.get("upload_id") or "").strip()
        if not upload_id:
            raise CommandError("--upload-id is required.")
        label = str(options.get("label") or "").strip() or "table_ingestion_snapshot"
        focus_row_start = options.get("focus_row_start")
        focus_row_end = options.get("focus_row_end")
        terms = parse_terms(options.get("term") or [])
        output_dir = resolve_output_dir(options.get("output_dir"))

        try:
            snapshot = capture_upload_snapshot(
                upload_id=upload_id,
                snapshot_label=label,
                terms=terms,
                focus_row_start=focus_row_start,
                focus_row_end=focus_row_end,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        stem = build_snapshot_stem(label, upload_id)
        json_path, md_path = write_snapshot_files(snapshot, output_dir=output_dir, stem=stem)

        scope_metrics = snapshot.get("scope_metrics") or {}
        quality = snapshot.get("quality_metrics") or {}
        self.stdout.write(self.style.SUCCESS(f"Snapshot captured for upload {upload_id}."))
        self.stdout.write(f"JSON: {json_path}")
        self.stdout.write(f"MD:   {md_path}")
        self.stdout.write(
            (
                "rows={rows} multi_scope_rate={multi} ambiguous_scope_rate={ambig} "
                "coverage={coverage} residual={residual}"
            ).format(
                rows=scope_metrics.get("row_chunk_count"),
                multi=scope_metrics.get("multi_scope_rate"),
                ambig=scope_metrics.get("ambiguous_scope_rate"),
                coverage=quality.get("table_bbox_coverage_ratio"),
                residual=quality.get("residual_text_ratio"),
            )
        )
