from __future__ import annotations

import copy
from typing import Iterable, Mapping

from .block_schema import _make_block

INLINE_NODE_LIMIT = 800
INLINE_TEXT_LIMIT = 800
BLOCK_LIMIT = 400


class RichBlockBlockMixin:
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
