from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.models import KnowledgeUpload
from apps.knowledge.ingestion.jobs import queue_ingestion_job
from apps.knowledge.preflight.service import ensure_upload_preflight


NEEDS_REUPLOAD_WARNINGS = {
    "Missing file metadata; ingestion cannot run.",
    "File is missing from storage; ingestion cannot read it.",
}


class Command(BaseCommand):
    help = (
        "Queue full knowledge re-ingestion jobs from the original uploaded sources "
        "(no delete/re-upload required)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--all",
            action="store_true",
            dest="all_uploads",
            help="Queue re-ingestion for all matching uploads.",
        )
        parser.add_argument(
            "--upload-id",
            action="append",
            dest="upload_ids",
            default=[],
            help="Specific upload ID(s) to re-ingest.",
        )
        parser.add_argument(
            "--business-id",
            action="append",
            dest="business_ids",
            default=[],
            help="Limit re-ingestion to one or more business IDs.",
        )
        parser.add_argument(
            "--include-archived",
            action="store_true",
            help="Also include archived uploads (default excludes archived).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum uploads to queue in this run (0 = no limit).",
        )
        parser.add_argument(
            "--trigger",
            default="bulk_manual_reingest",
            help="Trigger label stored in the queued ingestion job payload.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be queued without creating jobs.",
        )

    @staticmethod
    def _summarize_preflight(preflight: dict | None) -> tuple[str, str]:
        if not isinstance(preflight, dict):
            return ("unknown", "Preflight returned no data.")
        warnings = [str(item).strip() for item in (preflight.get("warnings") or []) if str(item).strip()]
        status = str(preflight.get("status") or "").lower()
        if any(warning in NEEDS_REUPLOAD_WARNINGS for warning in warnings):
            return ("needs_reupload", "; ".join(warnings))
        if status == "error":
            return ("blocked", "; ".join(warnings) or "Preflight blocked ingestion.")
        return ("reingestable", "; ".join(warnings))

    @staticmethod
    def _upload_label(upload: KnowledgeUpload) -> str:
        return (
            str(upload.display_name or "").strip()
            or str(upload.source_name or "").strip()
            or str(upload.id)
        )

    @staticmethod
    def _file_fields(upload: KnowledgeUpload) -> str:
        try:
            file_detail = upload.file_detail
        except Exception:
            file_detail = None
        filename = getattr(file_detail, "filename", "") or str(upload.source_name or "").strip() or "-"
        storage_path = getattr(file_detail, "storage_path", "") or "-"
        return f"filename={filename!r} storage_path={storage_path!r}"

    def handle(self, *args, **options):
        all_uploads = bool(options.get("all_uploads"))
        upload_ids: list[str] = options.get("upload_ids") or []
        business_ids: list[str] = options.get("business_ids") or []
        include_archived = bool(options.get("include_archived"))
        limit = max(0, int(options.get("limit") or 0))
        trigger = str(options.get("trigger") or "bulk_manual_reingest").strip() or "bulk_manual_reingest"
        dry_run = bool(options.get("dry_run"))

        if not all_uploads and not upload_ids and not business_ids:
            raise CommandError("Provide --all, --business-id, or --upload-id to choose what to re-ingest.")

        uploads = KnowledgeUpload.objects.select_related("business_profile", "file_detail").order_by("created_at")
        if not include_archived:
            uploads = uploads.exclude(status=KnowledgeStatus.ARCHIVED)
        if business_ids:
            uploads = uploads.filter(business_profile_id__in=business_ids)
        if upload_ids:
            uploads = uploads.filter(id__in=upload_ids)

        total = uploads.count()
        if total == 0:
            raise CommandError("No uploads matched the provided filters.")

        queued = 0
        already_running = 0
        already_scheduled = 0
        inspected = 0
        reingestable = 0
        needs_reupload = 0
        blocked = 0

        for upload in uploads.iterator(chunk_size=100):
            if limit and inspected >= limit:
                break
            inspected += 1

            preflight = ensure_upload_preflight(upload, trigger="queue_knowledge_reingestion", force=True)
            classification, detail = self._summarize_preflight(preflight)

            if classification == "needs_reupload":
                needs_reupload += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"upload={upload.id} name={self._upload_label(upload)!r} "
                        f"business={upload.business_profile_id} {self._file_fields(upload)} "
                        f"needs_reupload reason={detail}"
                    )
                )
                continue

            if classification == "blocked":
                blocked += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"upload={upload.id} name={self._upload_label(upload)!r} "
                        f"business={upload.business_profile_id} {self._file_fields(upload)} "
                        f"blocked reason={detail}"
                    )
                )
                continue

            reingestable += 1

            if dry_run:
                self.stdout.write(
                    f"[dry-run] upload={upload.id} name={self._upload_label(upload)!r} "
                    f"reingestable=yes business={upload.business_profile_id} "
                    f"status={upload.status} source={upload.source_type} {self._file_fields(upload)}"
                )
                continue

            job = queue_ingestion_job(upload, trigger=trigger, force=True)
            if job is None:
                self.stdout.write(
                    self.style.WARNING(
                        f"upload={upload.id} name={self._upload_label(upload)!r} "
                        f"{self._file_fields(upload)} skipped: source type {upload.source_type!r} does not support ingestion jobs"
                    )
                )
                continue
            if job.status == "running":
                already_running += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"upload={upload.id} name={self._upload_label(upload)!r} "
                        f"{self._file_fields(upload)} kept running job={job.id}"
                    )
                )
                continue
            if job.status in {"queued", "deferred"}:
                queued += 1
                self.stdout.write(
                    f"upload={upload.id} name={self._upload_label(upload)!r} "
                    f"{self._file_fields(upload)} queued job={job.id} status={job.status}"
                )
                continue
            already_scheduled += 1
            self.stdout.write(
                self.style.WARNING(
                    f"upload={upload.id} name={self._upload_label(upload)!r} "
                    f"{self._file_fields(upload)} returned existing job={job.id} status={job.status}"
                )
            )

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Matched={inspected} reingestable={reingestable} needs_reupload={needs_reupload} "
                    f"blocked={blocked} dry_run=yes"
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Matched={inspected} reingestable={reingestable} needs_reupload={needs_reupload} "
                f"blocked={blocked} queued={queued} running_kept={already_running} other_existing={already_scheduled}"
            )
        )
        if already_running:
            self.stdout.write(
                self.style.WARNING(
                    "Running jobs are kept in place. For a clean bulk re-ingest, stop ingestion workers first, "
                    "queue this command, then run `manage.py process_knowledge_ingestion --watch`."
                )
            )
