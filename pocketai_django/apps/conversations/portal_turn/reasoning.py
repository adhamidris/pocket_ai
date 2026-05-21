from __future__ import annotations

import copy
from typing import Mapping

from django.utils import timezone

from apps.conversations.content_blocks import new_block_id
from apps.conversations.rich_blocks import apply_block_ops


class PortalTurnReasoningMixin:
    def on_reasoning_event(self, event: Mapping[str, object] | None) -> None:
        if not event or not isinstance(event, Mapping):
            return
        event_type = str(event.get("type") or "").strip().lower()
        if event_type not in {"reasoning_delta", "reasoning_end"}:
            return
        call_id = str(event.get("call_id") or "").strip()
        if not call_id:
            return
        stage = str(event.get("stage") or "").strip() or "llm"
        label = str(event.get("label") or "").strip() or stage.replace("_", " ").strip() or "LLM"
        block_id = self.reasoning_block_id_by_call_id.get(call_id)

        if event_type == "reasoning_delta":
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta:
                return
            if not block_id:
                block = {
                    "block_id": new_block_id(),
                    "type": "reasoning",
                    "created_at": timezone.now().isoformat(),
                    "payload": {
                        "title": label,
                        "stage": stage,
                        "collapsed": False,
                        "code": "",
                    },
                }
                self._append_content_block(block)
                block_id = str(block.get("block_id") or "").strip()
                if not block_id:
                    return
                self.reasoning_block_id_by_call_id[call_id] = block_id
                self.append_event("block_start", {"block": copy.deepcopy(block)})
            block = self._get_content_block(block_id)
            if not block:
                return
            ops = [{"op": "append_code", "text": delta}]
            apply_block_ops(block, ops)
            self.append_event("block_delta", {"block_id": block_id, "ops": ops})
            return

        if event_type == "reasoning_end":
            if not block_id:
                return
            block = self._get_content_block(block_id)
            if block:
                payload_raw = block.get("payload")
                payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
                payload["collapsed"] = True
                payload["completed_at"] = timezone.now().isoformat()
                block["payload"] = payload
            self.append_event("block_end", {"block_id": block_id})

    def on_status_change(self, state: object) -> None:
        if not state:
            return
        code: str | None = None
        label: str | None = None
        meta: dict | None = None
        if isinstance(state, str):
            code = state.strip()
        elif isinstance(state, Mapping):
            raw_code = state.get("code") or state.get("state")
            if isinstance(raw_code, str):
                code = raw_code.strip()
            raw_label = state.get("label")
            if isinstance(raw_label, str):
                label = raw_label.strip()
            raw_meta = state.get("meta")
            if isinstance(raw_meta, dict):
                meta = raw_meta
        if not code:
            return
        payload: dict[str, object] = {"state": code}
        if label is not None:
            payload["label"] = label
        if meta:
            payload["meta"] = meta
        self.append_event("status", payload)

    def finalize_text(self) -> None:
        if self._tool_decision == "unknown":
            self.on_tool_decision("no_tools")
        if not self.block_ops_active:
            events = self.rich_builder.finalize()
            if events:
                self.trace.record("rich.finalize", {"events": int(len(events))})
                self.emit_block_events(events)
        # Ensure any buffered block_delta ops are emitted before finalization persists.
        self._flush_pending_block_delta()
        self.trace.record(
            "builder.finalize_text",
            {
                "blocks": int(len(self.blocks)),
                "block_ops_active": bool(self.block_ops_active),
                "had_tool_events": bool(self.had_tool_events),
            },
        )
