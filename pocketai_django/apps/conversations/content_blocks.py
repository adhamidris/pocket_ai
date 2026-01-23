from __future__ import annotations

import uuid
from typing import Any, Literal, Mapping, TypedDict

from django.utils import timezone

from .rich_blocks import rich_blocks_from_text

ContentBlockType = Literal[
    "text",
    "paragraph",
    "heading",
    "list",
    "list_item",
    "quote",
    "code_block",
    "reasoning",
    "tool_use",
    "tool_result",
    "table",
    "kv",
]


class ContentBlock(TypedDict):
    block_id: str
    type: ContentBlockType
    created_at: str
    payload: dict[str, Any]


def new_block_id() -> str:
    return f"blk_{uuid.uuid4().hex}"


def make_text_block(text: str, *, block_id: str | None = None, created_at: str | None = None) -> ContentBlock:
    return {
        "block_id": block_id or new_block_id(),
        "type": "text",
        "created_at": created_at or timezone.now().isoformat(),
        "payload": {"text": text},
    }


def make_structured_block(
    block_type: ContentBlockType,
    payload: Mapping[str, object] | None = None,
    *,
    block_id: str | None = None,
    created_at: str | None = None,
) -> ContentBlock:
    if block_type == "text":
        raise ValueError("Use make_text_block for text blocks.")
    return {
        "block_id": block_id or new_block_id(),
        "type": block_type,
        "created_at": created_at or timezone.now().isoformat(),
        "payload": dict(payload or {}),
    }


def content_blocks_from_response_blocks(response_blocks: object | None) -> list[dict[str, object]]:
    """
    Convert normalized `response_blocks` (table/text/kv) into portal `content_blocks`.

    The MCP/RAG orchestrators sometimes return structured blocks alongside prose
    so the UI can render reliable tables/rows without markdown heuristics.
    """

    if not response_blocks:
        return []
    if not isinstance(response_blocks, list):
        if isinstance(response_blocks, tuple):
            response_blocks = list(response_blocks)
        else:
            return []

    out: list[dict[str, object]] = []
    for raw in response_blocks:
        if not isinstance(raw, Mapping):
            continue
        block_type = str(raw.get("type") or "").strip().lower()
        if block_type == "text":
            lines_raw = raw.get("body_md") or raw.get("body") or raw.get("lines") or raw.get("text")
            lines: list[str] = []
            if isinstance(lines_raw, str):
                stripped = lines_raw.strip()
                if stripped:
                    lines = [stripped]
            elif isinstance(lines_raw, list):
                for entry in lines_raw:
                    if isinstance(entry, str) and entry.strip():
                        lines.append(entry.strip())
            if not lines:
                continue
            heading = str(raw.get("heading") or raw.get("title") or "").strip()
            text = "\n".join(lines).strip()
            if heading:
                text = f"### {heading}\n\n{text}" if text else f"### {heading}"
            out.extend(rich_blocks_from_text(text))
            continue
        if block_type == "table":
            payload = dict(raw)
            payload.pop("type", None)
            out.append(dict(make_structured_block("table", payload)))
            continue
        if block_type == "kv":
            payload = dict(raw)
            payload.pop("type", None)
            out.append(dict(make_structured_block("kv", payload)))
            continue
    return out


def ensure_assistant_text_blocks(body: str, *, existing_blocks: object | None = None) -> list[dict[str, object]]:
    """
    Phase 0 helper: ensure the assistant message has a canonical text block.

    - If existing blocks are present and include more than a single text block,
      keep them as-is (future phases will populate full block streams).
    - If blocks are empty/missing or represent a single text block, create/update
      the single text block so refresh can render deterministically.
    """

    blocks = _coerce_block_list(existing_blocks)
    body_value = body.strip()
    if not body_value:
        return blocks

    rich_blocks = rich_blocks_from_text(body_value)
    if not rich_blocks:
        return blocks

    legacy_text_indices = [idx for idx, block in enumerate(blocks) if _is_text_block(block)]
    has_rich = any(_is_rich_text_block(block) for block in blocks)
    if not blocks:
        return rich_blocks
    if has_rich:
        return blocks
    if legacy_text_indices and len(legacy_text_indices) == len(blocks):
        return rich_blocks
    return blocks + rich_blocks


def extract_text_from_content_blocks(value: object | None) -> str:
    blocks = _coerce_block_list(value)
    if not blocks:
        return ""
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        payload = block.get("payload")
        payload_map = payload if isinstance(payload, Mapping) else {}
        text = _inline_nodes_text(payload_map.get("content"))
        if block_type == "heading":
            level = payload_map.get("level")
            prefix = "#" * level + " " if isinstance(level, int) and 1 <= level <= 6 else ""
            heading_text = f"{prefix}{text}".strip()
            if heading_text:
                lines.append(heading_text)
            continue
        if block_type == "paragraph" and text:
            lines.append(text)
            continue
        if block_type == "list_item" and text:
            lines.append(f"- {text}")
            continue
        if block_type == "quote" and text:
            lines.append(f"> {text}")
            continue
        if block_type == "code_block":
            code_value = payload_map.get("code")
            if isinstance(code_value, str) and code_value.strip():
                lines.append(code_value.strip())
            continue
        # Ignore list container/table/kv/tool blocks for plain text fallback.
    return "\n".join(line for line in lines if line).strip()


def _coerce_block_list(value: object | None) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, object]] = []
    for entry in value:
        if isinstance(entry, dict):
            blocks.append(entry)
    return blocks


def _inline_nodes_text(nodes: object | None) -> str:
    if not isinstance(nodes, list):
        return ""
    parts: list[str] = []
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        text_value = node.get("text")
        if isinstance(text_value, str) and text_value:
            parts.append(text_value)
    return "".join(parts)


def _is_text_block(block: Mapping[str, object]) -> bool:
    if str(block.get("type") or "").strip().lower() != "text":
        return False
    payload = block.get("payload")
    if not isinstance(payload, Mapping):
        return False
    return isinstance(payload.get("text"), str)


def _is_rich_text_block(block: Mapping[str, object]) -> bool:
    block_type = str(block.get("type") or "").strip().lower()
    return block_type in {"paragraph", "heading", "list", "list_item", "quote", "code_block"}
