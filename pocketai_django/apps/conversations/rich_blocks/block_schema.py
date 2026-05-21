from __future__ import annotations

import copy
import uuid
from typing import Iterable, Mapping

from django.utils import timezone

from .inline import coerce_inline_nodes

INLINE_NODE_LIMIT = 800
INLINE_TEXT_LIMIT = 800
CODE_TEXT_LIMIT = 4000

ALLOWED_BLOCK_TYPES = {"paragraph", "heading", "list", "list_item", "quote", "code_block", "table"}
ALLOWED_BLOCK_OPS = {"append_inline", "append_code", "append_table_row", "set_table_cell_text"}

TABLE_CELL_LIMIT = 12
TABLE_ROW_LIMIT = 60


def new_block_id() -> str:
    return f"blk_{uuid.uuid4().hex}"

def _make_block(
    block_type: str,
    payload: Mapping[str, object] | None = None,
    *,
    block_id: str | None = None,
    created_at: str | None = None,
    parent_block_id: str | None = None,
) -> dict[str, object]:
    block: dict[str, object] = {
        "block_id": block_id or new_block_id(),
        "type": block_type,
        "created_at": created_at or timezone.now().isoformat(),
        "payload": dict(payload or {}),
    }
    if parent_block_id:
        block["parent_block_id"] = parent_block_id
    return block


def coerce_block(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    block_type = str(value.get("type") or "").strip().lower()
    if block_type not in ALLOWED_BLOCK_TYPES:
        return None
    block_id = str(value.get("block_id") or value.get("blockId") or "").strip() or new_block_id()
    created_at = value.get("created_at")
    created_at_value = created_at if isinstance(created_at, str) and created_at else timezone.now().isoformat()
    payload_raw = value.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    payload_out: dict[str, object] = {}
    if block_type == "table":
        columns = payload.get("columns", value.get("columns"))
        rows = payload.get("rows", value.get("rows"))
        if not isinstance(columns, list) or not columns:
            return None
        payload_out["columns"] = copy.deepcopy(columns[:TABLE_CELL_LIMIT])
        payload_out["rows"] = copy.deepcopy(rows[:TABLE_ROW_LIMIT]) if isinstance(rows, list) else []
        title = payload.get("title", value.get("title"))
        if isinstance(title, str) and title.strip():
            payload_out["title"] = title.strip()
        note = payload.get("note", value.get("note"))
        if isinstance(note, str) and note.strip():
            payload_out["note"] = note.strip()
    elif block_type == "code_block":
        code_value = payload.get("code", value.get("code"))
        if isinstance(code_value, str) and len(code_value) > CODE_TEXT_LIMIT:
            code_value = code_value[:CODE_TEXT_LIMIT]
        payload_out["code"] = code_value if isinstance(code_value, str) else ""
        language = payload.get("language", value.get("language"))
        if isinstance(language, str) and language.strip():
            payload_out["language"] = language.strip()
    else:
        content_value = payload.get("content", value.get("content"))
        if content_value is None:
            content_value = payload.get("text", value.get("text"))
        nodes = coerce_inline_nodes(content_value)
        if nodes:
            payload_out["content"] = nodes
        if block_type == "heading":
            level = payload.get("level", value.get("level"))
            if isinstance(level, int) and 1 <= level <= 6:
                payload_out["level"] = level
        if block_type == "list":
            ordered = payload.get("ordered", value.get("ordered"))
            if isinstance(ordered, bool):
                payload_out["ordered"] = ordered
    block: dict[str, object] = {
        "block_id": block_id,
        "type": block_type,
        "created_at": created_at_value,
        "payload": payload_out,
    }
    parent_block_id = value.get("parent_block_id") or value.get("parentBlockId")
    if isinstance(parent_block_id, str) and parent_block_id.strip():
        block["parent_block_id"] = parent_block_id.strip()
    return block


def coerce_block_ops(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    ops_out: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        op_type = str(entry.get("op") or entry.get("type") or "").strip().lower()
        if op_type not in ALLOWED_BLOCK_OPS:
            continue
        if op_type == "append_inline":
            nodes_source = entry.get("nodes")
            if nodes_source is None:
                nodes_source = entry.get("content")
            if nodes_source is None:
                nodes_source = entry.get("text")
            nodes = coerce_inline_nodes(nodes_source)
            if not nodes:
                continue
            ops_out.append({"op": "append_inline", "nodes": nodes})
        elif op_type == "append_code":
            text_value = entry.get("text") if entry.get("text") is not None else entry.get("code")
            if not isinstance(text_value, str) or not text_value:
                continue
            if len(text_value) > CODE_TEXT_LIMIT:
                text_value = text_value[:CODE_TEXT_LIMIT]
            ops_out.append({"op": "append_code", "text": text_value})
        elif op_type == "append_table_row":
            cells_raw = entry.get("cells") if entry.get("cells") is not None else entry.get("row")
            if not isinstance(cells_raw, list):
                continue
            cells: list[str] = []
            for cell in cells_raw[:TABLE_CELL_LIMIT]:
                if isinstance(cell, str):
                    cells.append(cell)
                elif cell is None:
                    cells.append("")
                else:
                    cells.append(str(cell))
            if not cells:
                continue
            row_out: dict[str, object] = {"cells": cells}
            rtl = entry.get("rtl")
            if isinstance(rtl, bool):
                row_out["rtl"] = rtl
            ops_out.append({"op": "append_table_row", **row_out})
        elif op_type == "set_table_cell_text":
            try:
                row_index = int(entry.get("row_index"))
                cell_index = int(entry.get("cell_index"))
            except (TypeError, ValueError):
                continue
            if row_index < 0 or row_index >= TABLE_ROW_LIMIT or cell_index < 0 or cell_index >= TABLE_CELL_LIMIT:
                continue
            text_value = entry.get("text")
            if text_value is None:
                text_out = ""
            elif isinstance(text_value, str):
                text_out = text_value
            else:
                text_out = str(text_value)
            ops_out.append(
                {
                    "op": "set_table_cell_text",
                    "row_index": row_index,
                    "cell_index": cell_index,
                    "text": text_out,
                    **({"row_complete": True} if entry.get("row_complete") is True else {}),
                }
            )
    return ops_out


def coerce_block_event(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    event_type = str(value.get("type") or "").strip().lower()
    payload = value.get("payload") if isinstance(value.get("payload"), Mapping) else {}
    if event_type == "block_start":
        block_raw = value.get("block") or payload.get("block")
        block = coerce_block(block_raw)
        if not block:
            return None
        return {"type": "block_start", "payload": {"block": block}}
    if event_type == "block_delta":
        block_id = str(
            value.get("block_id")
            or value.get("blockId")
            or payload.get("block_id")
            or payload.get("blockId")
            or ""
        ).strip()
        if not block_id:
            return None
        ops_raw = value.get("ops") or payload.get("ops")
        ops = coerce_block_ops(ops_raw)
        if not ops:
            return None
        return {"type": "block_delta", "payload": {"block_id": block_id, "ops": ops}}
    if event_type == "block_end":
        block_id = str(
            value.get("block_id")
            or value.get("blockId")
            or payload.get("block_id")
            or payload.get("blockId")
            or ""
        ).strip()
        if not block_id:
            return None
        return {"type": "block_end", "payload": {"block_id": block_id}}
    return None


def apply_block_ops(block: dict[str, object], ops: Iterable[Mapping[str, object]]) -> None:
    if not block or not isinstance(block, Mapping):
        return
    payload_raw = block.get("payload")
    payload: dict[str, object] = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    for op in ops:
        if not isinstance(op, Mapping):
            continue
        op_type = str(op.get("op") or "").strip().lower()
        if op_type == "append_inline":
            nodes = op.get("nodes")
            if not isinstance(nodes, list) or not nodes:
                continue
            content_raw = payload.get("content")
            content: list[dict[str, object]] = list(content_raw) if isinstance(content_raw, list) else []
            for node in nodes:
                if not isinstance(node, Mapping):
                    continue
                text_value = node.get("text")
                if not isinstance(text_value, str) or not text_value:
                    continue
                marks = node.get("marks")
                if content and content[-1].get("marks") == marks:
                    prev_text = str(content[-1].get("text") or "")
                    merged_text = f"{prev_text}{text_value}"
                    if len(merged_text) <= INLINE_TEXT_LIMIT:
                        content[-1]["text"] = merged_text
                        continue
                    remaining = text_value
                    space = INLINE_TEXT_LIMIT - len(prev_text)
                    if space > 0:
                        content[-1]["text"] = f"{prev_text}{text_value[:space]}"
                        remaining = text_value[space:]
                    while remaining:
                        if len(content) >= INLINE_NODE_LIMIT:
                            break
                        chunk = remaining[:INLINE_TEXT_LIMIT]
                        out_node: dict[str, object] = {"text": chunk}
                        if marks:
                            out_node["marks"] = copy.deepcopy(marks)
                        content.append(out_node)
                        remaining = remaining[INLINE_TEXT_LIMIT:]
                    continue
                if len(content) >= INLINE_NODE_LIMIT:
                    break
                out_node: dict[str, object] = {"text": text_value}
                if marks:
                    out_node["marks"] = copy.deepcopy(marks)
                content.append(out_node)
            payload["content"] = content
        elif op_type == "append_code":
            text_value = op.get("text")
            if not isinstance(text_value, str) or not text_value:
                continue
            existing = payload.get("code")
            payload["code"] = f"{existing or ''}{text_value}"
        elif op_type == "append_table_row":
            cells_raw = op.get("cells")
            if not isinstance(cells_raw, list) or not cells_raw:
                continue
            rows_raw = payload.get("rows")
            rows: list[dict[str, object]] = list(rows_raw) if isinstance(rows_raw, list) else []
            row_out: dict[str, object] = {"cells": list(cells_raw[:TABLE_CELL_LIMIT])}
            rtl = op.get("rtl")
            if isinstance(rtl, bool):
                row_out["rtl"] = rtl
            rows.append(row_out)
            payload["rows"] = rows[:TABLE_ROW_LIMIT]
        elif op_type == "set_table_cell_text":
            try:
                row_index = int(op.get("row_index"))
                cell_index = int(op.get("cell_index"))
            except (TypeError, ValueError):
                continue
            if row_index < 0 or row_index >= TABLE_ROW_LIMIT or cell_index < 0 or cell_index >= TABLE_CELL_LIMIT:
                continue
            rows_raw = payload.get("rows")
            rows: list[dict[str, object]] = list(rows_raw) if isinstance(rows_raw, list) else []
            while len(rows) <= row_index and len(rows) < TABLE_ROW_LIMIT:
                rows.append({"cells": []})
            if row_index >= len(rows):
                continue
            row_payload = dict(rows[row_index]) if isinstance(rows[row_index], Mapping) else {"cells": []}
            cells_raw = row_payload.get("cells")
            cells: list[str] = list(cells_raw) if isinstance(cells_raw, list) else []
            while len(cells) <= cell_index and len(cells) < TABLE_CELL_LIMIT:
                cells.append("")
            if cell_index >= len(cells):
                continue
            text_value = op.get("text")
            if text_value is None:
                text_out = ""
            elif isinstance(text_value, str):
                text_out = text_value
            else:
                text_out = str(text_value)
            cells[cell_index] = text_out
            row_payload["cells"] = cells[:TABLE_CELL_LIMIT]
            rtl = op.get("rtl")
            if isinstance(rtl, bool):
                row_payload["rtl"] = rtl
            rows[row_index] = row_payload
            payload["rows"] = rows[:TABLE_ROW_LIMIT]
    block["payload"] = payload
