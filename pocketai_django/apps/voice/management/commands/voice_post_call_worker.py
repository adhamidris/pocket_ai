from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context

from apps.voice.models import CallSession, CallStatus
from apps.voice.calls.post_processing import process_post_call
from apps.voice.providers.r2_storage import load_r2_config


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process completed voice calls (transcript finalization, summary, recording ingest)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--max-calls", type=int, default=None)
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--sleep", type=float, default=0.0)
        parser.add_argument("--lease-seconds", type=float, default=120.0)
        parser.add_argument("--max-retry-delay-seconds", type=float, default=900.0)

    def handle(self, *args, **options):
        max_calls = options.get("max_calls")
        watch = bool(options.get("watch"))
        sleep_seconds = float(options.get("sleep") or 0.0)
        if watch and sleep_seconds <= 0:
            sleep_seconds = 2.0
        lease_seconds = float(options.get("lease_seconds") or 120.0)
        max_retry_delay_seconds = float(options.get("max_retry_delay_seconds") or 900.0)

        processed = 0
        while True:
            if max_calls is not None and processed >= int(max_calls):
                break

            session = self._claim_next(lease_seconds=lease_seconds)
            if not session:
                if watch:
                    if processed == 0:
                        self.stdout.write(self.style.WARNING("No completed voice calls to post-process. Watching..."))
                    if sleep_seconds:
                        time.sleep(sleep_seconds)
                    continue
                if processed == 0:
                    self.stdout.write(self.style.WARNING("No completed voice calls to post-process."))
                break

            processed += 1
            try:
                with tenant_context(session.business_profile_id):
                    result = process_post_call(session)
                    refreshed = CallSession.objects.filter(id=session.id).first()

                r2_cfg = load_r2_config()
                needs_recording = bool(r2_cfg and refreshed and refreshed.consent_obtained and not refreshed.recording_r2_key)
                if needs_recording and not refreshed.recording_url:
                    delay = min(max_retry_delay_seconds, 60.0)
                    with tenant_bypass():
                        CallSession.objects.filter(id=session.id).update(
                            lease_expires_at=None,
                            run_after=timezone.now() + timedelta(seconds=delay),
                            updated_at=timezone.now(),
                        )
                    self.stdout.write(self.style.WARNING(f"Post-processed call {session.id} but recording callback not received; retrying later."))
                    continue

                if needs_recording and not bool(result.recording_uploaded) and refreshed.recording_url:
                    delay = min(max_retry_delay_seconds, 60.0)
                    with tenant_bypass():
                        CallSession.objects.filter(id=session.id).update(
                            lease_expires_at=None,
                            run_after=timezone.now() + timedelta(seconds=delay),
                            updated_at=timezone.now(),
                        )
                    self.stdout.write(self.style.WARNING(f"Post-processed call {session.id} but recording not uploaded; retrying later."))
                    continue

                with tenant_bypass():
                    CallSession.objects.filter(id=session.id).update(
                        post_processed=True,
                        post_processed_at=timezone.now(),
                        lease_expires_at=None,
                        run_after=None,
                        updated_at=timezone.now(),
                    )
                self.stdout.write(self.style.SUCCESS(f"Post-processed call {session.id} (msgs={result.transcript_messages_written})."))
            except Exception as exc:
                logger.exception("voice.post_call_worker_failed session=%s", session.id)
                delay = min(max_retry_delay_seconds, float(max(10.0, 2 ** int(session.attempt_count or 0))))
                with tenant_bypass():
                    CallSession.objects.filter(id=session.id).update(
                        lease_expires_at=None,
                        run_after=timezone.now() + timedelta(seconds=delay),
                        post_processing_error=str(exc)[:500],
                        updated_at=timezone.now(),
                    )
                self.stdout.write(self.style.ERROR(f"Failed post-processing call {session.id}: {exc}"))

            if watch and sleep_seconds:
                time.sleep(sleep_seconds)

    def _claim_next(self, *, lease_seconds: float) -> CallSession | None:
        now = timezone.now()
        lease_until = now + timedelta(seconds=max(5.0, float(lease_seconds)))

        with tenant_bypass():
            with transaction.atomic():
                candidate = (
                    CallSession.objects.select_for_update(skip_locked=True)
                    .filter(business_profile__isnull=False)
                    .filter(post_processed=False)
                    .filter(status__in=[CallStatus.COMPLETED, CallStatus.CANCELLED, CallStatus.FAILED])
                    .filter(Q(run_after__isnull=True) | Q(run_after__lte=now))
                    .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now))
                    .order_by("ended_at", "created_at")
                    .first()
                )
                if not candidate:
                    return None
                candidate.lease_expires_at = lease_until
                candidate.save(update_fields=["lease_expires_at", "updated_at"])
                return candidate
