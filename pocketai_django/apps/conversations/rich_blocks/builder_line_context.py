from __future__ import annotations

import copy
import re


class RichBlockLineContextMixin:
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
