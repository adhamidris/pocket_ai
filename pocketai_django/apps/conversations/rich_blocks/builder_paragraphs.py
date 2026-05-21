from __future__ import annotations

from typing import Mapping


class RichBlockParagraphMixin:
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
