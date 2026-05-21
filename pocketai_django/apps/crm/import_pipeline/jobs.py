from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import BusinessProfile, User
from apps.crm.domain.services import log_crm_activity
from apps.crm.import_pipeline.config import RETRY_BACKOFF_MINUTES
from apps.crm.import_pipeline.parsing import _finalize_job, _handle_job_failure, _infer_default_mapping, _load_rows
from apps.crm.import_pipeline.rows import _process_row
from apps.crm.models import (
    CrmActivityActor,
    CrmActivityType,
    CrmImportJob,
    CrmImportJobStatus,
    CrmImportRowResult,
    CrmImportRowStatus,
    CrmImportSourceFile,
    CrmImportTemplate,
)


def save_import_template(*, business_profile: BusinessProfile, created_by: User | None, name: str, mapping: dict[str, Any]) -> CrmImportTemplate:
    template, _created = CrmImportTemplate.objects.update_or_create(
        business_profile=business_profile,
        name=name.strip(),
        defaults={"mapping": mapping or {}, "created_by": created_by},
    )
    return template


def infer_default_mapping(columns: Sequence[str]) -> dict[str, Any]:
    return _infer_default_mapping(columns)


def enqueue_import_job(*, business_profile: BusinessProfile, source_file: CrmImportSourceFile, initiated_by: User | None, mapping: dict[str, Any], template: CrmImportTemplate | None = None) -> CrmImportJob:
    effective_mapping = mapping or (template.mapping if template else {}) or _infer_default_mapping(source_file.column_snapshot)
    job = CrmImportJob.objects.create(
        business_profile=business_profile,
        source_file=source_file,
        initiated_by=initiated_by,
        template=template,
        mapping=effective_mapping,
        run_after=timezone.now(),
    )
    log_crm_activity(
        business_profile=business_profile,
        actor=initiated_by,
        activity_type=CrmActivityType.OTHER,
        summary="Import job queued",
        detail=f"job={job.id} source={source_file.id}",
    )
    return job


def process_next_job() -> CrmImportJob | None:
    now = timezone.now()
    with transaction.atomic():
        job = (
            CrmImportJob.objects.select_for_update(skip_locked=True)
            .filter(Q(status=CrmImportJobStatus.QUEUED) | Q(status=CrmImportJobStatus.RUNNING, lease_expires_at__lte=now))
            .filter(Q(run_after__isnull=True) | Q(run_after__lte=now))
            .filter(attempt_count__lt=F("max_attempts"))
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        job.status = CrmImportJobStatus.RUNNING
        job.started_at = now
        job.lease_expires_at = now + timedelta(minutes=5)
        job.attempt_count += 1
        job.error_detail = ""
        job.save(update_fields=["status", "started_at", "lease_expires_at", "attempt_count", "error_detail", "updated_at"])
    log_crm_activity(
        business_profile=job.business_profile,
        actor=job.initiated_by,
        actor_type=CrmActivityActor.IMPORT,
        activity_type=CrmActivityType.OTHER,
        summary="Import job started",
        detail=f"job={job.id} attempt={job.attempt_count}",
    )
    try:
        _process_job(job)
    except Exception as exc:
        _handle_job_failure(job, exc)
    return job


def _process_job(job: CrmImportJob) -> None:
    source = job.source_file
    existing_results = {
        row_result.row_number: row_result.status
        for row_result in job.row_results.all().only("row_number", "status")
    }
    with source.file.open("rb") as handle:
        for row_number, row in enumerate(_load_rows(handle, file_format=source.file_format), start=1):
            if existing_results.get(row_number) in {
                CrmImportRowStatus.CREATED,
                CrmImportRowStatus.UPDATED,
                CrmImportRowStatus.DUPLICATE,
                CrmImportRowStatus.SKIPPED,
            }:
                continue
            try:
                with transaction.atomic():
                    status, messages, contact_id, company_id, duplicate_of_id = _process_row(job=job, row_number=row_number, row=row)
                    CrmImportRowResult.objects.update_or_create(
                        job=job,
                        row_number=row_number,
                        defaults={
                            "status": status,
                            "raw_data": row,
                            "messages": messages,
                            "contact_id": contact_id,
                            "company_id": company_id,
                            "duplicate_of_id": duplicate_of_id,
                        },
                    )
            except Exception as exc:
                CrmImportRowResult.objects.update_or_create(
                    job=job,
                    row_number=row_number,
                    defaults={
                        "status": CrmImportRowStatus.FAILED,
                        "raw_data": row,
                        "messages": [str(exc)],
                        "contact_id": None,
                        "company_id": None,
                        "duplicate_of_id": None,
                    },
                )
        _finalize_job(job)
