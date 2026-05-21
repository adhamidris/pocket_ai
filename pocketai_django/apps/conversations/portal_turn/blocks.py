from __future__ import annotations

import time
from typing import Mapping

from apps.conversations.content_blocks import new_block_id
from apps.conversations.rich_blocks import apply_block_ops, coerce_block_event


class PortalTurnBlockMixin:
    def _append_content_block(self, block: dict[str, object]) -> dict[str, object]:
        block_id = str(block.get("block_id") or "").strip()
        if not block_id:
            block_id = new_block_id()
            block["block_id"] = block_id
        self.blocks.append(block)
        self.blocks_by_id[block_id] = block
        return block

    def _get_content_block(self, block_id: str | None) -> dict[str, object] | None:
        key = (block_id or "").strip()
        if not key:
            return None
        return self.blocks_by_id.get(key)

    def _apply_block_event(self, event: Mapping[str, object]) -> None:
        event_type = str(event.get("type") or "").strip().lower()
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if event_type == "block_start":
            block = payload.get("block")
            if not isinstance(block, Mapping):
                return
            block_id = str(block.get("block_id") or "").strip()
            if not block_id:
                return
            existing = self.blocks_by_id.get(block_id)
            if existing is not None:
                existing.clear()
                existing.update(block)
            else:
                self.blocks.append(dict(block))
                self.blocks_by_id[block_id] = self.blocks[-1]
            return
        if event_type == "block_delta":
            block_id = str(payload.get("block_id") or "").strip()
            if not block_id:
                return
            ops = payload.get("ops")
            if not isinstance(ops, list):
                return
            block = self.blocks_by_id.get(block_id)
            if not block:
                return
            apply_block_ops(block, ops)
            return
        if event_type == "block_end":
            return

    def _apply_block_events_internally(self, events: list[dict[str, object]]) -> None:
        """Apply block events to internal state (blocks/blocks_by_id) without emitting to Redis."""
        if events:
            counts: dict[str, int] = {}
            for ev in events:
                if not isinstance(ev, Mapping):
                    continue
                t = str(ev.get("type") or "").strip().lower() or "event"
                counts[t] = counts.get(t, 0) + 1
            if counts:
                self.trace.record("blocks.apply_internal", {"events": int(len(events)), "types": counts})
        for event in events:
            if not isinstance(event, Mapping):
                continue
            self._apply_block_event(event)

    def emit_block_events(self, events: list[dict[str, object]]) -> None:
        for event in events:
            if not isinstance(event, Mapping):
                continue
            event_type = str(event.get("type") or "").strip()
            payload = dict(event.get("payload") or {})
            lowered = event_type.strip().lower()
            if lowered in {"block_start", "block_delta", "block_end"}:
                self._apply_block_event({"type": event_type, "payload": payload})

            if self.coalesce_block_deltas and lowered == "block_delta":
                block_id = str(payload.get("block_id") or "").strip()
                ops = payload.get("ops")
                if block_id and isinstance(ops, list) and ops:
                    should_flush = False
                    now = time.perf_counter()
                    if self._pending_delta_block_id and self._pending_delta_block_id != block_id:
                        should_flush = True
                    if self._pending_delta_last_flush_perf is not None:
                        elapsed_ms = (now - self._pending_delta_last_flush_perf) * 1000.0
                        if elapsed_ms >= float(self.delta_flush_interval_ms):
                            should_flush = True
                    # Flush if we're accumulating too many ops (avoid huge payloads).
                    if len(self._pending_delta_ops) + len(ops) >= int(self.delta_flush_max_ops):
                        should_flush = True
                    if should_flush:
                        self._flush_pending_block_delta()
                    if not self._pending_delta_block_id:
                        self._pending_delta_block_id = block_id
                    # Copy ops to avoid accidental mutation by upstream components.
                    for op in ops:
                        if isinstance(op, dict):
                            self._pending_delta_ops.append(dict(op))
                        else:
                            self._pending_delta_ops.append({"op": str(op)})
                    if self._pending_delta_last_flush_perf is None:
                        self._pending_delta_last_flush_perf = now
                    # Continue without emitting per-op block_delta events.
                    continue

            self.append_event(event_type, payload)

    def on_block_event(self, event: Mapping[str, object] | None) -> None:
        if not event:
            return
        normalized = coerce_block_event(event)
        if not normalized:
            return
        if not self.block_ops_active:
            self.block_ops_active = True
        self.emit_block_events([normalized])
