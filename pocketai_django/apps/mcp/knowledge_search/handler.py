from __future__ import annotations

import copy
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Mapping, Sequence

from django.conf import settings

from apps.accounts.feature_flags import FeatureFlagService
from apps.conversations.models import Conversation
from apps.rag.knowledge_search import KnowledgeSearchService
from apps.rag.rag_logging import structured_log

from ..runtime.budget_guidance import search_budget_exceeded_payload
from ..knowledge_support.identifier_helpers import _extract_identifier_candidate
from ..knowledge_support.observability import _log_snippet_payloads
from ..knowledge_support.query_helpers import _query_intent
from ..knowledge_support.result_helpers import (
    _sanitize_snippet_payloads_for_prompt,
    _serialize_snippets,
)
from ..knowledge_support.scope import _agent_knowledge_scope, _scope_upload_ids_to_uuids
from ..runtime.rag_observability import build_query_scope_observability
from ..runtime.search_cursor import _search_cursor_ttl_seconds
from ..tool_definitions import (
    DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
    MCP_PROMPT_MAX_SNIPPETS_CAP,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
    SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT,
    SEARCH_PREFETCH_ABSOLUTE_CAP,
)
from ..runtime.tool_runtime_helpers import _bounded_cache_store, _search_cache_key
from ..types import SearchBudgetExceeded, ToolExecutionContext
from .duplicate_detection import (
    _duplicate_intent_diagnostics,
)
from .agentic_response import _build_final_agentic_response
from .cursor_paging import _handle_search_cursor_page
from .manifests import _extract_agentic_manifests as _extract_agentic_manifests_impl
from .pagination import (
    _excluded_chunk_ids as _excluded_chunk_ids_impl,
    _read_budget_for_refs as _read_budget_for_refs_impl,
)
from .performance import (
    _effective_limit as _effective_limit_impl,
    _log_search_performance as _log_search_performance_impl,
)
from .query_variants import (
    _collect_queries,
    _prune_queries as _prune_queries_impl,
)
from .rate_limits import _enforce_search_rate_limit as _enforce_search_rate_limit_impl
from .response_observability import _attach_retrieval_observability
from .result_payload import _build_search_result_payload
from .search_history import _finalize_search_history
from .snippet_payloads import _prepare_snippet_payloads


logger = logging.getLogger(__name__)


def _knowledge_service() -> KnowledgeSearchService:
    try:
        from apps.mcp import tools as mcp_tools

        override = getattr(mcp_tools, "_knowledge_service", None)
        if override is not None and override is not _knowledge_service:
            return override()
    except Exception:
        pass
    return _default_knowledge_service()


@lru_cache(maxsize=1)
def _default_knowledge_service() -> KnowledgeSearchService:
    return KnowledgeSearchService()


@lru_cache(maxsize=1)
def _portal_file_embedding_service():
    if not bool(getattr(settings, "RAG_PORTAL_FILE_SEARCH_ENABLED", True)):
        return None
    try:
        from apps.rag.embeddings import build_embedding_service
    except Exception:
        return None
    return build_embedding_service()


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)

def _search_knowledge_handler(
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    new_contract_enabled = bool(getattr(settings, "MCP_NEW_CONTRACT_ENABLED", True))
    feature_flag_service = FeatureFlagService
    try:
        from apps.mcp import tools as mcp_tools

        feature_flag_service = getattr(mcp_tools, "FeatureFlagService", FeatureFlagService)
    except Exception:
        pass
    feature_state = feature_flag_service.snapshot(conversation.business_profile)
    rag_agentic_enabled = bool(getattr(feature_state, "rag_agentic_mode", False)) and new_contract_enabled

    pagination_enabled = bool(getattr(settings, "MCP_SEARCH_PAGINATION_ENABLED", True))
    cursor_ttl_seconds = _search_cursor_ttl_seconds()
    try:
        pagination_prefetch_min = int(getattr(settings, "MCP_SEARCH_PAGINATION_PREFETCH_MIN", 50) or 50)
    except (TypeError, ValueError):
        pagination_prefetch_min = 50
    pagination_prefetch_min = max(0, pagination_prefetch_min)

    exclude_seen = bool(getattr(settings, "MCP_SEARCH_EXCLUDE_SEEN_ENABLED", False))
    raw_exclude_seen = arguments.get("exclude_seen")
    if isinstance(raw_exclude_seen, bool):
        exclude_seen = raw_exclude_seen

    def _excluded_chunk_ids() -> set[str]:
        return _excluded_chunk_ids_impl(context=context, exclude_seen=exclude_seen)

    def _read_budget_for_refs(refs: Sequence[Mapping[str, object]]) -> dict[str, int] | None:
        return _read_budget_for_refs_impl(
            refs,
            max_chars_allowed=int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX),
        )

    def _extract_agentic_manifests(
        refs: Sequence[Mapping[str, object]],
    ) -> dict[str, dict[str, object]]:
        return _extract_agentic_manifests_impl(refs, context=context)

    def _enforce_search_rate_limit() -> Mapping[str, object] | None:
        return _enforce_search_rate_limit_impl(conversation)

    cursor_page = _handle_search_cursor_page(
        arguments=arguments,
        conversation=conversation,
        context=context,
        pagination_enabled=pagination_enabled,
        cursor_ttl_seconds=cursor_ttl_seconds,
        exclude_seen=exclude_seen,
        rag_agentic_enabled=rag_agentic_enabled,
        enforce_search_rate_limit=_enforce_search_rate_limit,
        excluded_chunk_ids=_excluded_chunk_ids,
        read_budget_for_refs=_read_budget_for_refs,
    )
    if cursor_page is not None:
        return cursor_page

    # Build queries list.
    # - `query` is the primary, single-query interface (back-compat and simpler).
    # - `queries[]` allows multiple variants for fanout.
    queries = _collect_queries(
        raw_query=arguments.get("query"),
        raw_queries=arguments.get("queries"),
    )
    primary_query = queries[0] if queries else ""

    # =========================================================================
    # DIAGNOSTIC: Log document context state at search start
    # =========================================================================
    structured_log(
        "mcp",
        "search.context_state",
        {
            "query": primary_query,
            "primary_upload_id": context.primary_upload_id if context else None,
            "primary_document_title": context.get_primary_document_title() if context else None,
            "referenced_upload_ids": list(context.referenced_upload_ids)[:5] if context else [],
            "search_history_count": len(context.search_history) if context else 0,
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )

    # =========================================================================
    # Document-continuity query rewriting removed
    # =========================================================================
    # Search queries must not be mutated with a previous/active document title.
    # Explicit document scoping should be represented as tool scope/filters, not
    # by rewriting "query" into "File name: query".
    rewrite_result = None
    rewrite_error: str | None = None
    rewrite_enabled = False

    query_scope_observability = build_query_scope_observability(
        context=context,
        rewrite_result=rewrite_result,
        rewrite_enabled=bool(rewrite_enabled),
        rewrite_error=rewrite_error,
    )
    structured_log(
        "mcp",
        "search.scope_decision",
        query_scope_observability,
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )

    # Fanout variants are controlled via MCP_SEARCH_MAX_QUERY_VARIANTS.
    # Keep this fully env-configurable so operators can tune recall/cost tradeoffs.
    query_variant_limit = max(
        1,
        int(getattr(settings, "MCP_SEARCH_MAX_QUERY_VARIANTS", DEFAULT_MAX_SEARCH_QUERY_VARIANTS)),
    )
    fanout_budget_ms = max(
        0,
        int(getattr(settings, "MCP_SEARCH_FANOUT_BUDGET_MS", 0) or 0),
    )

    def _prune_queries(values: Sequence[str]) -> list[str]:
        return _prune_queries_impl(values, query_variant_limit=query_variant_limit)


    optimized_queries = _prune_queries(queries)
    if len(optimized_queries) < len(queries):
        structured_log(
            "mcp",
            "search.query_pruned",
            {
                "original_count": len(queries),
                "kept": len(optimized_queries),
                "limit": query_variant_limit,
            },
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )
    queries = optimized_queries

    if not queries:
        return {
            "tool": "search_knowledge",
            "status": "error",
            "error": "query is required",
            "snippets": [],
        }

    # Layer 3: duplicate-search telemetry.
    # We still measure similar intents/result sets so operators can inspect search
    # thrash, but we no longer short-circuit live retrieval with stale refs.
    result_fingerprint_top_k = 8



    search_budget_reserved = False

    def _reserve_search_budget_once():
        nonlocal search_budget_reserved
        if search_budget_reserved:
            return None
        limited = _enforce_search_rate_limit()
        if limited is not None:
            return limited
        try:
            context.reserve_search()
        except SearchBudgetExceeded:
            return search_budget_exceeded_payload(context, reason="per_turn_limit")
        search_budget_reserved = True
        return None

    embedder_factory = _portal_file_embedding_service
    try:
        from apps.mcp import tools as mcp_tools

        embedder_factory = getattr(mcp_tools, "_portal_file_embedding_service", _portal_file_embedding_service)
    except Exception:
        pass
    intent_text, intent_embedding, duplicate_intent_diagnostics = _duplicate_intent_diagnostics(
        queries=queries,
        new_contract_enabled=new_contract_enabled,
        search_history=getattr(context, "search_history", None) or [],
        embedder=embedder_factory() if new_contract_enabled else None,
    )

    raw_limit = arguments.get("limit")
    try:
        page_size_requested = int(raw_limit) if raw_limit is not None else None
    except (TypeError, ValueError):
        page_size_requested = None
    if page_size_requested is None:
        # Server-side default when callers omit `limit`. Without this, the underlying
        # search service may treat limit=None as unbounded and return huge result sets.
        page_size_requested = SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT

    page_size = max(1, min(int(page_size_requested), MCP_PROMPT_MAX_SNIPPETS_CAP))
    fetch_limit = page_size
    if pagination_enabled and pagination_prefetch_min:
        # Prefetch more candidates than page_size so cursor pagination has results to serve.
        # Use SEARCH_PREFETCH_ABSOLUTE_CAP (not MCP_PROMPT_MAX_SNIPPETS_CAP) to avoid
        # clamping prefetch to the same value as page_size, which made pagination a no-op.
        fetch_limit = max(page_size, min(int(pagination_prefetch_min), SEARCH_PREFETCH_ABSOLUTE_CAP))
    requested_limit = fetch_limit

    service = _knowledge_service()
    identifier_filter: dict[str, object] | None = None
    locked_key = None
    locked_value = None

    agent_scope = _agent_knowledge_scope(conversation, context)
    agent_explicit_upload_ids = _scope_upload_ids_to_uuids(agent_scope.explicit_upload_ids) if agent_scope.explicit_upload_ids else None
    combined_upload_ids: list[uuid.UUID] | None = agent_explicit_upload_ids if agent_scope.restricted else None

    def _effective_limit(base_limit: int | None) -> int | None:
        return _effective_limit_impl(base_limit, max_snippets_cap=MCP_PROMPT_MAX_SNIPPETS_CAP)

    def _log_search_performance(
        *,
        snippets: Sequence[Mapping[str, object]],
        diagnostics: Mapping[str, object] | None,
        intent: str | None,
        limit_value: int | None,
        status: str,
        note: str | None = None,
    ) -> None:
        _log_search_performance_impl(
            conversation=conversation,
            agent_scope=agent_scope,
            combined_upload_ids=combined_upload_ids,
            snippets=snippets,
            diagnostics=diagnostics,
            intent=intent,
            limit_value=limit_value,
            status=status,
            note=note,
        )

    def _build_search_session_context() -> dict[str, object] | None:
        return None

    def _execute_single_query(
        query_text: str,
        *,
        intent_info_override: Mapping[str, object] | None = None,
        intent_override: str | None = None,
        limit_override: int | None = None,
        precomputed_result: object | None = None,
    ) -> Mapping[str, object]:
        # Thin MCP wrapper: execute a single RAG search and translate the result
        # into MCP's stable tool contract. Retrieval planning, ranking, fusion,
        # and fallback decisions must remain in apps.rag.knowledge_search.
        intent_info = intent_info_override or _query_intent(query_text)
        intent = intent_override or intent_info.get("intent")
        normalized_query = query_text.lower()
        aggregation_keywords = (
            "total",
            "sum",
            "overall",
            "aggregate",
            "اجمالي",
            "إجمالي",
            "الاجمالي",
            "المجموع",
        )
        aggregation_query = any(keyword in normalized_query for keyword in aggregation_keywords)
        limit_for_run = limit_override if limit_override is not None else _effective_limit(requested_limit)
        search_cache_key = _search_cache_key(
            query_text,
            limit_for_run,
            identifier_filter,
            locked_key,
            str(locked_value) if locked_value is not None else None,
        )
        cached_result = None
        if context.search_cache and search_cache_key in context.search_cache:
            cached_result = copy.deepcopy(context.search_cache[search_cache_key])
        if cached_result:
            structured_log(
                "mcp",
                "search.cache_hit",
                {
                    "query": query_text,
                    "intent": intent,
                    "limit": limit_for_run,
                },
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
            )
            cached_result["query"] = query_text
            cached_result["limit_used"] = limit_for_run
            cached_result["cache_hit"] = True
            cached_result.setdefault("query_intent", intent)
            cached_result.setdefault("intent_signal", intent_info)
            cached_result.setdefault("snippets", [])
            cached_result["snippets"] = [dict(snippet) for snippet in cached_result.get("snippets", [])]
            cached_result["cache_hit"] = True
            return cached_result

        if precomputed_result is not None:
            result = precomputed_result
        else:
            result = service.search(
                business_profile=conversation.business_profile,
                query=query_text,
                limit=limit_for_run,
                identifier_filter=identifier_filter,
                allowed_upload_ids=combined_upload_ids,
                allowed_explicit_upload_ids=agent_explicit_upload_ids,
                session_context=_build_search_session_context(),
            )
        combined_snippets: list[object] = list(getattr(result, "snippets", []) or [])
        snippet_payloads = _serialize_snippets(combined_snippets)

        # Track referenced documents so later turns can keep document context.
        doc_context_enabled = str(getattr(settings, "RAG_DOCUMENT_CONTEXT_ENABLED", "true")).lower() in {"1", "true", "yes"}
        if doc_context_enabled:
            for payload in snippet_payloads:
                upload_id = payload.get("upload_id") or payload.get("document_id")
                if upload_id:
                    # Extract document title from snippet
                    title = payload.get("title", "")
                    if title and " – " in title:
                        # Format is often "Document Name – chunk N"
                        title = title.split(" – ")[0].strip()
                    elif title and " - " in title:
                        title = title.split(" - ")[0].strip()

                    context.track_document_reference(
                        upload_id=str(upload_id),
                        title=title,
                        stage=payload.get("search_stage", "unknown"),
                        confidence=payload.get("confidence_score") if isinstance(payload.get("confidence_score"), (int, float)) else None,
                    )

        read_required, read_required_reasons_summary = _prepare_snippet_payloads(
            snippet_payloads,
            intent=intent,
        )
        snippet_payloads = _sanitize_snippet_payloads_for_prompt(snippet_payloads, conversation=conversation)
        log_meta = {"query": query_text, "intent": intent, "read_required": read_required}
        if read_required_reasons_summary:
            log_meta["read_required_reasons"] = sorted(read_required_reasons_summary)
        _log_snippet_payloads(
            tool="search_knowledge",
            conversation=conversation,
            snippet_payloads=snippet_payloads,
            meta=log_meta,
        )
        _log_search_performance(
            snippets=snippet_payloads,
            diagnostics=result.diagnostics,
            intent=intent,
            limit_value=limit_for_run,
            status=result.status,
        )
        result_diagnostics = dict(result.diagnostics or {})
        result_diagnostics.setdefault("query_scope", dict(query_scope_observability))

        payload = {
            "tool": "search_knowledge",
            "query": query_text,
            "limit": limit_for_run,
            "limit_used": limit_for_run,
            "query_intent": intent,
            "intent_signal": intent_info,
            "status": result.status,
            "diagnostics": result_diagnostics,
            "snippets": snippet_payloads,
        }

        payload["read_required_summary"] = {
            "any": read_required,
            "reasons": sorted(read_required_reasons_summary),
        }
        if search_cache_key:
            _bounded_cache_store(context.search_cache, search_cache_key, copy.deepcopy(payload))
        return payload

    resolved_runs: list[tuple[int, Mapping[str, object]]] = []
    pending_specs: list[tuple[int, str, Mapping[str, object], str | None, int | None]] = []
    non_cached_queries = 0
    for idx, query_text in enumerate(queries):
        intent_info = _query_intent(query_text)
        intent = intent_info.get("intent")
        limit_for_run = _effective_limit(requested_limit)
        search_cache_key = _search_cache_key(
            query_text,
            limit_for_run,
            identifier_filter,
            locked_key,
            str(locked_value) if locked_value is not None else None,
        )
        cached_result = None
        if context.search_cache and search_cache_key in context.search_cache:
            cached_result = copy.deepcopy(context.search_cache[search_cache_key])
        if cached_result:
            structured_log(
                "mcp",
                "search.cache_hit",
                {
                    "query": query_text,
                    "intent": intent,
                    "limit": limit_for_run,
                },
                context={"conversation": conversation.id, "business": conversation.business_profile_id},
                logger_obj=logger,
            )
            cached_result["query"] = query_text
            cached_result["limit_used"] = limit_for_run
            cached_result.setdefault("query_intent", intent)
            cached_result.setdefault("intent_signal", intent_info)
            cached_result.setdefault("snippets", [])
            cached_result["snippets"] = [dict(snippet) for snippet in cached_result.get("snippets", [])]
            cached_result["cache_hit"] = True
            resolved_runs.append((idx, cached_result))
            continue
        pending_specs.append((idx, query_text, intent_info, intent, limit_for_run))
        non_cached_queries += 1

    if non_cached_queries > 0:
        # Enforce limits only when this call needs a backend search.
        # Pure cache reuses (same intent/query in the same turn) should not
        # consume per-turn search budget.
        limited = _reserve_search_budget_once()
        if limited is not None:
            return limited

    executor: ThreadPoolExecutor | None = None
    futures: list[tuple[int, str, Mapping[str, object], str | None, int | None, object]] = []
    fanout_start = time.perf_counter()
    fanout_parallel_enabled = bool(getattr(settings, "MCP_SEARCH_FANOUT_PARALLEL", False))
    max_parallel_workers = int(getattr(settings, "MCP_SEARCH_FANOUT_PARALLEL_MAX_WORKERS", 4) or 4)
    max_parallel_workers = max(1, min(8, max_parallel_workers))
    use_parallel = bool(fanout_parallel_enabled and non_cached_queries > 1 and fanout_budget_ms <= 0)
    if use_parallel:
        executor = ThreadPoolExecutor(
            max_workers=min(non_cached_queries, max_parallel_workers),
            thread_name_prefix="mcp_search",
        )
    try:
        for idx, query_text, intent_info, intent, limit_for_run in pending_specs:
            if not executor and fanout_budget_ms:
                elapsed_ms = int((time.perf_counter() - fanout_start) * 1000)
                if resolved_runs and elapsed_ms >= fanout_budget_ms:
                    structured_log(
                        "mcp",
                        "search.fanout_budget_exceeded",
                        {
                            "budget_ms": fanout_budget_ms,
                            "elapsed_ms": elapsed_ms,
                            "queries_planned": len(pending_specs),
                            "queries_run": len(resolved_runs),
                        },
                        context={
                            "conversation": conversation.id,
                            "business": conversation.business_profile_id,
                        },
                        logger_obj=logger,
                        level=logging.WARNING,
                    )
                    break
            if executor:
                future = executor.submit(
                    service.search,
                    business_profile=conversation.business_profile,
                    query=query_text,
                    limit=limit_for_run,
                    identifier_filter=identifier_filter,
                    allowed_upload_ids=combined_upload_ids,
                    allowed_explicit_upload_ids=agent_explicit_upload_ids,
                    session_context=_build_search_session_context(),
                )
                futures.append((idx, query_text, intent_info, intent, limit_for_run, future))
            else:
                result = service.search(
                    business_profile=conversation.business_profile,
                    query=query_text,
                    limit=limit_for_run,
                    identifier_filter=identifier_filter,
                    allowed_upload_ids=combined_upload_ids,
                    allowed_explicit_upload_ids=agent_explicit_upload_ids,
                    session_context=_build_search_session_context(),
                )
                run_payload = _execute_single_query(
                    query_text,
                    intent_info_override=intent_info,
                    intent_override=intent,
                    limit_override=limit_for_run,
                    precomputed_result=result,
                )
                resolved_runs.append((idx, run_payload))
                if run_payload.get("status") not in {"ok", "not_found"}:
                    if len(queries) > 1:
                        run_payload = dict(run_payload)
                        run_payload["batched_queries"] = tuple(queries)
                    return run_payload
        if executor:
            for idx, query_text, intent_info, intent, limit_for_run, future in sorted(futures, key=lambda entry: entry[0]):
                result = future.result()
                run_payload = _execute_single_query(
                    query_text,
                    intent_info_override=intent_info,
                    intent_override=intent,
                    limit_override=limit_for_run,
                    precomputed_result=result,
                )
                resolved_runs.append((idx, run_payload))
                if run_payload.get("status") not in {"ok", "not_found"}:
                    if len(queries) > 1:
                        run_payload = dict(run_payload)
                        run_payload["batched_queries"] = tuple(queries)
                    return run_payload
    finally:
        if executor:
            executor.shutdown(wait=True)

    resolved_runs.sort(key=lambda entry: entry[0])
    runs = [payload for _, payload in resolved_runs]

    if not runs:
        return {
            "tool": "search_knowledge",
            "query": "",
            "limit": requested_limit,
            "status": "not_found",
            "snippets": [],
            "error": "no_query",
        }

    payload, final_status, results_full, total_found = _build_search_result_payload(
        runs=runs,
        queries=queries,
        conversation=conversation,
        context=context,
        page_size=page_size,
        pagination_enabled=pagination_enabled,
        rag_agentic_enabled=rag_agentic_enabled,
        cursor_ttl_seconds=cursor_ttl_seconds,
        fanout_budget_ms=fanout_budget_ms,
        fanout_parallel_enabled=fanout_parallel_enabled,
        use_parallel=use_parallel,
        exclude_seen=exclude_seen,
        excluded_chunk_ids=_excluded_chunk_ids,
    )

    final_response = _build_final_agentic_response(
        payload=payload,
        conversation=conversation,
        context=context,
        rag_agentic_enabled=rag_agentic_enabled,
        final_status=final_status,
        results_full=results_full,
        page_size=page_size,
        pagination_enabled=pagination_enabled,
        cursor_ttl_seconds=cursor_ttl_seconds,
        total_found=total_found,
        extract_agentic_manifests=_extract_agentic_manifests,
        read_budget_for_refs=_read_budget_for_refs,
    )

    final_response = _attach_retrieval_observability(
        final_response=final_response,
        conversation=conversation,
        query_scope_observability=query_scope_observability,
        final_status=final_status,
    )
    final_response = _finalize_search_history(
        final_response=final_response,
        context=context,
        new_contract_enabled=new_contract_enabled,
        result_fingerprint_top_k=result_fingerprint_top_k,
        duplicate_intent_diagnostics=duplicate_intent_diagnostics,
        intent_text=intent_text,
        intent_embedding=intent_embedding,
    )
    return final_response
