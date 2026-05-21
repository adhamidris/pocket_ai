from __future__ import annotations

import logging
import uuid
from typing import Sequence

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import KnowledgeIngestionJobStatus, KnowledgeIngestionJobType
from apps.knowledge.models import KnowledgeIngestionJob, KnowledgeUpload, KnowledgeUploadChunk
from apps.rag.embeddings import EmbeddingProviderError, LocalEmbeddingService, build_embedding_service

logger = logging.getLogger(__name__)


class IngestionEmbeddingsMixin:

    def _schedule_embedding_jobs(self, upload: KnowledgeUpload, chunk_ids: Sequence[str]) -> None:
        if not chunk_ids:
            return
        batch_size = self.embedding_job_payload_size
        business = upload.business_profile
        for idx in range(0, len(chunk_ids), batch_size):
            batch = chunk_ids[idx : idx + batch_size]
            job = KnowledgeIngestionJob.objects.create(
                business_profile=business,
                upload=upload,
                job_type=KnowledgeIngestionJobType.EMBED,
                status=KnowledgeIngestionJobStatus.QUEUED,
                max_attempts=self.embed_job_max_attempts,
                payload={"chunk_ids": batch},
            )
            logger.info("Queued embedding job upload=%s job=%s chunks=%s", upload.id, job.id, len(batch))
        backlog = self._embedding_backlog_count(business.id)
        if self.embedding_backlog_threshold and backlog >= self.embedding_backlog_threshold:
            logger.warning(
                "embedding.backlog threshold exceeded business=%s backlog=%s threshold=%s",
                business.id,
                backlog,
                self.embedding_backlog_threshold,
            )

    def _update_upload_embedding_metadata(self, upload: KnowledgeUpload, *, processed_ids: Sequence[str]) -> None:
        metadata = dict(upload.ingestion_metadata or {})
        pending = metadata.get("pending_embedding_chunks")
        if isinstance(pending, list):
            pending_set = {str(value) for value in pending}
            for chunk_id in processed_ids:
                pending_set.discard(str(chunk_id))
            if pending_set:
                metadata["pending_embedding_chunks"] = list(pending_set)[:50]
            else:
                metadata.pop("pending_embedding_chunks", None)
        remaining = KnowledgeUploadChunk.objects.filter(upload=upload, embedding__isnull=True).count()
        if remaining:
            metadata["pending_embedding_chunk_count"] = remaining
        else:
            metadata.pop("pending_embedding_chunk_count", None)
        upload.ingestion_metadata = metadata
        upload.save(update_fields=["ingestion_metadata", "updated_at"])

    def _invalidate_alias_cache(self, business_id: uuid.UUID) -> None:
        try:
            from apps.rag.knowledge_search import KnowledgeSearchService
        except ImportError:  # pragma: no cover - defensive import
            return
        KnowledgeSearchService.invalidate_alias_cache(business_id)
        KnowledgeSearchService.invalidate_query_cache(business_id)
        KnowledgeSearchService.invalidate_result_cache(business_id)
        
        # P0 #3: Invalidate table profile cache on new uploads
        try:
            from apps.rag.tables.profile_cache import invalidate_table_profile_cache
            invalidate_table_profile_cache(business_id)
        except Exception as exc:
            logger.warning(
                "table_profile_cache.invalidate_failed business=%s error=%s",
                business_id,
                str(exc)[:200],
            )

    def _embedding_backlog_count(self, business_id: uuid.UUID) -> int:
        return KnowledgeIngestionJob.objects.filter(
            business_profile_id=business_id,
            job_type=KnowledgeIngestionJobType.EMBED,
            status__in=(KnowledgeIngestionJobStatus.QUEUED, KnowledgeIngestionJobStatus.RUNNING),
        ).count()

    def _release_deferred_jobs(self, business_id: uuid.UUID) -> None:
        if not self.ingest_concurrency_limit:
            return
        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        active = KnowledgeIngestionJob.objects.filter(
            business_profile_id=business_id,
            job_type=KnowledgeIngestionJobType.INGEST,
        ).filter(
            Q(status=KnowledgeIngestionJobStatus.RUNNING)
            | (Q(status=KnowledgeIngestionJobStatus.QUEUED) & eligible)
        ).count()
        available = self.ingest_concurrency_limit - active
        if available <= 0:
            return
        deferred = list(
            KnowledgeIngestionJob.objects.filter(
                business_profile_id=business_id,
                status=KnowledgeIngestionJobStatus.DEFERRED,
                job_type=KnowledgeIngestionJobType.INGEST,
            )
            .order_by("created_at")[:available]
        )
        if not deferred:
            return
        ids = [job.id for job in deferred]
        KnowledgeIngestionJob.objects.filter(id__in=ids).update(status=KnowledgeIngestionJobStatus.QUEUED)
        logger.info("Promoted %s deferred ingestion jobs for business=%s", len(ids), business_id)

    def _normalize_embedding(self, vector: Sequence[float] | None) -> list[float] | None:
        if not vector:
            return None
        try:
            values = [float(v) for v in vector]
        except (TypeError, ValueError):
            return None
        expected = getattr(settings, "EMBED_DIM", None)
        if expected:
            if len(values) > expected:
                logger.warning("Embedding dimension mismatch: got %s, expected %s. Trimming.", len(values), expected)
                values = values[:expected]
            elif len(values) < expected:
                logger.warning("Embedding dimension mismatch: got %s, expected %s. Padding.", len(values), expected)
                values = values + [0.0] * (expected - len(values))
        return values

    def _get_fallback_embedding_service(self) -> LocalEmbeddingService | None:
        if isinstance(self.embedding_service, LocalEmbeddingService):
            return self.embedding_service
        if self._fallback_embedding_service:
            return self._fallback_embedding_service
        if self._fallback_embedding_attempted:
            return None
        self._fallback_embedding_attempted = True
        try:
            self._fallback_embedding_service = build_embedding_service("local")
        except EmbeddingProviderError as exc:
            logger.warning("Local embedding fallback unavailable: %s", exc)
            self._fallback_embedding_service = None
        return self._fallback_embedding_service

    def _apply_fallback_embeddings(
        self,
        upload: KnowledgeUpload,
        chunks: Sequence[KnowledgeUploadChunk],
    ) -> int:
        service = self._get_fallback_embedding_service()
        if not service:
            return 0
        texts = [chunk.content or "" for chunk in chunks]
        if not any(texts):
            return 0
        try:
            vectors = service.embed_texts(texts)
        except EmbeddingProviderError as exc:
            logger.warning("Fallback embedding generation failed upload=%s error=%s", upload.id, exc)
            return 0
        except Exception:
            logger.exception("Unexpected fallback embedding failure upload=%s", upload.id)
            return 0
        filled = 0
        for chunk, vector in zip(chunks, vectors):
            normalized = self._normalize_embedding(vector)
            if normalized:
                chunk.embedding = normalized
                filled += 1
        return filled
