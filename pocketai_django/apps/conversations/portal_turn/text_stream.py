from __future__ import annotations

from typing import Mapping


class PortalTurnTextStreamMixin:
    def _emit_text_chunk_as_blocks(self, chunk: str, *, track_pre_tool_blocks: bool = False) -> None:
        if not chunk:
            return
        self.trace.record_text("delta.in", chunk)
        events = self.rich_builder.feed_text(chunk)
        if events:
            self.trace.record("rich.feed_text", {"events": int(len(events))})
            if track_pre_tool_blocks:
                for event in events:
                    if not isinstance(event, Mapping):
                        continue
                    event_type = str(event.get("type") or "").strip().lower()
                    if event_type != "block_start":
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, Mapping):
                        continue
                    block = payload.get("block")
                    if not isinstance(block, Mapping):
                        continue
                    block_id = str(block.get("block_id") or "").strip()
                    if block_id and block_id not in self._pre_tool_stream_block_ids:
                        self._pre_tool_stream_block_ids.append(block_id)
            self.emit_block_events(events)

    def _prune_pre_tool_stream_blocks(self, *, reason: str) -> None:
        block_ids = [block_id for block_id in self._pre_tool_stream_block_ids if block_id]
        self._pre_tool_stream_block_ids = []
        if not block_ids:
            return
        block_id_set = set(block_ids)
        self.trace.record(
            "delta.pruned_pre_tool_blocks",
            {
                "blocks": int(len(block_ids)),
                "reason": str(reason or "tool_decision_used"),
            },
        )
        self.blocks = [block for block in self.blocks if str(block.get("block_id") or "").strip() not in block_id_set]
        self.blocks_by_id = {
            key: value
            for key, value in self.blocks_by_id.items()
            if key not in block_id_set
        }
        self.append_event("block_remove", {"block_ids": block_ids, "reason": reason or "tool_decision_used"})

    def on_tool_decision(self, decision: str | None) -> None:
        normalized = str(decision or "").strip().lower()
        if not normalized:
            return
        if normalized in {"used", "tool_calls", "tools_used"}:
            if self._tool_decision == "used":
                return
            if self._tool_decision == "no_tools":
                # Do not downgrade an already-confirmed no-tools turn.
                return
            self._tool_decision = "used"
            return
        if normalized in {"no_tools", "none", "no_tool_calls"}:
            if self._tool_decision == "unknown":
                self._tool_decision = "no_tools"
                self.trace.record("delta.tool_decision_no_tools", {"pre_tool_blocks": int(len(self._pre_tool_stream_block_ids))})
            return

    def on_stream_complete(self) -> None:
        if self._tool_decision == "unknown":
            self.on_tool_decision("no_tools")

    def on_response_text_delta(self, chunk: str) -> None:
        if not chunk:
            return
        if self.block_ops_active:
            self.trace.record_text("delta.dropped", chunk, {"reason": "block_ops_active"})
            return
        self._emit_text_chunk_as_blocks(chunk, track_pre_tool_blocks=self._tool_decision == "unknown")
