from __future__ import annotations

import logging
from typing import Mapping, Sequence

from django.conf import settings

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log


def _effective_limit(base_limit: int | None, *, max_snippets_cap: int) -> int | None:
    # Agentic contract: intent classification is advisory (routing/logging),
    # and must not override the caller's explicit result size request.
    limit_val = base_limit
    if limit_val is not None:
        limit_val = max(1, min(int(limit_val), max_snippets_cap))
    return limit_val


def _log_search_performance(
    *,
    conversation: Conversation,
    agent_scope,
    combined_upload_ids,
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
