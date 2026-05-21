from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlparse

INLINE_NODE_LIMIT = 800
INLINE_TEXT_LIMIT = 800

ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}
ALLOWED_INLINE_MARKS = {"bold", "italic", "code"}


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


def parse_inline_nodes_lenient(text: str) -> list[dict[str, object]]:
    """
    Parse inline markdown with a fallback for unfinished trailing markers.

    Streaming can end with an open marker sequence (e.g. ``**bold text``) when
    the provider truncates or omits the closing token. In that case, preserve
    readable formatting instead of rendering raw marker characters.
    """

    raw = text or ""
    if not raw:
        return []

    parsed = parse_inline_nodes(raw)

    if raw.startswith("**") and raw.count("**") == 1 and len(raw) > 2:
        return [{"text": raw[2:], "marks": ["bold"]}]
    if raw.startswith("*") and not raw.startswith("**") and raw.count("*") == 1 and len(raw) > 1:
        return [{"text": raw[1:], "marks": ["italic"]}]
    if raw.startswith("`") and raw.count("`") == 1 and len(raw) > 1:
        return [{"text": raw[1:], "marks": ["code"]}]

    return parsed


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
