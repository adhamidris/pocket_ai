from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import (
    KnowledgeIngestionJobStatus,
    KnowledgeSourceType,
    KnowledgeStatus,
)
from apps.knowledge.models import (
    KnowledgeIngestionJob,
    KnowledgeUpload,
)
from apps.knowledge.ingestion_jobs import queue_ingestion_job


class Command(BaseCommand):
    help = "Mark stalled ingestion jobs as failed and optionally requeue their uploads."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--older-than",
            type=int,
            default=15,
            help="Minutes a job can stay RUNNING before it is considered stalled (default: 15).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Maximum number of stalled jobs to reset in this run.",
        )
        parser.add_argument(
            "--requeue",
            action="store_true",
            help="Immediately queue fresh ingestion jobs for any stalled uploads that were reset.",
        )
        parser.add_argument(
            "--requeue-missing",
            action="store_true",
            help="Requeue uploads that never produced extracted text (same behavior as process_knowledge_ingestion --requeue-missing).",
        )

    def handle(self, *args, **options):
        minutes = max(1, int(options.get("older_than") or 15))
        limit = options.get("limit")
        should_requeue = bool(options.get("requeue"))
        requeue_missing = bool(options.get("requeue_missing"))

        cutoff = timezone.now() - timedelta(minutes=minutes)
        stalled_qs = KnowledgeIngestionJob.objects.filter(
            status=KnowledgeIngestionJobStatus.RUNNING,
            started_at__lt=cutoff,
        ).order_by("started_at")
        if limit:
            stalled_qs = stalled_qs[: int(limit)]
        stalled_jobs = list(stalled_qs.select_related("upload"))

        if not stalled_jobs:
            self.stdout.write(self.style.SUCCESS("No stalled ingestion jobs detected."))
        else:
            self.stdout.write(
                f"Resetting {len(stalled_jobs)} stalled job{'s' if len(stalled_jobs) != 1 else ''} older than {minutes} minute(s)…"
            )
        upload_ids: list[KnowledgeUpload] = []
        now = timezone.now()
        for job in stalled_jobs:
            KnowledgeIngestionJob.objects.filter(id=job.id).update(
                status=KnowledgeIngestionJobStatus.FAILED,
                finished_at=now,
                error_detail="auto-reset: stalled ingestion job",
            )
            if should_requeue and job.upload_id:
                upload_ids.append(job.upload_id)
        if should_requeue and upload_ids:
            uploads = KnowledgeUpload.objects.filter(id__in=upload_ids)
            queued = 0
            for upload in uploads:
                queued_job = queue_ingestion_job(upload, trigger="stalled_auto_requeue", force=True)
                if queued_job:
                    queued += 1
            self.stdout.write(self.style.SUCCESS(f"Requeued {queued} stalled upload{'s' if queued != 1 else ''}."))

        if requeue_missing:
            queued_missing = self._requeue_missing_uploads()
            if queued_missing:
                self.stdout.write(self.style.SUCCESS(f"Queued {queued_missing} upload{'s' if queued_missing != 1 else ''} missing extracted text."))
            else:
                self.stdout.write(self.style.SUCCESS("No uploads required missing-text requeue."))

    def _requeue_missing_uploads(self) -> int:
        uploads = (
            KnowledgeUpload.objects.filter(
                source_type__in=[
                    KnowledgeSourceType.FILE,
                    KnowledgeSourceType.LINK,
                    KnowledgeSourceType.INTEGRATION,
                ],
                text_detail__isnull=True,
            )
            .exclude(status=KnowledgeStatus.ARCHIVED)
            .order_by("created_at")
        )
        queued = 0
        for upload in uploads:
            job = queue_ingestion_job(upload, trigger="missing_text_auto_requeue", force=True)
            if job:
                queued += 1
        return queued
