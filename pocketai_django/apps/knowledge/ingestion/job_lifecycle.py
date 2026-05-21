from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from apps.accounts.models import KnowledgeIngestionJobStatus, KnowledgeIngestionJobType, KnowledgeStatus
from apps.knowledge.ingestion.contracts import UnsupportedFormatError
from apps.knowledge.models import KnowledgeIngestionJob


logger = logging.getLogger(__name__)


class IngestionJobLifecycleMixin:

    def _job_max_attempts(self, job: KnowledgeIngestionJob) -> int:
        configured = int(getattr(job, "max_attempts", 0) or 0)
        if configured > 0:
            return configured
        if job.job_type == KnowledgeIngestionJobType.EMBED:
            return self.embed_job_max_attempts
        return self.ingest_job_max_attempts

    def _job_retry_delay_seconds(self, attempt_count: int, job: KnowledgeIngestionJob) -> float:
        normalized_attempt = max(1, int(attempt_count))
        if job.job_type == KnowledgeIngestionJobType.EMBED:
            base = max(1.0, float(getattr(settings, "INGEST_EMBED_JOB_RETRY_BASE_SECONDS", self.job_retry_base_seconds)))
        else:
            base = self.job_retry_base_seconds
        delay = min(self.job_retry_max_seconds, base * (2 ** (normalized_attempt - 1)))
        jitter = 0.0
        if self.job_retry_jitter_seconds:
            jitter = random.uniform(0.0, self.job_retry_jitter_seconds)
        return delay + jitter

    def _heartbeat_job(self, job: KnowledgeIngestionJob, *, now: timezone.datetime | None = None) -> None:
        if not job or getattr(job, "status", None) != KnowledgeIngestionJobStatus.RUNNING:
            return
        current = now or timezone.now()
        lease = current + timedelta(seconds=self.job_lease_seconds)
        KnowledgeIngestionJob.objects.filter(id=job.id, status=KnowledgeIngestionJobStatus.RUNNING).update(
            lease_expires_at=lease
        )

    def _requeue_job_with_backoff(self, job: KnowledgeIngestionJob, message: str, *, reason: str) -> bool:
        """Return True when a retry was scheduled, False when the job is now terminal."""
        now = timezone.now()
        max_attempts = self._job_max_attempts(job)
        next_attempt = max(0, int(getattr(job, "attempt_count", 0) or 0)) + 1
        payload = dict(job.payload or {})
        attempt_events = payload.get("attempts")
        if not isinstance(attempt_events, list):
            attempt_events = []
        attempt_events.append(
            {
                "attempt": next_attempt,
                "at": now.isoformat(),
                "reason": reason,
                "error": (message or "")[:400],
            }
        )
        payload["attempts"] = attempt_events[-10:]
        payload["last_error_at"] = now.isoformat()
        payload["last_error_reason"] = reason

        if next_attempt < max_attempts:
            delay = self._job_retry_delay_seconds(next_attempt, job)
            run_after = now + timedelta(seconds=float(delay))
            KnowledgeIngestionJob.objects.filter(id=job.id).update(
                status=KnowledgeIngestionJobStatus.QUEUED,
                attempt_count=next_attempt,
                run_after=run_after,
                started_at=None,
                lease_expires_at=None,
                error_detail=message,
                payload=payload,
            )
            logger.warning(
                "ingest.job_retry_scheduled job=%s upload=%s type=%s attempt=%s/%s run_after=%s reason=%s error=%s",
                job.id,
                job.upload_id,
                job.job_type,
                next_attempt,
                max_attempts,
                run_after.isoformat(),
                reason,
                (message or "")[:200],
            )
            return True

        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.FAILED,
            attempt_count=next_attempt,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail=message,
            payload=payload,
        )
        logger.error(
            "ingest.job_retry_exhausted job=%s upload=%s type=%s attempts=%s error=%s",
            job.id,
            job.upload_id,
            job.job_type,
            next_attempt,
            (message or "")[:200],
        )
        return False

    def _mark_job_failed_terminal(self, job: KnowledgeIngestionJob, message: str, *, reason: str) -> None:
        now = timezone.now()
        next_attempt = max(0, int(getattr(job, "attempt_count", 0) or 0)) + 1
        payload = dict(job.payload or {})
        attempt_events = payload.get("attempts")
        if not isinstance(attempt_events, list):
            attempt_events = []
        attempt_events.append(
            {
                "attempt": next_attempt,
                "at": now.isoformat(),
                "reason": reason,
                "error": (message or "")[:400],
            }
        )
        payload["attempts"] = attempt_events[-10:]
        payload["last_error_at"] = now.isoformat()
        payload["last_error_reason"] = reason
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.FAILED,
            attempt_count=next_attempt,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail=message,
            payload=payload,
        )

    def _requeue_stale_running_jobs(self, *, limit: int = 25) -> int:
        if not self.requeue_stale_jobs:
            return 0
        now = timezone.now()
        cutoff = now - timedelta(seconds=self.job_lease_seconds)
        stale_jobs = list(
            KnowledgeIngestionJob.objects.filter(status=KnowledgeIngestionJobStatus.RUNNING)
            .filter(
                Q(lease_expires_at__lt=now)
                | Q(lease_expires_at__isnull=True, started_at__lt=cutoff)
            )
            .select_related("upload")
            .order_by("started_at")[: max(1, int(limit))]
        )
        if not stale_jobs:
            return 0
        for job in stale_jobs:
            scheduled = self._requeue_job_with_backoff(job, "auto-requeue: ingestion job lease expired", reason="lease_expired")
            if not scheduled:
                upload = job.upload
                if job.job_type == KnowledgeIngestionJobType.EMBED:
                    metadata = dict(upload.ingestion_metadata or {})
                    embedding_meta = metadata.get("embedding")
                    if not isinstance(embedding_meta, dict):
                        embedding_meta = {}
                    embedding_meta.update(
                        {
                            "status": "failed",
                            "job_id": str(job.id),
                            "failed_at": now.isoformat(),
                            "error": "auto-requeue: ingestion job lease expired",
                        }
                    )
                    metadata["embedding"] = embedding_meta
                    upload.ingestion_metadata = metadata
                    upload.save(update_fields=["ingestion_metadata", "updated_at"])
                else:
                    upload.ingestion_error = "auto-requeue: ingestion job lease expired"
                    upload.status = KnowledgeStatus.FAILED
                    upload.save(update_fields=["ingestion_error", "status", "updated_at"])
        logger.warning("ingest.job_requeued_stale count=%s", len(stale_jobs))
        return len(stale_jobs)


    def _mark_job_completed(self, job: KnowledgeIngestionJob, *, extra: dict[str, Any] | None = None) -> None:
        finished = timezone.now()
        payload = dict(job.payload or {})
        if extra:
            payload.update(extra)
        KnowledgeIngestionJob.objects.filter(id=job.id).update(
            status=KnowledgeIngestionJobStatus.COMPLETED,
            finished_at=finished,
            run_after=None,
            lease_expires_at=None,
            payload=payload,
        )
        self._invalidate_alias_cache(job.business_profile_id)
        self._release_deferred_jobs(job.business_profile_id)

    def _handle_failure(
        self,
        job: KnowledgeIngestionJob,
        message: str,
        *,
        exc: Exception | None = None,
        retryable: bool | None = None,
    ) -> None:
        error_message = (message or "").strip() or "ingestion failed"

        is_retryable = retryable
        if is_retryable is None:
            is_retryable = True
            if isinstance(exc, UnsupportedFormatError):
                is_retryable = False
            if job.job_type == KnowledgeIngestionJobType.INGEST:
                lowered = error_message.lower()
                if "extracted document is empty" in lowered or "unsupported file type" in lowered:
                    is_retryable = False
                if "is not installed" in lowered and "ingestion is not available" in lowered:
                    is_retryable = False

        if is_retryable:
            scheduled = self._requeue_job_with_backoff(job, error_message, reason="error")
            if scheduled:
                if job.job_type == KnowledgeIngestionJobType.INGEST:
                    upload = job.upload
                    upload.ingestion_error = error_message[:400]
                    upload.status = KnowledgeStatus.PROCESSING
                    upload.save(update_fields=["ingestion_error", "status", "updated_at"])
                self._release_deferred_jobs(job.business_profile_id)
                return
        else:
            self._mark_job_failed_terminal(job, error_message, reason="fatal")
        now = timezone.now()
        upload = job.upload
        if job.job_type == KnowledgeIngestionJobType.EMBED:
            metadata = dict(upload.ingestion_metadata or {})
            embedding_meta = metadata.get("embedding")
            if not isinstance(embedding_meta, dict):
                embedding_meta = {}
            embedding_meta.update(
                {
                    "status": "failed",
                    "job_id": str(job.id),
                    "failed_at": now.isoformat(),
                    "error": error_message[:400],
                }
            )
            metadata["embedding"] = embedding_meta
            upload.ingestion_metadata = metadata
            upload.save(update_fields=["ingestion_metadata", "updated_at"])
        else:
            upload.ingestion_error = error_message
            upload.status = KnowledgeStatus.FAILED
            upload.save(update_fields=["ingestion_error", "status", "updated_at"])
        self._release_deferred_jobs(job.business_profile_id)

    # ------------------------------------------------------------------
    # Helpers

    def _claim_next_job(self) -> KnowledgeIngestionJob | None:
        self._requeue_stale_running_jobs()

        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        qs = (
            KnowledgeIngestionJob.objects.filter(status=KnowledgeIngestionJobStatus.QUEUED)
            .filter(eligible)
            .annotate(
                priority=Case(
                    When(job_type=KnowledgeIngestionJobType.INGEST, then=Value(0)),
                    When(job_type=KnowledgeIngestionJobType.EMBED, then=Value(1)),
                    default=Value(5),
                    output_field=IntegerField(),
                )
            )
            .select_related("upload__file_detail", "upload__url_detail", "upload__business_profile")
            .order_by("priority", "created_at")
        )

        supports_skip_locked = bool(
            getattr(connection.features, "has_select_for_update", False)
            and getattr(connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(connection.features, "has_select_for_update_of", False))

        with transaction.atomic():
            # Lock only the ingestion job row to avoid FOR UPDATE errors on nullable outer joins.
            for_update_kwargs: dict[str, Any] = {}
            if supports_skip_locked:
                for_update_kwargs["skip_locked"] = True
            if supports_for_update_of:
                for_update_kwargs["of"] = ("self",)
            locked = qs.select_for_update(**for_update_kwargs)
            job = locked.first()
            if not job:
                return None
            lease = now + timedelta(seconds=self.job_lease_seconds)
            defaults = self._job_max_attempts(job)
            if job.job_type == KnowledgeIngestionJobType.INGEST:
                cancelled = (
                    KnowledgeIngestionJob.objects.filter(
                        upload_id=job.upload_id,
                        job_type=KnowledgeIngestionJobType.INGEST,
                        status__in=(
                            KnowledgeIngestionJobStatus.QUEUED,
                            KnowledgeIngestionJobStatus.DEFERRED,
                        ),
                    )
                    .exclude(id=job.id)
                    .update(
                        status=KnowledgeIngestionJobStatus.CANCELLED,
                        finished_at=now,
                        error_detail="auto-cancel: duplicate ingestion job",
                    )
                )
                if cancelled:
                    logger.warning(
                        "ingest.job_dedupe_cancelled upload=%s kept_job=%s cancelled=%s",
                        job.upload_id,
                        job.id,
                        cancelled,
                    )
            job.status = KnowledgeIngestionJobStatus.RUNNING
            job.started_at = now
            job.run_after = None
            job.lease_expires_at = lease
            if not job.max_attempts:
                job.max_attempts = defaults
            job.save(update_fields=["status", "started_at", "run_after", "lease_expires_at", "max_attempts", "updated_at"])
            return job
