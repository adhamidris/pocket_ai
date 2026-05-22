from __future__ import annotations

import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import MutableMapping

from apps.rag.contracts import AliasSearchResult, ChunkResult, KnowledgeSearchResult, KnowledgeSnippet, QueryTraits


@dataclass(slots=True)
class TableSearchPhaseResult:
    result: KnowledgeSearchResult | None
    table_blocked: bool
    table_reason: str | None


def resolve_table_search_phase(
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
    table_context: dict[str, object],
    diagnostics: dict[str, object],
    strategy_result,
    chunk_hits: tuple[ChunkResult, ...],
    context_hits: tuple[ChunkResult, ...],
    table_intent: bool,
    tables_available: bool,
    allowed_upload_ids,
    allowed_explicit_upload_ids,
    log_func: Callable[..., None],
) -> TableSearchPhaseResult:
    table_snippets: tuple[KnowledgeSnippet, ...] = tuple()
    table_reason: str | None = None
    should_run_table = False
    is_parallel_table_search = False  # NEW: Track if this is parallel (not fallback) search
    matched_columns_query = table_context.get("matched_columns_query")
    matched_columns_tokens = table_context.get("matched_columns_tokens")
    has_header_match = bool(matched_columns_query or matched_columns_tokens) if isinstance(matched_columns_query, set) else False
    specific_tokens = set(table_context.get("specific_tokens") or ())
    matched_columns_specific = table_context.get("matched_columns_specific")
    matched_row_labels = set(table_context.get("matched_row_labels") or ())
    allow_generic = bool(table_context.get("allow_generic"))
    has_specific_match = bool(matched_columns_specific or matched_row_labels) if specific_tokens else True
    table_blocked = bool(table_intent and specific_tokens and not has_specific_match and not allow_generic)
    has_table_chunk = any(
        bool((hit.chunk.metadata or {}).get("is_table_chunk"))
        for hit in chunk_hits[: search_service.table_chunk_sample_limit]
    )
    has_text_chunk = any(
        not bool((hit.chunk.metadata or {}).get("is_table_chunk"))
        for hit in chunk_hits[: search_service.table_chunk_sample_limit]
    )

    # Parallel table search is a retrieval concern. Keep the decision and the
    # resulting fusion here so MCP does not grow a second retrieval planner.
    # NEW: Parallel table search - run table search alongside vector search, not as fallback
    # This fixes semantic collisions where vector search returns confident but wrong results
    # (e.g., "Withdraw Bills for Collection" matching ATM withdrawal content)
    table_upload_ratio = float(table_context.get("table_upload_ratio") or 0)
    should_run_parallel_table = (
        search_service.parallel_table_search_enabled
        and tables_available
        and table_intent
        and not table_blocked
        and chunk_hits  # We have vector results (parallel mode, not fallback)
        and table_upload_ratio >= search_service.parallel_table_min_ratio
        and (has_text_chunk or bool(context_hits))
        # Keep parallel fusion for mixed candidates; table-only flows use table_direct/blended.
    )

    if should_run_parallel_table:
        should_run_table = True
        is_parallel_table_search = True
        table_reason = "parallel_multi_strategy"
        log_func(
            "parallel_table.triggered",
            {
                "query": traits.normalized,
                "table_upload_ratio": round(table_upload_ratio, 2),
                "min_ratio": search_service.parallel_table_min_ratio,
                "chunk_hits": len(chunk_hits),
            },
            indent=2,
            context={"business": business_profile.id},
        )
    elif tables_available and not table_blocked:
        # Original fallback logic (kept for cases where parallel is disabled or ratio too low)
        if not chunk_hits:
            should_run_table = True
            table_reason = "no_chunk_candidates"
        elif table_intent and search_service._chunk_hits_are_weak(chunk_hits, traits):
            should_run_table = True
            table_reason = "weak_chunk_candidates"
        elif table_intent and allow_generic and not has_table_chunk:
            should_run_table = True
            table_reason = "schema_context"
        elif table_intent and search_service._query_has_entity_tokens(business_profile, traits):
            should_run_table = True
            table_reason = "entity_query_parallel"
        elif table_intent and has_header_match:
            should_run_table = True
            table_reason = "header_match"
    elif table_blocked:
        diagnostics["table_reason"] = "specific_tokens_missing"

    if should_run_table:
        log_func(
            "table.search_run",
            {
                "query": traits.normalized,
                "reason": table_reason,
            },
            indent=2,
            context={
                "business": business_profile.id,
                "request": diagnostics.get("request_id"),
            },
        )
        table_start = time.perf_counter()
        comprehensive_flag = bool(table_context.get("comprehensive_intent"))
        log_func(
            "table.search_call",
            {
                "query": traits.normalized or traits.original,
                "comprehensive_intent_passed": comprehensive_flag,
                "table_context_comprehensive": table_context.get("comprehensive_intent"),
                "table_reason": table_reason,
                "limit": limit,
            },
            indent=2,
            context={"business": business_profile.id},
        )
        table_snippets = search_service._table_search_snippets(
            business_profile=business_profile,
            query_text=traits.normalized or traits.original,
            limit=limit,
            matched_columns=table_context["matched_columns"],
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            comprehensive_intent=comprehensive_flag,
        )
        table_duration_ms = int((time.perf_counter() - table_start) * 1000)
        diagnostics["table_duration_ms"] = table_duration_ms

    if table_snippets:
        # Fusion ownership stays in RAG. MCP should not try to reconcile
        # table/vector candidates again after this point.
        # NEW: For parallel table search, use RRF fusion to merge vector + table results
        if is_parallel_table_search and chunk_hits:
            diagnostics["path"] = "parallel_rrf"
            diagnostics["reason"] = "parallel_multi_strategy"
            diagnostics["table_reason"] = table_reason

            # Convert chunk hits to snippets for RRF fusion.
            vector_snippets = list(
                search_service._search_chunks(
                    chunk_hits,
                    limit=limit * 2,  # Get more candidates for fusion
                    business_profile=business_profile,
                    pathway="hybrid",
                    query=traits.normalized or traits.original or query,
                )
            )

            # RRF fusion of table + vector results
            rrf_merged = search_service._rrf_fusion_snippets(
                vector_snippets=vector_snippets,
                table_snippets=list(table_snippets),
                k=search_service.parallel_table_rrf_k,
            )

            if strategy_result and strategy_result.hints.diversify_tables and len(rrf_merged) > 1:
                blended, coverage_diag = search_service._diversify_table_snippets_with_diagnostics(
                    rrf_merged,
                    limit=limit,
                )
                diagnostics.update(coverage_diag)
                diagnostics["strategy_diversified"] = bool(
                    coverage_diag.get("coverage_diversification_applied")
                )
            else:
                blended = list(rrf_merged[:limit])

            blended, collapse_diag = search_service._collapse_snippets_by_evidence_group(
                blended,
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
                limit=limit,
            )
            diagnostics.update(collapse_diag)

            diagnostics["total_duration_ms"] = search_service._duration_ms(overall_start)
            diagnostics["snippet_count"] = len(blended)
            diagnostics["rrf_vector_count"] = len(vector_snippets)
            diagnostics["rrf_table_count"] = len(table_snippets)
            diagnostics["rrf_merged_count"] = len(rrf_merged)

            status = "ok" if blended else "not_found"
            status, blended_snippets, diagnostics = search_service._apply_phase6_semantics(
                status=status,
                snippets=tuple(blended),
                diagnostics=diagnostics,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
                table_blocked=table_blocked,
            )
            result_obj = KnowledgeSearchResult(
                snippets=blended_snippets,
                status=status,
                diagnostics=diagnostics,
            )
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
            return TableSearchPhaseResult(
                result=result_obj,
                table_blocked=table_blocked,
                table_reason=table_reason,
            )

        # Original blending logic (for fallback table search, not parallel)
        diagnostics["path"] = "table_direct" if not chunk_hits else "table_blended"
        diagnostics["reason"] = table_reason or diagnostics.get("reason") or "table_search"
        if table_reason:
            diagnostics["table_reason"] = table_reason
        diagnostics["total_duration_ms"] = search_service._duration_ms(overall_start)
        diagnostics["snippet_count"] = len(table_snippets)
        diversify_requested = bool(strategy_result and strategy_result.hints.diversify_tables)
        candidate_limit = limit
        if diversify_requested:
            candidate_limit = max(
                limit,
                int(math.ceil(limit * search_service.coverage_diversification_candidate_multiplier)),
            )
        blended_candidates: list[KnowledgeSnippet] = list(table_snippets)
        remaining = max(0, candidate_limit - len(blended_candidates))
        if chunk_hits and remaining:
            blended_candidates.extend(
                search_service._search_chunks(
                    chunk_hits,
                    limit=remaining,
                    business_profile=business_profile,
                    pathway="hybrid",
                    query=traits.normalized or traits.original or query,  # NEW: For query-aware row sampling
                )
            )
        if diversify_requested and len(blended_candidates) > 1:
            blended, coverage_diag = search_service._diversify_table_snippets_with_diagnostics(
                blended_candidates,
                limit=limit,
            )
            diagnostics.update(coverage_diag)
            diagnostics["strategy_diversified"] = bool(
                coverage_diag.get("coverage_diversification_applied")
            )
        else:
            blended = list(blended_candidates[:limit])

        blended, collapse_diag = search_service._collapse_snippets_by_evidence_group(
            blended,
            query_text=traits.normalized or traits.original or query,
            tokens=traits.tokens,
            limit=limit,
        )
        diagnostics.update(collapse_diag)

        status = "ok" if blended else "not_found"
        status, blended_snippets, diagnostics = search_service._apply_phase6_semantics(
            status=status,
            snippets=tuple(blended),
            diagnostics=diagnostics,
            business_profile=business_profile,
            traits=traits,
            table_context=table_context,
            table_blocked=table_blocked,
        )
        diagnostics["snippet_count"] = len(blended_snippets)
        result_obj = KnowledgeSearchResult(snippets=blended_snippets, status=status, diagnostics=diagnostics)
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
        return TableSearchPhaseResult(
            result=result_obj,
            table_blocked=table_blocked,
            table_reason=table_reason,
        )

    return TableSearchPhaseResult(
        result=None,
        table_blocked=table_blocked,
        table_reason=table_reason,
    )
