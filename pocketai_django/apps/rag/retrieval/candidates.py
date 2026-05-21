from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from apps.accounts.feature_flags import FeatureFlagService, FeatureState
from apps.rag.contracts import AliasSearchResult, ChunkResult, QueryTraits
from apps.rag.retrieval.candidate_lexical import CandidateLexicalMixin
from apps.rag.retrieval.candidate_merge import CandidateMergeMixin
from apps.rag.retrieval.candidate_scope import CandidateScopeMixin
from apps.rag.retrieval.candidate_tokens import CandidateTokenMixin
from apps.rag.retrieval.candidate_vectors import CandidateVectorMixin
from apps.rag.retrieval.hybrid_search import HybridSearchMixin


class CandidateRetrievalMixin(
    CandidateScopeMixin,
    CandidateTokenMixin,
    CandidateVectorMixin,
    CandidateLexicalMixin,
    CandidateMergeMixin,
    HybridSearchMixin,
):
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
