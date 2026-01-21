from __future__ import annotations

import uuid
from typing import Any, Literal, Mapping, TypedDict

from django.utils import timezone

ContentBlockType = Literal["text", "tool_use", "tool_result", "table", "kv"]


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
            out.append(dict(make_text_block(text)))
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

    text_block_indices = [idx for idx, block in enumerate(blocks) if _is_text_block(block)]
    if len(text_block_indices) == 1:
        idx = text_block_indices[0]
        block = dict(blocks[idx])
        block.setdefault("block_id", new_block_id())
        block.setdefault("created_at", timezone.now().isoformat())
        payload = block.get("payload")
        payload_out: dict[str, Any] = dict(payload) if isinstance(payload, Mapping) else {}
        payload_out["text"] = body_value
        block["payload"] = payload_out
        block["type"] = "text"
        blocks[idx] = block
        return blocks

    if not text_block_indices:
        blocks.append(dict(make_text_block(body_value)))
        return blocks

    # Multiple text blocks: keep as-is (avoid duplicating/reshaping segmented transcripts).
    return blocks


def _coerce_block_list(value: object | None) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, object]] = []
    for entry in value:
        if isinstance(entry, dict):
            blocks.append(entry)
    return blocks


def _is_text_block(block: Mapping[str, object]) -> bool:
    if str(block.get("type") or "").strip().lower() != "text":
        return False
    payload = block.get("payload")
    if not isinstance(payload, Mapping):
        return False
    return isinstance(payload.get("text"), str)
