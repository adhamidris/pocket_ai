from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.conversations.maintenance_job_processing import ConversationMaintenanceJobProcessingService
from apps.conversations.models import ConversationMaintenanceJob, ConversationMaintenanceJobStatus
from core.tenancy import tenant_bypass


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued conversation maintenance jobs (Phase 6: compaction/embedding)."

    def log_queue_health(self) -> None:
        with tenant_bypass():
            queued_count = ConversationMaintenanceJob.objects.filter(status=ConversationMaintenanceJobStatus.QUEUED).count()
            running_count = ConversationMaintenanceJob.objects.filter(status=ConversationMaintenanceJobStatus.RUNNING).count()

            oldest_queued = (
                ConversationMaintenanceJob.objects.filter(status=ConversationMaintenanceJobStatus.QUEUED)
                .order_by("created_at")
                .values_list("created_at", flat=True)
                .first()
            )
            if oldest_queued:
                age_seconds = (timezone.now() - oldest_queued).total_seconds()
                age_minutes = int(age_seconds / 60)
                logger.info(
                    "Conversation jobs: %s queued, %s running, oldest queued %sm (%ss)",
                    queued_count,
                    running_count,
                    age_minutes,
                    int(age_seconds),
                )
            else:
                logger.info("Conversation jobs: %s queued, %s running", queued_count, running_count)

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
            "--lease-seconds",
            type=float,
            default=60.0,
            help="Seconds to lease a job while executing (default: 60).",
        )
        parser.add_argument(
            "--max-stale-requeues",
            type=int,
            default=25,
            help="Maximum number of stale RUNNING jobs to requeue per pass (default: 25).",
        )
        parser.add_argument(
            "--max-retry-delay-seconds",
            type=float,
            default=900.0,
            help="Maximum backoff delay for retries in seconds (default: 900).",
        )
        parser.add_argument(
            "--unsafe-backoff-seconds",
            type=float,
            default=60.0,
            help="Delay when a job is not safe to execute (pending approvals/active runs).",
        )
        parser.add_argument(
            "--log-metrics-every",
            type=int,
            default=10,
            help="Log queue health metrics every N processed jobs (default: 10, 0 to disable).",
        )

    def handle(self, *args, **options):
        max_jobs = options.get("max_jobs")
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        log_metrics_every = int(options.get("log_metrics_every") or 10)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0

        service = ConversationMaintenanceJobProcessingService(
            lease_seconds=float(options.get("lease_seconds") or 60.0),
            max_stale_requeues_per_pass=int(options.get("max_stale_requeues") or 25),
            max_retry_delay_seconds=float(options.get("max_retry_delay_seconds") or 900.0),
            unsafe_backoff_seconds=float(options.get("unsafe_backoff_seconds") or 60.0),
        )

        if log_metrics_every > 0:
            self.log_queue_health()

        processed = 0
        while True:
            if max_jobs is not None and processed >= int(max_jobs):
                break

            result = service.process_next_job()
            if result is None:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No queued conversation jobs. Watching for new work..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No queued conversation jobs."))
                break

            processed += 1
            if result.status == ConversationMaintenanceJobStatus.SUCCEEDED:
                self.stdout.write(self.style.SUCCESS(f"Completed job {result.job_id}."))
            elif result.requeued:
                self.stdout.write(self.style.WARNING(f"Requeued job {result.job_id}: {result.error or 'deferred/retry scheduled'}"))
            else:
                self.stdout.write(self.style.ERROR(f"Failed job {result.job_id}: {result.error or 'unknown error'}"))

            if log_metrics_every > 0 and processed % log_metrics_every == 0:
                self.log_queue_health()

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)

