from __future__ import annotations

import time
import uuid
from typing import Sequence

from django.contrib.postgres.search import TrigramSimilarity
from django.core.cache import cache
from django.db.models import Q

from apps.accounts.feature_flags import FeatureFlagService, FeatureState
from apps.accounts.models import KnowledgeVisibility
from apps.knowledge.models import KnowledgeAlias
from apps.rag.contracts import AliasSearchResult, ChunkResult, QueryTraits
from apps.rag.query.normalizer import QueryNormalizer
from apps.rag.observability.logging import rag_log
from core.metrics import latency_monitor
from core.tenancy import tenant_context


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class AliasRetrievalMixin:
    def search_by_alias(
        self,
        *,
        business_profile,
        aliases: Sequence[str] | None = None,
        traits: QueryTraits | None = None,
        limit: int | None = None,
        feature_state: FeatureState | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> AliasSearchResult:
        traits = traits or self.analyze_query(" ".join(aliases or ()), business_profile=business_profile)
        alias_values = tuple(
            value
            for value in (aliases or traits.alias_candidates)
            if value
        )
        alias_threshold = self._alias_threshold_for_business(business_profile)
        normalized_aliases = tuple(
            self._normalize_alias_input(candidate) for candidate in alias_values if candidate
        )
        normalized_aliases = tuple(alias for alias in normalized_aliases if alias)
        if not normalized_aliases:
            return AliasSearchResult(hits=tuple(), diagnostics={"alias_candidates": 0}, short_circuit=False)
        feature_state = feature_state or FeatureFlagService.snapshot(business_profile)
        if not feature_state.alias_lookup:
            return AliasSearchResult(
                hits=tuple(),
                diagnostics={"alias_candidates": len(normalized_aliases), "stage": "alias_disabled"},
                short_circuit=False,
            )

        start = time.perf_counter()
        limit = limit or self.alias_result_cap
        hits, cache_diag = self._alias_exact_hits(
            business_profile=business_profile,
            aliases=normalized_aliases,
            limit=limit,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        diagnostics: dict[str, object] = {
            "alias_candidates": len(normalized_aliases),
            "alias_cache_hit": cache_diag.get("cache_hit", 0),
            "alias_cache_miss": cache_diag.get("cache_miss", 0),
            "alias_fts_threshold": alias_threshold,
        }
        total_cache_ops = diagnostics["alias_cache_hit"] + diagnostics["alias_cache_miss"]
        if total_cache_ops:
            diagnostics["alias_cache_hit_rate"] = round(diagnostics["alias_cache_hit"] / max(1, total_cache_ops), 3)

        if hits:
            diagnostics["stage"] = "alias_exact"
            diagnostics["duration_ms"] = int((time.perf_counter() - start) * 1000)
            latency_monitor.observe("rag.alias", diagnostics["duration_ms"], tags={"stage": diagnostics["stage"]})
            _rag_log(
                "alias.exact",
                {
                    "aliases": normalized_aliases,
                    "hits": len(hits),
                    "cache_hit": diagnostics["alias_cache_hit"],
                    "cache_miss": diagnostics["alias_cache_miss"],
                },
                indent=1,
                context={"business": business_profile.id},
            )
            return AliasSearchResult(
                hits=tuple(hits[:limit]),
                diagnostics=diagnostics,
                short_circuit=bool(traits.is_identifier_like),
            )

        fuzzy_hits = self._alias_fuzzy_hits(
            business_profile=business_profile,
            traits=traits,
            limit=self.alias_fts_limit,
            threshold=alias_threshold,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        diagnostics["stage"] = "alias_fts"
        diagnostics["identifier_tokens"] = self._identifier_like_tokens(traits)
        diagnostics["alias_fts_hits"] = len(fuzzy_hits)
        diagnostics["duration_ms"] = int((time.perf_counter() - start) * 1000)
        latency_monitor.observe("rag.alias", diagnostics["duration_ms"], tags={"stage": diagnostics["stage"]})
        _rag_log(
            "alias.fts",
            {
                "query": traits.normalized,
                "hits": len(fuzzy_hits),
                "threshold": f"{alias_threshold:.2f}",
            },
            indent=1,
            context={"business": business_profile.id},
        )
        return AliasSearchResult(
            hits=tuple(fuzzy_hits[: self.alias_fts_limit]),
            diagnostics=diagnostics,
            short_circuit=False,
        )

    @staticmethod
    def _normalize_alias_input(value: str) -> str:
        return QueryNormalizer._canonical_alias(value) if value else ""

    def _alias_exact_hits(
        self,
        *,
        business_profile,
        aliases: Sequence[str],
        limit: int,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> tuple[list[ChunkResult], dict[str, int]]:
        if not aliases:
            return [], {"cache_hit": 0, "cache_miss": 0}
        with tenant_context(business_profile.id if business_profile else None):
            version = self._get_alias_cache_version(business_profile.id)
            ordered_ids: list[uuid.UUID] = []
            cache_hit = 0
            cache_miss = 0
            missing_aliases: list[str] = []
            for alias in aliases:
                key = self._alias_cache_key(business_profile.id, alias, version)
                cached = cache.get(key)
                if cached:
                    cache_hit += 1
                    for entry in cached:
                        try:
                            ordered_ids.append(uuid.UUID(entry["chunk_id"]))
                        except (KeyError, ValueError, TypeError):
                            continue
                else:
                    cache_miss += 1
                    missing_aliases.append(alias)

            if missing_aliases:
                if allowed_upload_ids is not None and not allowed_upload_ids:
                    return [], {"cache_hit": cache_hit, "cache_miss": cache_miss}
                alias_qs = KnowledgeAlias.objects.filter(
                    business_profile=business_profile,
                    alias_normalized__in=missing_aliases,
                )
                if allowed_upload_ids is not None:
                    alias_qs = alias_qs.filter(entity__upload_id__in=allowed_upload_ids)
                else:
                    scope_clauses: list[Q] = []
                    if allowed_explicit_upload_ids:
                        scope_clauses.append(Q(entity__upload_id__in=allowed_explicit_upload_ids))
                    if scope_clauses:
                        clause = scope_clauses[0]
                        for extra in scope_clauses[1:]:
                            clause |= extra
                        alias_qs = alias_qs.filter(clause).distinct()
                alias_qs = alias_qs.select_related("entity", "entity__upload").order_by("alias_normalized")
                alias_records = list(alias_qs)
                chunk_ids = [
                    record.entity.chunk_id
                    for record in alias_records
                    if record.entity and record.entity.chunk_id
                ]
                chunk_lookup = self._fetch_chunks_by_ids(
                    business_profile=business_profile,
                    chunk_ids=chunk_ids,
                    allowed_upload_ids=allowed_upload_ids,
                    allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                )
                payloads: dict[str, list[dict[str, str]]] = {}
                for record in alias_records:
                    entity = record.entity
                    chunk = chunk_lookup.get(entity.chunk_id) if entity else None
                    if not chunk or not chunk.upload or chunk.upload.business_profile_id != business_profile.id:
                        continue
                    if chunk.upload.visibility == KnowledgeVisibility.INTERNAL:
                        continue
                    entry = {
                        "chunk_id": str(chunk.id),
                        "entity_name": entity.entity_name,
                        "entity_type": entity.entity_type,
                        "alias": record.alias_normalized,
                    }
                    payloads.setdefault(record.alias_normalized, []).append(entry)
                    ordered_ids.append(chunk.id)
                for alias_value, payload in payloads.items():
                    self._cache_alias_payload(business_profile.id, alias_value, payload)

            if not ordered_ids:
                return [], {"cache_hit": cache_hit, "cache_miss": cache_miss}

            chunk_lookup = self._fetch_chunks_by_ids(
                business_profile=business_profile,
                chunk_ids=ordered_ids[:limit],
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            hits: list[ChunkResult] = []
            for identifier in ordered_ids:
                chunk = chunk_lookup.get(identifier)
                if not chunk:
                    continue
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="alias_exact",
                        alias_confidence=1.0,
                        recency_score=self._recency_score(chunk.upload),
                        rerank_score=1.0,
                        diagnostics={"alias": str(identifier)},
                    )
                )
                if len(hits) >= limit:
                    break
            return hits, {"cache_hit": cache_hit, "cache_miss": cache_miss}

    def _alias_fuzzy_hits(
        self,
        *,
        business_profile,
        traits: QueryTraits,
        limit: int,
        threshold: float,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
    ) -> list[ChunkResult]:
        identifier_tokens = self._identifier_like_tokens(traits)
        if not identifier_tokens:
            return []
        query_text = " ".join(identifier_tokens[:4]) or (traits.normalized or traits.original or "")
        with tenant_context(business_profile.id if business_profile else None):
            alias_qs = KnowledgeAlias.objects.filter(business_profile=business_profile)
            if allowed_upload_ids is not None:
                if not allowed_upload_ids:
                    return []
                alias_qs = alias_qs.filter(entity__upload_id__in=allowed_upload_ids)
            else:
                scope_clauses: list[Q] = []
                if allowed_explicit_upload_ids:
                    scope_clauses.append(Q(entity__upload_id__in=allowed_explicit_upload_ids))
                if scope_clauses:
                    clause = scope_clauses[0]
                    for extra in scope_clauses[1:]:
                        clause |= extra
                    alias_qs = alias_qs.filter(clause).distinct()
            alias_qs = (
                alias_qs.annotate(sim=TrigramSimilarity("alias_search_vector", query_text))
                .filter(sim__gte=threshold)
                .order_by("-sim")[: max(limit, 10)]
                .select_related("entity", "entity__upload")
            )
            alias_records = list(alias_qs)
            chunk_ids = [
                record.entity.chunk_id
                for record in alias_records
                if record.entity and record.entity.chunk_id
            ]
            chunk_lookup = self._fetch_chunks_by_ids(
                business_profile=business_profile,
                chunk_ids=chunk_ids,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
            seen: set[uuid.UUID] = set()
            hits: list[ChunkResult] = []
            for record in alias_records:
                entity = record.entity
                chunk = chunk_lookup.get(entity.chunk_id) if entity else None
                if not chunk or not chunk.upload or chunk.upload.business_profile_id != business_profile.id:
                    continue
                if chunk.upload.visibility == KnowledgeVisibility.INTERNAL:
                    continue
                if chunk.id in seen:
                    continue
                seen.add(chunk.id)
                confidence = float(getattr(record, "sim", 0.0) or 0.0)
                hits.append(
                    ChunkResult(
                        chunk=chunk,
                        source_stage="alias_fts",
                        alias_confidence=min(1.0, confidence),
                        recency_score=self._recency_score(chunk.upload),
                        rerank_score=min(1.0, confidence),
                        diagnostics={"alias": record.alias_normalized},
                    )
                )
                if len(hits) >= limit:
                    break
            return hits
