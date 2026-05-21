from __future__ import annotations

import copy
import re

from .block_schema import apply_block_ops, coerce_block, coerce_block_event, coerce_block_ops, new_block_id
from .builder_batch import RichBlockBatchMixin
from .builder_blocks import RichBlockBlockMixin
from .builder_inline import RichBlockInlineMixin
from .builder_line_context import RichBlockLineContextMixin
from .builder_paragraphs import RichBlockParagraphMixin
from .builder_tables import RichBlockTableMixin
from .markdown_cleanup import _fix_malformed_markdown
from .table_markdown import (
    _looks_like_partial_markdown_table_candidate,
    _parse_markdown_table_divider,
    _parse_markdown_table_row,
)

class RichBlockStreamBuilder(
    RichBlockInlineMixin,
    RichBlockLineContextMixin,
    RichBlockBatchMixin,
    RichBlockTableMixin,
    RichBlockBlockMixin,
    RichBlockParagraphMixin,
):
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
        self.pending_table_header_line: str | None = None
        self.active_table_id: str | None = None
        self.active_table_partial_row_index: int | None = None
        self.active_table_partial_cells: list[str] = []

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
        if chunk.endswith("\n"):
            events.extend(self._close_paragraph_on_chunk_boundary())
        return events

    def finalize(self) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        events.extend(self._finalize_pending_line())
        events.extend(self._flush_pending_table_header())
        self._close_active_table()
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
        events.extend(self._flush_pending_table_header())
        self._close_active_table()
        events.extend(self._close_paragraph())
        self._close_list()
        return events

    def _close_paragraph_on_chunk_boundary(self) -> list[dict[str, object]]:
        """
        Close a streamed paragraph when the provider chunk ends exactly on a newline.

        Without this, the visible break between lines is delayed until the next chunk
        arrives because paragraph newlines are normally injected only when the next
        line starts. That creates transient "glued" lines during live streaming.
        """

        if self.in_code_block:
            return []
        if self.pending_line or self.line_kind is not None:
            return []
        if not self.active_paragraph_id:
            return []
        return self._close_paragraph()

    def _reset_line_state(self) -> None:
        self.line_kind = None
        self.line_parent_id = None
        self.line_block_id = None
        self.line_inline_buffer = ""


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

        if not self.in_code_block and not line_ended and not had_context:
            if self.pending_table_header_line or self.active_table_id:
                if self.active_table_id:
                    events.extend(self._sync_partial_table_row(self.pending_line, finalize=False))
                return events
            if _looks_like_partial_markdown_table_candidate(self.pending_line):
                return events

        if not self.in_code_block and line_ended:
            line = self.pending_line.rstrip("\r")
            if self.active_table_id:
                had_partial_row = self.active_table_partial_row_index is not None
                partial_events = self._sync_partial_table_row(line, finalize=True)
                if partial_events or had_partial_row:
                    self.pending_line = ""
                    self._reset_line_state()
                    return events + partial_events
                row_events = self._append_table_row(line)
                if row_events:
                    self.pending_line = ""
                    self._reset_line_state()
                    return events + row_events
                self._close_active_table()

            if self.pending_table_header_line:
                divider = _parse_markdown_table_divider(line)
                if divider:
                    events.extend(self._close_paragraph())
                    self._close_list()
                    events.extend(self._start_table_from_lines(self.pending_table_header_line, line))
                    self.pending_table_header_line = None
                    self.pending_line = ""
                    self._reset_line_state()
                    return events
                events.extend(self._flush_pending_table_header())

            if _parse_markdown_table_row(line) is not None:
                events.extend(self._close_paragraph())
                self._close_list()
                self.pending_table_header_line = line
                self.pending_line = ""
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

        line = self.pending_line.rstrip("\r")
        if self.active_table_id:
            had_partial_row = self.active_table_partial_row_index is not None
            partial_events = self._sync_partial_table_row(line, finalize=True)
            if partial_events or had_partial_row:
                self.pending_line = ""
                self._reset_line_state()
                return events + partial_events
            row_events = self._append_table_row(line)
            if row_events:
                self.pending_line = ""
                self._reset_line_state()
                return events + row_events
            self._close_active_table()

        if self.pending_table_header_line:
            divider = _parse_markdown_table_divider(line)
            if divider:
                events.extend(self._close_paragraph())
                self._close_list()
                events.extend(self._start_table_from_lines(self.pending_table_header_line, line))
                self.pending_table_header_line = None
                self.pending_line = ""
                self._reset_line_state()
                return events
            events.extend(self._flush_pending_table_header())

        if _parse_markdown_table_row(line) is not None:
            self.pending_table_header_line = line
            self.pending_line = ""
            self._reset_line_state()
            return events + self._flush_pending_table_header()

        events.extend(self._ensure_line_context(line_ended=True))
        if self.line_kind is not None:
            events.extend(self._flush_inline_buffer(final=True))
            if self.line_kind in {"heading", "list_item"} and self.line_block_id:
                events.append({"type": "block_end", "payload": {"block_id": self.line_block_id}})

        self.pending_line = ""
        self._reset_line_state()
        return events


def rich_blocks_from_text(text: str) -> list[dict[str, object]]:
    # Pre-process to fix common LLM formatting issues
    fixed_text = _fix_malformed_markdown(text or "")
    builder = RichBlockStreamBuilder()
    builder.feed_text(fixed_text)
    builder.finalize()
    return builder.snapshot()
