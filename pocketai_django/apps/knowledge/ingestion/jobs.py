from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import uuid

from django.conf import settings
from django.db.models import Count, Min, Q
from django.utils import timezone

from apps.accounts.models import (
    KnowledgeIngestionJobStatus,
    KnowledgeIngestionJobType,
    KnowledgeSourceType,
    KnowledgeStatus,
)
from apps.knowledge.models import KnowledgeIngestionJob, KnowledgeUpload


logger = logging.getLogger(__name__)


SUPPORTED_SOURCE_TYPES = {
    KnowledgeSourceType.FILE,
    KnowledgeSourceType.LINK,
    KnowledgeSourceType.TEXT,
    KnowledgeSourceType.INTEGRATION,
}


@dataclass(frozen=True)
class IngestionJobResult:
    job_id: uuid.UUID
    upload_id: uuid.UUID
    job_type: KnowledgeIngestionJobType
    status: KnowledgeIngestionJobStatus
    characters: int
    error: str | None = None


def queue_ingestion_job(upload: KnowledgeUpload, *, trigger: str = "upload", force: bool = False) -> KnowledgeIngestionJob | None:
    """
    Ensure an ingestion job exists for the upload if the source type requires parsing.
    """

    if upload.source_type not in SUPPORTED_SOURCE_TYPES:
        return None

    try:
        from apps.knowledge.knowledge_preflight import ensure_upload_preflight

        ensure_upload_preflight(upload, trigger=trigger)
    except Exception:  # pragma: no cover - preflight must never block ingestion
        logger.exception("knowledge.preflight.enqueue_failed upload=%s", getattr(upload, "id", None))

    pending_jobs = KnowledgeIngestionJob.objects.filter(
        upload=upload,
        status__in=(
            KnowledgeIngestionJobStatus.QUEUED,
            KnowledgeIngestionJobStatus.RUNNING,
            KnowledgeIngestionJobStatus.DEFERRED,
        ),
        job_type=KnowledgeIngestionJobType.INGEST,
    )
    existing = (
        pending_jobs.filter(status=KnowledgeIngestionJobStatus.RUNNING).order_by("created_at").first()
        or pending_jobs.filter(status=KnowledgeIngestionJobStatus.QUEUED).order_by("created_at").first()
        or pending_jobs.filter(status=KnowledgeIngestionJobStatus.DEFERRED).order_by("created_at").first()
    )
    if existing:
        duplicate_count = pending_jobs.exclude(id=existing.id).exclude(status=KnowledgeIngestionJobStatus.RUNNING).update(
            status=KnowledgeIngestionJobStatus.CANCELLED,
        )
        if duplicate_count:
            logger.warning(
                "Cancelled duplicate ingestion jobs upload=%s kept_job=%s cancelled=%s",
                upload.id,
                existing.id,
                duplicate_count,
            )
        if existing.status == KnowledgeIngestionJobStatus.RUNNING:
            logger.info("Ingestion job already running upload=%s job=%s", upload.id, existing.id)
            return existing
        if not force:
            logger.info("Ingestion job already scheduled upload=%s job=%s status=%s", upload.id, existing.id, existing.status)
            return existing
        KnowledgeIngestionJob.objects.filter(id=existing.id).update(status=KnowledgeIngestionJobStatus.CANCELLED)
        logger.info("Cancelled stale ingestion job upload=%s job=%s", upload.id, existing.id)

    if upload.status != KnowledgeStatus.PROCESSING:
        upload.status = KnowledgeStatus.PROCESSING
        upload.save(update_fields=["status", "updated_at"])

    active_limit = max(0, int(getattr(settings, "INGEST_MAX_ACTIVE_JOBS_PER_BUSINESS", 0)))
    job_status = KnowledgeIngestionJobStatus.QUEUED
    payload: dict[str, object] = {"trigger": trigger}
    if active_limit:
        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        active_jobs = KnowledgeIngestionJob.objects.filter(
            business_profile=upload.business_profile,
            job_type=KnowledgeIngestionJobType.INGEST,
        ).filter(
            Q(status=KnowledgeIngestionJobStatus.RUNNING)
            | (Q(status=KnowledgeIngestionJobStatus.QUEUED) & eligible)
        ).count()
        if active_jobs >= active_limit:
            job_status = KnowledgeIngestionJobStatus.DEFERRED
            payload["rate_limited"] = True

    job = KnowledgeIngestionJob.objects.create(
        business_profile=upload.business_profile,
        upload=upload,
        job_type=KnowledgeIngestionJobType.INGEST,
        status=job_status,
        max_attempts=max(1, int(getattr(settings, "INGEST_JOB_MAX_ATTEMPTS", 3))),
        payload=payload,
    )

    logger.info(
        "Queued ingestion job upload=%s job=%s trigger=%s status=%s",
        upload.id,
        job.id,
        trigger,
        job_status,
    )
    return job


def get_ingestion_queue_health(
    *,
    business_profile_id: uuid.UUID | None = None,
) -> dict[str, object]:
    """
    Lightweight queue health snapshot for ops dashboards/alerts.

    Intended to be called from long-running workers (e.g., process_knowledge_ingestion --watch).
    """

    qs = KnowledgeIngestionJob.objects.all()
    if business_profile_id:
        qs = qs.filter(business_profile_id=business_profile_id)

    status_counts: dict[str, int] = {}
    for row in qs.values("status").annotate(count=Count("id")):
        status = str(row.get("status") or "")
        if not status:
            continue
        status_counts[status] = int(row.get("count") or 0)

    by_type: dict[str, dict[str, int]] = {}
    for row in qs.values("job_type", "status").annotate(count=Count("id")):
        job_type = str(row.get("job_type") or "")
        status = str(row.get("status") or "")
        if not job_type or not status:
            continue
        by_type.setdefault(job_type, {})[status] = int(row.get("count") or 0)

    now = timezone.now()
    pending_qs = qs.filter(status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.DEFERRED))
    oldest_pending = pending_qs.aggregate(oldest=Min("created_at")).get("oldest")
    oldest_pending_age_s: int | None = None
    if oldest_pending:
        try:
            oldest_pending_age_s = max(0, int((now - oldest_pending).total_seconds()))
        except Exception:
            oldest_pending_age_s = None

    failed_last_hour = qs.filter(
        status=KnowledgeIngestionJobStatus.FAILED,
        finished_at__gte=(now - timedelta(hours=1)),
    ).count()
    running_lease_expired = qs.filter(
        status=KnowledgeIngestionJobStatus.RUNNING,
        lease_expires_at__isnull=False,
        lease_expires_at__lt=now,
    ).count()

    pending_count = int(status_counts.get(KnowledgeIngestionJobStatus.QUEUED, 0)) + int(
        status_counts.get(KnowledgeIngestionJobStatus.DEFERRED, 0)
    )
    return {
        "pending": pending_count,
        "queued": int(status_counts.get(KnowledgeIngestionJobStatus.QUEUED, 0)),
        "deferred": int(status_counts.get(KnowledgeIngestionJobStatus.DEFERRED, 0)),
        "running": int(status_counts.get(KnowledgeIngestionJobStatus.RUNNING, 0)),
        "failed": int(status_counts.get(KnowledgeIngestionJobStatus.FAILED, 0)),
        "failed_last_hour": int(failed_last_hour),
        "running_lease_expired": int(running_lease_expired),
        "oldest_pending_age_s": oldest_pending_age_s,
        "status_counts": status_counts,
        "by_type": by_type,
        "observed_at": now.isoformat(),
    }
