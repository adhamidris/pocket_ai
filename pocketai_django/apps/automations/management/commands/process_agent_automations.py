from __future__ import annotations

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.automations.processing import AutomationProcessingService


class Command(BaseCommand):
    help = "Process due automations and enqueue AgentRuns."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--watch", action="store_true", help="Workers watch by default.")
        parser.add_argument("--once", action="store_true", help="Process currently due automations once, then exit.")
        parser.add_argument("--sleep", type=float, default=5.0)

    def handle(self, *args, **options):
        limit = max(1, int(options.get("limit") or 25))
        watch = not bool(options.get("once"))
        sleep_seconds = max(0.5, float(options.get("sleep") or 5.0))
        service = AutomationProcessingService()

        if watch:
            self.stdout.write(self.style.SUCCESS("Watching for due automations. Use --once for a single pass."))

        idle_notified = False
        while True:
            cache.set("automation_processor_heartbeat", {"at": timezone.now().isoformat()}, timeout=180)
            processed = 0
            for _ in range(limit):
                result = service.process_next_due_automation()
                if result is None:
                    break
                processed += 1
                if result.run_id:
                    self.stdout.write(self.style.SUCCESS(f"Triggered automation {result.automation_id} (run {result.run_id})."))
                elif result.triggered_run_ids:
                    self.stdout.write(self.style.SUCCESS(f"Polled automation {result.automation_id}; runs={len(result.triggered_run_ids)}."))
                elif result.error:
                    self.stdout.write(self.style.WARNING(f"Automation {result.automation_id}: {result.action} ({result.error})."))
                else:
                    self.stdout.write(self.style.WARNING(f"Automation {result.automation_id}: {result.action}."))

            if not watch:
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No due automations."))
                return
            if processed == 0:
                if not idle_notified:
                    self.stdout.write(self.style.WARNING("No due automations. Watching for new work..."))
                    idle_notified = True
                time.sleep(sleep_seconds)
            else:
                idle_notified = False
