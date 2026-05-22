from __future__ import annotations

import uuid
from typing import MutableMapping

from apps.rag.contracts import AliasSearchResult, KnowledgeSearchResult, QueryTraits


def resolve_cached_search_result(
    *,
    search_service,
    business_profile,
    traits: QueryTraits,
    query: str,
    limit: int,
    alias_result: AliasSearchResult,
    feature_state,
    session_cache: MutableMapping[str, object] | None,
    cache_key: str,
    request_id: uuid.UUID,
    overall_start: float,
) -> KnowledgeSearchResult | None:
    cached_result = None
    if session_cache is not None:
        cached_result = search_service._session_cache_get(session_cache, cache_key)
        if cached_result:
            cached_status = str(cached_result.status or "").strip().lower() or "not_found"
            if cached_status == "needs_clarification":
                # Ignore stale clarification cache entries; agentic mode should proceed best-effort.
                cached_result = None
            else:
                cached_diag = dict(cached_result.diagnostics or {})
                cached_diag["cache_hit"] = True
                cached_diag["cache_scope"] = "session"
                cached_diag["request_id"] = str(request_id)
                cached_diag["total_duration_ms"] = search_service._duration_ms(overall_start)
                snippets = cached_result.snippets[:limit]
                snippets, collapse_diag = search_service._collapse_snippets_by_evidence_group(
                    snippets,
                    query_text=traits.normalized or traits.original or query,
                    tokens=traits.tokens,
                    limit=limit,
                )
                cached_diag.update(collapse_diag)
                cached_diag["snippet_count"] = len(snippets)
                result_obj = KnowledgeSearchResult(
                    snippets=snippets,
                    status=cached_status,
                    diagnostics=cached_diag,
                )
                search_service._record_retrieval_event(
                    business_profile=business_profile,
                    traits=traits,
                    alias_result=alias_result,
                    result=result_obj,
                    feature_state=feature_state,
                )
                search_service._log_search_summary(
                    business_profile=business_profile,
                    request_id=request_id,
                    result=result_obj,
                )
                return result_obj

    cached_result = search_service._result_cache_get(cache_key)
    if cached_result:
        cached_status = str(cached_result.status or "").strip().lower() or "not_found"
        if cached_status != "needs_clarification":
            cached_diag = dict(cached_result.diagnostics or {})
            cached_diag["cache_hit"] = True
            cached_diag["cache_scope"] = "business"
            cached_diag["request_id"] = str(request_id)
            cached_diag["total_duration_ms"] = search_service._duration_ms(overall_start)
            snippets = cached_result.snippets[:limit]
            snippets, collapse_diag = search_service._collapse_snippets_by_evidence_group(
                snippets,
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
                limit=limit,
            )
            cached_diag.update(collapse_diag)
            cached_diag["snippet_count"] = len(snippets)
            result_obj = KnowledgeSearchResult(
                snippets=snippets,
                status=cached_status,
                diagnostics=cached_diag,
            )
            search_service._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
            search_service._record_retrieval_event(
                business_profile=business_profile,
                traits=traits,
                alias_result=alias_result,
                result=result_obj,
                feature_state=feature_state,
            )
            search_service._log_search_summary(
                business_profile=business_profile,
                request_id=request_id,
                result=result_obj,
            )
            return result_obj

    return None
