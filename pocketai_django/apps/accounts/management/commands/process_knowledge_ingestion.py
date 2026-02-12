from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.accounts.models import (
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeSourceType,
    KnowledgeStatus,
)
from apps.knowledge.models import KnowledgeUpload
from apps.knowledge.knowledge_ingestion import (
    KnowledgeIngestionService,
    get_ingestion_queue_health,
    queue_ingestion_job,
)
from apps.rag.rag_logging import structured_log


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
        parser.add_argument(
            "--requeue-text-missing-chunks",
            action="store_true",
            help="Queue ingestion jobs for manual text uploads that have no chunks.",
        )
        parser.add_argument(
            "--health-interval",
            type=float,
            default=None,
            help="Seconds between queue health logs when --watch is set (defaults to INGEST_WORKER_HEALTH_INTERVAL_SECONDS).",
        )

    def handle(self, *args, **options):
        if options.get("requeue_missing"):
            queued = self._requeue_missing()
            self.stdout.write(self.style.SUCCESS(f"Queued {queued} uploads for ingestion."))
        if options.get("requeue_text_missing_chunks"):
            queued = self._requeue_text_missing_chunks()
            self.stdout.write(self.style.SUCCESS(f"Queued {queued} manual text uploads for ingestion."))

        service = KnowledgeIngestionService()
        max_jobs = options.get("max_jobs")
        watch = bool(options.get("watch"))
        sleep_seconds = options.get("sleep") or 0.0
        health_interval = options.get("health_interval")
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0
        if watch and health_interval is None:
            health_interval = float(getattr(settings, "INGEST_WORKER_HEALTH_INTERVAL_SECONDS", 60.0) or 60.0)
        if not watch:
            health_interval = 0.0
        processed = 0
        last_health_logged = 0.0

        while True:
            if max_jobs is not None and processed >= max_jobs:
                break
            if health_interval and (time.time() - last_health_logged) >= float(health_interval):
                self._log_queue_health()
                last_health_logged = time.time()

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

    def _log_queue_health(self) -> None:
        health = get_ingestion_queue_health()
        warn_backlog = int(getattr(settings, "INGEST_QUEUE_WARN_BACKLOG", 0) or 0)
        warn_oldest = int(getattr(settings, "INGEST_QUEUE_WARN_OLDEST_SECONDS", 0) or 0)
        warn_failed = int(getattr(settings, "INGEST_QUEUE_WARN_FAILED_LAST_HOUR", 0) or 0)

        pending = int(health.get("pending") or 0)
        oldest_s = health.get("oldest_pending_age_s")
        failed_hour = int(health.get("failed_last_hour") or 0)

        should_warn = False
        if warn_backlog and pending >= warn_backlog:
            should_warn = True
        if warn_oldest and isinstance(oldest_s, int) and oldest_s >= warn_oldest:
            should_warn = True
        if warn_failed and failed_hour >= warn_failed:
            should_warn = True

        structured_log(
            "rag",
            "ingest.queue_health",
            {
                "pending": pending,
                "queued": health.get("queued"),
                "deferred": health.get("deferred"),
                "running": health.get("running"),
                "failed_last_hour": failed_hour,
                "oldest_pending_age_s": oldest_s,
                "warn_backlog": warn_backlog or None,
                "warn_oldest_age_s": warn_oldest or None,
                "warn_failed_last_hour": warn_failed or None,
            },
            context={"worker": "process_knowledge_ingestion"},
            level=(logging.WARNING if should_warn else logging.INFO),
        )
        line = (
            f"Queue health: pending={pending} queued={health.get('queued')} deferred={health.get('deferred')} "
            f"running={health.get('running')} failed_last_hour={failed_hour} oldest_pending_age_s={oldest_s}"
        )
        if should_warn:
            self.stdout.write(self.style.WARNING(line))
        else:
            self.stdout.write(line)

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

    def _requeue_text_missing_chunks(self) -> int:
        uploads = (
            KnowledgeUpload.objects.filter(
                source_type=KnowledgeSourceType.TEXT,
                chunks__isnull=True,
            )
            .exclude(status=KnowledgeStatus.ARCHIVED)
            .order_by("created_at")
        )
        queued = 0
        for upload in uploads:
            if upload.status != KnowledgeStatus.PROCESSING:
                upload.status = KnowledgeStatus.PROCESSING
                upload.ingestion_error = ""
                upload.save(update_fields=["status", "ingestion_error", "updated_at"])
            job = queue_ingestion_job(upload, trigger="requeue_text_missing_chunks", force=True)
            if job:
                queued += 1
        return queued
