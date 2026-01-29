from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.conversations.agent_run_processing import AgentRunProcessingService
from apps.conversations.models import AgentRunStatus


class Command(BaseCommand):
    help = "Process queued agent runs (background sub-agent executions)."

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
            help="Keep running and poll for new runs instead of exiting when the queue is empty.",
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

    def handle(self, *args, **options):
        max_runs = options.get("max_runs")
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0

        service = AgentRunProcessingService(
            lease_seconds=float(options.get("lease_seconds") or 60.0),
            max_stale_requeues_per_pass=int(options.get("max_stale_requeues") or 25),
            max_retry_delay_seconds=float(options.get("max_retry_delay_seconds") or 900.0),
        )

        processed = 0
        while True:
            if max_runs is not None and processed >= int(max_runs):
                break

            result = service.process_next_run()
            if result is None:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No queued agent runs. Watching for new work..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No queued agent runs."))
                break

            processed += 1
            if result.status == AgentRunStatus.COMPLETED:
                self.stdout.write(self.style.SUCCESS(f"Completed run {result.run_id}."))
            elif result.status in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_USER, AgentRunStatus.WAITING_EXTERNAL}:
                self.stdout.write(self.style.WARNING(f"Paused run {result.run_id}: {result.status}"))
            elif result.requeued:
                self.stdout.write(self.style.WARNING(f"Requeued run {result.run_id}: {result.error or 'retry scheduled'}"))
            else:
                self.stdout.write(self.style.ERROR(f"Failed run {result.run_id}: {result.error or 'unknown error'}"))

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)
