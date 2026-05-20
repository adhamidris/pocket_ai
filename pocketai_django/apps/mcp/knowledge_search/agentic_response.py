from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Mapping, Sequence

from django.core.cache import cache

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log

from ..knowledge_support.agentic_response import _convert_to_agentic_search_response
from ..runtime.search_cursor import (
    _encode_search_cursor,
    _search_cursor_cache_key,
    _store_search_cursor_handle,
)
from ..types import ToolExecutionContext


logger = logging.getLogger(__name__)


def _build_final_agentic_response(
    *,
    payload: Mapping[str, object],
    conversation: Conversation,
    context: ToolExecutionContext,
    rag_agentic_enabled: bool,
    final_status: str,
    results_full: Sequence[object],
    page_size: int,
    pagination_enabled: bool,
    cursor_ttl_seconds: int,
    total_found: int,
    extract_agentic_manifests: Callable[[Sequence[Mapping[str, object]]], dict[str, dict[str, object]]],
    read_budget_for_refs: Callable[[Sequence[Mapping[str, object]]], dict[str, int] | None],
) -> object:
    # Convert to agentic format when enabled, and persist search intent metadata
    # for semantic dedup within this user turn.
    if rag_agentic_enabled:
        if final_status in {"ok", "not_found"} and results_full:
            # Agentic search must return up to `limit` unique refs. The legacy flow
            # converts only the first snippet page, which can collapse to fewer refs
            # (e.g., 10 snippets -> 3 refs) and starve the model of evidence breadth.
            #
            # Fix: derive refs from the full candidate set (prefetch window) and page
            # over refs (not snippets). This preserves stable pagination and avoids
            # skipping refs when dedupe collapses multiple snippets into one ref.
            full_payload = dict(payload)
            full_payload["snippets"] = [dict(snippet) for snippet in results_full if isinstance(snippet, Mapping)]
            agentic_full = _convert_to_agentic_search_response(
                full_payload,
                conversation=conversation,
                context=context,
            )
            refs_full_raw = agentic_full.get("refs")
            refs_full: list[dict[str, object]] = (
                [dict(ref) for ref in refs_full_raw if isinstance(ref, Mapping)]
                if isinstance(refs_full_raw, list)
                else []
            )
            refs_total_found = len(refs_full)
            refs_page = refs_full[:page_size]
            has_more_refs = bool(refs_total_found > len(refs_page))

            # Cache a ref paging session for cursor paging.
            next_cursor = None
            session_id = None
            if pagination_enabled and has_more_refs and refs_full:
                session_id = str(uuid.uuid4())
                cache_key = _search_cursor_cache_key(conversation=conversation, session_id=session_id)
                manifests_table = extract_agentic_manifests(refs_full)
                completeness_base = agentic_full.get("completeness")
                completeness_base_out = dict(completeness_base) if isinstance(completeness_base, Mapping) else {}
                completeness_base_out["refs_total_found"] = int(refs_total_found)
                completeness_base_out["paging_mode"] = "refs"
                completeness_base_out["snippets_total_found"] = int(total_found)
                cache.set(
                    cache_key,
                    {
                        "version": 2,
                        "query": payload.get("query"),
                        "query_intent": payload.get("query_intent"),
                        "page_size": page_size,
                        "snippets_total_found": int(total_found),
                        "refs_total_found": int(refs_total_found),
                        "refs": refs_full,
                        "completeness_base": completeness_base_out,
                        "table_row_anchor_manifests": manifests_table,
                    },
                    cursor_ttl_seconds,
                )
                next_cursor = _encode_search_cursor(session_id=session_id, offset=len(refs_page))

            completeness_out = dict(agentic_full.get("completeness") or {}) if isinstance(agentic_full.get("completeness"), Mapping) else {}
            # Normalize agentic completeness to what we actually returned.
            completeness_out.setdefault("total_found", int(total_found))  # snippet candidates
            completeness_out["snippets_total_found"] = int(total_found)
            completeness_out["refs_total_found"] = int(refs_total_found)
            completeness_out["paging_mode"] = "refs"
            completeness_out["ref_offset"] = 0
            completeness_out["shown"] = len(refs_page)
            completeness_out["has_more"] = bool(has_more_refs)

            final_response = {
                **{k: v for k, v in dict(agentic_full).items() if k not in {"refs", "next_cursor", "has_more", "total_found", "read_budget", "read_budget_hint", "completeness", "pagination"}},
                "tool": "search_knowledge",
                "query": payload.get("query"),
                "limit": int(page_size),
                "query_intent": payload.get("query_intent"),
                "status": agentic_full.get("status") if refs_page else "not_found",
                "refs": refs_page,
                "completeness": completeness_out,
                "pagination": {
                    "shown": len(refs_page),
                    "total": int(refs_total_found),
                    "has_more": bool(has_more_refs),
                },
            }
            read_budget = read_budget_for_refs(refs_page)
            if read_budget:
                final_response["read_budget"] = read_budget
            if next_cursor:
                final_response["pagination"]["next_cursor"] = (
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
                    "session_version": 2,
                    "session_stored": bool(next_cursor),
                    "paging_mode": "refs",
                    "cursor_used": False,
                    "offset": 0,
                    "limit": int(page_size),
                    "snippets_total_found": int(total_found),
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
        else:
            final_response = _convert_to_agentic_search_response(
                payload,
                conversation=conversation,
                context=context,
            )
    else:
        final_response = payload

    return final_response
