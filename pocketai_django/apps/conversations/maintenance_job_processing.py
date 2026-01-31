from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import connection as db_connection
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.conversations.compaction_service import ContextCompactionService
from apps.conversations.models import (
    Conversation,
    ConversationMaintenanceJob,
    ConversationMaintenanceJobKind,
    ConversationMaintenanceJobStatus,
)
from core.tenancy import tenant_bypass, tenant_context


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversationMaintenanceJobProcessResult:
    job_id: Any
    status: str
    requeued: bool = False
    error: str | None = None


def enqueue_compaction_job(
    conversation: Conversation,
    *,
    run_after=None,
) -> ConversationMaintenanceJob | None:
    """
    Enqueue a durable compaction job for a conversation.

    Idempotency is enforced via a partial unique constraint (only one queued/running
    job per conversation+kind).
    """

    now = timezone.now()
    try:
        return ConversationMaintenanceJob.objects.create(
            business_profile=conversation.business_profile,
            conversation=conversation,
            kind=ConversationMaintenanceJobKind.COMPACT_HISTORY,
            status=ConversationMaintenanceJobStatus.QUEUED,
            run_after=run_after if run_after is not None else now,
        )
    except IntegrityError:
        # Most commonly: an existing queued/running job for this conversation+kind.
        return None
    except Exception:
        logger.exception("conversation_job.enqueue_failed conversation=%s", getattr(conversation, "id", None))
        return None


class ConversationMaintenanceJobProcessingService:
    """
    Background worker service for conversation maintenance jobs.

    Phase 6: This replaces inline threads for compaction/embedding with a DB-backed queue
    that supports leases, retries, and multi-worker safety.
    """

    def __init__(
        self,
        *,
        lease_seconds: float = 60.0,
        max_stale_requeues_per_pass: int = 25,
        max_retry_delay_seconds: float = 900.0,
        unsafe_backoff_seconds: float = 60.0,
        claim_scan_limit: int = 25,
    ) -> None:
        self.lease_seconds = float(lease_seconds or 60.0)
        self.max_stale_requeues_per_pass = max(1, int(max_stale_requeues_per_pass or 25))
        self.max_retry_delay_seconds = float(max_retry_delay_seconds or 900.0)
        self.unsafe_backoff_seconds = max(1.0, float(unsafe_backoff_seconds or 60.0))
        self.claim_scan_limit = max(1, int(claim_scan_limit or 25))

    def process_next_job(self) -> ConversationMaintenanceJobProcessResult | None:
        self._requeue_stale_running_jobs(limit=self.max_stale_requeues_per_pass)
        job = self._claim_next_job()
        if not job:
            return None

        try:
            return self._execute_job(job)
        except Exception as exc:
            logger.exception("conversation_job.execute_failed job=%s kind=%s", job.id, job.kind)
            return self._requeue_job_with_backoff(job, f"execution failed: {exc}", reason="execution_failed")

    def _claim_next_job(self) -> ConversationMaintenanceJob | None:
        now = timezone.now()
        qs = (
            ConversationMaintenanceJob.objects.filter(status=ConversationMaintenanceJobStatus.QUEUED)
            .filter(Q(run_after__lte=now) | Q(run_after__isnull=True))
            .order_by("run_after", "created_at")
        )

        supports_skip_locked = bool(
            getattr(db_connection.features, "has_select_for_update", False)
            and getattr(db_connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(db_connection.features, "has_select_for_update_of", False))
        supports_for_update = bool(getattr(db_connection.features, "has_select_for_update", False))

        scan_limit = max(1, int(self.claim_scan_limit or 1))

        with tenant_bypass():
            with transaction.atomic():
                candidates: list[ConversationMaintenanceJob] = []
                if supports_for_update:
                    for_update_kwargs: dict[str, Any] = {}
                    if supports_skip_locked:
                        for_update_kwargs["skip_locked"] = True
                    if supports_for_update_of:
                        for_update_kwargs["of"] = ("self",)
                    candidates = list(qs.select_for_update(**for_update_kwargs)[:scan_limit])
                else:
                    candidates = list(qs[:scan_limit])

                if not candidates:
                    return None

                job = candidates[0]
                lease = now + timedelta(seconds=self.lease_seconds)
                updated = (
                    ConversationMaintenanceJob.objects.filter(
                        id=job.id,
                        status=ConversationMaintenanceJobStatus.QUEUED,
                    ).update(
                        status=ConversationMaintenanceJobStatus.RUNNING,
                        started_at=now,
                        lease_expires_at=lease,
                        updated_at=now,
                    )
                )
                if updated != 1:
                    return None

                job.status = ConversationMaintenanceJobStatus.RUNNING
                job.started_at = now
                job.lease_expires_at = lease
                return job

    def _defer_job(self, job: ConversationMaintenanceJob, *, delay_seconds: float, reason: str) -> None:
        now = timezone.now()
        delay = max(1.0, float(delay_seconds or 0.0))
        run_after = now + timedelta(seconds=delay)
        ConversationMaintenanceJob.objects.filter(id=job.id, status=ConversationMaintenanceJobStatus.RUNNING).update(
            status=ConversationMaintenanceJobStatus.QUEUED,
            run_after=run_after,
            lease_expires_at=None,
            started_at=None,
            updated_at=now,
        )
        job.status = ConversationMaintenanceJobStatus.QUEUED
        job.run_after = run_after
        job.lease_expires_at = None
        # Best-effort breadcrumb.
        try:
            meta = dict(job.metadata or {})
            meta["last_defer_reason"] = reason
            meta["last_defer_at"] = now.isoformat()
            ConversationMaintenanceJob.objects.filter(id=job.id).update(metadata=meta)
            job.metadata = meta
        except Exception:
            pass

    def _mark_succeeded(self, job: ConversationMaintenanceJob) -> ConversationMaintenanceJobProcessResult:
        now = timezone.now()
        ConversationMaintenanceJob.objects.filter(id=job.id, status=ConversationMaintenanceJobStatus.RUNNING).update(
            status=ConversationMaintenanceJobStatus.SUCCEEDED,
            finished_at=now,
            lease_expires_at=None,
            updated_at=now,
        )
        return ConversationMaintenanceJobProcessResult(job_id=job.id, status=ConversationMaintenanceJobStatus.SUCCEEDED)

    def _mark_failed(self, job: ConversationMaintenanceJob, error: str) -> ConversationMaintenanceJobProcessResult:
        now = timezone.now()
        ConversationMaintenanceJob.objects.filter(id=job.id).update(
            status=ConversationMaintenanceJobStatus.FAILED,
            finished_at=now,
            lease_expires_at=None,
            error_detail=str(error or "")[:4000],
            updated_at=now,
        )
        return ConversationMaintenanceJobProcessResult(job_id=job.id, status=ConversationMaintenanceJobStatus.FAILED, error=error)

    def _requeue_job_with_backoff(
        self,
        job: ConversationMaintenanceJob,
        error: str,
        *,
        reason: str,
    ) -> ConversationMaintenanceJobProcessResult:
        now = timezone.now()
        try:
            attempt_count = int(job.attempt_count or 0)
        except (TypeError, ValueError):
            attempt_count = 0
        try:
            max_attempts = int(job.max_attempts or 0)
        except (TypeError, ValueError):
            max_attempts = 0
        if max_attempts and attempt_count >= max_attempts:
            return self._mark_failed(job, error)

        # Exponential backoff with a small base and a hard cap.
        exponent = min(8, max(0, attempt_count))
        delay = min(self.max_retry_delay_seconds, float(5 * (2 ** exponent)))
        delay = max(5.0, float(delay))
        run_after = now + timedelta(seconds=delay)
        ConversationMaintenanceJob.objects.filter(id=job.id).update(
            status=ConversationMaintenanceJobStatus.QUEUED,
            run_after=run_after,
            lease_expires_at=None,
            started_at=None,
            error_detail=str(error or "")[:4000],
            updated_at=now,
        )
        return ConversationMaintenanceJobProcessResult(
            job_id=job.id,
            status=ConversationMaintenanceJobStatus.QUEUED,
            requeued=True,
            error=error,
        )

    def _execute_job(self, job: ConversationMaintenanceJob) -> ConversationMaintenanceJobProcessResult:
        # Switch into the tenant before touching conversation-scoped data.
        with tenant_context(job.business_profile_id):
            conversation = Conversation.objects.filter(id=job.conversation_id).only("id", "business_profile_id", "metadata").first()
            if not conversation:
                return self._mark_failed(job, "conversation not found")

            if job.kind == ConversationMaintenanceJobKind.COMPACT_HISTORY:
                return self._execute_compaction(job, conversation)

            return self._mark_failed(job, f"unknown job kind: {job.kind}")

    def _execute_compaction(self, job: ConversationMaintenanceJob, conversation: Conversation) -> ConversationMaintenanceJobProcessResult:
        service = ContextCompactionService()

        # Mandatory safety gate at execution time.
        if not service.is_safe_to_compact(conversation):
            self._defer_job(job, delay_seconds=self.unsafe_backoff_seconds, reason="not_safe_to_compact")
            return ConversationMaintenanceJobProcessResult(job_id=job.id, status=ConversationMaintenanceJobStatus.QUEUED, requeued=True)

        # Nothing to do anymore (another worker already compacted, or the conversation shrank).
        if not service.should_compact(conversation):
            return self._mark_succeeded(job)

        # Count only "real" execution attempts (not safety defers).
        now = timezone.now()
        try:
            job.attempt_count = int(job.attempt_count or 0) + 1
        except (TypeError, ValueError):
            job.attempt_count = 1
        ConversationMaintenanceJob.objects.filter(id=job.id).update(attempt_count=job.attempt_count, updated_at=now)

        try:
            service.compact(conversation=conversation)
        except Exception as exc:
            return self._requeue_job_with_backoff(job, str(exc), reason="compaction_failed")

        return self._mark_succeeded(job)

    def _requeue_stale_running_jobs(self, *, limit: int) -> None:
        now = timezone.now()
        cutoff = now - timedelta(seconds=max(1.0, float(self.lease_seconds or 60.0)))
        limit = max(1, int(limit or 1))
        with tenant_bypass():
            stale_ids = list(
                ConversationMaintenanceJob.objects.filter(status=ConversationMaintenanceJobStatus.RUNNING)
                .filter(Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True, started_at__lt=cutoff))
                .values_list("id", flat=True)[:limit]
            )
            if not stale_ids:
                return
            ConversationMaintenanceJob.objects.filter(id__in=stale_ids).update(
                status=ConversationMaintenanceJobStatus.QUEUED,
                lease_expires_at=None,
                started_at=None,
                run_after=now + timedelta(seconds=5),
                updated_at=now,
            )
