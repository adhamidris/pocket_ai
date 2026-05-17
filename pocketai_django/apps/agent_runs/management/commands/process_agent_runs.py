from __future__ import annotations

import logging
import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.agent_runs.processing import AgentRunProcessingService
from apps.agent_runs.models import AgentRun, AgentRunStatus
from core.tenancy import tenant_bypass


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued agent runs (background executions)."

    def log_queue_health(self) -> None:
        """Log queue health metrics for monitoring."""
        with tenant_bypass():
            queued_count = AgentRun.objects.filter(status=AgentRunStatus.QUEUED).count()
            running_count = AgentRun.objects.filter(status=AgentRunStatus.RUNNING).count()

            # Get age of oldest queued run
            oldest_queued = (
                AgentRun.objects.filter(status=AgentRunStatus.QUEUED)
                .order_by('created_at')
                .values_list('created_at', flat=True)
                .first()
            )

            if oldest_queued:
                age_seconds = (timezone.now() - oldest_queued).total_seconds()
                age_minutes = int(age_seconds / 60)
                logger.info(
                    f"Queue health: {queued_count} queued, {running_count} running, "
                    f"oldest waiting {age_minutes}m ({age_seconds:.0f}s)"
                )

                # Warn if queue is building up
                if queued_count > 100:
                    logger.warning(f"Queue depth high: {queued_count} runs queued")
                if age_minutes > 5:
                    logger.warning(f"Queue latency high: oldest run waiting {age_minutes} minutes")
            else:
                logger.info(f"Queue health: {queued_count} queued, {running_count} running")

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-runs",
            type=int,
            default=None,
            help="Maximum number of runs to process before exiting.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Deprecated compatibility flag; workers now watch by default.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process currently queued runs once, then exit.",
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
            help="Seconds to lease a run while executing (default: 60).",
        )
        parser.add_argument(
            "--max-stale-requeues",
            type=int,
            default=25,
            help="Maximum number of stale RUNNING runs to requeue per pass (default: 25).",
        )
        parser.add_argument(
            "--max-retry-delay-seconds",
            type=float,
            default=900.0,
            help="Maximum backoff delay for retries in seconds (default: 900).",
        )
        parser.add_argument(
            "--log-metrics-every",
            type=int,
            default=10,
            help="Log queue health metrics every N processed runs (default: 10, 0 to disable).",
        )

    def handle(self, *args, **options):
        max_runs = options.get("max_runs")
        watch = not bool(options.get("once"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        log_metrics_every = int(options.get("log_metrics_every") or 10)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0

        service = AgentRunProcessingService(
            lease_seconds=float(options.get("lease_seconds") or 60.0),
            max_stale_requeues_per_pass=int(options.get("max_stale_requeues") or 25),
            max_retry_delay_seconds=float(options.get("max_retry_delay_seconds") or 900.0),
        )

        # Log initial queue health
        if log_metrics_every > 0:
            self.log_queue_health()
        if watch:
            self.stdout.write(self.style.SUCCESS("Watching for queued agent runs. Use --once for a single pass."))

        processed = 0
        idle_notified = False
        while True:
            cache.set("agent_run_processor_heartbeat", {"at": timezone.now().isoformat()}, timeout=180)
            if max_runs is not None and processed >= int(max_runs):
                break

            result = service.process_next_run()
            if result is None:
                if watch:
                    if not idle_notified:
                        self.stdout.write(self.style.WARNING("No queued agent runs. Watching for new work..."))
                        idle_notified = True
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No queued agent runs."))
                break

            processed += 1
            idle_notified = False
            if result.status == AgentRunStatus.COMPLETED:
                self.stdout.write(self.style.SUCCESS(f"Completed run {result.run_id}."))
            elif result.status in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_USER, AgentRunStatus.WAITING_EXTERNAL}:
                self.stdout.write(self.style.WARNING(f"Paused run {result.run_id}: {result.status}"))
            elif result.requeued:
                self.stdout.write(self.style.WARNING(f"Requeued run {result.run_id}: {result.error or 'retry scheduled'}"))
            else:
                self.stdout.write(self.style.ERROR(f"Failed run {result.run_id}: {result.error or 'unknown error'}"))

            # Log queue health metrics periodically
            if log_metrics_every > 0 and processed % log_metrics_every == 0:
                self.log_queue_health()

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)
