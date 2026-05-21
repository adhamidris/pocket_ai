from __future__ import annotations

import time

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector, TrigramSimilarity
from django.db import connection

from apps.rag.contracts import ChunkResult, QueryTraits
from apps.rag.query.normalizer import QueryNormalizer
from core.otel import otel_trace


TRACER = otel_trace.get_tracer(__name__)


class CandidateLexicalMixin:

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
