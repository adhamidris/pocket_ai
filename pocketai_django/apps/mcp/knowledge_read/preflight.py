from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from django.conf import settings

from ..types import ToolExecutionContext


@dataclass(slots=True)
class AgenticReadPreflight:
    ordered_items: list[dict[str, object]]
    prompt_output_limit: int
    max_chars_allowed: int
    max_chars: int
    overflow_margin: int
    overflow_remaining: int
    mode: str
    response: dict[str, object] | None = None


def prepare_agentic_read_request(
    *,
    arguments: Mapping[str, object],
    context: ToolExecutionContext,
) -> AgenticReadPreflight:
    raw_items = arguments.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return AgenticReadPreflight(
            ordered_items=[],
            prompt_output_limit=12000,
            max_chars_allowed=0,
            max_chars=0,
            overflow_margin=0,
            overflow_remaining=0,
            mode="auto",
            response={
                "tool": "read_knowledge",
                "status": "error",
                "error": "missing_items",
                "error_code": "missing_items",
                "evidence": [],
                "hint": "refs[] is required (use ids/cursors from search_knowledge/read_knowledge).",
            },
        )

    # Per-call output cap: stay under MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS minus margin.
    try:
        raw_prompt_output_limit = getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", 12000)
        prompt_output_limit = int(raw_prompt_output_limit if raw_prompt_output_limit is not None else 12000)
    except (TypeError, ValueError):
        prompt_output_limit = 12000
    try:
        raw_safety_margin = (
            getattr(settings, "MCP_READ_KNOWLEDGE_MAX_CHARS_MARGIN", None)
            or getattr(settings, "MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", 800)
        )
        safety_margin = int(raw_safety_margin if raw_safety_margin is not None else 800)
    except (TypeError, ValueError):
        safety_margin = 800
    safe_prompt_limit = max(200, prompt_output_limit - max(0, safety_margin))

    remaining_turn_budget: int | None = None
    if context.char_budget_per_turn is not None:
        remaining_turn_budget = max(0, int(context.char_budget_per_turn) - int(context.characters_used or 0))

    max_chars_allowed = max(200, min(20000, safe_prompt_limit))
    if remaining_turn_budget is not None:
        max_chars_allowed = max(0, min(max_chars_allowed, remaining_turn_budget))
    if max_chars_allowed < 200:
        return AgenticReadPreflight(
            ordered_items=[],
            prompt_output_limit=prompt_output_limit,
            max_chars_allowed=max_chars_allowed,
            max_chars=0,
            overflow_margin=0,
            overflow_remaining=0,
            mode="auto",
            response={
                "tool": "read_knowledge",
                "status": "throttled",
                "error": "prompt_budget_exceeded",
                "error_code": "prompt_budget_exceeded",
                "evidence": [],
                "hint": "Prompt budget exceeded for this turn. Answer from collected evidence or ask a narrower question.",
            },
        )

    raw_max_chars = arguments.get("max_chars")
    try:
        requested_max_chars = int(raw_max_chars)
    except (TypeError, ValueError):
        requested_max_chars = None
    if requested_max_chars is None:
        max_chars = max_chars_allowed
    else:
        max_chars = max(200, requested_max_chars)
        if max_chars > max_chars_allowed:
            return AgenticReadPreflight(
                ordered_items=[],
                prompt_output_limit=prompt_output_limit,
                max_chars_allowed=max_chars_allowed,
                max_chars=max_chars,
                overflow_margin=0,
                overflow_remaining=0,
                mode="auto",
                response={
                    "tool": "read_knowledge",
                    "status": "constraint_error",
                    "error": "max_chars_exceeded",
                    "error_code": "max_chars_exceeded",
                    "evidence": [],
                    "requested_max_chars": max_chars,
                    "max_chars_allowed": max_chars_allowed,
                    "hint": (
                        f"max_chars is too high for this chat (requested {max_chars}, max {max_chars_allowed}). "
                        f"Retry with max_chars <= {max_chars_allowed}, or read fewer items."
                    ),
                },
            )

    try:
        raw_overflow_margin = int(getattr(settings, "MCP_READ_KNOWLEDGE_OVERFLOW_MARGIN", 200) or 0)
    except (TypeError, ValueError):
        raw_overflow_margin = 200
    overflow_margin = max(0, min(int(raw_overflow_margin), max(0, int(max_chars_allowed) - int(max_chars))))
    overflow_remaining = int(overflow_margin)

    # The backend chooses the correct representation and paging strategy.
    # `mode` is intentionally not part of the public agentic contract.
    mode = "auto"

    # Dedup items by (id,cursor) while preserving order.
    ordered_items: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str | None, int | None, int | None]] = set()
    for entry in raw_items:
        if not isinstance(entry, Mapping):
            continue
        item_id = str(entry.get("id") or "").strip()
        if not item_id:
            continue
        cursor = entry.get("cursor")
        cursor_str = str(cursor).strip() if isinstance(cursor, str) and cursor.strip() else None
        row_start_raw = entry.get("row_start")
        row_limit_raw = entry.get("row_limit")
        row_start_value: int | None = None
        if row_start_raw is not None and row_start_raw != "":
            try:
                row_start_value = max(0, int(row_start_raw))
            except (TypeError, ValueError):
                row_start_value = None
        row_limit_value: int | None = None
        if row_limit_raw is not None and row_limit_raw != "":
            try:
                row_limit_value = int(row_limit_raw)
            except (TypeError, ValueError):
                row_limit_value = None
        if row_limit_value is not None:
            row_limit_value = max(1, min(200, int(row_limit_value)))

        key = (item_id, cursor_str, row_start_value, row_limit_value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out_item: dict[str, object] = {"id": item_id, "cursor": cursor_str}
        if row_start_value is not None:
            out_item["row_start"] = row_start_value
        if row_limit_value is not None:
            out_item["row_limit"] = row_limit_value
        ordered_items.append(out_item)

    # Allow cursor-continuation reads and block non-cursor re-reads of the same ref within a turn.
    already_read_ids: set[str] = getattr(context, "read_ref_ids_this_turn", None) or set()
    filtered_items: list[dict[str, object]] = []
    blocked_items: list[dict[str, object]] = []
    for item in ordered_items:
        item_id = str(item["id"])
        has_cursor = bool(item.get("cursor"))
        has_row_start = item.get("row_start") is not None
        if item_id in already_read_ids and not has_cursor and not has_row_start:
            blocked_items.append({"id": item_id, "error": "Already read this turn. Answer from available evidence."})
        else:
            filtered_items.append(item)

    if not filtered_items:
        return AgenticReadPreflight(
            ordered_items=[],
            prompt_output_limit=prompt_output_limit,
            max_chars_allowed=max_chars_allowed,
            max_chars=max_chars,
            overflow_margin=overflow_margin,
            overflow_remaining=overflow_remaining,
            mode=mode,
            response={
                "tool": "read_knowledge",
                "status": "already_read",
                "error_code": "already_read",
                "evidence": [],
                "blocked": blocked_items,
                "hint": "All requested refs were already read this turn. Answer from the evidence you have.",
            },
        )

    if blocked_items:
        ordered_items = filtered_items

    if not ordered_items:
        return AgenticReadPreflight(
            ordered_items=[],
            prompt_output_limit=prompt_output_limit,
            max_chars_allowed=max_chars_allowed,
            max_chars=max_chars,
            overflow_margin=overflow_margin,
            overflow_remaining=overflow_remaining,
            mode=mode,
            response={
                "tool": "read_knowledge",
                "status": "error",
                "error": "missing_items",
                "error_code": "missing_items",
                "evidence": [],
                "hint": "items[] must contain at least one {id} from search_knowledge results.",
            },
        )

    return AgenticReadPreflight(
        ordered_items=ordered_items,
        prompt_output_limit=prompt_output_limit,
        max_chars_allowed=max_chars_allowed,
        max_chars=max_chars,
        overflow_margin=overflow_margin,
        overflow_remaining=overflow_remaining,
        mode=mode,
    )
