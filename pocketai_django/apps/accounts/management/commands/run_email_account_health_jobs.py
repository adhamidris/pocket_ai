from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.integrations.email_health_jobs import EmailAccountHealthJobRunner


class Command(BaseCommand):
    help = "Run background jobs that check email account health (token refresh, connectivity)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--once", action="store_true", help="Process a batch of jobs and exit.")
        parser.add_argument("--limit", type=int, default=25, help="Max jobs to process per run/loop.")
        parser.add_argument("--lease-seconds", type=int, default=120, help="Lease duration for claimed jobs.")
        parser.add_argument("--idle-sleep", type=float, default=1.5, help="Sleep duration when no jobs are found (loop mode).")

    def handle(self, *args, **options) -> None:
        runner = EmailAccountHealthJobRunner(
            lease_seconds=int(options["lease_seconds"]),
            idle_sleep_s=float(options["idle_sleep"]),
        )
        limit = int(options["limit"])
        if options["once"]:
            runner.run_once(limit=limit)
            return
        runner.run_forever(limit_per_tick=limit)
