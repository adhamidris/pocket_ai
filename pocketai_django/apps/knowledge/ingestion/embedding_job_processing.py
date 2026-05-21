from __future__ import annotations

import uuid

from django.utils import timezone

from apps.accounts.models import KnowledgeIngestionJobStatus
from apps.knowledge.ingestion.jobs import IngestionJobResult
from apps.knowledge.models import KnowledgeIngestionJob, KnowledgeUploadChunk
from apps.rag.embeddings import EmbeddingProviderError
from core.otel import otel_trace


TRACER = otel_trace.get_tracer(__name__)


class IngestionEmbeddingJobProcessingMixin:

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
