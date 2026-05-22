from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Mapping, MutableMapping

from apps.rag.contracts import AliasSearchResult, KnowledgeSearchResult, QueryTraits


def resolve_alias_short_circuit(
    *,
    search_service,
    business_profile,
    traits: QueryTraits,
    query: str,
    limit: int,
    alias_result: AliasSearchResult,
    table_context: Mapping[str, object],
    diagnostics: dict[str, object],
    feature_state,
    session_cache: MutableMapping[str, object] | None,
    cache_key: str,
    request_id: uuid.UUID,
    overall_start: float,
    log_func: Callable[..., None],
) -> KnowledgeSearchResult | None:
    if not (alias_result.short_circuit and alias_result.hits):
        return None

    chunk_ids = [hit.chunk_id for hit in alias_result.hits[: max(limit, search_service.alias_result_cap)]]
    neighbor = max(1, search_service.alias_neighbor_window)
    snippets = tuple(
        search_service.load_chunk_contents(
            business_profile=business_profile,
            chunk_ids=chunk_ids,
            neighbor=neighbor,
        )
    )[:limit]
    snippets, collapse_diag = search_service._collapse_snippets_by_evidence_group(
        snippets,
        query_text=traits.normalized or traits.original or query,
        tokens=traits.tokens,
        limit=limit,
    )
    diagnostics["path"] = "alias_exact"
    diagnostics["alias_stage"] = diagnostics.get("alias_stage") or alias_result.diagnostics.get("stage")
    diagnostics.update(collapse_diag)
    log_func(
        "alias.short_circuit",
        {
            "query": traits.normalized,
            "hits": len(snippets),
            "neighbor": neighbor,
        },
        indent=1,
        context={
            "business": business_profile.id,
            "request": diagnostics.get("request_id"),
        },
    )
    status = "ok" if snippets else "not_found"
    status, snippets, diagnostics = search_service._apply_phase6_semantics(
        status=status,
        snippets=snippets,
        diagnostics=diagnostics,
        business_profile=business_profile,
        traits=traits,
        table_context=table_context,
        table_blocked=False,
    )
    diagnostics["total_duration_ms"] = search_service._duration_ms(overall_start)
    diagnostics["snippet_count"] = len(snippets)
    result_obj = KnowledgeSearchResult(snippets=snippets, status=status, diagnostics=diagnostics)
    search_service._result_cache_set(cache_key, result_obj, limit=limit)
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
