"""
Knowledge result shaping helpers for MCP search/read tools.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from apps.conversations.models import Conversation
from apps.knowledge.models import KnowledgeUpload
from apps.rag.knowledge_payloads import serialize_knowledge_snippet
from apps.rag.knowledge_search import KnowledgeSearchService

from .types import ToolExecutionContext


def _detect_full_page_intent(
    conversation: Conversation,
    requested_mode: str | None,
    *,
    document_entry: Mapping[str, object] | None = None,
    upload: KnowledgeUpload | None = None,
) -> bool:
    """
    Decide whether to allow a full-page read. Prefers excerpts unless:
    - The visitor explicitly asks for a page/section.
    - The referenced table is small (few rows/columns) and not truncated.
    """

    def _coerce_int(value: object) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    if requested_mode:
        return requested_mode.strip().lower() == "full_page"

    latest = (
        conversation.messages.order_by("-sent_at", "-created_at")
        .first()
    )
    body = latest.body if latest else ""
    text = (body or "").lower()
    explicit_keywords = (
        "full page",
        "entire page",
        "whole page",
        "page ",
        "صفحة",
        "sheet",
        "section",
        "document",
        "pdf",
    )
    if any(keyword in text for keyword in explicit_keywords):
        return True

    diag = document_entry.get("source_diagnostics") if isinstance(document_entry, Mapping) else {}
    truncated = bool(diag.get("table_truncated"))
    total_rows = _coerce_int(diag.get("table_total_rows") or diag.get("table_indexed_rows"))
    if not total_rows and upload:
        metadata = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
        table_stats = metadata.get("table_stats") if isinstance(metadata, Mapping) else None
        if isinstance(table_stats, Mapping):
            total_rows = _coerce_int(table_stats.get("total_rows"))
            truncated = truncated or bool(table_stats.get("partial_index"))

    if total_rows and total_rows <= 20 and not truncated:
        return True

    return False


def _budget_allows_full_page(
    context: ToolExecutionContext,
    *,
    business_profile,
    service: KnowledgeSearchService,
) -> bool:
    if context.char_budget_per_turn is None:
        return True
    inline_cap = service.inline_char_limit_for_business(business_profile)
    remaining = max(0, context.char_budget_per_turn - context.characters_used)
    threshold = max(800, int(inline_cap * 0.6))
    return remaining >= threshold


def _apply_seen_item_filter(
    snippets: list[dict[str, object]],
    context: ToolExecutionContext,
    mark_as_seen: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Compute seen-item metadata without filtering results.

    We track which items were previously shown so the LLM can decide
    whether to repeat or summarize. We no longer suppress repeats.
    """
    if not snippets:
        return snippets, {"shown": 0, "total_found": 0, "already_seen": 0, "has_more": False}

    already_seen_count = 0
    for snippet in snippets:
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        if not chunk_id:
            continue
        if context.is_chunk_seen(chunk_id):
            already_seen_count += 1
        if mark_as_seen:
            context.mark_chunk_shown(chunk_id)

    completeness = {
        "shown": len(snippets),
        "total_found": len(snippets),
        "already_seen": already_seen_count,
        "has_more": False,
    }

    return snippets, completeness


def _mark_snippets_as_seen(snippets: list[dict[str, object]], context: ToolExecutionContext) -> None:
    """Mark snippets as seen after they've been finalized for the response."""
    for snippet in snippets:
        chunk_id = str(snippet.get("chunk_id") or snippet.get("id") or "")
        if chunk_id:
            context.mark_chunk_shown(chunk_id)


def _apply_seen_row_filter(
    rows: list[dict[str, object]],
    document_id: str,
    context: ToolExecutionContext,
    mark_as_seen: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Compute seen-row metadata without filtering results."""
    if not rows:
        return rows, {"shown": 0, "total_found": 0, "already_seen": 0, "has_more": False}

    already_seen_count = 0
    for row in rows:
        row_index = row.get("row_index")
        if row_index is None:
            continue
        if context.is_row_seen(document_id, row_index):
            already_seen_count += 1
        if mark_as_seen:
            context.mark_row_shown(document_id, row_index)

    completeness = {
        "shown": len(rows),
        "total_found": len(rows),
        "already_seen": already_seen_count,
        "has_more": False,
    }

    return rows, completeness


def _mark_rows_as_seen(rows: list[dict[str, object]], document_id: str, context: ToolExecutionContext) -> None:
    """Mark rows as seen after they've been finalized for the response."""
    for row in rows:
        row_index = row.get("row_index")
        if row_index is not None:
            context.mark_row_shown(document_id, row_index)


def _serialize_snippets(snippets: Sequence[object]) -> list[dict[str, object]]:
    """
    Convert KnowledgeSnippet instances into prompt/diagnostic-friendly dicts.
    """

    payloads: list[dict[str, object]] = []
    for snippet in snippets:
        try:
            payloads.append(serialize_knowledge_snippet(snippet))
        except Exception:
            continue
    seen: set[tuple[str, str | None]] = set()
    deduped: list[dict[str, object]] = []
    for entry in payloads:
        evidence_group_id = str(entry.get("evidence_group_id") or "").strip()
        chunk_id = str(entry.get("chunk_id") or "").strip()
        entry_id = str(entry.get("id") or "").strip()
        upload_id = str(entry.get("upload_id") or "").strip() or None
        identity = evidence_group_id or chunk_id or entry_id
        if not identity:
            deduped.append(entry)
            continue
        key = (identity, upload_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _sanitize_snippet_payloads_for_prompt(
    snippet_payloads: Sequence[Mapping[str, object]],
    *,
    conversation: Conversation,
) -> list[dict[str, object]]:
    if not snippet_payloads:
        return []
    sanitized: list[dict[str, object]] = []
    for payload in snippet_payloads:
        if not isinstance(payload, Mapping):
            continue
        out = dict(payload)
        # Never pass extracted identifier values to the LLM; they can leak user data.
        out.pop("identifiers", None)
        sanitized.append(out)
    return sanitized


def _estimate_tokens(characters: int) -> int:
    """
    Rough heuristic to approximate tokens for logging purposes.
    """

    if characters <= 0:
        return 0
    return max(1, math.ceil(characters / 4))
