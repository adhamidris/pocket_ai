from __future__ import annotations

from .inline import find_inline_safe_boundary, parse_inline_nodes, parse_inline_nodes_lenient


class RichBlockInlineMixin:
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

        # Keep unresolved markdown markers buffered so we don't leak raw token text
        # (e.g. literal "**") into streamed output.
        if safe_idx <= 0 and not final:
            leading_marker = buffer.startswith("**") or buffer.startswith("*") or buffer.startswith("`") or buffer.startswith("[")
            if leading_marker:
                self.line_inline_buffer = buffer
                return events

        if safe_idx > 0:
            safe_text = buffer[:safe_idx]
            nodes = parse_inline_nodes(safe_text)
            events.extend(self._emit_inline_nodes(self.line_block_id, nodes))
            buffer = buffer[safe_idx:]

        if final and buffer:
            # Final tail: prefer lenient parsing over raw marker output.
            nodes = parse_inline_nodes_lenient(buffer)
            if nodes:
                events.extend(self._emit_inline_nodes(self.line_block_id, nodes))
            else:
                events.extend(self._emit_inline_nodes(self.line_block_id, [{"text": buffer}]))
            buffer = ""

        self.line_inline_buffer = buffer
        return events
