from __future__ import annotations

import logging
import time

from django.conf import settings

from apps.accounts.models import KnowledgeIngestionJobStatus, KnowledgeIngestionJobType
from apps.core.logging_utils import LogEmoji, log_start
from apps.knowledge.ingestion.contracts import KnowledgeIngestionError
from apps.knowledge.ingestion.job_lifecycle import IngestionJobLifecycleMixin
from apps.knowledge.ingestion.jobs import IngestionJobResult
from apps.knowledge.models import KnowledgeIngestionJob
from apps.rag.observability.logging import structured_log
from core.otel import otel_trace
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)


class IngestionJobProcessingMixin(IngestionJobLifecycleMixin):

    def process_next_job(self) -> IngestionJobResult | None:
        job = self._claim_next_job()

        if job is None:
            return None
        tenant_id = getattr(job, "business_profile_id", None)
        try:
            with tenant_context(tenant_id):
                with TRACER.start_as_current_span("ingest.process_job") as span:
                    if span.is_recording():
                        span.set_attribute("ingest.job_id", str(job.id))
                        span.set_attribute("ingest.job_type", str(job.job_type))
                    if job.job_type == KnowledgeIngestionJobType.EMBED:
                        result = self._process_embedding_job(job)
                    else:
                        upload = job.upload
                        job_started_at = time.perf_counter()
                        logger.info("ingest.start upload=%s job=%s source_type=%s", upload.id, job.id, upload.source_type)
                        # New emoji-enhanced logging
                        log_start(
                            logger,
                            "INGEST",
                            f"Document: {upload.source_name or upload.display_name}",
                            {
                                "job_id": job.id,
                                "upload_id": upload.id,
                                "source_type": upload.source_type,
                                "size_bytes": getattr(upload, "size_bytes", None),
                            },
                            emoji=LogEmoji.UPLOAD,
                        )
                        structured_log(
                            "rag",
                            "ingest.job_start",
                            {
                                "job_id": str(job.id),
                                "upload_id": str(upload.id),
                                "source_type": upload.source_type,
                                "size_bytes": getattr(upload, "size_bytes", None),
                            },
                            context={"business": upload.business_profile_id, "upload": upload.id, "job": job.id},
                            logger_obj=logger,
                        )
                        try:
                            from apps.knowledge.preflight.service import ensure_upload_preflight

                            preflight = ensure_upload_preflight(upload, trigger="ingest_job_start")
                            if isinstance(preflight, dict):
                                payload = dict(job.payload or {})
                                payload["preflight"] = {
                                    "status": preflight.get("status"),
                                    "format": preflight.get("format"),
                                    "suggested_kind": preflight.get("suggested_kind"),
                                    "warnings": list(preflight.get("warnings") or [])[:8],
                                }
                                KnowledgeIngestionJob.objects.filter(id=job.id).update(payload=payload)
                                job.payload = payload
                                if str(preflight.get("status") or "").lower() == "error":
                                    warnings = preflight.get("warnings") or []
                                    description = "; ".join([str(w) for w in warnings if w])[:400] if warnings else "Preflight blocked ingestion."
                                    raise KnowledgeIngestionError(f"preflight: {description}")
                        except KnowledgeIngestionError:
                            raise
                        except Exception as exc:  # pragma: no cover - preflight must not block ingestion
                            logger.warning("knowledge.preflight.job_start_failed upload=%s error=%s", upload.id, exc)
                        try:
                            with TRACER.start_as_current_span("ingest.extract") as extract_span:
                                extraction = self._extract_upload(upload)
                                characters = len(extraction.text)
                                if extract_span.is_recording():
                                    extract_span.set_attribute("ingest.characters", characters)
                                    extract_span.set_attribute("ingest.format", extraction.format_hint or "unknown")
                            with TRACER.start_as_current_span("ingest.persist") as persist_span:
                                self._persist_extraction(upload, extraction)
                                self._mark_job_completed(job, extra={"characters": characters, "format": extraction.format_hint})
                                if persist_span.is_recording():
                                    persist_span.set_attribute("ingest.characters", characters)
                            logger.info(
                                "ingest.done upload=%s job=%s chars=%s format=%s",
                                upload.id,
                                job.id,
                                characters,
                                extraction.format_hint,
                            )
                            duration_ms = int((time.perf_counter() - job_started_at) * 1000.0)
                            warn_ms = int(getattr(settings, "INGEST_SLO_WARN_MS", 60000) or 0)
                            slow = bool(warn_ms and duration_ms >= warn_ms)
                            ingestion_meta = upload.ingestion_metadata if isinstance(upload.ingestion_metadata, dict) else {}
                            dataset_meta = ingestion_meta.get("dataset") if isinstance(ingestion_meta, dict) else None
                            dataset_enabled = bool(isinstance(dataset_meta, dict) and dataset_meta.get("enabled"))
                            sheet_count = None
                            row_count = None
                            storage_format = None
                            if isinstance(dataset_meta, dict):
                                storage_format = dataset_meta.get("storage_format")
                                row_count = dataset_meta.get("row_count")
                                sheets = dataset_meta.get("sheets")
                                if isinstance(sheets, list):
                                    sheet_count = len([s for s in sheets if isinstance(s, dict)])

                            structured_log(
                                "rag",
                                "ingest.job_done",
                                {
                                    "job_id": str(job.id),
                                    "upload_id": str(upload.id),
                                    "status": "completed",
                                    "duration_ms": duration_ms,
                                    "format": extraction.format_hint,
                                    "chars": characters,
                                    "chunk_count": getattr(upload, "chunk_count", None),
                                    "token_count": getattr(upload, "token_count", None),
                                    "dataset_enabled": dataset_enabled,
                                    "dataset_storage_format": storage_format,
                                    "dataset_row_count": row_count,
                                    "dataset_sheet_count": sheet_count,
                                    "slo": "slow" if slow else None,
                                    "slo_warn_ms": warn_ms if slow else None,
                                },
                                context={"business": upload.business_profile_id, "upload": upload.id, "job": job.id},
                                logger_obj=logger,
                                level=logging.WARNING if slow else logging.INFO,
                            )

                            result = IngestionJobResult(
                                job_id=job.id,
                                upload_id=upload.id,
                                job_type=job.job_type,
                                status=KnowledgeIngestionJobStatus.COMPLETED,
                                characters=characters,
                            )
                        except KnowledgeIngestionError as exc:
                            self._handle_failure(job, str(exc), exc=exc)
                            logger.warning("Ingestion failed upload=%s job=%s error=%s", upload.id, job.id, exc)
                            duration_ms = int((time.perf_counter() - job_started_at) * 1000.0)
                            job.refresh_from_db(fields=["status"])
                            structured_log(
                                "rag",
                                "ingest.job_done",
                                {
                                    "job_id": str(job.id),
                                    "upload_id": str(upload.id),
                                    "status": "failed",
                                    "duration_ms": duration_ms,
                                    "error": str(exc)[:200],
                                },
                                context={"business": upload.business_profile_id, "upload": upload.id, "job": job.id},
                                logger_obj=logger,
                                level=logging.WARNING,
                            )
                            result = IngestionJobResult(
                                job_id=job.id,
                                upload_id=upload.id,
                                job_type=job.job_type,
                                status=job.status,
                                characters=0,
                                error=str(exc),
                            )
                if span.is_recording():
                    span.set_attribute("ingest.result_status", result.status.value)
                return result
        except Exception as exc:  # pragma: no cover - defensive guardrail
            logger.exception(
                "ingest.unexpected_error upload=%s job=%s", getattr(job, "upload_id", None), getattr(job, "id", None)
            )
            self._handle_failure(job, f"unexpected ingestion error: {exc}", exc=exc)
            job.refresh_from_db(fields=["status"])
            structured_log(
                "rag",
                "ingest.job_done",
                {
                    "job_id": str(getattr(job, "id", "")),
                    "upload_id": str(getattr(job, "upload_id", "")),
                    "status": "failed",
                    "error": str(exc)[:200],
                },
                context={
                    "business": getattr(job, "business_profile_id", None),
                    "upload": getattr(job, "upload_id", None),
                    "job": getattr(job, "id", None),
                },
                logger_obj=logger,
                level=logging.ERROR,
            )
            return IngestionJobResult(
                job_id=job.id,
                upload_id=job.upload_id,
                job_type=job.job_type,
                status=job.status,
                characters=0,
                error=str(exc),
            )
