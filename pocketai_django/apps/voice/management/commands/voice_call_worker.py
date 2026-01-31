from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.tenancy import tenant_bypass

from apps.voice.call_processing import VoiceCallWorkerService
from apps.voice.models import CallSession, CallStatus


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process queued voice call sessions (outbound calls)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--max-calls",
            type=int,
            default=None,
            help="Maximum number of call sessions to process before exiting.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Keep running and poll for new calls instead of exiting when the queue is empty.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.0,
            help="Seconds to sleep between polling attempts (defaults to 2s when --watch is set).",
        )
        parser.add_argument(
            "--lease-seconds",
            type=float,
            default=60.0,
            help="Seconds to lease a call session while initiating (default: 60).",
        )
        parser.add_argument(
            "--max-retry-delay-seconds",
            type=float,
            default=900.0,
            help="Maximum backoff delay for retries in seconds (default: 900).",
        )
        parser.add_argument(
            "--log-metrics-every",
            type=int,
            default=10,
            help="Log queue health metrics every N processed calls (default: 10, 0 to disable).",
        )

    def log_queue_health(self) -> None:
        with tenant_bypass():
            queued_count = CallSession.objects.filter(status=CallStatus.QUEUED).count()
            active_count = CallSession.objects.filter(
                status__in=[CallStatus.INITIATING, CallStatus.RINGING, CallStatus.IN_PROGRESS]
            ).count()
            oldest_queued = (
                CallSession.objects.filter(status=CallStatus.QUEUED)
                .order_by("queued_at")
                .values_list("queued_at", flat=True)
                .first()
            )
            if oldest_queued:
                age_seconds = (timezone.now() - oldest_queued).total_seconds()
                age_minutes = int(age_seconds / 60)
                logger.info(
                    "voice.queue_health queued=%s active=%s oldest_wait_minutes=%s",
                    queued_count,
                    active_count,
                    age_minutes,
                )
            else:
                logger.info("voice.queue_health queued=%s active=%s", queued_count, active_count)

    def handle(self, *args, **options):
        max_calls = options.get("max_calls")
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0

        log_metrics_every = int(options.get("log_metrics_every") or 10)

        service = VoiceCallWorkerService(
            lease_seconds=float(options.get("lease_seconds") or 60.0),
            max_retry_delay_seconds=float(options.get("max_retry_delay_seconds") or 900.0),
        )

        if log_metrics_every > 0:
            self.log_queue_health()

        processed = 0
        while True:
            if max_calls is not None and processed >= int(max_calls):
                break

            result = service.process_next_call()
            if result is None:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No queued voice calls. Watching for new work..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No queued voice calls."))
                break

            processed += 1
            if result.status == CallStatus.RINGING:
                self.stdout.write(self.style.SUCCESS(f"Initiated call {result.call_session_id}."))
            elif result.requeued:
                self.stdout.write(self.style.WARNING(f"Requeued call {result.call_session_id}: {result.error or 'retry scheduled'}"))
            elif result.status in {CallStatus.CANCELLED, CallStatus.FAILED}:
                self.stdout.write(self.style.ERROR(f"{result.status} call {result.call_session_id}: {result.error or 'error'}"))
            else:
                self.stdout.write(self.style.WARNING(f"Updated call {result.call_session_id}: {result.status}"))

            if log_metrics_every > 0 and processed % log_metrics_every == 0:
                self.log_queue_health()

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)

