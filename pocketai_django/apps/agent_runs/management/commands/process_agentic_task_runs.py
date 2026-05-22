from __future__ import annotations

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.agent_runs.models import AgentRunStatus
from apps.agent_runs.processing import AgentRunProcessingService


class Command(BaseCommand):
    help = "Process queued Agentic Task runs."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--max-runs", type=int, default=None, help="Maximum number of runs to process before exiting.")
        parser.add_argument("--watch", action="store_true", help="Deprecated compatibility flag; workers now watch by default.")
        parser.add_argument("--once", action="store_true", help="Process currently queued runs once, then exit.")
        parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between polling attempts.")
        parser.add_argument("--lease-seconds", type=float, default=60.0, help="Seconds to lease a run while executing.")
        parser.add_argument("--max-stale-requeues", type=int, default=25, help="Maximum stale RUNNING runs to requeue per pass.")
        parser.add_argument("--max-retry-delay-seconds", type=float, default=900.0, help="Maximum retry backoff delay in seconds.")

    def handle(self, *args, **options):
        max_runs = options.get("max_runs")
        watch = not bool(options.get("once"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0

        service = AgentRunProcessingService(
            lease_seconds=float(options.get("lease_seconds") or 60.0),
            max_stale_requeues_per_pass=int(options.get("max_stale_requeues") or 25),
            max_retry_delay_seconds=float(options.get("max_retry_delay_seconds") or 900.0),
            run_kind="agentic_task",
        )

        if watch:
            self.stdout.write(self.style.SUCCESS("Watching for queued Agentic Task runs. Use --once for a single pass."))

        processed = 0
        idle_notified = False
        while True:
            cache.set("agentic_task_run_processor_heartbeat", {"at": timezone.now().isoformat()}, timeout=180)
            if max_runs is not None and processed >= int(max_runs):
                break

            result = service.process_next_run()
            if result is None:
                if watch:
                    if not idle_notified:
                        self.stdout.write(self.style.WARNING("No queued Agentic Task runs. Watching for new work..."))
                        idle_notified = True
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No queued Agentic Task runs."))
                break

            processed += 1
            idle_notified = False
            if result.status == AgentRunStatus.COMPLETED:
                self.stdout.write(self.style.SUCCESS(f"Completed Agentic Task run {result.run_id}."))
            elif result.status in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_USER, AgentRunStatus.WAITING_EXTERNAL}:
                self.stdout.write(self.style.WARNING(f"Paused Agentic Task run {result.run_id}: {result.status}"))
            elif result.requeued:
                self.stdout.write(self.style.WARNING(f"Requeued Agentic Task run {result.run_id}: {result.error or 'retry scheduled'}"))
            else:
                self.stdout.write(self.style.ERROR(f"Failed Agentic Task run {result.run_id}: {result.error or 'unknown error'}"))

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)
