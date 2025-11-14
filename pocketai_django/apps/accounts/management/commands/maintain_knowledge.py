from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from apps.accounts.models import KnowledgeAlias, KnowledgeEntity


class Command(BaseCommand):
    help = "Perform hygiene tasks for knowledge tables (orphans + optional vacuum)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--skip-orphans",
            action="store_true",
            help="Skip orphan cleanup.",
        )
        parser.add_argument(
            "--skip-vacuum",
            action="store_true",
            help="Skip VACUUM/ANALYZE passes.",
        )

    def handle(self, *args, **options):
        if not options.get("skip_orphans"):
            self._purge_orphans()
        if not options.get("skip_vacuum"):
            self._vacuum_tables()

    def _purge_orphans(self) -> None:
        alias_deleted, _ = KnowledgeAlias.objects.filter(entity__isnull=True).delete()
        entity_deleted, _ = KnowledgeEntity.objects.filter(upload__isnull=True).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Removed {alias_deleted} orphaned aliases and {entity_deleted} orphaned entities.",
            )
        )

    def _vacuum_tables(self) -> None:
        tables = (
            "accounts_knowledge_upload_chunk",
            "accounts_knowledge_alias",
        )
        with connection.cursor() as cursor:
            for table in tables:
                start = time.perf_counter()
                cursor.execute(f"VACUUM (ANALYZE) {table}")
                duration = time.perf_counter() - start
                self.stdout.write(self.style.SUCCESS(f"Vacuumed {table} in {duration:.2f}s"))
