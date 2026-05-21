from __future__ import annotations

import time
import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import KnowledgeIntegrationStatus
from apps.integrations.models import KnowledgeIntegration
from apps.integrations.sync.service import IntegrationSyncService, IntegrationSyncError


class Command(BaseCommand):
    help = "Sync integration-backed knowledge uploads and queue ingestion jobs."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--integration-id",
            dest="integration_id",
            help="Run sync for a specific KnowledgeIntegration UUID.",
        )
        parser.add_argument(
            "--business-id",
            dest="business_id",
            help="Limit sync to a specific BusinessProfile UUID.",
        )
        parser.add_argument(
            "--resource-id",
            dest="resource_ids",
            action="append",
            help="Limit sync to specific resource_ids (repeat for multiples).",
        )
        parser.add_argument(
            "--include-error",
            action="store_true",
            dest="include_error",
            help="Also sync integrations currently in ERROR status.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            dest="watch",
            help="Keep running and re-sync integrations on an interval.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=300.0,
            dest="sleep",
            help="Seconds to wait between watch cycles (default: 300).",
        )
        parser.add_argument(
            "--ignore-schedule",
            action="store_true",
            dest="ignore_schedule",
            help="Trigger sync regardless of integration schedule cadence.",
        )

    def handle(self, *args, **options):
        integration_id = self._parse_uuid(options.get("integration_id"), "integration-id")
        business_id = self._parse_uuid(options.get("business_id"), "business-id")
        resource_ids = options.get("resource_ids") or None
        include_error = bool(options.get("include_error"))
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 300.0)
        ignore_schedule = bool(options.get("ignore_schedule"))

        service = IntegrationSyncService()

        def run_cycle() -> int:
            integrations = self._fetch_integrations(integration_id, business_id, include_error)
            if not integrations:
                self.stdout.write(self.style.WARNING("No integrations matched the provided filters."))
                return 0
            try:
                results = service.sync_integrations(
                    integrations,
                    resource_ids=resource_ids,
                    ignore_schedule=ignore_schedule,
                )
            except IntegrationSyncError as exc:
                raise CommandError(str(exc)) from exc

            for result in results:
                summary = (
                    f"integration={result.integration_id} provider={result.provider} status={result.status}"
                )
                if result.status != "completed":
                    if result.message:
                        summary = f"{summary} reason={result.message}"
                    self.stdout.write(self.style.WARNING(summary))
                    continue
                success = result.success_count
                failures = result.failure_count
                summary = f"{summary} success={success} failed={failures} rows={result.rows_ingested}"
                line_writer = self.style.WARNING if failures else self.style.SUCCESS
                self.stdout.write(line_writer(summary))
                for outcome in result.resources:
                    if outcome.status == "success":
                        msg = (
                            f"  - {outcome.resource_id}: wrote {outcome.bytes_written} bytes, "
                            f"rows={outcome.rows_ingested}, upload={outcome.upload_id} job={outcome.job_id or 'n/a'}"
                        )
                        self.stdout.write(msg)
                    elif outcome.status == "unchanged":
                        msg = f"  - {outcome.resource_id}: no changes detected (rows=0), skipped ingest"
                        self.stdout.write(msg)
                    else:
                        msg = f"  - {outcome.resource_id}: FAILED {outcome.message or 'Unknown error'}"
                        self.stdout.write(self.style.ERROR(msg))
            return len(results)

        if watch:
            self.stdout.write(self.style.WARNING("Watching for integrations to sync..."))
            while True:
                processed = run_cycle()
                if sleep_seconds > 0:
                    if processed:
                        self.stdout.write(f"Sleeping for {sleep_seconds:.0f}s before next cycle...")
                    time.sleep(sleep_seconds)
        else:
            processed = run_cycle()
            if processed == 0:
                # When nothing matched but we were not watching, exit with success message.
                self.stdout.write("No integrations processed.")

    def _fetch_integrations(self, integration_id, business_id, include_error: bool) -> list[KnowledgeIntegration]:
        statuses = [KnowledgeIntegrationStatus.CONNECTED, KnowledgeIntegrationStatus.SYNCING]
        if include_error:
            statuses.append(KnowledgeIntegrationStatus.ERROR)
        qs = (
            KnowledgeIntegration.objects.filter(status__in=statuses)
            .select_related("business_profile", "created_by")
            .order_by("name")
        )
        if integration_id:
            qs = qs.filter(id=integration_id)
        if business_id:
            qs = qs.filter(business_profile_id=business_id)
        return list(qs)

    def _parse_uuid(self, value: str | None, field: str):
        if not value:
            return None
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise CommandError(f"{field} must be a valid UUID") from exc
