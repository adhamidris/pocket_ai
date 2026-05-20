from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from typing import Mapping

from django.core.cache import cache

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log

from ..knowledge_support.observability import _log_tool_metrics
from ..knowledge_support.read_guards import _extract_no_result_reason
from ..knowledge_support.result_helpers import (
    _apply_seen_item_filter,
    _mark_snippets_as_seen,
)
from ..knowledge_support.search_fusion import _fuse_batched_search_runs
from ..runtime.search_cursor import _encode_search_cursor, _search_cursor_cache_key
from ..types import ToolExecutionContext
from .pagination import _page_snippets
from .status import _select_final_status


logger = logging.getLogger(__name__)


def _build_search_result_payload(
    *,
    runs: Sequence[Mapping[str, object]],
    queries: Sequence[str],
    conversation: Conversation,
    context: ToolExecutionContext,
    page_size: int,
    pagination_enabled: bool,
    rag_agentic_enabled: bool,
    cursor_ttl_seconds: int,
    fanout_budget_ms: int,
    fanout_parallel_enabled: bool,
    use_parallel: bool,
    exclude_seen: bool,
    excluded_chunk_ids: Callable[[], set[str]],
) -> tuple[dict[str, object], str, list[object], int]:
    primary_run = runs[0]
    limit_cap = primary_run.get("limit_used")
    clip_limit = int(limit_cap) if isinstance(limit_cap, int) and limit_cap > 0 else None
    deduped_snippets, fusion = _fuse_batched_search_runs(
        runs,
        clip_limit=clip_limit,
    )

    results_full = deduped_snippets
    total_found = len(results_full)

    excluded_chunk_ids_value = excluded_chunk_ids()
    page_snippets, next_offset, has_more, excluded_count = _page_snippets(
        results_full,
        offset=0,
        page_size=page_size,
        excluded_chunk_ids=excluded_chunk_ids_value,
    )

    next_cursor = None
    session_id = None
    if pagination_enabled and has_more and results_full and not rag_agentic_enabled:
        session_id = str(uuid.uuid4())
        cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
        cache.set(
            cache_key,
            {
                "version": 1,
                "query": primary_run.get("query"),
                "query_intent": primary_run.get("query_intent") or primary_run.get("intent"),
                "page_size": page_size,
                "results": results_full,
            },
            cursor_ttl_seconds,
        )
        next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset)

    _page_snippets_out, completeness = _apply_seen_item_filter(page_snippets, context, mark_as_seen=False)
    page_snippets = _page_snippets_out
    completeness["total_found"] = total_found
    completeness["has_more"] = bool(has_more)
    if excluded_count:
        completeness["excluded_seen"] = excluded_count
    if completeness.get("shown", 0) > 0 and not completeness.get("has_more"):
        if completeness.get("already_seen") == completeness.get("shown"):
            completeness["all_previously_shown"] = True
            completeness["message"] = (
                f"All {completeness['shown']} matching results have already been shown in this conversation. "
                "Try a different search term or ask the user if they need something specific."
            )

    # Mark the returned page as seen (for follow-up paging within this turn).
    _mark_snippets_as_seen(page_snippets, context)

    # Log seen-item tracking results for debugging
    if completeness.get("already_seen", 0) > 0 or completeness.get("clipped", 0) > 0:
        structured_log(
            "mcp",
            "search.seen_tracking",
            {
                "shown": completeness.get("shown", 0),
                "already_seen": completeness.get("already_seen", 0),
                "clipped": completeness.get("clipped", 0),
                "total_found": completeness.get("total_found", 0),
                "all_previously_shown": completeness.get("all_previously_shown", False),
            },
            context={"conversation": conversation.id, "business": conversation.business_profile_id},
            logger_obj=logger,
        )

    final_status, status_source_run = _select_final_status(
        runs=runs,
        primary_run=primary_run,
        page_snippets=page_snippets,
    )

    for snippet in page_snippets:
        context.add_retrieval_candidate(snippet)

    metrics = _log_tool_metrics(
        tool="search_knowledge",
        conversation=conversation,
        snippets=page_snippets,
        extra={
            "status": final_status,
            "primary_status": primary_run.get("status"),
            "status_source_query": status_source_run.get("query"),
            "limit": page_size,
            "query_length": len(str(primary_run.get("query") or "")),
            "fusion": fusion,
            "fanout_budget_ms": fanout_budget_ms,
            "fanout_parallel_enabled": bool(fanout_parallel_enabled),
            "fanout_parallel_used": bool(use_parallel),
            "queries_planned": len(queries),
            "queries_run": len(runs),
        },
    )
    context.reserve_characters(int(metrics.get("char_count", 0)))

    diag = dict(status_source_run.get("diagnostics") or {})
    try:
        diag["final_status_source_index"] = int(runs.index(status_source_run))
    except ValueError:
        diag["final_status_source_index"] = 0
    diag["final_status_source_query"] = status_source_run.get("query")
    if final_status == "not_found":
        no_result_reason = _extract_no_result_reason(diag)
        if not no_result_reason:
            for run in runs:
                run_diag = run.get("diagnostics")
                if not isinstance(run_diag, Mapping):
                    continue
                candidate_reason = _extract_no_result_reason(run_diag)
                if candidate_reason:
                    no_result_reason = candidate_reason
                    break
        if no_result_reason:
            diag["no_result_reason"] = no_result_reason
    diag["batched_runs"] = [
        {
            "query": run.get("query"),
            "status": run.get("status"),
            "snippet_count": len(run.get("snippets", [])),
            "cache_hit": bool(run.get("cache_hit")),
        }
        for run in runs
    ]
    diag["batched_queries"] = queries

    query_intent = (
        status_source_run.get("query_intent")
        or status_source_run.get("intent")
        or primary_run.get("query_intent")
        or primary_run.get("intent")
    )

    if not page_snippets and total_found and exclude_seen:
        completeness["all_previously_shown"] = True
        completeness["message"] = (
            f"No new results: the top {total_found} matches were already shown earlier in this conversation. "
            "Use the earlier results, or change the query to find different matches."
        )

    payload = {
        "tool": "search_knowledge",
        "query": primary_run.get("query"),
        "limit": page_size,
        "query_intent": query_intent,
        "intent_signal": primary_run.get("intent_signal"),
        "status": final_status,
        "diagnostics": diag,
        "snippets": page_snippets,
    }

    # Always include completeness metadata for transparent decisions
    payload["completeness"] = completeness
    payload["has_more"] = bool(has_more)
    if next_cursor:
        payload["next_cursor"] = next_cursor

    if fusion:
        payload["fusion"] = fusion
    if len(queries) > 1:
        payload["batched_queries"] = tuple(queries)

    return payload, final_status, results_full, total_found
