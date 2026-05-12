from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from apps.accounts.models import BusinessProfile
from apps.conversations.retention_purge import TenantRetentionPurgeService
from core.tenancy import tenant_bypass


class Command(BaseCommand):
    help = "Purge expired memory items and compacted history segments per-tenant retention policy."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply deletions (default is dry-run).",
        )
        parser.add_argument(
            "--business-id",
            type=str,
            default=None,
            help="Purge only a single tenant (BusinessProfile UUID).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Delete in batches to reduce locks (default: 1000).",
        )
        parser.add_argument(
            "--vacuum",
            action="store_true",
            help="Run VACUUM (ANALYZE) on affected tables after purge (Postgres only).",
        )
        parser.add_argument(
            "--max-businesses",
            type=int,
            default=None,
            help="Stop after processing N businesses (for staged rollouts).",
        )

    def handle(self, *args, **options):
        dry_run = not bool(options.get("apply"))
        batch_size = max(50, int(options.get("batch_size") or 1000))
        max_businesses = options.get("max_businesses")
        if max_businesses is not None:
            max_businesses = max(1, int(max_businesses))

        business_id_raw = (options.get("business_id") or "").strip()
        business_id = None
        if business_id_raw:
            try:
                business_id = uuid.UUID(business_id_raw)
            except ValueError:
                raise SystemExit("--business-id must be a UUID")

        service = TenantRetentionPurgeService()
        now = timezone.now()

        total_deleted_memory_items = 0
        total_deleted_segments = 0
        total_trimmed_segments = 0
        total_updated_segments = 0
        total_skipped = 0
        total_processed = 0

        mode = "DRY RUN" if dry_run else "APPLY"
        self.stdout.write(f"Retention purge ({mode}) started at {now.isoformat()}")

        with tenant_bypass():
            qs = BusinessProfile.objects.all().only("id")
            # Cheap pre-filter: only tenants that have a max retention configured.
            if business_id is None:
                qs = qs.filter(memory_config__maximum_retention_days__isnull=False)
            else:
                qs = qs.filter(id=business_id)

            for business in qs.iterator(chunk_size=200):
                total_processed += 1
                result = service.purge_business(business, dry_run=dry_run, batch_size=batch_size, now=now)
                if result.skipped:
                    total_skipped += 1
                    self.stdout.write(
                        f"- {result.business_id}: skipped ({result.skip_reason}) max_retention_days={result.max_retention_days}"
                    )
                else:
                    total_deleted_memory_items += result.deleted_memory_items
                    total_deleted_segments += result.deleted_segments
                    total_trimmed_segments += result.trimmed_segments
                    total_updated_segments += result.updated_segments
                    self.stdout.write(
                        f"- {result.business_id}: memory_items={result.deleted_memory_items} "
                        f"segments_deleted={result.deleted_segments} segments_trimmed={result.trimmed_segments} "
                        f"segments_updated={result.updated_segments} max_retention_days={result.max_retention_days}"
                    )
                    if result.errors:
                        for err in result.errors[:10]:
                            self.stdout.write(f"  ! {err}")

                if max_businesses is not None and total_processed >= max_businesses:
                    break

        self.stdout.write(
            "Done. processed=%s skipped=%s deleted_memory_items=%s deleted_segments=%s trimmed_segments=%s updated_segments=%s"
            % (
                total_processed,
                total_skipped,
                total_deleted_memory_items,
                total_deleted_segments,
                total_trimmed_segments,
                total_updated_segments,
            )
        )

        if options.get("vacuum"):
            self._vacuum_tables()

    def _vacuum_tables(self) -> None:
        if connection.vendor != "postgresql":
            self.stdout.write(self.style.WARNING("VACUUM skipped (not PostgreSQL)."))
            return

        tables = (
            "conversations_memory_item",
            "conversations_compacted_history_segment",
        )
        # VACUUM cannot run inside a transaction; ensure autocommit.
        was_autocommit = connection.get_autocommit()
        try:
            if not was_autocommit:
                connection.set_autocommit(True)
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f"VACUUM (ANALYZE) {table}")
                    self.stdout.write(self.style.SUCCESS(f"Vacuumed {table}"))
        finally:
            if not was_autocommit:
                connection.set_autocommit(False)
