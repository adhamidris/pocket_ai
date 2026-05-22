from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from apps.rag.contracts import ChunkResult, QueryTraits


@dataclass(slots=True)
class CandidatePreparationResult:
    chunk_hits: tuple[ChunkResult, ...]
    context_hits: tuple[ChunkResult, ...]
    table_intent: bool
    scope_summary: dict[str, object]


def prepare_candidate_hits(
    *,
    search_service,
    business_profile,
    traits: QueryTraits,
    chunk_hits: tuple[ChunkResult, ...],
    table_context: dict[str, object],
    diagnostics: dict[str, object],
    log_func: Callable[..., None],
) -> CandidatePreparationResult:
    table_intent = bool(table_context.get("has_intent"))

    # Hierarchical table retrieval: expand parent/preview chunks to row chunks for table-intent queries.
    # Row chunks contain actual answer data (e.g., "EGP 500") while parent chunks often have OCR noise.
    # This enables: table discovery -> row expansion -> answer from rows (parents for context only).
    # EXCEPTION: For comprehensive queries ("list all cards", "every product"), keep preview chunks
    # as they contain the full table structure needed for enumeration/comparison answers.
    comprehensive_intent = bool(table_context.get("comprehensive_intent"))
    table_signal_header = bool(
        table_context.get("matched_columns_query")
        or table_context.get("matched_columns_tokens")
    )
    table_signal_specific = bool(
        table_context.get("matched_columns_specific")
        or table_context.get("matched_row_labels")
    )
    table_signal_from_hits = any(
        bool((hit.diagnostics or {}).get("specific_match_strong"))
        or bool((hit.diagnostics or {}).get("specific_match"))
        or bool((hit.diagnostics or {}).get("header_match"))
        for hit in chunk_hits[: search_service.table_chunk_sample_limit]
    )
    table_signal_direct_stage = any(
        str(getattr(hit, "source_stage", "") or "").strip().lower() in {"table_direct", "table_blended"}
        for hit in chunk_hits[: search_service.table_chunk_sample_limit]
    )
    table_row_expansion_relevant = bool(
        table_signal_specific
        or table_signal_header
        or table_signal_from_hits
        or table_signal_direct_stage
    )
    if table_intent and not comprehensive_intent and chunk_hits and table_row_expansion_relevant:
        expanded_rows = search_service._expand_table_rows(
            business_profile,
            chunk_hits,
            max_rows_per_table=search_service.table_row_expansion_limit,
            query_tokens=traits.tokens,
        )
        if expanded_rows:
            chunk_hits, merge_diagnostics = search_service._merge_expanded_table_hits(
                chunk_hits,
                expanded_rows,
                query_tokens=traits.tokens,
            )
            diagnostics["table_row_expansion"] = merge_diagnostics.get("expanded_rows", 0)
            diagnostics["table_row_expansion_relevant"] = merge_diagnostics.get("relevant_rows", 0)
            diagnostics["table_row_expansion_supplemental"] = merge_diagnostics.get("supplemental_rows", 0)
            diagnostics["table_parent_suppressed"] = merge_diagnostics.get("parent_chunks_suppressed", 0)
            diagnostics["table_parent_limited"] = merge_diagnostics.get("parent_chunks_limited", 0)
            log_func(
                "table.row_expansion",
                {
                    "expanded_rows": merge_diagnostics.get("expanded_rows", 0),
                    "relevant_rows": merge_diagnostics.get("relevant_rows", 0),
                    "supplemental_rows": merge_diagnostics.get("supplemental_rows", 0),
                    "parent_chunks_suppressed": merge_diagnostics.get("parent_chunks_suppressed", 0),
                    "parent_chunks_limited": merge_diagnostics.get("parent_chunks_limited", 0),
                    "total_after": len(chunk_hits),
                },
                indent=1,
                context={
                    "business": business_profile.id,
                    "request": diagnostics.get("request_id"),
                },
            )
    elif table_intent and not comprehensive_intent and chunk_hits:
        diagnostics["table_row_expansion_skipped"] = "query_signal_not_specific"

    # Always suppress legacy PDF "json_entity" chunks (historically created from per-row table entities).
    # These chunks tend to be low-context ("Table_1: ...") and can dominate retrieval even for normal Q&A.
    if chunk_hits:
        before = len(chunk_hits)
        chunk_hits = tuple(hit for hit in chunk_hits if not search_service._is_legacy_pdf_table_entity_chunk(hit.chunk))
        removed = before - len(chunk_hits)
        if removed:
            diagnostics["pdf_table_entity_chunks_filtered"] = removed
            log_func(
                "pdf.table_entities.filtered",
                {"removed": removed, "remaining": len(chunk_hits)},
                indent=1,
                context={
                    "business": business_profile.id,
                    "request": diagnostics.get("request_id"),
                },
            )

    # For non-table queries, prefer page/text chunks and suppress extracted doc-table preview chunks.
    # This prevents PDFs-with-tables from dominating retrieval unless the query is actually table-critical.
    if chunk_hits and not table_intent:
        before = len(chunk_hits)
        chunk_hits = tuple(hit for hit in chunk_hits if not search_service._is_doc_table_preview_chunk(hit.chunk))
        removed = before - len(chunk_hits)
        if removed:
            diagnostics["doc_table_chunks_filtered"] = removed
            log_func(
                "table.preview.filtered",
                {"removed": removed, "remaining": len(chunk_hits)},
                indent=1,
                context={
                    "business": business_profile.id,
                    "request": diagnostics.get("request_id"),
                },
            )

    filler_tokens = search_service._filler_tokens_for_business(business_profile)
    postclip_scope_summary = search_service._build_scope_summary_from_candidates(
        chunk_hits,
        business_profile=business_profile,
        query_tokens=traits.tokens,
        filler_tokens=filler_tokens,
    )
    preclip_scope_summary = search_service._normalize_scope_summary(
        diagnostics.get("scope_summary_preclip"),
    )
    scope_summary = preclip_scope_summary or postclip_scope_summary
    diagnostics["scope_summary"] = scope_summary
    diagnostics["scope_summary_source"] = "preclip_fused" if preclip_scope_summary else "postclip_hits"
    if preclip_scope_summary:
        diagnostics["scope_summary_postclip"] = postclip_scope_summary
    log_func(
        "scope.summary",
        {
            "total_matches": scope_summary.get("total_matches"),
            "distinct_docs": scope_summary.get("distinct_docs"),
            "categories": len(scope_summary.get("category_counts") or {}),
            "is_broad_scope": scope_summary.get("is_broad_scope"),
            "source": diagnostics.get("scope_summary_source"),
        },
        indent=1,
        context={
            "business": business_profile.id,
            "request": diagnostics.get("request_id"),
        },
    )
    auto_score_diag = search_service._score_auto_mode_candidates(
        chunk_hits,
        query_tokens=traits.tokens,
        specific_tokens=tuple(table_context.get("specific_tokens") or ()),
    )
    diagnostics.update(auto_score_diag)
    auto_arbitration_diag = search_service._arbitrate_auto_mode(
        scoring_diagnostics=diagnostics,
        table_intent_hint=table_intent,
    )
    diagnostics.update(auto_arbitration_diag)
    table_intent = bool(auto_arbitration_diag.get("auto_arbitration_table_intent", table_intent))

    # NOTE: `auto_arbitration_needs_clarification` is intentionally ignored in agentic mode.
    # We proceed with the best-effort route and allow later fusion (e.g. parallel table search)
    # to reconcile table/text evidence without pausing the conversation.

    chunk_hits, context_hits, route_diag = search_service._route_chunk_hits(
        chunk_hits,
        table_intent=table_intent,
        table_context=table_context,
    )
    diagnostics.update(route_diag)
    diagnostics["auto_decision_contract"] = search_service._derive_auto_decision_contract(
        route_diagnostics=route_diag,
        scoring_diagnostics=diagnostics,
        requires_clarification=False,
        scope_summary=scope_summary,
    )
    diagnostics["chunk_candidate_count"] = len(chunk_hits)

    return CandidatePreparationResult(
        chunk_hits=chunk_hits,
        context_hits=context_hits,
        table_intent=table_intent,
        scope_summary=scope_summary,
    )
