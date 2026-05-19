from __future__ import annotations

import copy
import hashlib
import logging
import math
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Mapping, Sequence

from django.conf import settings
from django.core.cache import cache

from apps.accounts.feature_flags import FeatureFlagService
from apps.conversations.models import Conversation
from apps.rag.knowledge_search import KnowledgeSearchService
from apps.rag.rag_logging import structured_log
from apps.rag.tabular_limits import ToolRateLimit, enforce_tool_rate_limit

from ..budget_guidance import build_repeat_search_guidance, search_budget_exceeded_payload
from ..knowledge_agentic_response import _convert_to_agentic_search_response
from ..knowledge_identifier_helpers import _extract_identifier_candidate
from ..knowledge_observability import _log_snippet_payloads, _log_tool_metrics
from ..knowledge_query_helpers import _compute_read_required, _query_intent
from ..knowledge_result_helpers import (
    _apply_seen_item_filter,
    _mark_snippets_as_seen,
    _sanitize_snippet_payloads_for_prompt,
    _serialize_snippets,
)
from ..knowledge_scope import _agent_knowledge_scope, _scope_upload_ids_to_uuids
from ..knowledge_search_fusion import _fuse_batched_search_runs
from ..rag_observability import build_query_scope_observability, build_retrieval_observability
from ..search_cursor import (
    _decode_search_cursor,
    _encode_search_cursor,
    _resolve_search_cursor_from_handle,
    _search_cursor_cache_key,
    _search_cursor_ttl_seconds,
    _store_search_cursor_handle,
)
from ..tool_definitions import (
    DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
    MCP_PROMPT_MAX_SNIPPETS_CAP,
    READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
    SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT,
    SEARCH_PREFETCH_ABSOLUTE_CAP,
)
from ..tool_runtime_helpers import _bounded_cache_store, _search_cache_key
from ..types import SearchBudgetExceeded, ToolExecutionContext, ToolRateLimitExceeded


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _knowledge_service() -> KnowledgeSearchService:
    try:
        from apps.mcp import tools as mcp_tools

        override = getattr(mcp_tools, "_knowledge_service", None)
        if override is not None and override is not _knowledge_service:
            return override()
    except Exception:
        pass
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
    feature_state = FeatureFlagService.snapshot(conversation.business_profile)
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
        if not exclude_seen:
            return set()
        try:
            shown = context.get_all_shown_this_conversation()
            chunk_ids = shown.get("chunk_ids", set())
            if isinstance(chunk_ids, set):
                return {str(cid) for cid in chunk_ids if cid}
        except Exception:
            pass
        # Best-effort fallback.
        return {
            str(cid)
            for cid in (getattr(context, "seen_chunk_ids", set()) | getattr(context, "newly_shown_chunk_ids", set()))
            if cid
        }

    def _page_snippets(
        snippets: Sequence[Mapping[str, object]],
        *,
        offset: int,
        page_size: int,
        excluded_chunk_ids: set[str],
    ) -> tuple[list[dict[str, object]], int, bool, int]:
        out: list[dict[str, object]] = []
        excluded = 0
        idx = max(0, int(offset))
        size = max(1, int(page_size))

        def _chunk_id(entry: Mapping[str, object]) -> str:
            return str(entry.get("chunk_id") or entry.get("id") or "").strip()

        while idx < len(snippets) and len(out) < size:
            entry = snippets[idx]
            idx += 1
            if not isinstance(entry, Mapping):
                continue
            cid = _chunk_id(entry)
            if cid and cid in excluded_chunk_ids:
                excluded += 1
                continue
            out.append(dict(entry))

        has_more = False
        if idx < len(snippets):
            if not excluded_chunk_ids:
                has_more = True
            else:
                for j in range(idx, len(snippets)):
                    entry = snippets[j]
                    if not isinstance(entry, Mapping):
                        continue
                    cid = _chunk_id(entry)
                    if cid and cid not in excluded_chunk_ids:
                        has_more = True
                        break

        return out, idx, has_more, excluded

    def _read_budget_for_refs(refs: Sequence[Mapping[str, object]]) -> dict[str, int] | None:
        if not refs:
            return None
        total_suggested = 0
        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            try:
                total_suggested += int(ref.get("read_chars") or 0)
            except (TypeError, ValueError):
                continue
        max_chars_allowed = int(READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX)
        return {
            "suggested_chars": min(int(total_suggested), int(max_chars_allowed)),
            "max_chars": int(max_chars_allowed),
        }

    def _extract_agentic_manifests(
        refs: Sequence[Mapping[str, object]],
    ) -> dict[str, dict[str, object]]:
        """
        Persist only the manifests needed to resolve table_chunk refs.

        These manifests live on ToolExecutionContext and are populated by
        _convert_to_agentic_search_response. Cursor paging needs them to be
        present so read_knowledge can hydrate table anchors without requiring a re-search.
        """
        table_manifests: dict[str, dict[str, object]] = {}
        table_cache = getattr(context, "table_row_anchor_manifests", None)
        if not isinstance(table_cache, dict):
            return table_manifests

        for ref in refs:
            if not isinstance(ref, Mapping):
                continue
            kind = str(ref.get("kind") or "").strip().lower()
            ref_id = str(ref.get("id") or "").strip()
            if not ref_id:
                continue
            if kind == "table_chunk" and isinstance(table_cache, dict):
                manifest = table_cache.get(ref_id)
                if isinstance(manifest, Mapping):
                    table_manifests[ref_id] = dict(manifest)
        return table_manifests

    def _enforce_search_rate_limit() -> Mapping[str, object] | None:
        window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
        try:
            calls_per_minute = int(getattr(settings, "MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE", 120) or 0)
        except (TypeError, ValueError):
            calls_per_minute = 120
        calls_per_minute = 0 if calls_per_minute < 0 else calls_per_minute
        try:
            enforce_tool_rate_limit(
                business_profile=conversation.business_profile,
                tool="search_knowledge",
                rate_limit=ToolRateLimit(
                    calls_per_minute=None if calls_per_minute <= 0 else calls_per_minute,
                    window_seconds=window_seconds,
                    scope="business",
                ),
            )
        except ToolRateLimitExceeded as exc:
            return {
                "tool": "search_knowledge",
                "status": "throttled",
                "error": "rate_limited",
                "error_code": "rate_limited",
                "snippets": [],
                "throttle_notice": {"type": "rate_limited", "message": str(exc)},
            }
        return None

    raw_cursor = _coerce_str(arguments.get("cursor")).strip()
    if raw_cursor:
        resolved_cursor = _resolve_search_cursor_from_handle(context, conversation, raw_cursor) or raw_cursor
        # Cursor paging: bypass duplicate-intent reuse and serve next page from server cache.
        limited = _enforce_search_rate_limit()
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
                read_budget = _read_budget_for_refs(refs_page)
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

        excluded_chunk_ids = _excluded_chunk_ids()
        page_snippets, next_offset, has_more, excluded_count = _page_snippets(
            results_full,
            offset=offset,
            page_size=page_size,
            excluded_chunk_ids=excluded_chunk_ids,
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

    # Build queries list.
    # - `query` is the primary, single-query interface (back-compat and simpler).
    # - `queries[]` allows multiple variants for fanout.
    raw_queries_param = arguments.get("queries")
    raw_query_param = _coerce_str(arguments.get("query")).strip()
    queries: list[str] = []
    seen_queries: set[str] = set()

    def _append_query(candidate: str) -> None:
        normalized = candidate.strip()
        if not normalized:
            return
        lowered = normalized.lower()
        if lowered in seen_queries:
            return
        seen_queries.add(lowered)
        queries.append(normalized)

    if raw_query_param:
        _append_query(raw_query_param)
    if isinstance(raw_queries_param, (list, tuple)):
        for candidate in raw_queries_param:
            candidate_str = _coerce_str(candidate).strip()
            if candidate_str:
                _append_query(candidate_str)

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
        if len(values) <= query_variant_limit:
            return list(values)
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in values:
            normalized = entry.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(entry)
            if len(deduped) >= query_variant_limit:
                break
        if not deduped and values:
            deduped.append(values[0])
        return deduped

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

    def _normalize_intent_text(values: Sequence[str]) -> str:
        parts: list[str] = []
        seen: set[str] = set()
        for raw in values:
            token = str(raw or "").strip().lower()
            if not token:
                continue
            if token in seen:
                continue
            seen.add(token)
            parts.append(token)
        return " | ".join(parts)

    def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float | None:
        if not a or not b:
            return None
        if len(a) != len(b):
            return None
        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for x, y in zip(a, b, strict=False):
            try:
                xf = float(x)
                yf = float(y)
            except (TypeError, ValueError):
                return None
            dot += xf * yf
            norm_a += xf * xf
            norm_b += yf * yf
        if norm_a <= 0.0 or norm_b <= 0.0:
            return None
        return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))

    def _response_top_ids(response: Mapping[str, object], *, top_k: int) -> list[str]:
        ids: list[str] = []
        refs = response.get("refs")
        if isinstance(refs, list):
            for entry in refs:
                if not isinstance(entry, Mapping):
                    continue
                ref_id = str(entry.get("evidence_group_id") or entry.get("id") or "").strip()
                if ref_id:
                    ids.append(ref_id)
                if len(ids) >= top_k:
                    break
            return ids[:top_k]

        snippets_local = response.get("snippets")
        if isinstance(snippets_local, list):
            for entry in snippets_local:
                if not isinstance(entry, Mapping):
                    continue
                ref_id = str(
                    entry.get("evidence_group_id")
                    or entry.get("chunk_id")
                    or entry.get("id")
                    or ""
                ).strip()
                if ref_id:
                    ids.append(ref_id)
                if len(ids) >= top_k:
                    break
        return ids[:top_k]

    def _response_result_fingerprint(response: Mapping[str, object], *, top_k: int) -> tuple[str, list[str]]:
        top_ids = _response_top_ids(response, top_k=top_k)
        if not top_ids:
            return "", []
        digest = hashlib.sha256("|".join(top_ids).encode("utf-8")).hexdigest()[:16]
        return digest, top_ids

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

    intent_text = _normalize_intent_text(queries)
    intent_embedding: list[float] | None = None
    duplicate_intent_diagnostics: dict[str, object] | None = None
    if new_contract_enabled:
        embedder = _portal_file_embedding_service()
        if embedder and intent_text:
            try:
                embedded = embedder.embed_text(intent_text)
                if isinstance(embedded, list) and embedded:
                    intent_embedding = [float(v) for v in embedded]
            except Exception:
                intent_embedding = None

        history = getattr(context, "search_history", None) or []
        best_match: Mapping[str, object] | None = None
        best_similarity: float | None = None
        if intent_text and isinstance(history, list) and history:
            # Only compare against a small recent window to avoid unbounded work.
            for entry in reversed(history[-12:]):
                if not isinstance(entry, Mapping):
                    continue
                prior_response = entry.get("response")
                if not isinstance(prior_response, Mapping):
                    continue
                prior_intent = str(entry.get("intent") or entry.get("query") or "").strip().lower()
                if not prior_intent:
                    continue

                similarity: float | None = None
                if intent_embedding is not None and embedder:
                    prior_embedding = entry.get("embedding")
                    if not isinstance(prior_embedding, list) or not prior_embedding:
                        try:
                            embedded = embedder.embed_text(prior_intent)
                            if isinstance(embedded, list) and embedded:
                                prior_embedding = [float(v) for v in embedded]
                                # Cache embedding for future comparisons (not returned to the LLM).
                                try:
                                    entry["embedding"] = prior_embedding
                                except Exception:
                                    pass
                        except Exception:
                            prior_embedding = None
                    if isinstance(prior_embedding, list) and prior_embedding:
                        similarity = _cosine_similarity(intent_embedding, prior_embedding)

                if similarity is None:
                    similarity = 1.0 if prior_intent == intent_text else 0.0

                if best_similarity is None or similarity > best_similarity:
                    best_similarity = similarity
                    best_match = entry

            if best_match is not None and best_similarity is not None and best_similarity >= 0.85:
                duplicate_intent_diagnostics = {
                    "duplicate_intent_similarity": round(float(best_similarity), 4),
                    "duplicate_intent_query": str(best_match.get("intent") or best_match.get("query") or "").strip(),
                }

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
        # Agentic contract: intent classification is advisory (routing/logging),
        # and must not override the caller's explicit result size request.
        limit_val = base_limit
        if limit_val is not None:
            limit_val = max(1, min(int(limit_val), MCP_PROMPT_MAX_SNIPPETS_CAP))
        return limit_val

    def _log_search_performance(
        *,
        snippets: Sequence[Mapping[str, object]],
        diagnostics: Mapping[str, object] | None,
        intent: str | None,
        limit_value: int | None,
        status: str,
        note: str | None = None,
    ) -> None:
        diag = dict(diagnostics or {})
        snippet_count = len(snippets)
        diag.setdefault("snippet_count", snippet_count)
        warn_ms = int(getattr(settings, "MCP_SLO_SEARCH_WARN_MS", 1200) or 0)
        detail = {
            "status": status,
            "intent": intent,
            "path": diag.get("path"),
            "snippet_count": snippet_count,
            "limit": limit_value,
            "agent_scope_mode": agent_scope.mode,
            "agent_scope_explicit_uploads": len(agent_scope.explicit_upload_ids) if agent_scope.restricted else None,
            "effective_scope_uploads": len(combined_upload_ids) if combined_upload_ids is not None else None,
            "total_ms": diag.get("total_duration_ms"),
            "alias_ms": diag.get("alias_duration_ms"),
            "vector_ms": diag.get("vector_duration_ms"),
            "lexical_ms": diag.get("fts_duration_ms"),
            "rerank_ms": diag.get("rerank_duration_ms"),
            "table_ms": diag.get("table_duration_ms"),
            "table_context_ms": diag.get("table_context_ms"),
            "table_presence_ms": diag.get("table_presence_ms"),
            "chunk_candidates": diag.get("chunk_candidate_count"),
            "alias_hits": diag.get("alias_hits"),
            "table_reason": diag.get("table_reason"),
            "cache_hit": diag.get("cache_hit"),
            "cache_scope": diag.get("cache_scope"),
            "read_required": sum(1 for payload in snippets if isinstance(payload, Mapping) and payload.get("read_required")),
        }
        if note:
            detail["note"] = note
        total_ms = detail.get("total_ms")
        slow = bool(
            warn_ms
            and isinstance(total_ms, (int, float))
            and float(total_ms) >= float(warn_ms)
        )
        if slow:
            detail["slo"] = "slow"
            detail["slo_warn_ms"] = warn_ms
        structured_log(
            "mcp",
            "search.performance",
            detail,
            context={
                "business": conversation.business_profile_id,
                "conversation": conversation.id,
            },
            level=logging.WARNING if slow else logging.INFO,
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

        read_required = False
        read_required_reasons_summary: set[str] = set()
        for payload in snippet_payloads:
            payload_read_required, reasons = _compute_read_required(payload)
            payload["read_required"] = payload_read_required
            if reasons:
                payload["read_required_reasons"] = reasons
                read_required_reasons_summary.update(reasons)
            if payload_read_required:
                read_required = True
            chunk_id = payload.get("chunk_id") or payload.get("id")
            upload_id = payload.get("upload_id")
            chunk_index = payload.get("chunk_index")

            # FIXED: Use actual page number from metadata, not chunk_index + 1
            # Per Codex review: text.page now means PDF page number, not chunk index
            payload_meta = payload.get("metadata") or {}
            if isinstance(payload_meta, dict):
                actual_page = (
                    payload_meta.get("table_page_number") or
                    payload_meta.get("chunk_page") or
                    payload_meta.get("page_number") or
                    payload.get("page_number")
                )
            else:
                actual_page = payload.get("page_number")

            is_table_payload = bool(
                payload.get("is_table_chunk")
                or payload.get("structured_table_count")
                or payload.get("table_read_only")
                or (isinstance(payload_meta, dict) and payload_meta.get("is_table_chunk"))
            )
            read_id = str(chunk_id or "").strip() if is_table_payload else str(upload_id or chunk_id or "").strip()
            # Build read_hint with page (if known) or offset (for chunk-based access)
            mode_hint = "full_page" if is_table_payload else ("full_page" if intent == "identifier" else "excerpt")
            read_hint: dict[str, object] = {
                # For table chunks, prefer the chunk id so readers can upgrade to full-page table content.
                "document_id": read_id,
                "mode": mode_hint,
            }

            if actual_page:
                try:
                    page_num = int(actual_page)
                    if page_num >= 1:
                        read_hint["page"] = page_num
                except (TypeError, ValueError):
                    pass

            # Fallback: use offset if no page number known
            if "page" not in read_hint and isinstance(chunk_index, int):
                read_hint["offset"] = chunk_index

            payload["read_hint"] = read_hint
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

    primary_run = runs[0]
    limit_cap = primary_run.get("limit_used")
    clip_limit = int(limit_cap) if isinstance(limit_cap, int) and limit_cap > 0 else None
    deduped_snippets, fusion = _fuse_batched_search_runs(
        runs,
        clip_limit=clip_limit,
    )

    results_full = deduped_snippets
    total_found = len(results_full)

    excluded_chunk_ids = _excluded_chunk_ids()
    page_snippets, next_offset, has_more, excluded_count = _page_snippets(
        results_full,
        offset=0,
        page_size=page_size,
        excluded_chunk_ids=excluded_chunk_ids,
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

    def _normalized_run_status(run: Mapping[str, object] | None) -> str:
        if not isinstance(run, Mapping):
            return ""
        return str(run.get("status") or "").strip().lower()

    # Agentic RAG should not block on "needs_clarification". Always return best-effort evidence
    # (if any) and let the assistant handle ambiguity/conflicts in the response.
    status_source_run: Mapping[str, object] = next(
        (run for run in runs if _normalized_run_status(run) == "ok"),
        primary_run,
    )
    if page_snippets:
        final_status = "ok"
    else:
        non_default_status_run = next(
            (
                run
                for run in runs
                if _normalized_run_status(run) not in {"", "not_found", "needs_clarification"}
            ),
            None,
        )
        if non_default_status_run is not None:
            status_source_run = non_default_status_run
            final_status = _normalized_run_status(non_default_status_run)
        else:
            status_source_run = runs[-1]
            final_status = _normalized_run_status(status_source_run) or "not_found"
            if final_status == "needs_clarification":
                final_status = "not_found"

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
                manifests_table = _extract_agentic_manifests(refs_full)
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
            read_budget = _read_budget_for_refs(refs_page)
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

    if isinstance(final_response, dict):
        diagnostics_out = final_response.get("diagnostics")
        if not isinstance(diagnostics_out, dict):
            diagnostics_out = {}
        diagnostics_out.setdefault("query_scope", dict(query_scope_observability))
        retrieval_observability = build_retrieval_observability(
            query_scope=query_scope_observability,
            diagnostics=diagnostics_out,
            completeness=(
                final_response.get("completeness")
                if isinstance(final_response.get("completeness"), Mapping)
                else {}
            ),
            refs=final_response.get("refs"),
            snippets=final_response.get("snippets"),
            status=str(final_response.get("status") or final_status or ""),
        )
        if retrieval_observability:
            final_response["retrieval_observability"] = retrieval_observability
            diagnostics_out["retrieval_observability"] = retrieval_observability
        if diagnostics_out:
            final_response["diagnostics"] = diagnostics_out
        structured_log(
            "mcp",
            "search.observability",
            retrieval_observability,
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )

    result_fingerprint = ""
    result_top_ids: list[str] = []
    if isinstance(final_response, Mapping):
        result_fingerprint, result_top_ids = _response_result_fingerprint(
            final_response,
            top_k=result_fingerprint_top_k,
        )

    duplicate_result_diagnostics: dict[str, object] | None = None
    if new_contract_enabled and result_fingerprint:
        history = getattr(context, "search_history", None) or []
        if isinstance(history, list) and history:
            fingerprint_match: Mapping[str, object] | None = None
            for entry in reversed(history[-12:]):
                if not isinstance(entry, Mapping):
                    continue
                prior_response = entry.get("response")
                if not isinstance(prior_response, Mapping):
                    continue
                prior_fingerprint = str(entry.get("result_fingerprint") or "").strip()
                prior_top_ids_raw = entry.get("result_top_ids")
                if not prior_fingerprint:
                    prior_fingerprint, prior_top_ids = _response_result_fingerprint(
                        prior_response,
                        top_k=result_fingerprint_top_k,
                    )
                    prior_top_ids_raw = prior_top_ids
                if prior_fingerprint != result_fingerprint:
                    continue
                normalized_prior_top_ids = [
                    str(value).strip()
                    for value in (prior_top_ids_raw if isinstance(prior_top_ids_raw, list) else [])
                    if str(value).strip()
                ][:result_fingerprint_top_k]
                if normalized_prior_top_ids and normalized_prior_top_ids != result_top_ids[:result_fingerprint_top_k]:
                    continue
                fingerprint_match = entry
                break

            if fingerprint_match is not None:
                duplicate_result_diagnostics = {
                    "duplicate_result_fingerprint": result_fingerprint,
                    "duplicate_result_top_ids": result_top_ids[:result_fingerprint_top_k],
                }

    if isinstance(final_response, dict):
        diagnostics = final_response.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
        if duplicate_intent_diagnostics:
            diagnostics.update(duplicate_intent_diagnostics)
            try:
                similarity_value = duplicate_intent_diagnostics.get("duplicate_intent_similarity")
                similarity_float = float(similarity_value) if similarity_value is not None else None
            except (TypeError, ValueError):
                similarity_float = None
            final_response["search_repeat_guidance"] = build_repeat_search_guidance(
                context,
                similarity=similarity_float,
            )
        if duplicate_result_diagnostics:
            diagnostics.update(duplicate_result_diagnostics)
        if diagnostics:
            final_response["diagnostics"] = diagnostics

    try:
        history_entry = {
            "intent": intent_text,
            "embedding": intent_embedding,
            "response": copy.deepcopy(final_response),
            "result_fingerprint": result_fingerprint,
            "result_top_ids": list(result_top_ids[:result_fingerprint_top_k]),
        }
        context.search_history.append(history_entry)
        if len(context.search_history) > 25:
            context.search_history = context.search_history[-25:]
    except Exception:
        pass

    return final_response
