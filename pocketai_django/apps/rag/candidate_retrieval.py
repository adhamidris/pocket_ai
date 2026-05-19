from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Mapping, Sequence

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, TrigramSimilarity
from django.core.cache import cache
from django.db import connection
from django.db.models import Q
from django.utils import timezone
from pgvector.django import CosineDistance

from apps.accounts.feature_flags import FeatureFlagService, FeatureState
from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import apply_customer_visible_chunks
from apps.knowledge.models import KnowledgeUpload, KnowledgeUploadChunk
from apps.rag.contracts import AliasSearchResult, ChunkResult, HybridSearchResult, QueryTraits
from apps.rag.embeddings import EmbeddingProviderError
from apps.rag.query_normalizer import QueryNormalizer
from apps.rag.rag_logging import rag_log
from core.metrics import latency_monitor
from core.otel import otel_trace
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)
TRACER = otel_trace.get_tracer(__name__)


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class CandidateRetrievalMixin:
    def _chunk_hits(
        self,
        business_profile,
        *,
        traits: QueryTraits,
        limit: int,
        alias_result: AliasSearchResult | None = None,
        feature_state: FeatureState | None = None,
        diagnostics: dict[str, object] | None = None,
        vector_ceiling: float | None = None,
        table_context: Mapping[str, object] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> tuple[ChunkResult, ...]:
        alias_result = alias_result or AliasSearchResult(tuple(), {})
        if alias_result.short_circuit and alias_result.hits:
            return tuple(alias_result.hits[:limit])

        alias_candidates = alias_result.hits if alias_result and not alias_result.short_circuit else tuple()
        ann_cap = self._effective_chunk_cap(business_profile, "ann")
        feature_state = feature_state or FeatureFlagService.snapshot(business_profile)
        ceiling = vector_ceiling if vector_ceiling is not None else self._vector_ceiling_for_business(business_profile)
        hybrid = self.search_free_text(
            business_profile=business_profile,
            query=traits.normalized or traits.original,
            limit=max(limit, ann_cap * 2),
            traits=traits,
            alias_candidates=alias_candidates,
            feature_state=feature_state,
            table_context=table_context,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            session_context=session_context,
        )
        if diagnostics is not None:
            diagnostics["vector_distance_ceiling"] = ceiling
            diagnostics["vector_candidates"] = hybrid.diagnostics.get("vector_candidates")
            diagnostics["fts_candidates"] = hybrid.diagnostics.get("fts_candidates")
            diagnostics["vector_duration_ms"] = hybrid.diagnostics.get("vector_duration_ms")
            diagnostics["fts_duration_ms"] = hybrid.diagnostics.get("fts_duration_ms")
            diagnostics["rerank_duration_ms"] = hybrid.diagnostics.get("rerank_duration_ms")
            diagnostics["vector_distance_mean"] = hybrid.diagnostics.get("vector_distance_mean")
            diagnostics["vector_distance_min"] = hybrid.diagnostics.get("vector_distance_min")
            diagnostics["vector_distance_max"] = hybrid.diagnostics.get("vector_distance_max")
            diagnostics["fts_threshold"] = hybrid.diagnostics.get("fts_threshold")
            diagnostics["fts_condensed_query"] = hybrid.diagnostics.get("fts_condensed_query")
            diagnostics["fts_token_filter_min_length"] = hybrid.diagnostics.get("fts_token_filter_min_length")
            diagnostics["fts_tokens_used"] = tuple(hybrid.diagnostics.get("fts_tokens_used") or ())[:5]
        candidates = list(hybrid.hits)
        if not candidates:
            return tuple()

        prioritized = self._prioritize_token_hits(
            candidates,
            traits.tokens,
            fallback=max(self.token_gate_fallback, limit * 2),
        )
        if not prioritized:
            return tuple()

        filtered = self._apply_vector_threshold(
            prioritized,
            hybrid.query_vector,
            ceiling=ceiling,
            min_keep=limit,
        )
        if diagnostics is not None:
            diagnostics["vector_candidates_post_threshold"] = len(filtered)
            diagnostics["scope_candidates_preclip"] = filtered
            diagnostics["chunk_hits_rerank_reused"] = True
            diagnostics["scope_summary_preclip"] = self._build_scope_summary_from_candidates(
                filtered,
                business_profile=business_profile,
                query_tokens=traits.tokens,
                filler_tokens=self._filler_tokens_for_business(business_profile),
            )
        comprehensive_intent = bool((table_context or {}).get("comprehensive_intent"))
        preserve_head = 0 if comprehensive_intent else min(self.mmr_preserve_head, limit, len(filtered))
        if diagnostics is not None:
            diagnostics["chunk_hits_mmr_preserve_head"] = preserve_head
            diagnostics["chunk_hits_mmr_applied"] = bool(hybrid.query_vector and len(filtered) > preserve_head)
        if preserve_head:
            head = list(filtered[:preserve_head])
            tail_candidates = filtered[preserve_head:]
            if hybrid.query_vector and tail_candidates and limit > preserve_head:
                tail = self._mmr_select(
                    tail_candidates,
                    hybrid.query_vector,
                    k=max(0, limit - preserve_head),
                    lam=self.mmr_lambda,
                )
                final = head + tail
            else:
                final = head
        else:
            final = self._mmr_select(filtered, hybrid.query_vector, k=limit, lam=self.mmr_lambda)
        return tuple(final)

    def search_free_text(
        self,
        *,
        business_profile,
        query: str,
        limit: int,
        traits: QueryTraits,
        alias_candidates: Sequence[ChunkResult] | None = None,
        feature_state: FeatureState | None = None,
        table_context: Mapping[str, object] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> HybridSearchResult:
        business_id = getattr(business_profile, "id", None) if business_profile else None
        with tenant_context(business_id):
            return self._search_free_text_inner(
                business_profile=business_profile,
                query=query,
                limit=limit,
                traits=traits,
                alias_candidates=alias_candidates,
                feature_state=feature_state,
                table_context=table_context,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                session_context=session_context,
            )

    def _search_free_text_inner(
        self,
        *,
        business_profile,
        query: str,
        limit: int,
        traits: QueryTraits,
        alias_candidates: Sequence[ChunkResult] | None = None,
        feature_state: FeatureState | None = None,
        table_context: Mapping[str, object] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> HybridSearchResult:
        with TRACER.start_as_current_span("knowledge.hybrid_search") as span:
            base_qs = self._base_chunk_queryset(
                business_profile,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            query_text = (query or "").strip() or traits.normalized or traits.original
            feature_state = feature_state or FeatureFlagService.snapshot(business_profile)
            query_vector: list[float] | None
            vector_diag: dict[str, object]
            vector_ms = 0
            vector_hits: Sequence[ChunkResult]
            backend = str(getattr(settings, "RAG_SEARCH_BACKEND", "postgres") or "postgres").strip().lower()
            azure_enabled = backend == "azure"
            azure_diag: dict[str, object] = {}
            azure_duration_ms = 0
            lexical_hits: Sequence[ChunkResult]
            lexical_ms = 0
            lexical_diag: dict[str, object] = {}

            if feature_state.hybrid_search:
                query_vector, vector_diag = self._build_query_vector(
                    business_profile=business_profile,
                    query_text=query_text.lower(),
                )
            else:
                query_vector = None
                vector_diag = {"vector_disabled": True}

            if azure_enabled:
                try:
                    from apps.rag.azure_ai_search import (
                        AzureAISearchConfig,
                        build_scope_filter,
                        search as azure_search,
                    )
                except Exception as exc:  # pragma: no cover - optional dependency
                    azure_enabled = False
                    azure_diag = {"azure_import_error": str(exc)[:200]}
                else:
                    config = AzureAISearchConfig.from_settings()
                    if not config:
                        azure_enabled = False
                        azure_diag = {"azure_configured": False}
                    else:
                        filter_expr, scope_diag = build_scope_filter(
                            business_id=business_profile.id,
                            allowed_upload_ids=allowed_upload_ids,
                            agent_explicit_upload_ids=allowed_explicit_upload_ids,
                            upload_filter_threshold=config.upload_filter_threshold,
                        )
                        top = max(limit * 8, 40)
                        if scope_diag.get("scope_filter_skipped"):
                            top = max(top, limit * 20, 200)
                        if allowed_upload_ids is not None and len(allowed_upload_ids) > config.upload_filter_threshold:
                            top = max(top, limit * 20, 200)
                        top = min(500, int(top))
                        try:
                            rows, azure_query_diag = azure_search(
                                config=config,
                                business_id=business_profile.id,
                                query_text=query_text,
                                query_vector=query_vector if feature_state.hybrid_search else None,
                                top=top,
                                filter=filter_expr,
                                semantic_enabled=config.semantic_enabled,
                                semantic_config=config.semantic_config,
                                request_timeout_s=config.request_timeout_s,
                            )
                            azure_duration_ms = int(azure_query_diag.get("duration_ms") or 0)
                            azure_diag = {
                                "retrieval_backend": "azure",
                                "azure_index": config.index_name,
                                "azure_candidates_raw": len(rows),
                                "azure_duration_ms": azure_duration_ms,
                            }
                            azure_diag.update(scope_diag)
                            azure_diag.update(azure_query_diag)
                            ordered: list[uuid.UUID] = []
                            scores: dict[uuid.UUID, float] = {}
                            meta: dict[uuid.UUID, dict[str, object]] = {}
                            for row in rows:
                                chunk_id_raw = row.get("chunk_id") if isinstance(row, Mapping) else None
                                try:
                                    chunk_id = uuid.UUID(str(chunk_id_raw))
                                except (TypeError, ValueError):
                                    continue
                                if chunk_id not in scores:
                                    ordered.append(chunk_id)
                                score_raw = row.get("score") if isinstance(row, Mapping) else None
                                try:
                                    scores[chunk_id] = float(score_raw) if score_raw is not None else 0.0
                                except (TypeError, ValueError):
                                    scores[chunk_id] = 0.0
                                meta[chunk_id] = dict(row) if isinstance(row, Mapping) else {}
                            chunk_lookup = self._fetch_chunks_by_ids(
                                business_profile=business_profile,
                                chunk_ids=ordered,
                                allowed_upload_ids=allowed_upload_ids,
                                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                            )
                            max_score = max(scores.values(), default=0.0)
                            hits: list[ChunkResult] = []
                            for chunk_id in ordered:
                                chunk = chunk_lookup.get(chunk_id)
                                if not chunk:
                                    continue
                                raw_score = scores.get(chunk_id, 0.0)
                                normalized = (raw_score / max_score) if max_score else 0.0
                                hit_diag = {
                                    "stage": "azure_search",
                                    "azure_score": raw_score,
                                    "azure_score_norm": round(normalized, 6),
                                }
                                extra = meta.get(chunk_id)
                                if extra:
                                    hit_diag["azure_upload_id"] = extra.get("upload_id")
                                    hit_diag["azure_chunk_index"] = extra.get("chunk_index")
                                    hit_diag["azure_title"] = extra.get("title")
                                    hit_diag["azure_format"] = extra.get("format")
                                    hit_diag["azure_index_type"] = extra.get("index_type")
                                    hit_diag["azure_is_table_chunk"] = extra.get("is_table_chunk")
                                hits.append(
                                    ChunkResult(
                                        chunk=chunk,
                                        source_stage="azure_search",
                                        lexical_score=min(1.0, max(0.0, float(normalized))),
                                        recency_score=self._recency_score(chunk.upload),
                                        diagnostics=hit_diag,
                                    )
                                )
                            lexical_hits = tuple(hits)
                            lexical_ms = azure_duration_ms
                            lexical_diag = {"lexical_strategy": "azure"}
                        except Exception as exc:  # pragma: no cover - external dependency
                            azure_enabled = False
                            azure_diag = {
                                "retrieval_backend": "azure_failed",
                                "azure_error": str(exc)[:250],
                            }

            if azure_enabled:
                vector_hits = tuple()
            else:
                if feature_state.hybrid_search:
                    vector_hits, vector_ms = self._vector_candidates(
                        business_id=business_profile.id,
                        base_qs=base_qs,
                        query_vector=query_vector,
                        limit=limit,
                        traits=traits,
                    )
                else:
                    vector_hits = tuple()
                lexical_hits, lexical_ms, lexical_diag = self._lexical_candidates(
                    business_profile=business_profile,
                    base_qs=base_qs,
                    traits=traits,
                    limit=limit,
                )
                lexical_diag.setdefault("retrieval_backend", "postgres")
            latency_monitor.observe("rag.vector", vector_ms, tags={"business": str(business_profile.id)})
            latency_monitor.observe("rag.lexical", lexical_ms, tags={"business": str(business_profile.id)})
            if azure_duration_ms:
                latency_monitor.observe("rag.azure", azure_duration_ms, tags={"business": str(business_profile.id)})
            merged = self._merge_candidates(
                alias_candidates or tuple(),
                vector_hits,
                lexical_hits,
            )
            reranked, rerank_ms, rerank_diag = self._rerank_candidates(
                merged,
                query_vector if self.embedding_service else None,
                traits=traits,
                feature_state=feature_state,
                table_context=table_context,
                session_context=session_context,
            )
            latency_monitor.observe(
                "rag.rerank",
                rerank_ms,
                tags={
                    "business": str(business_profile.id),
                },
            )
            diagnostics = {
                "vector_candidates": len(vector_hits),
                "vector_duration_ms": vector_ms,
                "fts_candidates": len(lexical_hits),
                "fts_duration_ms": lexical_ms,
                "rerank_duration_ms": rerank_ms,
                "hybrid_enabled": feature_state.hybrid_search,
            }
            diagnostics.update(lexical_diag)
            diagnostics.update(vector_diag)
            diagnostics.update(rerank_diag)
            if azure_diag:
                diagnostics.update(azure_diag)
            diagnostics.update(self._vector_distance_stats(vector_hits))
            diagnostics["stage"] = "hybrid"
            _rag_log(
                "hybrid.summary",
                {
                    "alias_stage": len(alias_candidates or ()),
                    "vector_candidates": len(vector_hits),
                    "fts_candidates": len(lexical_hits),
                    "hybrid_enabled": feature_state.hybrid_search,
                },
                indent=1,
                context={"business": business_profile.id},
            )
            if span.is_recording():
                span.set_attribute("knowledge.hybrid.vector_ms", vector_ms)
                span.set_attribute("knowledge.hybrid.lexical_ms", lexical_ms)
                if azure_duration_ms:
                    span.set_attribute("knowledge.hybrid.azure_ms", azure_duration_ms)
                span.set_attribute("knowledge.hybrid.rerank_ms", rerank_ms)
                span.set_attribute("knowledge.hybrid.candidates", len(reranked))
            return HybridSearchResult(
                hits=tuple(reranked),
                query_vector=query_vector,
                diagnostics=diagnostics,
            )

    def _fetch_chunks_by_ids(
        self,
        *,
        business_profile,
        chunk_ids: Sequence[uuid.UUID],
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> dict[uuid.UUID, KnowledgeUploadChunk]:
        if not chunk_ids:
            return {}
        qs = KnowledgeUploadChunk.objects.filter(
            business_profile=business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
            id__in=chunk_ids,
        )
        qs = self._apply_chunk_scope(
            qs,
            business_profile=business_profile,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        qs = apply_customer_visible_chunks(qs.select_related("upload"))
        return {chunk.id: chunk for chunk in qs}

    def _base_chunk_queryset(
        self,
        business_profile,
        *,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ):
        qs = KnowledgeUploadChunk.objects.filter(
            business_profile=business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
        ).filter(
            Q(metadata__search_tier__isnull=True) | ~Q(metadata__search_tier="drill_down")
        )
        qs = self._apply_chunk_scope(
            qs,
            business_profile=business_profile,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        return apply_customer_visible_chunks(qs.select_related("upload"))

    def _apply_chunk_scope(
        self,
        queryset,
        *,
        business_profile,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ):
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return queryset.none()
            return queryset.filter(upload_id__in=allowed_upload_ids)

        clauses: list[Q] = []
        if allowed_explicit_upload_ids:
            clauses.append(Q(upload_id__in=allowed_explicit_upload_ids))
        if not clauses:
            return queryset
        combined = clauses[0]
        for clause in clauses[1:]:
            combined |= clause
        return queryset.filter(combined)

    def _merge_candidates(self, *groups: Sequence[ChunkResult]) -> list[ChunkResult]:
        """
        Merge candidates from multiple search pathways, keeping the best occurrence
        of each chunk for deterministic results.
        """
        best: dict[uuid.UUID, ChunkResult] = {}
        order: list[uuid.UUID] = []

        def _merge_score(hit: ChunkResult) -> float:
            vec_contrib = -(hit.vector_distance or 0.0) if hit.vector_distance else 0.0
            return hit.alias_confidence + hit.lexical_score + vec_contrib

        for group in groups:
            for hit in group:
                cid = hit.chunk_id
                if cid not in best:
                    order.append(cid)
                    best[cid] = hit
                else:
                    existing_score = _merge_score(best[cid])
                    new_score = _merge_score(hit)
                    if new_score > existing_score:
                        best[cid] = hit

        return [best[cid] for cid in order]

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

    @staticmethod
    def _vector_distance_stats(candidates: Sequence[ChunkResult]) -> dict[str, float]:
        distances = [
            float(hit.vector_distance)
            for hit in candidates
            if isinstance(hit.vector_distance, (int, float))
        ]
        if not distances:
            return {}
        average = sum(distances) / len(distances)
        similarities = [1.0 - distance for distance in distances]
        sim_average = sum(similarities) / len(similarities)
        clamped_scores = [max(-1.0, min(1.0, sim)) for sim in similarities]
        score_average = sum(clamped_scores) / len(clamped_scores)
        return {
            "vector_distance_min": min(distances),
            "vector_distance_max": max(distances),
            "vector_distance_mean": round(average, 5),
            "vector_similarity_min": round(min(similarities), 5),
            "vector_similarity_max": round(max(similarities), 5),
            "vector_similarity_mean": round(sim_average, 5),
            "vector_score_mean": round(score_average, 5),
        }

    def _condensed_query_for_fts(self, business_profile, traits: QueryTraits) -> str:
        tokens = list(traits.tokens or ())
        if not tokens:
            return traits.normalized or traits.original or ""
        filler = self._filler_tokens_for_business(business_profile)
        filtered = [token for token in tokens if token and token not in filler]
        min_length = self._significant_token_min_length(business_profile)
        strong = [token for token in filtered if len(token) >= min_length]
        max_tokens = self._fts_condense_max_tokens(business_profile)
        chosen: list[str] = []
        seen: set[str] = set()
        for token in strong:
            if token in seen:
                continue
            seen.add(token)
            chosen.append(token)
            if len(chosen) >= max_tokens:
                break
        if not chosen:
            fallback = filtered or tokens
            chosen = []
            seen.clear()
            for token in fallback:
                if not token:
                    continue
                if token in filler:
                    continue
                if token in seen:
                    continue
                seen.add(token)
                chosen.append(token)
                if len(chosen) >= max_tokens:
                    break
        condensed = " ".join(chosen).strip()
        return condensed or traits.normalized or traits.original or ""

    def _lexical_candidates(
        self,
        *,
        business_profile,
        base_qs,
        traits: QueryTraits,
        limit: int,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        if getattr(settings, "RAG_FTS_ENABLED", True) and connection.vendor == "postgresql":
            hits, duration_ms, diag = self._fts_candidates(
                business_profile=business_profile,
                base_qs=base_qs,
                traits=traits,
                limit=limit,
            )
            if hits:
                return hits, duration_ms, diag
            fallback_hits, fallback_ms, fallback_diag = self._trigram_candidates(
                business_profile=business_profile,
                base_qs=base_qs,
                traits=traits,
                limit=limit,
            )
            fallback_diag.update(
                {
                    "lexical_strategy": "fts+trigram",
                    "fts_fallback": True,
                    "fts_duration_ms": duration_ms,
                    "fts_candidates": diag.get("fts_candidates", 0),
                    "fts_rank_max": diag.get("fts_rank_max", 0.0),
                    "fts_error": diag.get("fts_error"),
                    "fts_config": diag.get("fts_config"),
                    "fts_search_type": diag.get("fts_search_type"),
                }
            )
            total_ms = duration_ms + fallback_ms
            return fallback_hits, total_ms, fallback_diag

        return self._trigram_candidates(
            business_profile=business_profile,
            base_qs=base_qs,
            traits=traits,
            limit=limit,
        )

    def _fts_candidates(
        self,
        *,
        business_profile,
        base_qs,
        traits: QueryTraits,
        limit: int,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        with TRACER.start_as_current_span("knowledge.lexical_candidates") as span:
            condensed_query = self._condensed_query_for_fts(business_profile, traits)
            start = time.perf_counter()
            row_limit = max(limit * 8, 40)
            config = "simple"
            search_type = "plain"
            vector = SearchVector("content", config=config)
            token_min_length = self._significant_token_min_length(business_profile)
            filler = self._filler_tokens_for_business(business_profile)
            condensed_tokens = tuple(
                token
                for token in QueryNormalizer._TOKEN_SPLIT.split(
                    QueryNormalizer._normalize_query_text(condensed_query).lower()
                )
                if token
            )
            significant = [token for token in condensed_tokens if len(token) >= token_min_length and token not in filler]
            combined_query: SearchQuery | None = None
            for token in significant:
                token_query = SearchQuery(token, search_type=search_type, config=config)
                combined_query = token_query if combined_query is None else (combined_query | token_query)

            if combined_query is None:
                combined_query = SearchQuery(condensed_query, search_type=search_type, config=config)

            generic_anchor_tokens = set(filler)
            generic_anchor_tokens.update(self.table_query_keywords)
            generic_anchor_tokens.update(self.table_column_hint_base)
            generic_anchor_tokens.update({"card", "cards", "credit"})
            anchor_token = next(
                (token for token in significant if token not in generic_anchor_tokens),
                None,
            )
            filter_query = (
                SearchQuery(anchor_token, search_type=search_type, config=config)
                if anchor_token
                else combined_query
            )
            try:
                fts_qs = (
                    base_qs.annotate(
                        fts_vector=vector,
                        rank=SearchRank(vector, combined_query, cover_density=True),
                    )
                    .filter(fts_vector=filter_query)
                    .order_by("-rank", "id")[:row_limit]
                )
                rows = [(chunk, float(getattr(chunk, "rank", 0.0) or 0.0)) for chunk in fts_qs]
            except Exception as exc:  # pragma: no cover - DB / config edge cases
                duration_ms = int((time.perf_counter() - start) * 1000)
                diag: dict[str, object] = {
                    "lexical_strategy": "fts_error",
                    "fts_config": config,
                    "fts_search_type": search_type,
                    "fts_error": str(exc)[:200],
                }
                if span.is_recording():
                    span.set_attribute("knowledge.lexical_hits", 0)
                    span.set_attribute("knowledge.lexical_duration_ms", duration_ms)
                return [], duration_ms, diag

            max_rank = max((rank for _, rank in rows), default=0.0)
            hits: list[ChunkResult] = []
            for chunk, rank in rows:
                normalized_rank = (rank / max_rank) if max_rank > 0 else 0.0
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="content_fts",
                        lexical_score=min(1.0, max(0.0, normalized_rank)),
                        recency_score=self._recency_score(chunk.upload),
                        diagnostics={
                            "stage": "content_fts",
                            "fts_rank": round(rank, 6),
                            "fts_rank_norm": round(normalized_rank, 6),
                        },
                    )
                )
            duration_ms = int((time.perf_counter() - start) * 1000)
            diag = {
                "lexical_strategy": "fts",
                "fts_config": config,
                "fts_search_type": search_type,
                "fts_condensed_query": condensed_query,
                "fts_anchor_token": anchor_token,
                "fts_candidates": len(hits),
                "fts_rank_max": round(max_rank, 6) if max_rank else 0.0,
            }
            if span.is_recording():
                span.set_attribute("knowledge.lexical_hits", len(hits))
                span.set_attribute("knowledge.lexical_duration_ms", duration_ms)
            return hits, duration_ms, diag

    def _trigram_candidates(
        self,
        *,
        business_profile,
        base_qs,
        traits: QueryTraits,
        limit: int,
    ) -> tuple[list[ChunkResult], int, dict[str, object]]:
        with TRACER.start_as_current_span("knowledge.lexical_candidates") as span:
            condensed_query = self._condensed_query_for_fts(business_profile, traits)
            threshold = self._lexical_threshold_for_business(business_profile, traits)
            token_min_length = self._significant_token_min_length(business_profile)
            condensed_tokens = tuple(
                token
                for token in QueryNormalizer._TOKEN_SPLIT.split(
                    QueryNormalizer._normalize_query_text(condensed_query).lower()
                )
                if token
            )
            filler = self._filler_tokens_for_business(business_profile)
            ordered_tokens = tuple(token for token in traits.tokens if token and token not in filler)
            token_filter = self._build_fts_token_filter(
                ordered_tokens or condensed_tokens or traits.tokens,
                min_length=token_min_length,
            )
            fts_base = base_qs.filter(token_filter) if token_filter else base_qs
            row_limit = max(limit * 8, 40)
            start = time.perf_counter()
            fts_qs = (
                fts_base.annotate(sim=TrigramSimilarity("content", condensed_query))
                .filter(sim__gte=threshold)
                .order_by("-sim", "id")[:row_limit]
            )
            hits: list[ChunkResult] = []
            for chunk in fts_qs:
                sim = getattr(chunk, "sim", 0.0) or 0.0
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="content_trigram",
                        lexical_score=float(sim),
                        recency_score=self._recency_score(chunk.upload),
                        diagnostics={"stage": "content_trigram"},
                    )
                )
            duration_ms = int((time.perf_counter() - start) * 1000)
            diag = {
                "lexical_strategy": "trigram",
                "fts_threshold": threshold,
                "fts_condensed_query": condensed_query,
                "fts_tokens_used": condensed_tokens[:5],
                "fts_token_filter_min_length": token_min_length,
                "trigram_threshold": threshold,
                "trigram_condensed_query": condensed_query,
                "trigram_tokens_used": condensed_tokens[:5],
                "trigram_token_filter_min_length": token_min_length,
            }
            if span.is_recording():
                span.set_attribute("knowledge.lexical_hits", len(hits))
                span.set_attribute("knowledge.lexical_duration_ms", duration_ms)
                span.set_attribute("knowledge.lexical_threshold", threshold)
            return hits, duration_ms, diag

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

    @staticmethod
    def _lexical_threshold(traits: QueryTraits) -> float:
        if traits.token_count <= 3:
            return 0.25
        if traits.token_count <= 6:
            return 0.2
        return 0.15

    @staticmethod
    def _extract_query_tokens(query: str) -> tuple[str, ...]:
        if not query:
            return tuple()
        normalized = QueryNormalizer._normalize_query_text(query).lower()
        tokens = [token for token in QueryNormalizer._TOKEN_SPLIT.split(normalized) if len(token) >= 3]
        seen: dict[str, None] = {}
        for token in tokens:
            if token and token not in seen:
                seen[token] = None
        return tuple(seen.keys())

    def _prioritize_token_hits(
        self,
        candidates: Sequence[ChunkResult],
        tokens: tuple[str, ...],
        fallback: int,
    ) -> list[ChunkResult]:
        if not tokens:
            return list(candidates)
        matched: list[ChunkResult] = []
        remainder: list[ChunkResult] = []
        for candidate in candidates:
            cont = self._chunk_contains_tokens(candidate.chunk, tokens)
            if cont:
                matched.append(candidate)
            else:
                remainder.append(candidate)
        if not matched:
            return list(candidates)
        tail = remainder[:fallback] if fallback else []
        return matched + tail

    @staticmethod
    def _chunk_contains_tokens(chunk: KnowledgeUploadChunk, tokens: tuple[str, ...]) -> bool:
        if not tokens:
            return True
        text = (chunk.content or "").lower()
        if not text:
            return False
        if any(token in text for token in tokens):
            return True
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        alias_blob = str(metadata.get("alias_string") or "").lower()
        if alias_blob and any(token in alias_blob for token in tokens):
            return True
        return False

    def _build_fts_token_filter(self, tokens: tuple[str, ...], *, min_length: int) -> Q | None:
        if not tokens:
            return None
        significant = [token for token in tokens if len(token) >= min_length][:3]
        if not significant:
            return None
        clause = Q()
        for token in significant:
            clause |= Q(content__icontains=token) | Q(metadata__alias_string__icontains=token)
        return clause

    def _ann_factor_for_query(self, traits: QueryTraits) -> float:
        token_count = traits.token_count
        if token_count <= 3:
            return self.short_query_ann_multiplier
        if token_count <= 6:
            return 1.5
        return 1.0
