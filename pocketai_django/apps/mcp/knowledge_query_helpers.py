"""
Query intent and read-sufficiency helpers for MCP knowledge tools.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence

from .types import ToolExecutionContext


def _query_intent(query: str) -> dict[str, object]:
    """
    Lightweight heuristic to classify the query and suggest search/read defaults.
    """
    text = (query or "").strip()
    lowered = text.lower()
    tokens = [t for t in re.split(r"[^a-z0-9]+", lowered) if t]
    has_digits = any(ch.isdigit() for ch in text)
    identifier_like = has_digits or any(sym in text for sym in ("-", "_"))
    table_hit = any(
        kw in lowered
        for kw in (
            "table",
            "sheet",
            "csv",
            "grid",
            "column",
            "row",
            "spreadsheet",
            "report",
            "statement",
        )
    )
    length = len(tokens)
    if identifier_like and length <= 6:
        intent = "identifier"
    elif table_hit:
        intent = "table"
    elif length >= 14:
        intent = "long"
    else:
        intent = "short"
    return {
        "intent": intent,
        "tokens": length,
        "has_digits": has_digits,
        "table": table_hit,
    }


def _snippet_text_for_sufficiency(payload: Mapping[str, object]) -> str:
    for key in ("content", "summary", "snippet", "preview", "text", "raw_text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _snippet_has_truncation(payload: Mapping[str, object]) -> bool:
    if payload.get("truncated") or payload.get("partial_index") or payload.get("truncation_note"):
        return True
    diagnostics = payload.get("source_diagnostics")
    if not isinstance(diagnostics, Mapping):
        return False
    return any(
        diagnostics.get(key)
        for key in (
            "table_truncated",
            "truncated_rows",
            "truncated_columns",
            "truncated_tables",
            "truncated_entities",
            "table_partial_tables",
            "partial_index",
        )
    )


def _snippet_has_table_truncation(payload: Mapping[str, object]) -> bool:
    diagnostics = payload.get("source_diagnostics")
    if not isinstance(diagnostics, Mapping):
        return False
    return any(
        diagnostics.get(key)
        for key in (
            "table_truncated",
            "truncated_rows",
            "truncated_columns",
            "truncated_tables",
            "table_partial_tables",
        )
    )


def _compute_read_required(
    payload: Mapping[str, object],
) -> tuple[bool, list[str]]:
    read_state = str(payload.get("read_state") or "summary").strip().lower()
    if read_state not in {"summary", "preview"}:
        return False, []

    reasons: list[str] = []
    snippet_text = _snippet_text_for_sufficiency(payload)
    if not snippet_text:
        reasons.append("preview_empty")

    if _snippet_has_truncation(payload):
        reasons.append("content_truncated")

    if _snippet_has_table_truncation(payload):
        reasons.append("table_truncated")

    return bool(reasons), reasons


def _match_knowledge_entry(
    context: ToolExecutionContext | None,
    identifiers: Sequence[str],
) -> Mapping[str, object] | None:
    """
    Locate snippet metadata associated with the requested chunk/upload.
    """

    if not context or not identifiers:
        return None
    normalized = {str(value).strip().lower() for value in identifiers if value}
    if not normalized:
        return None
    for entry in getattr(context, "knowledge_results", []):
        if not isinstance(entry, Mapping):
            continue
        candidate_ids = {
            str(entry.get("chunk_id") or "").strip().lower(),
            str(entry.get("upload_id") or "").strip().lower(),
            str(entry.get("id") or "").strip().lower(),
        }
        if normalized & {cid for cid in candidate_ids if cid}:
            return entry
    return None
