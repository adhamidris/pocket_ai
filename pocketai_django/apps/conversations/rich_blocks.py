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


def find_inline_safe_boundary(text: str) -> int:
    """
    Return the last index (0..len(text)) that can be safely parsed for inline marks.

    We only support a small subset of markdown inline constructs (bold/italic/code/link),
    so the "safe boundary" is when all of these constructs are closed (not mid-token).

    This powers incremental streaming: we can emit stable inline nodes without showing
    raw markdown markers, while buffering any incomplete tail until more text arrives.
    """

    if not text:
        return 0

    in_code = False
    in_bold = False
    in_italic = False
    in_link_label = False
    in_link_href = False

    last_safe = 0
    i = 0
    length = len(text)

    while i < length:
        ch = text[i]

        if in_link_label:
            if ch == "]" and i + 1 < length and text[i + 1] == "(":
                in_link_label = False
                in_link_href = True
                i += 2
                continue
            i += 1
            continue

        if in_link_href:
            if ch == ")":
                in_link_href = False
                i += 1
                if not (in_code or in_bold or in_italic or in_link_label or in_link_href):
                    last_safe = i
                continue
            i += 1
            continue

        if in_code:
            if ch == "`":
                in_code = False
            i += 1
            if not (in_code or in_bold or in_italic or in_link_label or in_link_href):
                last_safe = i
            continue

        if in_bold:
            if text.startswith("**", i):
                in_bold = False
                i += 2
                if not (in_code or in_bold or in_italic or in_link_label or in_link_href):
                    last_safe = i
                continue
            i += 1
            continue

        if in_italic:
            if ch == "*":
                in_italic = False
                i += 1
                if not (in_code or in_bold or in_italic or in_link_label or in_link_href):
                    last_safe = i
                continue
            i += 1
            continue

        # Normal state: open markers.
        if text.startswith("**", i):
            in_bold = True
            i += 2
            continue
        if ch == "*":
            in_italic = True
            i += 1
            continue
        if ch == "`":
            in_code = True
            i += 1
            continue
        if ch == "[":
            in_link_label = True
            i += 1
            continue

        i += 1
        if not (in_code or in_bold or in_italic or in_link_label or in_link_href):
            last_safe = i

    return last_safe


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
        # Streaming line state (for incremental block_delta emission before newline).
        self.line_kind: str | None = None
        self.line_parent_id: str | None = None
        self.line_block_id: str | None = None
        self.line_inline_buffer = ""
        self.active_paragraph_id: str | None = None
        self.active_paragraph_parent: str | None = None
        # Stack of nested list contexts.
        # Each entry: {"list_id": str, "parent": str|None, "ordered": bool, "indent": int, "item_id": str|None}
        self.list_stack: list[dict] = []
        self.active_quote_id: str | None = None
        self.in_code_block = False
        self.active_code_block_id: str | None = None

    # Backwards-compatible computed accessors for legacy code paths.
    @property
    def active_list_id(self) -> str | None:
        return self.list_stack[-1]["list_id"] if self.list_stack else None

    @property
    def active_list_ordered(self) -> bool | None:
        return self.list_stack[-1]["ordered"] if self.list_stack else None

    @property
    def active_list_parent(self) -> str | None:
        return self.list_stack[-1]["parent"] if self.list_stack else None

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
        events: list[dict[str, object]] = []
        parts = chunk.split("\n")
        for segment in parts[:-1]:
            events.extend(self._ingest_line_segment(segment, line_ended=True))
        # Tail segment (no newline)
        events.extend(self._ingest_line_segment(parts[-1], line_ended=False))
        return events

    def finalize(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        events.extend(self._finalize_pending_line())
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
        events.extend(self._finalize_pending_line(force=True))
        events.extend(self._close_paragraph())
        self._close_list()
        return events

    def _reset_line_state(self) -> None:
        self.line_kind = None
        self.line_parent_id = None
        self.line_block_id = None
        self.line_inline_buffer = ""

    def _append_line_inline_segment(self, segment: str) -> None:
        if not segment:
            return
        self.line_inline_buffer = f"{self.line_inline_buffer}{segment}"

    def _emit_inline_nodes(self, block_id: str, nodes: list[dict[str, object]]) -> list[dict[str, object]]:
        if not block_id or not nodes:
            return []
        ops = self._append_inline(block_id, nodes)
        if not ops:
            return []
        return [{"type": "block_delta", "payload": {"block_id": block_id, "ops": ops}}]

    def _flush_inline_buffer(self, *, final: bool) -> list[dict[str, object]]:
        if not self.line_block_id or not self.line_inline_buffer:
            return []
        buffer = self.line_inline_buffer
        safe_idx = find_inline_safe_boundary(buffer)
        events: list[dict[str, object]] = []

        # Streaming smoothness guardrail: if markdown-safe parsing cannot make progress
        # for too long, emit most of the buffer as plain text and keep only a short tail.
        # This avoids late "burst" deltas when providers emit chunks with unfinished marks.
        if safe_idx <= 0 and not final and len(buffer) >= 24:
            emit_upto = max(0, len(buffer) - 8)
            if emit_upto > 0:
                events.extend(self._emit_inline_nodes(self.line_block_id, [{"text": buffer[:emit_upto]}]))
                buffer = buffer[emit_upto:]
                safe_idx = 0

        if safe_idx > 0:
            safe_text = buffer[:safe_idx]
            nodes = parse_inline_nodes(safe_text)
            events.extend(self._emit_inline_nodes(self.line_block_id, nodes))
            buffer = buffer[safe_idx:]

        if final and buffer:
            # Emit any unfinished tail as plain text so we don't drop characters.
            events.extend(self._emit_inline_nodes(self.line_block_id, [{"text": buffer}]))
            buffer = ""

        self.line_inline_buffer = buffer
        return events

    def _ensure_line_context(self, *, line_ended: bool) -> list[dict[str, object]]:
        """
        If possible, classify the current pending_line into a block type and start it.

        For line fragments that could still become a heading/list marker, we defer
        classification until either we have enough prefix to decide or the line ends.
        """

        if self.line_kind is not None:
            return []

        events: list[dict[str, object]] = []
        raw = (self.pending_line or "").rstrip("\r")
        if not raw:
            return events

        # Code fences are line-level; only act once the line ends.
        if raw.strip().startswith("```") and not line_ended:
            return events

        parent_id: str | None = None
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

        self.line_parent_id = parent_id

        if not content.strip():
            events.extend(self._close_paragraph())
            self._close_list()
            return events

        heading_match = re.match(r"^(#{1,3})\s+(.*)$", content)
        if not line_ended and content.startswith("#") and not heading_match:
            return events

        list_match = re.match(r"^(\s*)([-*+])\s+(.*)$", content)
        ordered_match = re.match(r"^(\s*)(\d+)\.\s+(.*)$", content)
        if not line_ended and not (list_match or ordered_match):
            trimmed = content.lstrip()
            if trimmed and trimmed[0] in {"-", "*", "+"}:
                if len(trimmed) == 1 or not trimmed[1].isspace():
                    return events
            if trimmed and trimmed[0].isdigit():
                if re.match(r"^\d+\.?$", trimmed):
                    return events

        if heading_match:
            events.extend(self._close_paragraph())
            self._close_list()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            if not line_ended and not heading_text.strip():
                return events
            heading_block = self._start_block("heading", {"level": level, "content": []}, parent=parent_id)
            heading_id = str(heading_block.get("block_id") or "")
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(heading_block)}})
            self.line_kind = "heading"
            self.line_block_id = heading_id
            self.line_inline_buffer = heading_text
            return events

        if list_match or ordered_match:
            events.extend(self._close_paragraph())
            ordered = bool(ordered_match)
            match = ordered_match or list_match
            indent = len(match.group(1))
            item_text = ordered_match.group(3) if ordered_match else list_match.group(3)
            if not line_ended and not item_text.strip():
                return events

            current_indent = self.list_stack[-1]["indent"] if self.list_stack else -1
            current_ordered = self.list_stack[-1]["ordered"] if self.list_stack else None
            current_item_id = self.list_stack[-1].get("item_id") if self.list_stack else None

            if not self.list_stack or indent > current_indent:
                # Nesting deeper — new sub-list parented to current list_item (or parent_id if no stack).
                list_parent_id = current_item_id if self.list_stack else parent_id
                list_payload: dict[str, object] = {"ordered": ordered}
                if ordered_match:
                    try:
                        list_payload["start"] = int(ordered_match.group(2))
                    except (TypeError, ValueError):
                        pass
                list_block = self._start_block("list", list_payload, parent=list_parent_id)
                new_list_id = str(list_block.get("block_id") or "")
                self.list_stack.append({
                    "list_id": new_list_id,
                    "parent": list_parent_id,
                    "ordered": ordered,
                    "indent": indent,
                    "item_id": None,
                })
                events.append({"type": "block_start", "payload": {"block": copy.deepcopy(list_block)}})
            elif indent < current_indent:
                # De-indenting — pop stack to matching level.
                self._pop_list_to_depth(indent)
                if not self.list_stack or self.list_stack[-1]["ordered"] != ordered:
                    list_parent_id = parent_id
                    list_payload = {"ordered": ordered}
                    if ordered_match:
                        try:
                            list_payload["start"] = int(ordered_match.group(2))
                        except (TypeError, ValueError):
                            pass
                    list_block = self._start_block("list", list_payload, parent=list_parent_id)
                    new_list_id = str(list_block.get("block_id") or "")
                    self.list_stack.append({
                        "list_id": new_list_id,
                        "parent": list_parent_id,
                        "ordered": ordered,
                        "indent": indent,
                        "item_id": None,
                    })
                    events.append({"type": "block_start", "payload": {"block": copy.deepcopy(list_block)}})
            else:
                # Same indent — continue current list or start new if type changed.
                if self.list_stack and self.list_stack[-1]["ordered"] != ordered:
                    # Pop the current same-indent entry since the ordered type changed.
                    while self.list_stack and self.list_stack[-1]["indent"] >= indent:
                        self.list_stack.pop()
                    list_parent_id = parent_id
                    list_payload = {"ordered": ordered}
                    if ordered_match:
                        try:
                            list_payload["start"] = int(ordered_match.group(2))
                        except (TypeError, ValueError):
                            pass
                    list_block = self._start_block("list", list_payload, parent=list_parent_id)
                    new_list_id = str(list_block.get("block_id") or "")
                    self.list_stack.append({
                        "list_id": new_list_id,
                        "parent": list_parent_id,
                        "ordered": ordered,
                        "indent": indent,
                        "item_id": None,
                    })
                    events.append({"type": "block_start", "payload": {"block": copy.deepcopy(list_block)}})

            # Create list_item parented to current list.
            active = self.list_stack[-1]
            item_block = self._start_block("list_item", {"content": []}, parent=active["list_id"])
            item_id = str(item_block.get("block_id") or "")
            active["item_id"] = item_id
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(item_block)}})
            self.line_kind = "list_item"
            self.line_block_id = item_id
            self.line_inline_buffer = item_text
            return events

        # Default: paragraph line.
        self._close_list()
        paragraph, is_new_paragraph = self._ensure_paragraph(parent_id)
        paragraph_id = str(paragraph.get("block_id") or "")
        if is_new_paragraph:
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(paragraph)}})
        elif self._paragraph_has_content(paragraph_id):
            ops = self._append_inline(paragraph_id, [{"text": "\n"}])
            if ops:
                events.append({"type": "block_delta", "payload": {"block_id": paragraph_id, "ops": ops}})
        self.line_kind = "paragraph"
        self.line_block_id = paragraph_id
        self.line_inline_buffer = content
        return events

    def _ingest_line_segment(self, segment: str, *, line_ended: bool) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []

        if line_ended and segment:
            segment = segment.rstrip("\r")

        had_context = self.line_kind is not None

        if segment:
            self.pending_line = f"{self.pending_line}{segment}"
            if had_context:
                self._append_line_inline_segment(segment)

        if not self.in_code_block and line_ended and not self.pending_line and not self.line_inline_buffer:
            # Blank line: break paragraphs, lists, and quote context.
            events.extend(self._close_paragraph())
            self._close_list()
            self.active_quote_id = None
            self._reset_line_state()
            return events

        if self.in_code_block:
            if not line_ended:
                return events
            line = self.pending_line.rstrip("\r")
            self.pending_line = ""
            if line.strip().startswith("```"):
                if self.active_code_block_id:
                    events.append({"type": "block_end", "payload": {"block_id": self.active_code_block_id}})
                self.in_code_block = False
                self.active_code_block_id = None
            elif self.active_code_block_id:
                ops = self._append_code(self.active_code_block_id, f"{line}\n")
                if ops:
                    events.append({"type": "block_delta", "payload": {"block_id": self.active_code_block_id, "ops": ops}})
            self._reset_line_state()
            return events

        if line_ended and self.pending_line.strip().startswith("```"):
            raw = self.pending_line.rstrip("\r")
            self.pending_line = ""
            fence = raw.strip()[3:]
            language = fence.strip() if fence.strip() else ""
            events.extend(self._close_paragraph())
            self._close_list()
            code_block = self._start_block("code_block", {"language": language, "code": ""}, parent=self.active_quote_id)
            self.in_code_block = True
            self.active_code_block_id = str(code_block.get("block_id") or "")
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(code_block)}})
            self._reset_line_state()
            return events

        if not had_context:
            events.extend(self._ensure_line_context(line_ended=line_ended))

        if self.line_kind is not None:
            events.extend(self._flush_inline_buffer(final=line_ended))

        if line_ended:
            if self.line_kind in {"heading", "list_item"} and self.line_block_id:
                events.append({"type": "block_end", "payload": {"block_id": self.line_block_id}})
            self.pending_line = ""
            self._reset_line_state()

        return events

    def _finalize_pending_line(self, *, force: bool = False) -> list[dict[str, object]]:
        if not self.pending_line and not self.line_inline_buffer:
            return []

        events: list[dict[str, object]] = []

        if self.in_code_block:
            if self.pending_line and self.active_code_block_id:
                line = self.pending_line.rstrip("\r")
                if line.strip().startswith("```"):
                    events.append({"type": "block_end", "payload": {"block_id": self.active_code_block_id}})
                    self.in_code_block = False
                    self.active_code_block_id = None
                else:
                    ops = self._append_code(self.active_code_block_id, line)
                    if ops:
                        events.append({"type": "block_delta", "payload": {"block_id": self.active_code_block_id, "ops": ops}})
            self.pending_line = ""
            self._reset_line_state()
            return events

        events.extend(self._ensure_line_context(line_ended=True))
        if self.line_kind is not None:
            events.extend(self._flush_inline_buffer(final=True))
            if self.line_kind in {"heading", "list_item"} and self.line_block_id:
                events.append({"type": "block_end", "payload": {"block_id": self.line_block_id}})

        self.pending_line = ""
        self._reset_line_state()
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
            match = ordered_match or list_match
            indent = len(match.group(1))
            item_text = ordered_match.group(3) if ordered_match else list_match.group(3)

            current_indent = self.list_stack[-1]["indent"] if self.list_stack else -1
            current_ordered = self.list_stack[-1]["ordered"] if self.list_stack else None
            current_item_id = self.list_stack[-1].get("item_id") if self.list_stack else None

            if not self.list_stack or indent > current_indent:
                list_parent_id = current_item_id if self.list_stack else parent_id
                list_payload: dict[str, object] = {"ordered": ordered}
                if ordered_match:
                    try:
                        list_payload["start"] = int(ordered_match.group(2))
                    except (TypeError, ValueError):
                        pass
                list_block = self._start_block("list", list_payload, parent=list_parent_id)
                new_list_id = str(list_block.get("block_id") or "")
                self.list_stack.append({
                    "list_id": new_list_id,
                    "parent": list_parent_id,
                    "ordered": ordered,
                    "indent": indent,
                    "item_id": None,
                })
                events.append({"type": "block_start", "payload": {"block": copy.deepcopy(list_block)}})
            elif indent < current_indent:
                self._pop_list_to_depth(indent)
                if not self.list_stack or self.list_stack[-1]["ordered"] != ordered:
                    list_parent_id = parent_id
                    list_payload = {"ordered": ordered}
                    if ordered_match:
                        try:
                            list_payload["start"] = int(ordered_match.group(2))
                        except (TypeError, ValueError):
                            pass
                    list_block = self._start_block("list", list_payload, parent=list_parent_id)
                    new_list_id = str(list_block.get("block_id") or "")
                    self.list_stack.append({
                        "list_id": new_list_id,
                        "parent": list_parent_id,
                        "ordered": ordered,
                        "indent": indent,
                        "item_id": None,
                    })
                    events.append({"type": "block_start", "payload": {"block": copy.deepcopy(list_block)}})
            else:
                if self.list_stack and self.list_stack[-1]["ordered"] != ordered:
                    while self.list_stack and self.list_stack[-1]["indent"] >= indent:
                        self.list_stack.pop()
                    list_parent_id = parent_id
                    list_payload = {"ordered": ordered}
                    if ordered_match:
                        try:
                            list_payload["start"] = int(ordered_match.group(2))
                        except (TypeError, ValueError):
                            pass
                    list_block = self._start_block("list", list_payload, parent=list_parent_id)
                    new_list_id = str(list_block.get("block_id") or "")
                    self.list_stack.append({
                        "list_id": new_list_id,
                        "parent": list_parent_id,
                        "ordered": ordered,
                        "indent": indent,
                        "item_id": None,
                    })
                    events.append({"type": "block_start", "payload": {"block": copy.deepcopy(list_block)}})

            active = self.list_stack[-1]
            item_block = self._start_block("list_item", {"content": []}, parent=active["list_id"])
            item_id = str(item_block.get("block_id") or "")
            active["item_id"] = item_id
            events.append({"type": "block_start", "payload": {"block": copy.deepcopy(item_block)}})
            nodes = parse_inline_nodes(item_text)
            ops = self._append_inline(item_id, nodes)
            if ops:
                events.append({"type": "block_delta", "payload": {"block_id": item_id, "ops": ops}})
            events.append({"type": "block_end", "payload": {"block_id": item_id}})
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
        """Close all nested lists (clears the entire stack)."""
        self.list_stack.clear()

    def _close_all_lists(self) -> None:
        """Alias for _close_list — clears the entire stack."""
        self.list_stack.clear()

    def _pop_list_to_depth(self, indent: int) -> list[dict[str, object]]:
        """Pop stack entries whose indent is strictly greater than the given indent level."""
        events: list[dict[str, object]] = []
        while self.list_stack and self.list_stack[-1]["indent"] > indent:
            self.list_stack.pop()
        return events


def _fix_embedded_list_in_item(line: str) -> list[str]:
    """Split a list-prefixed line that contains embedded inline numbered items.

    For example:
        ``- Description includes Facebook event 5. Gas reading 6. Another``
    becomes:
        ``- Description includes Facebook event``
        ``5. Gas reading``
        ``6. Another``

    Returns ``[line]`` unchanged when no embedded items are found.
    """
    stripped = line.lstrip()
    leading_ws = line[: len(line) - len(stripped)]

    # Determine the existing list prefix and the remaining text.
    bullet_m = re.match(r"^([-*+])\s+", stripped)
    ordered_m = re.match(r"^(\d+)\.\s+", stripped)
    if bullet_m:
        prefix = stripped[: bullet_m.end()]
        text_body = stripped[bullet_m.end():]
    elif ordered_m:
        prefix = stripped[: ordered_m.end()]
        text_body = stripped[ordered_m.end():]
    else:
        return [line]

    if not text_body:
        return [line]

    # Look for embedded numbered items inside the remaining text.
    # Require at least some non-trivial text before the first embedded number
    # to avoid false positives like "- See item 3. It works great".
    embedded = list(re.finditer(r"(\d+)\.\s+", text_body))
    if not embedded:
        return [line]

    # We need either 2+ embedded numbered items, or 1 embedded number that is
    # preceded by substantial text (heuristic: >=10 chars before it).
    first_emb = None
    if len(embedded) >= 2:
        # Pick the first embedded number that has non-trivial preceding text.
        for m in embedded:
            before = text_body[: m.start()].rstrip()
            if len(before) >= 6:
                first_emb = m
                break
        if first_emb is None:
            return [line]
    elif len(embedded) == 1:
        m = embedded[0]
        before = text_body[: m.start()].rstrip()
        if len(before) < 10:
            return [line]
        first_emb = m
    else:
        return [line]

    # Split: keep original bullet with text up to the embedded number,
    # then each embedded numbered item on its own line.
    trimmed_text = text_body[: first_emb.start()].rstrip()
    result = [f"{leading_ws}{prefix}{trimmed_text}"]

    rest = text_body[first_emb.start():]
    parts = re.split(r"(\d+)\.\s+", rest)
    i = 1
    while i < len(parts):
        num = parts[i]
        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            result.append(f"{num}. {content}")
        i += 2

    return result if len(result) > 1 else [line]


def _fix_malformed_markdown(text: str) -> str:
    """
    Fix common LLM markdown formatting issues before parsing.

    Handles:
    - Inline numbered lists: "text 1. item 2. item" → "text:\n\n1. item\n2. item"
    - Inline bullet lists: "text - item - item" → "text:\n\n- item\n- item"
    - Missing blank lines before lists
    """
    if not text:
        return text

    lines = text.split("\n")
    fixed_lines: list[str] = []

    for line in lines:
        # Lines that are already list items: still check for embedded inline lists.
        stripped = line.lstrip()
        is_existing_list_item = re.match(r"^(\d+)\.\s+", stripped) or re.match(r"^[-*+]\s+", stripped)
        if is_existing_list_item:
            fixed = _fix_embedded_list_in_item(line)
            fixed_lines.extend(fixed)
            continue

        # Skip code blocks (don't modify content inside code fences)
        if stripped.startswith("```"):
            fixed_lines.append(line)
            continue

        # Fix inline numbered lists: "text 1. item 2. item 3. item"
        # Pattern: word/punctuation followed by " 1. " mid-line (not at start)
        inline_numbered_pattern = r"(\S)(\s+)(\d+)\.\s+(\S)"
        if re.search(inline_numbered_pattern, line):
            # Check if this looks like an inline list (multiple numbered items on same line)
            numbered_items = list(re.finditer(r"(\d+)\.\s+", line))
            if len(numbered_items) >= 2:
                # Multiple numbered items on one line - likely malformed list
                # Find where the list starts (first number that follows text)
                first_match = None
                for match in numbered_items:
                    # Check if there's text before this number (not just whitespace)
                    before = line[:match.start()].rstrip()
                    if before and not re.match(r"^\s*$", before):
                        first_match = match
                        break

                if first_match:
                    # Split into intro text and list items
                    intro = line[:first_match.start()].rstrip()
                    rest = line[first_match.start():]

                    # Add colon to intro if it doesn't end with punctuation
                    if intro and intro[-1] not in ":;.,!?":
                        intro = intro + ":"

                    # Split the rest into individual list items
                    items = re.split(r"(\d+)\.\s+", rest)
                    formatted_items: list[str] = []
                    i = 1
                    while i < len(items):
                        if i + 1 < len(items):
                            num = items[i]
                            content = items[i + 1].strip()
                            if content:
                                formatted_items.append(f"{num}. {content}")
                        i += 2

                    if formatted_items:
                        fixed_lines.append(intro)
                        fixed_lines.append("")  # Blank line before list
                        fixed_lines.extend(formatted_items)
                        continue

        # Fix inline bullet lists: "text - item - another item"
        # Only if there are multiple " - " patterns suggesting a list
        bullet_matches = list(re.finditer(r"\s[-*+]\s+\S", line))
        if len(bullet_matches) >= 2:
            # Check if first bullet is mid-sentence (has text before it)
            first_match = bullet_matches[0]
            before = line[:first_match.start()].strip()
            if before and not re.match(r"^[-*+]\s", before):
                # Split into intro and items
                intro = before
                rest = line[first_match.start():]

                # Add colon to intro if needed
                if intro and intro[-1] not in ":;.,!?":
                    intro = intro + ":"

                # Split by bullet markers
                items = re.split(r"\s+([-*+])\s+", rest)
                formatted_items: list[str] = []
                i = 1
                while i < len(items):
                    if i + 1 < len(items):
                        marker = items[i]
                        content = items[i + 1].strip()
                        if content:
                            formatted_items.append(f"{marker} {content}")
                    i += 2

                if formatted_items:
                    fixed_lines.append(intro)
                    fixed_lines.append("")  # Blank line before list
                    fixed_lines.extend(formatted_items)
                    continue

        # No fixes needed for this line
        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def rich_blocks_from_text(text: str) -> list[dict[str, object]]:
    # Pre-process to fix common LLM formatting issues
    fixed_text = _fix_malformed_markdown(text or "")
    builder = RichBlockStreamBuilder()
    builder.feed_text(fixed_text)
    builder.finalize()
    return builder.snapshot()
