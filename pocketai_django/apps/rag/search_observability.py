from __future__ import annotations

import logging
import time
import uuid
from typing import Mapping

from pgvector.django import CosineDistance

from apps.accounts.feature_flags import FeatureState
from apps.accounts.models import KnowledgeStatus, KnowledgeVisibility
from apps.knowledge.models import KnowledgeUploadShadowChunk
from apps.rag.contracts import AliasSearchResult, KnowledgeSearchResult, QueryTraits
from apps.rag.quality_monitor import QualityMonitor
from apps.rag.rag_logging import rag_log


logger = logging.getLogger(__name__)


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class SearchObservabilityMixin:
    @staticmethod
    def _duration_ms(start: float | None) -> int:
        if start is None:
            return 0
        elapsed = (time.perf_counter() - start) * 1000
        return int(max(0.0, elapsed))

    def _record_retrieval_event(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        alias_result: AliasSearchResult,
        result: KnowledgeSearchResult,
        feature_state: FeatureState | None = None,
    ) -> None:
        try:
            QualityMonitor.record_retrieval_sample(
                business_profile=business_profile,
                query_type="identifier" if traits.is_identifier_like else "natural",
                alias_hit=bool(alias_result.short_circuit and alias_result.hits),
                fallback_used=result.status == "not_found" or result.diagnostics.get("path") == "fallback",
                latency_ms=result.diagnostics.get("total_duration_ms"),
                stage=result.diagnostics.get("path"),
                feature_flags=feature_state.as_dict() if feature_state else None,
                diagnostics=result.diagnostics,
                result_status=result.status,
            )
        except Exception as exc:  # pragma: no cover - monitoring must not block retrieval
            logger.warning(
                "quality.retrieval.monitor_failed business=%s error=%s",
                business_profile.id if business_profile else None,
                exc,
            )

    def _log_search_summary(
        self,
        *,
        business_profile,
        request_id: uuid.UUID,
        result: KnowledgeSearchResult,
    ) -> None:
        diagnostics = dict(result.diagnostics or {})
        query_preview = (diagnostics.get("normalized_query") or diagnostics.get("original_query") or "").replace(
            "\n",
            " ",
        )
        if len(query_preview) > 200:
            query_preview = f"{query_preview[:200]}..."
        top_score = None
        top_diag: Mapping[str, object] = {}
        top_breakdown: Mapping[str, object] = {}
        if result.snippets:
            top_score = result.snippets[0].confidence_score
            top_diag = result.snippets[0].source_diagnostics or {}
            maybe_breakdown = top_diag.get("score_breakdown") if isinstance(top_diag, Mapping) else None
            if isinstance(maybe_breakdown, Mapping):
                top_breakdown = maybe_breakdown
        _rag_log(
            "search.summary",
            {
                "stage": diagnostics.get("path") or "unknown",
                "status": result.status,
                "snippets": diagnostics.get("snippet_count") or len(result.snippets),
                "reason": diagnostics.get("reason"),
                "features": diagnostics.get("feature_flags"),
                "tokens": diagnostics.get("token_count"),
                "total_ms": diagnostics.get("total_duration_ms"),
                "alias_ms": diagnostics.get("alias_duration_ms"),
                "vector_ms": diagnostics.get("vector_duration_ms"),
                "lexical_ms": diagnostics.get("fts_duration_ms"),
                "rerank_ms": diagnostics.get("rerank_duration_ms"),
                "table_ms": diagnostics.get("table_duration_ms"),
                "table_context_ms": diagnostics.get("table_context_ms"),
                "table_presence_ms": diagnostics.get("table_presence_ms"),
                "identifier": diagnostics.get("identifier_like"),
                "alias_stage": diagnostics.get("alias_stage"),
                "chunk_candidates": diagnostics.get("chunk_candidate_count"),
                "tabular_intent": diagnostics.get("tabular_intent"),
                "tables_available": diagnostics.get("tables_available"),
                "table_reason": diagnostics.get("table_reason"),
                "vector_ceiling": diagnostics.get("vector_distance_ceiling"),
                "vector_distance_min": diagnostics.get("vector_distance_min"),
                "vector_distance_mean": diagnostics.get("vector_distance_mean"),
                "vector_distance_max": diagnostics.get("vector_distance_max"),
                "vector_similarity_min": diagnostics.get("vector_similarity_min"),
                "vector_similarity_mean": diagnostics.get("vector_similarity_mean"),
                "vector_similarity_max": diagnostics.get("vector_similarity_max"),
                "top_score": top_score,
                "top_vector": top_breakdown.get("vector"),
                "top_lexical": top_breakdown.get("lexical"),
                "top_alias": top_breakdown.get("alias"),
                "top_entity": top_breakdown.get("entity"),
                "top_recency": top_breakdown.get("recency"),
                "mmr_lambda": self.mmr_lambda,
                "w_vector": self.rerank_weights.get("vector"),
                "w_lexical": self.rerank_weights.get("lexical"),
                "w_alias": self.rerank_weights.get("alias"),
                "w_entity": self.rerank_weights.get("entity"),
                "w_recency": self.rerank_weights.get("recency"),
                "alias_threshold": diagnostics.get("alias_fts_threshold"),
                "query": query_preview,
            },
            indent=1,
            context={
                "business": business_profile.id,
                "request": request_id,
            },
        )
        feature_flags = diagnostics.get("feature_flags") if isinstance(diagnostics.get("feature_flags"), dict) else {}
        if feature_flags.get("rag_eval_logging"):
            self._log_snippet_previews(
                business_profile=business_profile,
                request_id=request_id,
                result=result,
            )
        if feature_flags.get("rag_shadow_retrieval"):
            self._log_shadow_vector_snapshot(
                business_profile=business_profile,
                request_id=request_id,
                query_text=diagnostics.get("normalized_query") or diagnostics.get("original_query") or "",
            )

    def _log_snippet_previews(
        self,
        *,
        business_profile,
        request_id: uuid.UUID,
        result: KnowledgeSearchResult,
        limit: int = 3,
    ) -> None:
        snippets = result.snippets or ()
        if not snippets:
            return
        preview_limit = max(1, min(limit, len(snippets)))
        previews: list[dict[str, object]] = []
        for snippet in snippets[:preview_limit]:
            content = (snippet.content or "").strip()
            summary = (snippet.summary or "").strip()
            preview = content or summary
            if len(preview) > 240:
                preview = f"{preview[:240]}..."
            if len(summary) > 160:
                summary = f"{summary[:160]}..."
            previews.append(
                {
                    "chunk_id": str(snippet.chunk_id) if snippet.chunk_id else None,
                    "upload_id": str(snippet.upload_id) if snippet.upload_id else None,
                    "title": snippet.title,
                    "summary": summary,
                    "preview": preview,
                    "search_stage": snippet.search_stage,
                    "is_table_chunk": bool(snippet.is_table_chunk),
                }
            )
        _rag_log(
            "search.snippets",
            {"count": preview_limit, "items": previews},
            indent=2,
            context={
                "business": business_profile.id,
                "request": request_id,
            },
        )

    def _log_shadow_vector_snapshot(
        self,
        *,
        business_profile,
        request_id: uuid.UUID,
        query_text: str,
        limit: int = 3,
    ) -> None:
        query_text = (query_text or "").strip()
        if not query_text:
            return
        query_vector, diagnostics = self._build_query_vector(
            business_profile=business_profile,
            query_text=query_text,
        )
        if not query_vector:
            return
        qs = (
            KnowledgeUploadShadowChunk.objects.filter(
                business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
            )
            .exclude(upload__visibility=KnowledgeVisibility.INTERNAL)
            .exclude(embedding__isnull=True)
            .annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance")[: max(1, limit)]
        )
        hits: list[dict[str, object]] = []
        for chunk in qs:
            preview = (chunk.content or "").strip()
            if len(preview) > 200:
                preview = f"{preview[:200]}..."
            hits.append(
                {
                    "chunk_id": str(chunk.id),
                    "upload_id": str(chunk.upload_id),
                    "distance": float(getattr(chunk, "distance", 0.0) or 0.0),
                    "preview": preview,
                }
            )
        _rag_log(
            "shadow.vector",
            {
                "count": len(hits),
                "query": query_text[:160] + "..." if len(query_text) > 160 else query_text,
                "vector_cache_hit": diagnostics.get("vector_cache_hit"),
                "hits": hits,
            },
            indent=2,
            context={
                "business": business_profile.id,
                "request": request_id,
            },
        )
