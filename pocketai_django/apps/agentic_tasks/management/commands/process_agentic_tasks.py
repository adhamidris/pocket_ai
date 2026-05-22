from __future__ import annotations

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.agentic_tasks.processing import AgenticTaskProcessingService


class Command(BaseCommand):
    help = "Process due Agentic Tasks and enqueue AgentRuns."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--watch", action="store_true", help="Workers watch by default.")
        parser.add_argument("--once", action="store_true", help="Process currently due Agentic Tasks once, then exit.")
        parser.add_argument("--sleep", type=float, default=5.0)

    def handle(self, *args, **options):
        limit = max(1, int(options.get("limit") or 25))
        watch = not bool(options.get("once"))
        sleep_seconds = max(0.5, float(options.get("sleep") or 5.0))
        service = AgenticTaskProcessingService()

        if watch:
            self.stdout.write(self.style.SUCCESS("Watching for due Agentic Tasks. Use --once for a single pass."))

        idle_notified = False
        while True:
            cache.set("agentic_task_processor_heartbeat", {"at": timezone.now().isoformat()}, timeout=180)
            processed = 0
            for _ in range(limit):
                result = service.process_next_due_agentic_task()
                if result is None:
                    break
                processed += 1
                if result.run_id:
                    self.stdout.write(self.style.SUCCESS(f"Triggered agentic_task {result.agentic_task_id} (run {result.run_id})."))
                elif result.error:
                    self.stdout.write(self.style.WARNING(f"AgenticTask {result.agentic_task_id}: {result.action} ({result.error})."))
                else:
                    self.stdout.write(self.style.WARNING(f"AgenticTask {result.agentic_task_id}: {result.action}."))

            if not watch:
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No due Agentic Tasks."))
                return
            if processed == 0:
                if not idle_notified:
                    self.stdout.write(self.style.WARNING("No due Agentic Tasks. Watching for new work..."))
                    idle_notified = True
                time.sleep(sleep_seconds)
            else:
                idle_notified = False
