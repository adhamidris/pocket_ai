from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from django.conf import settings

from apps.accounts.feature_flags import FeatureFlagService, FeatureState
from apps.rag.contracts import ChunkResult, HybridSearchResult, QueryTraits
from apps.rag.observability.logging import rag_log
from core.metrics import latency_monitor
from core.otel import otel_trace
from core.tenancy import tenant_context

TRACER = otel_trace.get_tracer(__name__)


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class HybridSearchMixin:

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
                    from apps.rag.integrations.azure_ai_search import (
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
