from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.conversations.agent_watcher_processing import AgentWatcherProcessingService


class Command(BaseCommand):
    help = "Process polling-based agent watchers and enqueue AgentRuns."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-polls",
            type=int,
            default=None,
            help="Maximum number of watcher polls to process before exiting.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Keep running and poll for due watchers instead of exiting when none are due.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to sleep between polling attempts (defaults to 5s when --watch is set).",
        )
        parser.add_argument(
            "--lease-seconds",
            type=float,
            default=60.0,
            help="Seconds to lease a watcher while polling (default: 60).",
        )

    def handle(self, *args, **options):
        max_polls = options.get("max_polls")
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 5.0

        service = AgentWatcherProcessingService(lease_seconds=float(options.get("lease_seconds") or 60.0))
        processed = 0

        while True:
            if max_polls is not None and processed >= int(max_polls):
                break

            result = service.process_next_watcher()
            if result is None:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No due watchers. Watching for new work..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No due watchers."))
                break

            processed += 1
            if result.action == "polled":
                count = len(result.triggered_run_ids)
                self.stdout.write(self.style.SUCCESS(f"Polled watcher {result.watcher_id} (triggered {count} runs)."))
            elif result.action == "backoff":
                self.stdout.write(self.style.WARNING(f"Watcher {result.watcher_id} scheduled backoff: {result.error or ''}"))
            elif result.action == "paused":
                self.stdout.write(self.style.ERROR(f"Paused watcher {result.watcher_id}: {result.error or ''}"))
            else:
                self.stdout.write(self.style.WARNING(f"Watcher {result.watcher_id}: {result.action}"))

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)

