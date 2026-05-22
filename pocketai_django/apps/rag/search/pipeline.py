from __future__ import annotations

import math
import time
import uuid
from typing import Any, Mapping, MutableMapping, Sequence

from apps.accounts.feature_flags import FeatureFlagService
from apps.rag.contracts import (
    AliasSearchResult,
    KnowledgeSearchResult,
    QueryTraits,
)
from apps.rag.observability.logging import rag_log
from apps.rag.search.entrypoint import SearchEntrypointMixin
from apps.rag.search.pipeline_alias import resolve_alias_short_circuit
from apps.rag.search.pipeline_cache import resolve_cached_search_result
from apps.rag.search.pipeline_candidates import prepare_candidate_hits
from apps.rag.search.pipeline_tables import resolve_table_search_phase
from apps.rag.retrieval.strategies import RetrievalContext
from core.otel import otel_trace


TRACER = otel_trace.get_tracer(__name__)


def _rag_log(
    stage: str,
    detail: Any | None = None,
    *,
    indent: int = 0,
    context: Mapping[str, Any] | None = None,
) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class SearchPipelineMixin(SearchEntrypointMixin):

    def _search_inner(
        self,
        *,
        business_profile,
        query: str,
        limit: int | None = None,
        traits: QueryTraits | None = None,
        alias_result: AliasSearchResult | None = None,
        session_cache: MutableMapping[str, object] | None = None,
        identifier_filter: Mapping[str, str] | None = None,
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        session_context: Mapping[str, object] | None = None,
    ) -> KnowledgeSearchResult:
        """
        Core retrieval pipeline.

        This method intentionally owns the "search brain" for knowledge lookup:
        classification, strategy routing, candidate generation, table-aware
        branching, fusion, reranking, and fallback arbitration all happen here.

        Keep MCP out of these decisions. MCP should pass queries in, then shape
        the returned evidence into refs and read contracts without re-ranking or
        re-planning the retrieval result set.
        """
        traits = traits or self.analyze_query(query, business_profile=business_profile)
        overall_start = time.perf_counter()
        feature_state = FeatureFlagService.snapshot(business_profile)
        request_id = uuid.uuid4()
        limit = self._snippet_limit_for_business(business_profile, limit)
        alias_chunk_cap = self._effective_chunk_cap(business_profile, "alias")
        ann_chunk_cap = self._effective_chunk_cap(business_profile, "ann")
        vector_ceiling = self._vector_ceiling_for_business(business_profile)
        table_context_start = time.perf_counter()
        table_context = self._table_query_context(
            business_profile,
            traits,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        table_context_ms = int((time.perf_counter() - table_context_start) * 1000.0)
        
        # Retrieval strategy selection belongs in RAG, not in MCP. MCP may pass
        # one or more query strings, but the retrieval policy for a given query
        # must stay centralized in this layer.
        # Execute retrieval strategy based on classified intent (Phase 3)
        classification = table_context.get("query_classification")
        strategy_result = None
        if classification and not classification.requires_clarification:
            retrieval_context = RetrievalContext(
                business_profile=business_profile,
                query=query,
                traits=traits,
                classification=classification,
                table_context=table_context,
                limit=limit,
                alias_result=alias_result,
                session_cache=session_cache,
                identifier_filter=identifier_filter,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                feature_state=feature_state,
                request_id=uuid.uuid4(),
            )
            strategy_result = self.strategy_router.execute(retrieval_context)
            _rag_log(
                "strategy.executed",
                {
                    "strategy": strategy_result.diagnostics.get("strategy"),
                    "intent": classification.intent.value,
                    "confidence": round(classification.confidence, 2),
                    "effective_limit": strategy_result.effective_limit,
                    "hints": strategy_result.hints.to_dict(),
                },
                indent=1,
                context={"business": business_profile.id if business_profile else None},
            )
        elif classification and classification.requires_clarification:
            _rag_log(
                "strategy.skipped_clarification",
                {
                    "intent": classification.intent.value,
                    "confidence": round(classification.confidence, 2),
                    "question": classification.clarification_question,
                },
                indent=1,
                context={"business": business_profile.id if business_profile else None},
            )

        # Apply strategy effective_limit when available (safety cap: never exceed 4x base)
        if strategy_result and strategy_result.effective_limit:
            limit = min(strategy_result.effective_limit, limit * 4)
        section_focus_terms: tuple[str, ...] = tuple()
        if classification and isinstance(classification.retrieval_hints, Mapping):
            raw_terms = classification.retrieval_hints.get("section_focus_terms") or ()
            if isinstance(raw_terms, (list, tuple, set)):
                section_focus_terms = tuple(
                    str(item).strip().lower()
                    for item in raw_terms
                    if str(item).strip()
                )[:8]
        prefer_section_context = bool(
            classification.prefers_section_context() if classification else False
        )
        modality_bias = str(
            classification.modality_bias() if classification else "mixed"
        ).strip().lower()
        if strategy_result and strategy_result.hints.prefer_section_context:
            prefer_section_context = True
        if strategy_result:
            strategy_modality_bias = str(strategy_result.hints.modality_bias or "").strip().lower()
            if strategy_modality_bias in {"table", "text", "mixed"}:
                modality_bias = strategy_modality_bias
        if prefer_section_context or section_focus_terms:
            table_context = dict(table_context)
            table_context["prefer_section_context"] = prefer_section_context
            table_context["section_focus_terms"] = list(section_focus_terms)
        if modality_bias in {"table", "text", "mixed"}:
            table_context = dict(table_context)
            table_context["modality_bias"] = modality_bias

        table_presence_start = time.perf_counter()
        tables_available = self._business_has_tables(
            business_profile,
            cached_columns=table_context.get("available_columns"),
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        table_presence_ms = int((time.perf_counter() - table_presence_start) * 1000.0)
        alias_blocked = False
        if alias_result is None:
            with TRACER.start_as_current_span("knowledge.alias_lookup") as alias_span:
                alias_result = self.search_by_alias(
                    business_profile=business_profile,
                    traits=traits,
                    limit=self.alias_result_cap,
                    feature_state=feature_state,
                    allowed_upload_ids=allowed_upload_ids,
                    allowed_explicit_upload_ids=allowed_explicit_upload_ids,
                )
                if alias_span.is_recording():
                    alias_span.set_attribute("knowledge.alias_candidates", len(traits.alias_candidates))
                    alias_span.set_attribute("knowledge.alias_short_circuit", bool(alias_result.short_circuit))
        elif alias_result.short_circuit and not traits.is_identifier_like:
            alias_blocked = True
            alias_result = AliasSearchResult(
                hits=alias_result.hits,
                diagnostics=dict(alias_result.diagnostics or {}),
                short_circuit=False,
            )
        # Agentic RAG: do not surface "needs clarification" contracts. Always proceed best-effort.
        auto_decision_contract = self._derive_auto_decision_contract(requires_clarification=False)
        diagnostics: dict[str, object] = {
            "original_query": traits.original,
            "normalized_query": traits.normalized,
            "token_count": traits.token_count,
            "identifier_like": traits.is_identifier_like,
            "has_digits": traits.has_digits,
            "has_dashes": traits.has_dashes,
            "has_underscores": traits.has_underscores,
            "alias_candidate_count": len(traits.alias_candidates),
            "feature_flags": feature_state.as_dict(),
            "request_id": str(request_id),
            "tabular_intent": table_context["has_intent"],
            "tabular_columns_matched": sorted(table_context["matched_columns"])[:5],
            "tabular_columns_token_match": sorted(table_context.get("matched_columns_tokens") or ())[:5],
            "tabular_columns_specific": sorted(table_context.get("matched_columns_specific") or ())[:5],
            "tabular_row_label_matches": sorted(table_context.get("matched_row_labels") or ())[:5],
            "tabular_specific_tokens": sorted(table_context.get("specific_tokens") or ())[:5],
            "snippet_limit": limit,
            "alias_chunks_per_upload": alias_chunk_cap,
            "ann_chunks_per_upload": ann_chunk_cap,
            "vector_distance_ceiling": vector_ceiling,
            "tables_available": tables_available,
            "table_context_ms": table_context_ms,
            "table_presence_ms": table_presence_ms,
            "tabular_columns_hint": sorted(table_context.get("semantic_columns") or ())[:5],
            "tabular_table_dominant": bool(table_context.get("table_dominant")),
            "tabular_table_upload_ratio": table_context.get("table_upload_ratio"),
            "tabular_table_count": table_context.get("table_count"),
            "tabular_table_uploads": table_context.get("table_uploads"),
            "tabular_allow_generic": bool(table_context.get("allow_generic")),
            "tabular_comprehensive_intent": bool(table_context.get("comprehensive_intent")),
            "tabular_prefer_section_context": bool(table_context.get("prefer_section_context")),
            "tabular_section_focus_terms": list(table_context.get("section_focus_terms") or ())[:6],
            "document_continuity_allowed": bool(
                session_context.get("document_continuity_allowed") if session_context else False
            ),
            "document_continuity_reason": (
                session_context.get("document_continuity_reason") if session_context else None
            ),
            "query_rewrite_strategy": (
                session_context.get("query_rewrite_strategy") if session_context else None
            ),
            "query_rewrite_confidence": (
                session_context.get("query_rewrite_confidence") if session_context else None
            ),
            "intent_name": classification.intent.value if classification else None,
            "intent_confidence": round(classification.confidence, 3) if classification else None,
            "intent_source": classification.source if classification else None,
            "intent_fallback_used": bool(classification.fallback_used) if classification else False,
            "intent_fallback_attempted": bool(table_context.get("intent_fallback_attempted")),
            "intent_fallback_applied": bool(table_context.get("intent_fallback_applied")),
            # Keep the classifier signal for debugging, but do not turn it into a blocking clarification.
            "intent_requires_clarification": False,
            "intent_clarification_question": "",
            "intent_classifier_requires_clarification": bool(classification.requires_clarification) if classification else False,
            "intent_classifier_clarification_question": (
                classification.clarification_question if classification else ""
            ),
            "tenant_lexicon_entity_terms_count": int(table_context.get("tenant_lexicon_entity_terms_count") or 0),
            "tenant_lexicon_attribute_terms_count": int(table_context.get("tenant_lexicon_attribute_terms_count") or 0),
            "alias_short_circuit_blocked": alias_blocked,
            "table_reason": None,
            "chunk_candidate_count": 0,
            "auto_decision_contract": auto_decision_contract,
        }
        if alias_result.diagnostics:
            diagnostics.update(dict(alias_result.diagnostics))
        diagnostics["alias_stage"] = diagnostics.get("stage")
        diagnostics["alias_hits"] = len(alias_result.hits)
        alias_duration_ms = None
        if alias_result and alias_result.diagnostics:
            try:
                alias_duration_ms = int(alias_result.diagnostics.get("duration_ms"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                alias_duration_ms = None
        if alias_duration_ms is not None:
            diagnostics["alias_duration_ms"] = alias_duration_ms
        
        # Add strategy pattern diagnostics (Phase 3)
        if strategy_result:
            diagnostics["strategy_name"] = strategy_result.diagnostics.get("strategy")
            diagnostics["strategy_intent"] = strategy_result.diagnostics.get("intent")
            diagnostics["strategy_confidence"] = strategy_result.diagnostics.get("confidence")
            diagnostics["strategy_effective_limit"] = strategy_result.effective_limit
            diagnostics["strategy_multiplier"] = strategy_result.hints.snippet_limit_multiplier
            diagnostics["strategy_diversify_tables"] = strategy_result.hints.diversify_tables
            diagnostics["strategy_comprehensive"] = strategy_result.hints.comprehensive_intent
            diagnostics["strategy_prefer_section_context"] = strategy_result.hints.prefer_section_context
            diagnostics["strategy_modality_bias"] = strategy_result.hints.modality_bias
            diagnostics["strategy_applied_limit"] = limit

        # Agentic RAG should not block on "clarification". Keep the signal for diagnostics,
        # but continue retrieval and answer best-effort with available evidence.
        if classification and classification.requires_clarification and not traits.is_identifier_like:
            diagnostics.setdefault("clarification_suggested", True)
            diagnostics.setdefault("clarification_reason", "low_intent_confidence")
            if classification.clarification_question:
                diagnostics.setdefault(
                    "clarification_question",
                    classification.clarification_question,
                )
        
        cache_key = self._result_cache_key(
            business_profile=business_profile,
            traits=traits,
            limit=limit,
            alias_result=alias_result,
            table_context=table_context,
            feature_state=feature_state,
            identifier_filter=identifier_filter,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
        )
        cached_result = resolve_cached_search_result(
            search_service=self,
            business_profile=business_profile,
            traits=traits,
            query=query,
            limit=limit,
            alias_result=alias_result,
            feature_state=feature_state,
            session_cache=session_cache,
            cache_key=cache_key,
            request_id=request_id,
            overall_start=overall_start,
        )
        if cached_result:
            return cached_result
        alias_short_circuit_result = resolve_alias_short_circuit(
            search_service=self,
            business_profile=business_profile,
            traits=traits,
            query=query,
            limit=limit,
            alias_result=alias_result,
            table_context=table_context,
            diagnostics=diagnostics,
            feature_state=feature_state,
            session_cache=session_cache,
            cache_key=cache_key,
            request_id=request_id,
            overall_start=overall_start,
            log_func=_rag_log,
        )
        if alias_short_circuit_result:
            return alias_short_circuit_result

        chunk_hits = self._chunk_hits(
            business_profile,
            traits=traits,
            limit=max(limit * 3, self.max_chunks_per_upload * limit),
            alias_result=alias_result,
            feature_state=feature_state,
            diagnostics=diagnostics,
            vector_ceiling=vector_ceiling,
            table_context=table_context,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            session_context=session_context,
        )
        _rag_log(
            "table.search_decision",
            {
                "query": traits.normalized,
                "tables_available": tables_available,
                "has_intent": table_context["has_intent"],
                "chunk_hits": len(chunk_hits),
            },
            indent=1,
            context={
                "business": business_profile.id,
                "request": diagnostics.get("request_id"),
            },
        )
        diagnostics["chunk_candidate_count_raw"] = len(chunk_hits)
        candidate_prep = prepare_candidate_hits(
            search_service=self,
            business_profile=business_profile,
            traits=traits,
            chunk_hits=chunk_hits,
            table_context=table_context,
            diagnostics=diagnostics,
            log_func=_rag_log,
        )
        chunk_hits = candidate_prep.chunk_hits
        _context_hits = candidate_prep.context_hits
        table_intent = candidate_prep.table_intent
        scope_summary = candidate_prep.scope_summary
        table_phase = resolve_table_search_phase(
            search_service=self,
            business_profile=business_profile,
            traits=traits,
            query=query,
            limit=limit,
            alias_result=alias_result,
            feature_state=feature_state,
            session_cache=session_cache,
            cache_key=cache_key,
            request_id=request_id,
            overall_start=overall_start,
            table_context=table_context,
            diagnostics=diagnostics,
            strategy_result=strategy_result,
            chunk_hits=chunk_hits,
            context_hits=_context_hits,
            table_intent=table_intent,
            tables_available=tables_available,
            allowed_upload_ids=allowed_upload_ids,
            allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            log_func=_rag_log,
        )
        table_blocked = table_phase.table_blocked
        table_reason = table_phase.table_reason
        if table_phase.result:
            return table_phase.result

        chunk_snippet_limit = limit
        diversify_requested = bool(strategy_result and strategy_result.hints.diversify_tables)
        if diversify_requested:
            chunk_snippet_limit = max(
                limit,
                int(math.ceil(limit * self.coverage_diversification_candidate_multiplier)),
            )
        snippets = tuple(
            self._search_chunks(
                chunk_hits,
                limit=chunk_snippet_limit,
                business_profile=business_profile,
                pathway="hybrid",
                query=traits.normalized or traits.original or query,  # NEW: For query-aware row sampling
            )
        )
        if snippets:
            diagnostics["path"] = diagnostics.get("path") or "hybrid"
            diagnostics.setdefault("table_reason", table_reason)
            if diversify_requested and len(snippets) > 1:
                diversified_snippets, coverage_diag = self._diversify_table_snippets_with_diagnostics(
                    snippets,
                    limit=limit,
                )
                diagnostics.update(coverage_diag)
                diagnostics["strategy_diversified"] = bool(
                    coverage_diag.get("coverage_diversification_applied")
                )
                snippets = tuple(diversified_snippets)
            snippets, collapse_diag = self._collapse_snippets_by_evidence_group(
                snippets,
                query_text=traits.normalized or traits.original or query,
                tokens=traits.tokens,
                limit=limit,
            )
            diagnostics.update(collapse_diag)
            diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
            status, snippets, diagnostics = self._apply_phase6_semantics(
                status="ok",
                snippets=snippets,
                diagnostics=diagnostics,
                business_profile=business_profile,
                traits=traits,
                table_context=table_context,
                table_blocked=table_blocked,
            )
            diagnostics["snippet_count"] = len(snippets)
            result_obj = KnowledgeSearchResult(snippets=snippets, status=status, diagnostics=diagnostics)
            self._result_cache_set(cache_key, result_obj, limit=limit)
            self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
            self._record_retrieval_event(
                business_profile=business_profile,
                traits=traits,
                alias_result=alias_result,
                result=result_obj,
                feature_state=feature_state,
            )
            self._log_search_summary(
                business_profile=business_profile,
                request_id=request_id,
                result=result_obj,
            )
            return result_obj

        fallback = tuple(
            self._fallback_snippets(
                business_profile=business_profile,
                limit=limit,
                allowed_upload_ids=allowed_upload_ids,
                allowed_explicit_upload_ids=allowed_explicit_upload_ids,
            )
        )
        fallback, collapse_diag = self._collapse_snippets_by_evidence_group(
            fallback,
            query_text=traits.normalized or traits.original or query,
            tokens=traits.tokens,
            limit=limit,
        )
        diagnostics["path"] = "fallback"
        diagnostics["reason"] = "fallback_used"
        status = "ok" if fallback else "not_found"
        status, fallback, diagnostics = self._apply_phase6_semantics(
            status=status,
            snippets=fallback,
            diagnostics=diagnostics,
            business_profile=business_profile,
            traits=traits,
            table_context=table_context,
            table_blocked=table_blocked,
        )
        diagnostics["total_duration_ms"] = self._duration_ms(overall_start)
        diagnostics.setdefault("table_reason", table_reason)
        diagnostics.update(collapse_diag)
        diagnostics["snippet_count"] = len(fallback)
        result_obj = KnowledgeSearchResult(snippets=fallback, status=status, diagnostics=diagnostics)
        self._result_cache_set(cache_key, result_obj, limit=limit)
        self._session_cache_set(session_cache, cache_key, result_obj, limit=limit)
        self._record_retrieval_event(
            business_profile=business_profile,
            traits=traits,
            alias_result=alias_result,
            result=result_obj,
            feature_state=feature_state,
        )
        self._log_search_summary(
            business_profile=business_profile,
            request_id=request_id,
            result=result_obj,
        )
        return result_obj
