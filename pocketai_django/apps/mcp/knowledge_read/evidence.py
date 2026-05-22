from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping

from ..types import ToolExecutionContext


@dataclass(slots=True)
class AppendReadEvidenceResult:
    content_added: bool
    item_chars: int
    remaining_chars: int
    overflow_remaining: int


def append_read_evidence(
    *,
    context: ToolExecutionContext,
    contents: list[dict[str, object]],
    read: list[dict[str, object]],
    deferred: list[dict[str, object]],
    item_id: str,
    upload_id: str,
    title: str,
    payload_type: str,
    evidence_kind: str,
    payload: dict[str, object],
    complete: bool,
    cursor_used: str | None,
    next_cursor: str | None,
    evidence_coverage_hint: dict[str, object] | None,
    max_chars_allowed: int,
    remaining_chars: int,
    overflow_remaining: int,
    store_cursor_handle: Callable[[str | None], str | None],
) -> AppendReadEvidenceResult:
    # If we couldn't read anything for this item, mark as deferred rather than returning empty content.
    has_payload = False
    if payload_type == "table":
        rows_value = payload.get("rows")
        has_payload = isinstance(rows_value, list) and bool(rows_value)
    else:
        text_value = payload.get("text")
        has_payload = isinstance(text_value, str) and bool(text_value)
    if not has_payload:
        if not complete:
            # Budget was too small to fit even one row/block; data exists but didn't fit.
            deferred.append(
                {
                    "id": item_id,
                    "reason": "budget_too_small",
                    "hint": "Budget too small to fit content. Retry with a higher max_chars (at least the suggested_max_chars from search).",
                }
            )
        else:
            deferred.append(
                {
                    "id": item_id,
                    "reason": "empty",
                    "hint": "No readable content was available for this item.",
                }
            )
        read.append({"id": item_id, "status": "deferred"})
        return AppendReadEvidenceResult(
            content_added=False,
            item_chars=0,
            remaining_chars=remaining_chars,
            overflow_remaining=overflow_remaining,
        )

    try:
        if payload_type == "table":
            item_chars = len(json.dumps(payload, ensure_ascii=False, default=str))
        else:
            item_chars = len(str(payload.get("text") or ""))
    except Exception:
        item_chars = 0

    overflow_used = max(0, item_chars - max(0, int(remaining_chars)))
    if overflow_used:
        overflow_remaining = max(0, int(overflow_remaining) - int(overflow_used))
    remaining_chars = max(0, remaining_chars - item_chars)

    table_more_rows_available = bool(
        payload_type == "table"
        and isinstance(payload.get("next_row_start"), (int, float))
        and not bool(next_cursor)
    )

    evidence_entry: dict[str, object] = {
        "id": item_id,
        "document_id": upload_id,
        "title": title,
        "type": payload_type,
        "kind": evidence_kind,
        "payload": payload,
        "chars": item_chars,
        "complete": bool(complete and not next_cursor),
        # Back-compat: orchestrator coverage ledger expects "truncated" on items.
        # For paged table reads, "more rows available" is not the same as a risky
        # truncation; keep completion false but avoid overstating truncation.
        "truncated": bool((not complete or bool(next_cursor)) and not table_more_rows_available),
    }
    if table_more_rows_available:
        evidence_entry["more_rows_available"] = True
    if isinstance(evidence_coverage_hint, Mapping) and evidence_coverage_hint:
        evidence_entry["coverage_hint"] = dict(evidence_coverage_hint)
    if cursor_used:
        evidence_entry["cursor_used"] = cursor_used
    next_cursor_handle = store_cursor_handle(next_cursor) if next_cursor else None
    if next_cursor_handle:
        evidence_entry["next_cursor"] = next_cursor_handle
    contents.append(evidence_entry)
    context.add_read_evidence(evidence_entry)

    is_truncated = not complete or bool(next_cursor_handle)
    read_entry: dict[str, object] = {
        "id": item_id,
        "status": "full" if not is_truncated else ("more_available" if table_more_rows_available else "truncated"),
        "chars": item_chars,
    }
    if is_truncated:
        # Build informative hint so the LLM can decide whether to follow up.
        hint_parts: list[str] = []
        if payload_type == "table":
            next_row_start = payload.get("next_row_start")
            if isinstance(next_row_start, (int, float)):
                if table_more_rows_available:
                    hint_parts.append(f"More rows available with row_start={int(next_row_start)} if needed.")
                else:
                    hint_parts.append(f"Continue with row_start={int(next_row_start)}.")
            hint_parts.append(f"Max chars allowed: {int(max_chars_allowed)}.")
        else:
            hint_parts.append(
                f"Only use next_cursor if you still need data not yet returned. "
                f"Max chars allowed: {int(max_chars_allowed)}."
            )
        read_entry["hint"] = " ".join(hint_parts)
    read.append(read_entry)

    return AppendReadEvidenceResult(
        content_added=True,
        item_chars=item_chars,
        remaining_chars=remaining_chars,
        overflow_remaining=overflow_remaining,
    )
