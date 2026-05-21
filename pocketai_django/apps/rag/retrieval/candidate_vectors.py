from __future__ import annotations

import hashlib
import logging
import time
from typing import Sequence

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from pgvector.django import CosineDistance

from apps.knowledge.models import KnowledgeUpload
from apps.rag.contracts import ChunkResult, QueryTraits
from apps.rag.embeddings import EmbeddingProviderError
from apps.rag.observability.logging import rag_log
from core.otel import otel_trace


logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class CandidateVectorMixin:

    def _vector_candidates(
        self,
        *,
        business_id,
        base_qs,
        query_vector: list[float] | None,
        limit: int,
        traits: QueryTraits,
    ) -> tuple[list[ChunkResult], int]:
        if not query_vector:
            return [], 0
        adaptive_factor = self._ann_factor_for_query(traits)
        with TRACER.start_as_current_span("knowledge.vector_candidates") as span:
            if span.is_recording():
                span.set_attribute("knowledge.business_id", str(business_id))
                span.set_attribute("knowledge.vector.limit", limit)
                span.set_attribute("knowledge.vector.adaptive_factor", adaptive_factor)
                span.set_attribute("knowledge.vector_tokens", traits.token_count)
            base_k = max(limit * 10, 60)
            k_value = int(base_k * adaptive_factor)
            start = time.perf_counter()
            if self.ivfflat_probes:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SET ivfflat.probes = %s", [self.ivfflat_probes])
                except Exception:  # pragma: no cover - diagnostic only
                    logger.debug("Unable to set ivfflat.probes", exc_info=True)
            ann_qs = (
                base_qs.exclude(embedding__isnull=True)
                .annotate(distance=CosineDistance("embedding", query_vector))
                .order_by("distance", "id")[:k_value]
            )
            hits: list[ChunkResult] = []
            distances: list[float] = []
            for chunk in ann_qs:
                distance = getattr(chunk, "distance", None)
                dist_val = None
                if distance is not None:
                    try:
                        dist_val = float(distance)
                        distances.append(dist_val)
                    except (TypeError, ValueError):
                        dist_val = None
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="vector_ann",
                        vector_distance=dist_val,
                        recency_score=self._recency_score(chunk.upload),
                        diagnostics={"stage": "vector_ann"},
                    )
                )
            duration_ms = int((time.perf_counter() - start) * 1000)
            distance_min = min(distances) if distances else None
            distance_max = max(distances) if distances else None
            distance_avg = (sum(distances) / len(distances)) if distances else None
            if span.is_recording():
                span.set_attribute("knowledge.vector_duration_ms", duration_ms)
                if distance_min is not None:
                    span.set_attribute("knowledge.vector_distance_min", distance_min)
                if distance_avg is not None:
                    span.set_attribute("knowledge.vector_distance_mean", distance_avg)
            _rag_log(
                "vector.candidates",
                {
                    "query_tokens": traits.token_count,
                    "candidates": len(hits),
                    "d_min": f"{distance_min:.4f}" if distance_min is not None else None,
                    "d_max": f"{distance_max:.4f}" if distance_max is not None else None,
                    "d_avg": f"{distance_avg:.4f}" if distance_avg is not None else None,
                },
                indent=1,
                context={"business": business_id},
            )
            return hits, duration_ms

    def _build_query_vector(
        self,
        *,
        business_profile,
        query_text: str,
    ) -> tuple[list[float] | None, dict[str, object]]:
        diagnostics: dict[str, object] = {"vector_cache_hit": False}
        if not self.embedding_service:
            return None, diagnostics
        model_name = getattr(self.embedding_service, "model", "local")
        qvec_version = self._get_query_cache_version(business_profile.id)
        digest_source = f"{business_profile.id}:{model_name}:{query_text}".encode("utf-8")
        cache_key = f"rag:qvec:{qvec_version}:{hashlib.sha256(digest_source).hexdigest()[:32]}"
        query_vector: list[float] | None = cache.get(cache_key)
        diagnostics["vector_cache_hit"] = query_vector is not None
        if query_vector is None:
            try:
                t0 = time.time()
                query_vector = self.embedding_service.embed_text(query_text)
                payload = list(query_vector) if isinstance(query_vector, (list, tuple)) else query_vector
                approx_size = len(payload) * 8 if isinstance(payload, list) else 0
                if approx_size <= self.query_vector_cache_max_bytes:
                    cache.set(cache_key, payload, timeout=self.query_vector_cache_ttl)
                diagnostics["vector_embed_ms"] = int((time.time() - t0) * 1000)
            except EmbeddingProviderError as exc:
                logger.warning("Query embedding failed: %s", exc)
                query_vector = None
        return query_vector, diagnostics

    def _apply_vector_threshold(
        self,
        candidates: Sequence[ChunkResult],
        query_vector: list[float] | None,
        *,
        ceiling: float | None,
        min_keep: int = 0,
    ) -> list[ChunkResult]:
        threshold = ceiling if ceiling is not None else self.vector_distance_ceiling
        if not threshold or threshold <= 0 or not query_vector:
            return list(candidates)
        filtered = [
            hit
            for hit in candidates
            if hit.vector_distance is None or hit.vector_distance <= threshold
        ]
        if not filtered:
            return list(candidates)
        if min_keep > 0 and len(filtered) < min_keep:
            seen = {hit.chunk_id for hit in filtered}
            for hit in candidates:
                if hit.chunk_id in seen:
                    continue
                filtered.append(hit)
                seen.add(hit.chunk_id)
                if len(filtered) >= min_keep:
                    break
        return filtered

    def _mmr_select(
        self,
        candidates: Sequence[ChunkResult],
        query_vector: list[float] | None,
        *,
        k: int,
        lam: float,
    ) -> list[ChunkResult]:
        if not query_vector:
            return list(candidates)[:k]
        selected: list[ChunkResult] = []
        remaining = list(candidates)
        while remaining and len(selected) < k:

            def score(hit: ChunkResult) -> tuple[float, str]:
                vector = self._chunk_embedding(hit)
                rel = self._cosine_similarity(query_vector, vector) if vector else 0.0
                diversity = 0.0
                if selected and vector:
                    sims = [
                        self._cosine_similarity(self._chunk_embedding(other), vector)
                        for other in selected
                        if self._chunk_embedding(other)
                    ]
                    diversity = max(sims) if sims else 0.0
                mmr_score = lam * rel - (1 - lam) * diversity
                return (mmr_score, str(hit.chunk_id))

            best = max(remaining, key=score)
            selected.append(best)
            remaining.remove(best)
        return selected

    @staticmethod
    def _chunk_embedding(hit: ChunkResult) -> Sequence[float] | None:
        embedding = getattr(hit.chunk, "embedding", None)
        return embedding if isinstance(embedding, Sequence) else None

    @staticmethod
    def _recency_score(upload: KnowledgeUpload) -> float:
        updated = getattr(upload, "updated_at", None)
        if not updated:
            return 0.0
        age_days = max(0.0, (timezone.now() - updated).total_seconds() / 86400.0)
        half_life = max(1.0, float(getattr(settings, "RAG_RECENCY_DECAY_DAYS", 90)))
        floor = float(getattr(settings, "RAG_RECENCY_MIN_FLOOR", 0.05))
        boost = float(getattr(settings, "RAG_RECENCY_BONUS_FRESH", 0.15))
        recency = 0.5 ** (age_days / half_life)
        if age_days <= 7:
            recency += boost
        return max(floor, min(1.0, recency))

    def _ann_factor_for_query(self, traits: QueryTraits) -> float:
        token_count = traits.token_count
        if token_count <= 3:
            return self.short_query_ann_multiplier
        if token_count <= 6:
            return 1.5
        return 1.0
