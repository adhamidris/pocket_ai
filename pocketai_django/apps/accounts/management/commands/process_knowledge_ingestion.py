from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.accounts.models import (
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeSourceType,
    KnowledgeStatus,
    KnowledgeUpload,
)
from apps.services.knowledge_ingestion import KnowledgeIngestionService, queue_ingestion_job


class Command(BaseCommand):
    help = "Process queued knowledge ingestion jobs."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=None,
            help="Maximum number of jobs to process before exiting.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Keep running and poll for new jobs instead of exiting when the queue is empty.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to sleep between polling attempts (defaults to 2s when --watch is set).",
        )
        parser.add_argument(
            "--requeue-missing",
            action="store_true",
            help="Queue ingestion jobs for uploads missing extracted text.",
        )

    def handle(self, *args, **options):
        if options.get("requeue_missing"):
            queued = self._requeue_missing()
            self.stdout.write(self.style.SUCCESS(f"Queued {queued} uploads for ingestion."))

        service = KnowledgeIngestionService()
        max_jobs = options.get("max_jobs")
        watch = bool(options.get("watch"))
        sleep_seconds = options.get("sleep") or 0.0
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0
        processed = 0

        while True:
            if max_jobs is not None and processed >= max_jobs:
                break

            result = service.process_next_job()
            if result is None:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No queued ingestion jobs. Watching for new work..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No queued ingestion jobs."))
                break

            processed += 1
            if result.status == KnowledgeIngestionJobStatus.COMPLETED:
                units = "chars" if result.job_type == KnowledgeIngestionJobType.INGEST else "chunks"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Processed {result.job_type} job {result.job_id} for upload {result.upload_id} ({result.characters} {units})."
                    )
                )
            elif result.status == KnowledgeIngestionJobStatus.QUEUED:
                self.stdout.write(
                    self.style.WARNING(
                        f"Requeued {result.job_type} job {result.job_id} for upload {result.upload_id}: {result.error or 'retry scheduled'}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"Failed job {result.job_id} for upload {result.upload_id}: {result.error or 'unknown error'}"
                    )
                )
            if sleep_seconds and watch:
                time.sleep(sleep_seconds)

    def _requeue_missing(self) -> int:
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
            job = queue_ingestion_job(upload, trigger="requeue_missing", force=True)
            if job:
                queued += 1
        return queued
