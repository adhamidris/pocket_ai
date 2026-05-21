from __future__ import annotations

import copy
import re

from .inline import parse_inline_nodes


class RichBlockBatchMixin:
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
