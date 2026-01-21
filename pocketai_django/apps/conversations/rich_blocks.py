from __future__ import annotations

import copy
import re
import uuid
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from django.utils import timezone

INLINE_NODE_LIMIT = 800
INLINE_TEXT_LIMIT = 800
BLOCK_LIMIT = 400
CODE_TEXT_LIMIT = 4000

ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}
ALLOWED_BLOCK_TYPES = {"paragraph", "heading", "list", "list_item", "quote", "code_block"}
ALLOWED_INLINE_MARKS = {"bold", "italic", "code"}
ALLOWED_BLOCK_OPS = {"append_inline", "append_code"}


def new_block_id() -> str:
    return f"blk_{uuid.uuid4().hex}"


def sanitize_href(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme and parsed.scheme.lower() in ALLOWED_LINK_SCHEMES:
        return raw
    return None


def _append_inline_node(nodes: list[dict[str, object]], text: str, marks: list[object] | None = None) -> None:
    if not text:
        return
    if len(text) > INLINE_TEXT_LIMIT:
        text = text[:INLINE_TEXT_LIMIT]
    node: dict[str, object] = {"text": text}
    if marks:
        node["marks"] = marks
    nodes.append(node)


def _merge_inline_nodes(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for node in nodes:
        if not node or "text" not in node:
            continue
        text = str(node.get("text") or "")
        if not text:
            continue
        marks = node.get("marks")
        if merged:
            prev = merged[-1]
            if prev.get("marks") == marks:
                prev["text"] = f"{prev.get('text', '')}{text}"
                continue
        merged.append({"text": text, **({"marks": marks} if marks else {})})
    return merged


def parse_inline_nodes(text: str) -> list[dict[str, object]]:
    raw = (text or "")
    if not raw:
        return []
    nodes: list[dict[str, object]] = []
    i = 0
    length = len(raw)
    while i < length:
        next_marker = length
        for marker in ("`", "[", "*"):
            idx = raw.find(marker, i)
            if idx != -1 and idx < next_marker:
                next_marker = idx
        if next_marker == length:
            _append_inline_node(nodes, raw[i:])
            break
        if next_marker > i:
            _append_inline_node(nodes, raw[i:next_marker])
            i = next_marker
        if i >= length:
            break
        ch = raw[i]
        if ch == "`":
            end = raw.find("`", i + 1)
            if end != -1:
                _append_inline_node(nodes, raw[i + 1 : end], marks=["code"])
                i = end + 1
                continue
            _append_inline_node(nodes, raw[i])
            i += 1
            continue
        if ch == "[":
            close = raw.find("]", i + 1)
            if close != -1 and close + 1 < length and raw[close + 1] == "(":
                end = raw.find(")", close + 2)
                if end != -1:
                    label = raw[i + 1 : close]
                    href_raw = raw[close + 2 : end]
                    href = sanitize_href(href_raw)
                    if href and label:
                        _append_inline_node(nodes, label, marks=[{"type": "link", "href": href}])
                    else:
                        _append_inline_node(nodes, raw[i : end + 1])
                    i = end + 1
                    continue
            _append_inline_node(nodes, raw[i])
            i += 1
            continue
        if ch == "*":
            if raw.startswith("**", i):
                end = raw.find("**", i + 2)
                if end != -1:
                    _append_inline_node(nodes, raw[i + 2 : end], marks=["bold"])
                    i = end + 2
                    continue
            end = raw.find("*", i + 1)
            if end != -1:
                _append_inline_node(nodes, raw[i + 1 : end], marks=["italic"])
                i = end + 1
                continue
            _append_inline_node(nodes, raw[i])
            i += 1
            continue
        _append_inline_node(nodes, raw[i])
        i += 1
    merged = _merge_inline_nodes(nodes)
    if len(merged) > INLINE_NODE_LIMIT:
        return merged[:INLINE_NODE_LIMIT]
    return merged


def coerce_inline_nodes(value: object) -> list[dict[str, object]]:
    if isinstance(value, str):
        text_value = value
        if len(text_value) > INLINE_TEXT_LIMIT:
            text_value = text_value[:INLINE_TEXT_LIMIT]
        return [{"text": text_value}] if text_value else []
    if not isinstance(value, list):
        return []
    nodes: list[dict[str, object]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        text_value = entry.get("text")
        if not isinstance(text_value, str) or not text_value:
            continue
        if len(text_value) > INLINE_TEXT_LIMIT:
            text_value = text_value[:INLINE_TEXT_LIMIT]
        marks_out: list[object] = []
        marks = entry.get("marks")
        if isinstance(marks, list):
            for mark in marks:
                if isinstance(mark, str) and mark in ALLOWED_INLINE_MARKS:
                    marks_out.append(mark)
                elif isinstance(mark, Mapping) and str(mark.get("type") or "") == "link":
                    href = sanitize_href(str(mark.get("href") or ""))
                    if href:
                        marks_out.append({"type": "link", "href": href})
        node: dict[str, object] = {"text": text_value}
        if marks_out:
            node["marks"] = marks_out
        nodes.append(node)
        if len(nodes) >= INLINE_NODE_LIMIT:
            break
    return _merge_inline_nodes(nodes)


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
    if block_type == "code_block":
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
                if len(content) >= INLINE_NODE_LIMIT:
                    break
                if isinstance(node, Mapping) and isinstance(node.get("text"), str) and node.get("text"):
                    content.append(dict(node))
            payload["content"] = content
        elif op_type == "append_code":
            text_value = op.get("text")
            if not isinstance(text_value, str) or not text_value:
                continue
            existing = payload.get("code")
            payload["code"] = f"{existing or ''}{text_value}"
    block["payload"] = payload


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


class RichBlockStreamBuilder:
    def __init__(self) -> None:
        self.blocks: list[dict[str, object]] = []
        self.blocks_by_id: dict[str, dict[str, object]] = {}
        self.pending_line = ""
        self.active_paragraph_id: str | None = None
        self.active_paragraph_parent: str | None = None
        self.active_list_id: str | None = None
        self.active_list_parent: str | None = None
        self.active_list_ordered: bool | None = None
        self.active_quote_id: str | None = None
        self.in_code_block = False
        self.active_code_block_id: str | None = None

    def snapshot(self) -> list[dict[str, object]]:
        return [dict(block) for block in self.blocks]

    def append_external_block(self, block: dict[str, object]) -> None:
        block_id = str(block.get("block_id") or "").strip()
        if not block_id:
            block_id = new_block_id()
            block["block_id"] = block_id
        self.blocks.append(block)
        self.blocks_by_id[block_id] = block

    def feed_text(self, chunk: str) -> list[dict[str, object]]:
        if not chunk:
            return []
        self.pending_line = f"{self.pending_line}{chunk}"
        events: list[dict[str, object]] = []
        while "\n" in self.pending_line:
            line, remainder = self.pending_line.split("\n", 1)
            self.pending_line = remainder
            events.extend(self._process_line(line, line_ended=True))
        return events

    def finalize(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        if self.pending_line:
            events.extend(self._process_line(self.pending_line, line_ended=False))
            self.pending_line = ""
        if self.in_code_block and self.active_code_block_id:
            events.append({"type": "block_end", "payload": {"block_id": self.active_code_block_id}})
            self.in_code_block = False
            self.active_code_block_id = None
        events.extend(self._close_paragraph())
        self._close_list()
        self.active_quote_id = None
        return events

    def break_flow(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        if self.pending_line:
            events.extend(self._process_line(self.pending_line, line_ended=False))
            self.pending_line = ""
        events.extend(self._close_paragraph())
        self._close_list()
        return events

    def _register_block(self, block: dict[str, object]) -> dict[str, object]:
        if len(self.blocks) >= BLOCK_LIMIT:
            return block
        self.blocks.append(block)
        self.blocks_by_id[str(block.get("block_id") or "")] = block
        return block

    def _start_block(self, block_type: str, payload: Mapping[str, object] | None = None, parent: str | None = None) -> dict[str, object]:
        block = _make_block(block_type, payload, parent_block_id=parent)
        self._register_block(block)
        return block

    def _append_inline(self, block_id: str, nodes: Iterable[dict[str, object]]) -> list[dict[str, object]]:
        block = self.blocks_by_id.get(block_id)
        if not block:
            return []
        payload = block.get("payload")
        payload_out: dict[str, object] = dict(payload) if isinstance(payload, Mapping) else {}
        content_raw = payload_out.get("content")
        content: list[dict[str, object]] = list(content_raw) if isinstance(content_raw, list) else []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if len(content) >= INLINE_NODE_LIMIT:
                break
            text_value = node.get("text")
            if not isinstance(text_value, str) or not text_value:
                continue
            content.append(node)
        payload_out["content"] = content
        block["payload"] = payload_out
        return [{"op": "append_inline", "nodes": list(nodes)}]

    def _append_code(self, block_id: str, text: str) -> list[dict[str, object]]:
        block = self.blocks_by_id.get(block_id)
        if not block or not text:
            return []
        payload = block.get("payload")
        payload_out: dict[str, object] = dict(payload) if isinstance(payload, Mapping) else {}
        existing = payload_out.get("code")
        existing_text = existing if isinstance(existing, str) else ""
        payload_out["code"] = f"{existing_text}{text}"
        block["payload"] = payload_out
        return [{"op": "append_code", "text": text}]

    def _process_line(self, line: str, *, line_ended: bool) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        line = line.rstrip("\r")
        if self.in_code_block:
            if line.strip().startswith("```"):
                if self.active_code_block_id:
                    events.append({"type": "block_end", "payload": {"block_id": self.active_code_block_id}})
                self.in_code_block = False
                self.active_code_block_id = None
                return events
            if self.active_code_block_id:
                text = f"{line}\n" if line_ended else line
                ops = self._append_code(self.active_code_block_id, text)
                if ops:
                    events.append({"type": "block_delta", "payload": {"block_id": self.active_code_block_id, "ops": ops}})
            return events

        raw = line
        if raw.strip().startswith("```"):
            fence = raw.strip()[3:]
            language = fence.strip() if fence.strip() else ""
            events.extend(self._close_paragraph())
            self._close_list()
            code_block = self._start_block("code_block", {"language": language, "code": ""}, parent=self.active_quote_id)
            self.in_code_block = True
            self.active_code_block_id = str(code_block.get("block_id") or "")
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(code_block)}})
            return events

        parent_id = None
        content = raw
        stripped = raw.lstrip()
        if stripped.startswith(">"):
            content = stripped[1:]
            if content.startswith(" "):
                content = content[1:]
            if not self.active_quote_id:
                quote_block = self._start_block("quote", {})
                self.active_quote_id = str(quote_block.get("block_id") or "")
                events.append({"type": "block_start", "payload": {"block": copy.deepcopy(quote_block)}})
            parent_id = self.active_quote_id
        else:
            if self.active_quote_id is not None:
                events.extend(self._close_paragraph())
                self._close_list()
            self.active_quote_id = None

        if not content.strip():
            events.extend(self._close_paragraph())
            self._close_list()
            return events

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", content)
        if heading_match:
            events.extend(self._close_paragraph())
            self._close_list()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            heading_block = self._start_block("heading", {"level": level, "content": []}, parent=parent_id)
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(heading_block)}})
            nodes = parse_inline_nodes(heading_text)
            ops = self._append_inline(str(heading_block.get("block_id") or ""), nodes)
            if ops:
                events.append({"type": "block_delta", "payload": {"block_id": heading_block.get("block_id"), "ops": ops}})
            events.append({"type": "block_end", "payload": {"block_id": heading_block.get("block_id")}})
            return events

        list_match = re.match(r"^(\s*)([-*+])\s+(.*)$", content)
        ordered_match = re.match(r"^(\s*)(\d+)\.\s+(.*)$", content)
        if list_match or ordered_match:
            events.extend(self._close_paragraph())
            ordered = bool(ordered_match)
            item_text = ordered_match.group(3) if ordered_match else list_match.group(3)
            list_parent = parent_id
            if (
                not self.active_list_id
                or self.active_list_ordered != ordered
                or self.active_list_parent != list_parent
            ):
                list_payload: dict[str, object] = {"ordered": ordered}
                if ordered_match:
                    try:
                        list_payload["start"] = int(ordered_match.group(2))
                    except (TypeError, ValueError):
                        pass
                list_block = self._start_block("list", list_payload, parent=list_parent)
                self.active_list_id = str(list_block.get("block_id") or "")
                self.active_list_ordered = ordered
                self.active_list_parent = list_parent
                events.append({"type": "block_start", "payload": {"block": copy.deepcopy(list_block)}})
            item_block = self._start_block("list_item", {"content": []}, parent=self.active_list_id)
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(item_block)}})
            nodes = parse_inline_nodes(item_text)
            ops = self._append_inline(str(item_block.get("block_id") or ""), nodes)
            if ops:
                events.append({"type": "block_delta", "payload": {"block_id": item_block.get("block_id"), "ops": ops}})
            events.append({"type": "block_end", "payload": {"block_id": item_block.get("block_id")}})
            return events

        self._close_list()
        paragraph, is_new_paragraph = self._ensure_paragraph(parent_id)
        paragraph_id = str(paragraph.get("block_id") or "")
        if is_new_paragraph:
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(paragraph)}})
        nodes = parse_inline_nodes(content)
        ops_nodes = []
        if self._paragraph_has_content(paragraph_id):
            ops_nodes.append({"text": "\n"})
        ops_nodes.extend(nodes)
        ops = self._append_inline(paragraph_id, ops_nodes)
        if ops:
            events.append({"type": "block_delta", "payload": {"block_id": paragraph_id, "ops": ops}})
        return events

    def _ensure_paragraph(self, parent_id: str | None) -> tuple[dict[str, object], bool]:
        if self.active_paragraph_id and self.active_paragraph_parent == parent_id:
            existing = self.blocks_by_id.get(self.active_paragraph_id)
            if existing:
                return existing, False
        paragraph = self._start_block("paragraph", {"content": []}, parent=parent_id)
        self.active_paragraph_id = str(paragraph.get("block_id") or "")
        self.active_paragraph_parent = parent_id
        return paragraph, True

    def _paragraph_has_content(self, paragraph_id: str) -> bool:
        block = self.blocks_by_id.get(paragraph_id)
        if not block:
            return False
        payload = block.get("payload")
        if not isinstance(payload, Mapping):
            return False
        content = payload.get("content")
        return isinstance(content, list) and len(content) > 0

    def _close_paragraph(self) -> list[dict[str, object]]:
        if not self.active_paragraph_id:
            self.active_paragraph_parent = None
            return []
        paragraph_id = self.active_paragraph_id
        self.active_paragraph_id = None
        self.active_paragraph_parent = None
        return [{"type": "block_end", "payload": {"block_id": paragraph_id}}]

    def _close_list(self) -> None:
        self.active_list_id = None
        self.active_list_parent = None
        self.active_list_ordered = None


def rich_blocks_from_text(text: str) -> list[dict[str, object]]:
    builder = RichBlockStreamBuilder()
    builder.feed_text(text or "")
    builder.finalize()
    return builder.snapshot()
