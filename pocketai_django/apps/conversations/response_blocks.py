from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

MAX_TEXT_LINES = 32
MAX_TEXT_LENGTH = 800
MAX_TABLE_COLUMNS = 12
MAX_TABLE_ROWS = 60
MAX_KV_ENTRIES = 60
MAX_KV_KEY_LENGTH = 80
MAX_KV_VALUE_LENGTH = 480


def normalize_response_blocks(value: object) -> tuple[dict[str, object], ...]:
    """
    Convert arbitrary model output into a sanitized block payload.

    The normalizer tolerates providers returning raw JSON strings, tuples, or
    partially-formed dicts and trims them down to frontend-friendly objects.
    """

    raw_blocks = _coerce_block_list(value)
    normalized: list[dict[str, object]] = []
    for block in raw_blocks:
        normalized_block = _normalize_block(block)
        if normalized_block:
            normalized.append(normalized_block)
    return tuple(normalized)


def _coerce_block_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        return _coerce_block_list(parsed)
    if isinstance(value, Mapping):
        for key in ("response_blocks", "blocks", "items"):
            nested = value.get(key)
            if nested is not None:
                return _coerce_block_list(nested)
        return []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _normalize_block(block: object) -> dict[str, object] | None:
    if not isinstance(block, Mapping):
        return None
    block_type = _clean_text(
        block.get("type") or block.get("block_type"),
        lower=True,
        limit=64,
    )
    if not block_type:
        return None
    if block_type in {"text", "text_section", "paragraph"}:
        return _normalize_text_block(block)
    if block_type in {"table", "table_section"}:
        return _normalize_table_block(block)
    if block_type in {"kv", "key_value", "key_values", "kv_section"}:
        return _normalize_kv_block(block)
    return None


def _normalize_text_block(block: Mapping[str, object]) -> dict[str, object] | None:
    body_lines = _normalize_text_lines(
        block.get("body_md")
        or block.get("body")
        or block.get("lines")
        or block.get("paragraphs")
        or block.get("text")
    )
    if not body_lines:
        return None
    normalized: dict[str, object] = {"type": "text", "body_md": body_lines}
    heading = _clean_text(block.get("heading") or block.get("title"))
    if heading:
        normalized["heading"] = heading
    rtl = _coerce_bool(block.get("rtl") or block.get("direction"))
    if rtl is not None:
        normalized["rtl"] = rtl
    return normalized


def _normalize_text_lines(value: object) -> list[str]:
    lines: list[str] = []
    if isinstance(value, str):
        text = _clean_text(value)
        if text:
            lines.append(text)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for entry in value:
            text = _clean_text(entry)
            if text:
                lines.append(text)
            if len(lines) >= MAX_TEXT_LINES:
                break
    return lines[:MAX_TEXT_LINES]


def _normalize_table_block(block: Mapping[str, object]) -> dict[str, object] | None:
    columns = _normalize_table_columns(block.get("columns"))
    if not columns:
        return None
    rows = _normalize_table_rows(block.get("rows"), len(columns))
    if not rows:
        return None
    normalized: dict[str, object] = {
        "type": "table",
        "columns": columns,
        "rows": rows,
    }
    title = _clean_text(block.get("title") or block.get("heading"))
    if title:
        normalized["title"] = title
    note = _clean_text(block.get("note") or block.get("summary"))
    if note:
        normalized["note"] = note
    rtl = _coerce_bool(block.get("rtl"))
    if rtl is not None:
        normalized["rtl"] = rtl
    return normalized


def _normalize_table_columns(value: object) -> list[dict[str, object]]:
    columns: list[dict[str, object]] = []
    iterable: Sequence[object]
    if isinstance(value, str):
        iterable = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        iterable = value
    else:
        return columns
    for index, entry in enumerate(iterable):
        label = ""
        align: str | None = None
        key: str | None = None
        if isinstance(entry, Mapping):
            label = _clean_text(
                entry.get("label")
                or entry.get("title")
                or entry.get("text")
                or entry.get("value")
            )
            align_candidate = _clean_text(entry.get("align"), lower=True, limit=6)
            if align_candidate in {"left", "center", "right"}:
                align = align_candidate
            key_candidate = _clean_identifier(entry.get("key"))
            if key_candidate:
                key = key_candidate
        else:
            label = _clean_text(entry)
        if not label:
            continue
        column_entry: dict[str, object] = {"key": key or f"col_{index}", "label": label}
        if align:
            column_entry["align"] = align
        columns.append(column_entry)
        if len(columns) >= MAX_TABLE_COLUMNS:
            break
    return columns


def _normalize_table_rows(value: object, column_count: int) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, object]] = []
    for entry in value:
        cells = _normalize_row_cells(entry, column_count)
        if not cells:
            continue
        row_entry: dict[str, object] = {"cells": cells}
        if isinstance(entry, Mapping):
            rtl = _coerce_bool(entry.get("rtl"))
            if rtl is not None:
                row_entry["rtl"] = rtl
        rows.append(row_entry)
        if len(rows) >= MAX_TABLE_ROWS:
            break
    return rows


def _normalize_row_cells(entry: object, column_count: int) -> list[str]:
    if isinstance(entry, Mapping):
        candidate = entry.get("cells") or entry.get("values")
        if candidate is None:
            candidate = entry.get("row")
    else:
        candidate = entry
    if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes, bytearray)):
        iterable = candidate
    else:
        iterable = [candidate]
    cells: list[str] = []
    for cell in iterable:
        text: Any
        if isinstance(cell, Mapping):
            text = cell.get("value") or cell.get("text") or cell.get("label") or cell.get("display")
        else:
            text = cell
        cells.append(_clean_text(text))
        if column_count and len(cells) >= column_count:
            break
    if column_count:
        while len(cells) < column_count:
            cells.append("")
        cells = cells[:column_count]
    # Drop trailing empty rows
    if not any(cell for cell in cells):
        return []
    return cells


def _normalize_kv_block(block: Mapping[str, object]) -> dict[str, object] | None:
    entries = _normalize_kv_entries(
        block.get("entries") or block.get("items") or block.get("pairs") or block.get("data") or block.get("values")
    )
    if not entries:
        return None
    normalized: dict[str, object] = {"type": "kv", "entries": entries}
    title = _clean_text(block.get("title") or block.get("heading"))
    if title:
        normalized["title"] = title
    note = _clean_text(block.get("note") or block.get("summary"))
    if note:
        normalized["note"] = note
    rtl = _coerce_bool(block.get("rtl"))
    if rtl is not None:
        normalized["rtl"] = rtl
    return normalized


def _normalize_kv_entries(value: object) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    if value is None:
        return items
    if isinstance(value, Mapping):
        iterable: list[object] = []
        for key, raw_val in list(value.items())[:MAX_KV_ENTRIES]:
            iterable.append({"key": key, "value": raw_val})
        value = iterable
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return items
    for entry in value:
        key: str | None = None
        val: str | None = None
        if isinstance(entry, Mapping):
            key = _clean_text(entry.get("key") or entry.get("name") or entry.get("label"), limit=MAX_KV_KEY_LENGTH)
            val = _clean_text(
                entry.get("value") or entry.get("text") or entry.get("display") or entry.get("content"),
                limit=MAX_KV_VALUE_LENGTH,
            )
        elif isinstance(entry, Sequence) and not isinstance(entry, (str, bytes, bytearray)) and len(entry) >= 2:
            key = _clean_text(entry[0], limit=MAX_KV_KEY_LENGTH)
            val = _clean_text(entry[1], limit=MAX_KV_VALUE_LENGTH)
        elif isinstance(entry, str):
            line = entry.strip()
            if not line:
                continue
            if ":" in line:
                left, right = line.split(":", 1)
                key = _clean_text(left, limit=MAX_KV_KEY_LENGTH)
                val = _clean_text(right, limit=MAX_KV_VALUE_LENGTH)
        if not key:
            continue
        items.append({"key": key, "value": val or ""})
        if len(items) >= MAX_KV_ENTRIES:
            break
    return items


def _coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _clean_text(value: object, *, lower: bool = False, limit: int = MAX_TEXT_LENGTH) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if lower:
        text = text.lower()
    return text[:limit]


def _clean_identifier(value: object) -> str | None:
    if value is None:
        return None
    text = "".join(ch for ch in str(value).strip() if ch.isalnum() or ch in {"_", "-"})
    return text[:48] or None
