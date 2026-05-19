from __future__ import annotations

import logging
import time
import uuid
from typing import Sequence

from django.db import transaction
from django.utils import timezone

from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadChunk
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


class IngestionSearchIndexingMixin:

    def _schedule_azure_search_index_update(
        self,
        *,
        upload: KnowledgeUpload,
        format_hint: str | None,
        now: timezone.datetime,
        previous_chunk_count: int,
        chunk_objects: Sequence[KnowledgeUploadChunk],
    ) -> None:
        """
        Index freshly ingested chunks into Azure AI Search (P2) after DB commit.

        This is best-effort: ingestion should succeed even if the external index
        is temporarily unavailable. When Azure search is the active retrieval
        backend, failures are recorded in upload.ingestion_metadata for visibility.
        """

        def _on_commit() -> None:
            try:
                from apps.rag.azure_ai_search import (
                    AzureAISearchConfig,
                    delete_upload,
                    upsert_upload_chunks,
                )
            except Exception:
                return

            config = AzureAISearchConfig.from_settings()
            if not config:
                return

            business_id = getattr(upload, "business_profile_id", None)
            if not business_id:
                return

            started = time.perf_counter()
            status = "ok"
            error = ""
            try:
                with tenant_context(business_id):
                    title = (upload.display_name or upload.source_name or upload.external_reference or str(upload.id)).strip()
                    chunk_payloads = [
                        {
                            "chunk_id": chunk.id,
                            "chunk_index": chunk.chunk_index,
                            "content": chunk.content,
                            "embedding": chunk.embedding,
                            "metadata": chunk.metadata,
                        }
                        for chunk in chunk_objects
                    ]
                if previous_chunk_count:
                    delete_upload(config=config, upload_id=upload.id, chunk_count=previous_chunk_count)
                upsert_upload_chunks(
                    config=config,
                    business_id=uuid.UUID(str(business_id)),
                    upload_id=upload.id,
                    title=title,
                    format_hint=format_hint,
                    updated_at=now,
                    chunks=chunk_payloads,
                )
            except Exception as exc:  # pragma: no cover - external dependency
                status = "failed"
                error = str(exc)[:300]
                logger.warning(
                    "azure_search.index_failed business=%s upload=%s error=%s",
                    business_id,
                    upload.id,
                    error,
                )
            finally:
                duration_ms = int((time.perf_counter() - started) * 1000.0)
                try:
                    from apps.rag.knowledge_search import KnowledgeSearchService

                    KnowledgeSearchService.invalidate_result_cache(uuid.UUID(str(business_id)))
                except Exception:
                    pass
                try:
                    with tenant_context(business_id):
                        refreshed = KnowledgeUpload.objects.filter(id=upload.id).values("ingestion_metadata").first()
                        meta = dict((refreshed or {}).get("ingestion_metadata") or {})
                        meta["azure_search"] = {
                            "status": status,
                            "index_name": config.index_name,
                            "chunk_count": int(getattr(upload, "chunk_count", 0) or 0),
                            "duration_ms": duration_ms,
                            "indexed_at": now.isoformat(),
                            "previous_chunk_count": int(previous_chunk_count),
                            "error": error,
                        }
                        KnowledgeUpload.objects.filter(id=upload.id).update(ingestion_metadata=meta)
                except Exception:
                    pass

        try:
            transaction.on_commit(_on_commit)
        except Exception:  # pragma: no cover - defensive
            return

    def _try_update_azure_search_embeddings(
        self,
        *,
        upload: KnowledgeUpload,
        chunks: Sequence[KnowledgeUploadChunk],
    ) -> None:
        """
        Best-effort: when embeddings are generated asynchronously, update the Azure
        index vectors so hybrid search quality remains stable.
        """

        try:
            from apps.rag.azure_ai_search import AzureAISearchConfig, update_chunk_embeddings
        except Exception:
            return

        config = AzureAISearchConfig.from_settings()
        if not config:
            return

        business_id = getattr(upload, "business_profile_id", None)
        if not business_id:
            return

        payloads = [
            {
                "chunk_id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "embedding": chunk.embedding,
            }
            for chunk in chunks
            if chunk.embedding is not None
        ]
        if not payloads:
            return
        try:
            update_chunk_embeddings(config=config, upload_id=upload.id, chunks=payloads)
        except Exception as exc:  # pragma: no cover - external dependency
            logger.warning(
                "azure_search.embedding_update_failed business=%s upload=%s error=%s",
                business_id,
                upload.id,
                str(exc)[:250],
            )
