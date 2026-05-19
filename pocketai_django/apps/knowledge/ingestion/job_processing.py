from __future__ import annotations

import logging
import random
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from apps.accounts.models import KnowledgeIngestionJobStatus, KnowledgeIngestionJobType, KnowledgeStatus
from apps.core.logging_utils import LogEmoji, log_start
from apps.knowledge.ingestion.contracts import KnowledgeIngestionError, UnsupportedFormatError
from apps.knowledge.ingestion.jobs import IngestionJobResult
from apps.knowledge.models import KnowledgeIngestionJob, KnowledgeUploadChunk
from apps.rag.embeddings import EmbeddingProviderError
from apps.rag.rag_logging import structured_log
from core.otel import otel_trace
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)


class IngestionJobProcessingMixin:

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
                            from apps.knowledge.knowledge_preflight import ensure_upload_preflight

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

    def _process_embedding_job(self, job: KnowledgeIngestionJob) -> IngestionJobResult:
        with TRACER.start_as_current_span("ingest.embed_job") as span:
            payload = job.payload or {}
            chunk_ids = payload.get("chunk_ids") if isinstance(payload, dict) else []
            normalized_ids: list[uuid.UUID] = []
            for value in chunk_ids or []:
                try:
                    normalized_ids.append(uuid.UUID(str(value)))
                except (TypeError, ValueError):
                    continue
            if span.is_recording():
                span.set_attribute("ingest.embed.chunk_ids", len(normalized_ids))
            if not normalized_ids:
                self._mark_job_completed(job, extra={"embedded_chunks": 0})
                return IngestionJobResult(
                    job_id=job.id,
                    upload_id=job.upload_id,
                    job_type=job.job_type,
                    status=KnowledgeIngestionJobStatus.COMPLETED,
                    characters=0,
                )
            chunks = list(
                KnowledgeUploadChunk.objects.filter(
                    id__in=normalized_ids,
                    upload=job.upload,
                ).order_by("chunk_index")
            )
            if not chunks:
                self._mark_job_completed(job, extra={"embedded_chunks": 0})
                return IngestionJobResult(
                    job_id=job.id,
                    upload_id=job.upload_id,
                    job_type=job.job_type,
                    status=KnowledgeIngestionJobStatus.COMPLETED,
                    characters=0,
                )
            provider = self.embedding_service or self._get_fallback_embedding_service()
            if not provider:
                error = "Embedding backend unavailable"
                self._handle_failure(job, error, retryable=True)
                job.refresh_from_db(fields=["status"])
                return IngestionJobResult(
                    job_id=job.id,
                    upload_id=job.upload_id,
                    job_type=job.job_type,
                    status=job.status,
                    characters=0,
                    error=error,
                )
            updated: list[KnowledgeUploadChunk] = []
            processed = 0
            batched: list[list[KnowledgeUploadChunk]] = [
                chunks[i : i + self.embedding_batch_size] for i in range(0, len(chunks), self.embedding_batch_size)
            ]
            for batch in batched:
                texts = [chunk.content or "" for chunk in batch]
                if not any(texts):
                    continue
                try:
                    with TRACER.start_as_current_span("ingest.embed_batch") as batch_span:
                        vectors = provider.embed_texts(texts)
                        if batch_span.is_recording():
                            batch_span.set_attribute("ingest.embed.batch_size", len(batch))
                except EmbeddingProviderError as exc:
                    self._handle_failure(job, f"Embedding batch failed: {exc}", exc=exc)
                    job.refresh_from_db(fields=["status"])
                    return IngestionJobResult(
                        job_id=job.id,
                        upload_id=job.upload_id,
                        job_type=job.job_type,
                        status=job.status,
                        characters=processed,
                        error=str(exc),
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    self._handle_failure(job, f"Embedding batch exception: {exc}", exc=exc)
                    job.refresh_from_db(fields=["status"])
                    return IngestionJobResult(
                        job_id=job.id,
                        upload_id=job.upload_id,
                        job_type=job.job_type,
                        status=job.status,
                        characters=processed,
                        error=str(exc),
                    )
                for chunk, vector in zip(batch, vectors):
                    normalized = self._normalize_embedding(vector)
                    if normalized:
                        chunk.embedding = normalized
                        chunk.updated_at = timezone.now()
                        updated.append(chunk)
                        processed += 1
            processed_ids = [str(chunk.id) for chunk in updated]
            if updated:
                KnowledgeUploadChunk.objects.bulk_update(updated, ["embedding", "updated_at"])
                self._try_update_azure_search_embeddings(upload=job.upload, chunks=updated)
            if processed_ids:
                self._update_upload_embedding_metadata(job.upload, processed_ids=processed_ids)
            self._mark_job_completed(job, extra={"embedded_chunks": processed})
            if span.is_recording():
                span.set_attribute("ingest.embed.chunks", processed)
            return IngestionJobResult(
                job_id=job.id,
                upload_id=job.upload_id,
                job_type=job.job_type,
                status=KnowledgeIngestionJobStatus.COMPLETED,
                characters=processed,
            )
