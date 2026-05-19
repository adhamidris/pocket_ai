from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.models import KnowledgeUpload
from apps.knowledge.ingestion.contracts import KnowledgeIngestionError
from apps.knowledge.ingestion.service import KnowledgeIngestionService
from core.tenancy import tenant_context


class Command(BaseCommand):
    help = (
        "Rebuild canonical knowledge chunks from persisted page/table artifacts "
        "(no file re-extraction)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--upload-id",
            action="append",
            dest="upload_ids",
            default=[],
            help="Specific upload ID(s) to rebuild.",
        )
        parser.add_argument(
            "--business-id",
            action="append",
            dest="business_ids",
            default=[],
            help="Limit rebuild to one or more business IDs.",
        )
        parser.add_argument(
            "--include-non-active",
            action="store_true",
            help="Also process non-active uploads (default processes active only).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Maximum uploads to process in this run (0 = no limit).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be rebuilt without making changes.",
        )

    def handle(self, *args, **options):
        upload_ids: list[str] = options.get("upload_ids") or []
        business_ids: list[str] = options.get("business_ids") or []
        include_non_active = bool(options.get("include_non_active"))
        limit = max(0, int(options.get("limit") or 0))
        dry_run = bool(options.get("dry_run"))

        uploads = KnowledgeUpload.objects.all().order_by("created_at")
        if not include_non_active:
            uploads = uploads.filter(status=KnowledgeStatus.ACTIVE)
        if business_ids:
            uploads = uploads.filter(business_profile_id__in=business_ids)
        if upload_ids:
            uploads = uploads.filter(id__in=upload_ids)

        total = uploads.count()
        if total == 0:
            raise CommandError("No uploads matched the provided filters.")

        service = KnowledgeIngestionService()
        processed = 0
        failed = 0
        skipped = 0

        for upload in uploads.iterator(chunk_size=50):
            if limit and processed >= limit:
                break
            with tenant_context(upload.business_profile_id):
                if dry_run:
                    try:
                        extraction = service._build_extraction_from_persisted_artifacts(upload)
                    except KnowledgeIngestionError as exc:
                        failed += 1
                        self.stderr.write(f"[dry-run] upload={upload.id} failed: {exc}")
                        continue
                    pages = len(extraction.pages or [])
                    tables = len(extraction.tables or [])
                    self.stdout.write(
                        f"[dry-run] upload={upload.id} pages={pages} tables={tables} "
                        f"format={extraction.format_hint}"
                    )
                    processed += 1
                    continue

                try:
                    service.reingest_from_persisted_artifacts(upload)
                except KnowledgeIngestionError as exc:
                    failed += 1
                    self.stderr.write(f"upload={upload.id} failed: {exc}")
                    continue
                except Exception as exc:  # pragma: no cover - defensive
                    failed += 1
                    self.stderr.write(f"upload={upload.id} unexpected error: {exc}")
                    continue

                refreshed = KnowledgeUpload.objects.filter(id=upload.id).values(
                    "chunk_count",
                    "status",
                ).first()
                if not refreshed:
                    skipped += 1
                    self.stderr.write(f"upload={upload.id} missing after rebuild; skipped")
                    continue
                processed += 1
                self.stdout.write(
                    f"upload={upload.id} rebuilt status={refreshed.get('status')} "
                    f"chunks={int(refreshed.get('chunk_count') or 0)}"
                )

        summary = f"Processed={processed} failed={failed}"
        if skipped:
            summary += f" skipped={skipped}"
        self.stdout.write(self.style.SUCCESS(summary))
