from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Mapping, Sequence

from django.core.cache import cache

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log

from ..knowledge_support.agentic_response import _convert_to_agentic_search_response
from ..knowledge_support.result_helpers import _apply_seen_item_filter
from ..runtime.budget_guidance import search_budget_exceeded_payload
from ..runtime.search_cursor import (
    _decode_search_cursor,
    _encode_search_cursor,
    _resolve_search_cursor_from_handle,
    _search_cursor_cache_key,
    _store_search_cursor_handle,
)
from ..tool_definitions import (
    MCP_PROMPT_MAX_SNIPPETS_CAP,
    SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT,
)
from ..types import SearchBudgetExceeded, ToolExecutionContext
from .pagination import _page_snippets


logger = logging.getLogger(__name__)


def _handle_search_cursor_page(
    *,
    arguments: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
    pagination_enabled: bool,
    cursor_ttl_seconds: int,
    exclude_seen: bool,
    rag_agentic_enabled: bool,
    enforce_search_rate_limit: Callable[[], Mapping[str, object] | None],
    excluded_chunk_ids: Callable[[], set[str]],
    read_budget_for_refs: Callable[[Sequence[Mapping[str, object]]], dict[str, int] | None],
) -> Mapping[str, object] | None:
    raw_cursor = _coerce_str(arguments.get("cursor")).strip()
    if not raw_cursor:
        return None

    if raw_cursor:
        resolved_cursor = _resolve_search_cursor_from_handle(context, conversation, raw_cursor) or raw_cursor
        # Cursor paging: bypass duplicate-intent reuse and serve next page from server cache.
        limited = enforce_search_rate_limit()
        if limited is not None:
            return limited
        try:
            context.reserve_search()
        except SearchBudgetExceeded:
            return search_budget_exceeded_payload(context, reason="cursor_paging_limit")

        if not pagination_enabled:
            return {
                "tool": "search_knowledge",
                "status": "constraint_error",
                "error": "pagination_disabled",
                "error_code": "pagination_disabled",
            }

        decoded = _decode_search_cursor(resolved_cursor, max_age_seconds=cursor_ttl_seconds)
        if not decoded:
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "invalid_cursor",
                "error_code": "invalid_cursor",
                "snippets": [],
            }
        session_id = str(decoded.get("sid") or "").strip()
        try:
            offset = int(decoded.get("o") or 0)
        except (TypeError, ValueError):
            offset = 0
        if not session_id:
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "invalid_cursor",
                "error_code": "invalid_cursor",
                "snippets": [],
            }

        cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
        session = cache.get(cache_key)
        if not isinstance(session, Mapping):
            return {
                "tool": "search_knowledge",
                "status": "error",
                "error": "cursor_expired",
                "error_code": "cursor_expired",
                "snippets": [],
            }

        raw_limit = arguments.get("limit")
        try:
            page_size = int(raw_limit) if raw_limit is not None else None
        except (TypeError, ValueError):
            page_size = None
        if page_size is None:
            try:
                page_size = int(session.get("page_size") or 0) or SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT
            except (TypeError, ValueError):
                page_size = SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT
        page_size = max(1, min(int(page_size), MCP_PROMPT_MAX_SNIPPETS_CAP))

        session_version = 1
        try:
            session_version = int(session.get("version") or 1)
        except (TypeError, ValueError):
            session_version = 1

        # Agentic ref paging (v2): return up to limit refs (not snippet rows) and page over refs.
        if session_version >= 2:
            refs_full = session.get("refs")
            if isinstance(refs_full, list):
                offset_refs = max(0, int(offset))
                refs_total_found = session.get("refs_total_found")
                try:
                    refs_total_found = int(refs_total_found) if refs_total_found is not None else len(refs_full)
                except (TypeError, ValueError):
                    refs_total_found = len(refs_full)

                snippet_total_found = session.get("snippets_total_found")
                try:
                    snippet_total_found_int = int(snippet_total_found) if snippet_total_found is not None else None
                except (TypeError, ValueError):
                    snippet_total_found_int = None

                # Rehydrate anchor manifests so read_knowledge can resolve table_chunk refs.
                manifests_table = session.get("table_row_anchor_manifests")
                if isinstance(manifests_table, Mapping):
                    cache_value = getattr(context, "table_row_anchor_manifests", None)
                    if isinstance(cache_value, dict):
                        for key, value in dict(manifests_table).items():
                            if isinstance(value, Mapping):
                                cache_value[str(key)] = dict(value)

                refs_page = [dict(ref) for ref in refs_full[offset_refs : offset_refs + page_size] if isinstance(ref, Mapping)]
                next_offset = offset_refs + len(refs_page)
                has_more_refs = bool(next_offset < refs_total_found)
                next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset) if has_more_refs else None

                completeness_base = session.get("completeness_base")
                completeness: dict[str, object] = dict(completeness_base) if isinstance(completeness_base, Mapping) else {}
                # For transparency, keep snippet totals even though we page over refs.
                if snippet_total_found_int is not None:
                    completeness.setdefault("total_found", snippet_total_found_int)
                    completeness["snippets_total_found"] = snippet_total_found_int
                completeness["refs_total_found"] = int(refs_total_found)
                completeness["paging_mode"] = "refs"
                completeness["ref_offset"] = int(offset_refs)
                completeness["shown"] = len(refs_page)
                completeness["has_more"] = bool(has_more_refs)

                payload: dict[str, object] = {
                    "tool": "search_knowledge",
                    "query": str(session.get("query") or "").strip(),
                    "limit": page_size,
                    "query_intent": session.get("query_intent"),
                    "status": "ok" if refs_page else "not_found",
                    "diagnostics": {"cursor_used": True, "offset": offset_refs, "paging_mode": "refs"},
                    "refs": refs_page,
                    "completeness": completeness,
                    "pagination": {
                        "shown": len(refs_page),
                        "total": int(refs_total_found),
                        "has_more": bool(has_more_refs),
                    },
                }
                read_budget = read_budget_for_refs(refs_page)
                if read_budget:
                    payload["read_budget"] = read_budget
                if next_cursor:
                    payload["pagination"]["next_cursor"] = (
                        _store_search_cursor_handle(
                            context,
                            conversation,
                            next_cursor,
                            ttl_seconds=cursor_ttl_seconds,
                        )
                        or next_cursor
                    )

                structured_log(
                    "mcp",
                    "search.agentic_paging",
                    {
                        "session_version": int(session_version),
                        "paging_mode": "refs",
                        "cursor_used": True,
                        "offset": int(offset_refs),
                        "limit": int(page_size),
                        "snippets_total_found": snippet_total_found_int,
                        "refs_total_found": int(refs_total_found),
                        "returned_refs": len(refs_page),
                        "has_more": bool(has_more_refs),
                    },
                    context={
                        "conversation": conversation.id,
                        "business": conversation.business_profile_id,
                    },
                    logger_obj=logger,
                )

                return payload

        results_full = session.get("results")
        if not isinstance(results_full, list):
            results_full = []

        excluded_chunk_ids_value = excluded_chunk_ids()
        page_snippets, next_offset, has_more, excluded_count = _page_snippets(
            results_full,
            offset=offset,
            page_size=page_size,
            excluded_chunk_ids=excluded_chunk_ids_value,
        )
        next_cursor = _encode_search_cursor(session_id=session_id, offset=next_offset) if has_more else None

        _page_snippets_out, completeness = _apply_seen_item_filter(page_snippets, context, mark_as_seen=False)
        page_snippets = _page_snippets_out
        completeness["total_found"] = len(results_full)
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

        query_text = str(session.get("query") or "").strip()
        query_intent = session.get("query_intent")
        if not page_snippets and results_full and exclude_seen:
            completeness["all_previously_shown"] = True
            completeness["message"] = (
                "No new results: all remaining matches were already shown earlier in this conversation. "
                "Use the earlier results, or change the query to find different matches."
            )
        payload: dict[str, object] = {
            "tool": "search_knowledge",
            "query": query_text,
            "limit": page_size,
            "query_intent": query_intent,
            "status": "ok" if page_snippets else "not_found",
            "diagnostics": {"cursor_used": True, "offset": offset},
            "snippets": page_snippets,
            "completeness": completeness,
            "has_more": bool(has_more),
        }
        if next_cursor:
            payload["next_cursor"] = next_cursor

        # Convert to agentic format when enabled.
        if rag_agentic_enabled:
            return _convert_to_agentic_search_response(
                payload,
                conversation=conversation,
                context=context,
            )
        return payload


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)
