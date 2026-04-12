from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.crm.imports import process_next_job


class Command(BaseCommand):
    help = "Process queued CRM import jobs."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true", help="Keep polling for queued jobs.")
        parser.add_argument("--sleep", type=float, default=2.0, help="Polling interval when --watch is used.")

    def handle(self, *args, **options):
        watch = bool(options.get("watch"))
        sleep_seconds = max(0.25, float(options.get("sleep") or 2.0))
        while True:
            job = process_next_job()
            if job is None and not watch:
                return
            if not watch:
                continue
            if job is None:
                time.sleep(sleep_seconds)
