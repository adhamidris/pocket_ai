from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.conversations.agent_automation_processing import AgentAutomationProcessingService


class Command(BaseCommand):
    help = "Process cron-triggered automations and enqueue AgentRuns."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-triggers",
            type=int,
            default=None,
            help="Maximum number of automations to process before exiting.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Keep running and poll for due automations instead of exiting when none are due.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to sleep between polling attempts (defaults to 5s when --watch is set).",
        )

    def handle(self, *args, **options):
        max_triggers = options.get("max_triggers")
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 5.0

        service = AgentAutomationProcessingService()
        processed = 0
        while True:
            if max_triggers is not None and processed >= int(max_triggers):
                break

            result = service.process_next_due_automation()
            if result is None:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No due automations. Watching for new work..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No due automations."))
                break

            processed += 1
            if result.action == "triggered":
                self.stdout.write(self.style.SUCCESS(f"Triggered automation {result.automation_id} (run {result.run_id})."))
            elif result.action == "scheduled":
                self.stdout.write(self.style.WARNING(f"Initialized schedule for automation {result.automation_id}."))
            elif result.action == "paused":
                self.stdout.write(self.style.ERROR(f"Paused automation {result.automation_id}: {result.error or 'invalid schedule'}"))
            else:
                self.stdout.write(self.style.WARNING(f"Automation {result.automation_id}: {result.action}"))

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)

