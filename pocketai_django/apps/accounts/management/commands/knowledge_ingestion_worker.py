from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run an always-on knowledge ingestion worker (alias for process_knowledge_ingestion --watch)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--sleep",
            type=float,
            default=2.0,
            help="Seconds to sleep between polling attempts (default: 2).",
        )
        parser.add_argument(
            "--health-interval",
            type=float,
            default=None,
            help="Seconds between queue health logs (defaults to INGEST_WORKER_HEALTH_INTERVAL_SECONDS).",
        )
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=None,
            help="Maximum number of jobs to process before exiting (optional).",
        )

    def handle(self, *args, **options):
        call_command(
            "process_knowledge_ingestion",
            watch=True,
            sleep=options.get("sleep"),
            health_interval=options.get("health_interval"),
            max_jobs=options.get("max_jobs"),
        )
