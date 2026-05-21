from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.conversations.models import PortalTurn, PortalTurnStatus
from apps.conversations.portal_turn.processing import PortalTurnProcessingService
from core.tenancy import tenant_bypass


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued portal turns (DB-leased chat portal streaming executions)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-turns",
            type=int,
            default=None,
            help="Maximum number of turns to process before exiting.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Keep running and poll for new turns instead of exiting when the queue is empty.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to sleep between polling attempts (defaults to 0.25s when --watch is set).",
        )
        parser.add_argument(
            "--lease-seconds",
            type=float,
            default=None,
            help="Seconds to lease a turn while executing (default: PORTAL_TURN_WORKER_LEASE_SECONDS or 60).",
        )
        parser.add_argument(
            "--log-metrics-every",
            type=int,
            default=20,
            help="Log queue health metrics every N processed turns (default: 20, 0 to disable).",
        )

    def log_queue_health(self) -> None:
        with tenant_bypass():
            counts = {
                status: PortalTurn.objects.filter(status=status).count()
                for status in (
                    PortalTurnStatus.STREAMING,
                    PortalTurnStatus.WAITING_APPROVAL,
                    PortalTurnStatus.FINALIZING,
                    PortalTurnStatus.FINALIZED,
                    PortalTurnStatus.FAILED,
                    PortalTurnStatus.CANCELLED,
                )
            }
            oldest = (
                PortalTurn.objects.filter(status__in=[PortalTurnStatus.STREAMING, PortalTurnStatus.FINALIZING])
                .order_by("created_at")
                .values_list("created_at", flat=True)
                .first()
            )
        if oldest:
            age_seconds = (timezone.now() - oldest).total_seconds()
            logger.info(
                "Portal turns queue health: streaming=%s waiting=%s finalizing=%s failed=%s cancelled=%s finalized=%s oldest_waiting=%ss",
                counts[PortalTurnStatus.STREAMING],
                counts[PortalTurnStatus.WAITING_APPROVAL],
                counts[PortalTurnStatus.FINALIZING],
                counts[PortalTurnStatus.FAILED],
                counts[PortalTurnStatus.CANCELLED],
                counts[PortalTurnStatus.FINALIZED],
                int(age_seconds),
            )
        else:
            logger.info(
                "Portal turns queue health: streaming=%s waiting=%s finalizing=%s failed=%s cancelled=%s finalized=%s",
                counts[PortalTurnStatus.STREAMING],
                counts[PortalTurnStatus.WAITING_APPROVAL],
                counts[PortalTurnStatus.FINALIZING],
                counts[PortalTurnStatus.FAILED],
                counts[PortalTurnStatus.CANCELLED],
                counts[PortalTurnStatus.FINALIZED],
            )

    def handle(self, *args, **options):
        max_turns = options.get("max_turns")
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 0.25

        lease_seconds_opt = options.get("lease_seconds")
        if lease_seconds_opt is None:
            lease_seconds = float(getattr(settings, "PORTAL_TURN_WORKER_LEASE_SECONDS", 60) or 60)
        else:
            lease_seconds = float(lease_seconds_opt or 60)

        log_metrics_every = int(options.get("log_metrics_every") or 20)

        service = PortalTurnProcessingService(lease_seconds=int(lease_seconds))

        if log_metrics_every > 0:
            self.log_queue_health()

        processed = 0
        while True:
            if max_turns is not None and processed >= int(max_turns):
                break

            result = service.process_next_turn()
            if result is None:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No queued portal turns. Watching for new work..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No queued portal turns."))
                break

            processed += 1
            status = str(result.status or "")
            if status == PortalTurnStatus.FINALIZED:
                self.stdout.write(self.style.SUCCESS(f"Completed turn {result.turn_id}."))
            elif status == PortalTurnStatus.CANCELLED:
                self.stdout.write(self.style.WARNING(f"Turn {result.turn_id} cancelled."))
            else:
                self.stdout.write(self.style.ERROR(f"Turn {result.turn_id} ended with status={status}: {result.error or ''}".strip()))

            if log_metrics_every > 0 and processed % log_metrics_every == 0:
                self.log_queue_health()

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)

