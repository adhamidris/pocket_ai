from __future__ import annotations

import copy
import uuid
from typing import Iterable

from .models import PortalTurnEvent
from .rich_blocks import apply_block_ops


def fold_turn_events(*, turn_id: uuid.UUID) -> list[dict[str, object]]:
    """Replay portal turn events into ordered content_blocks."""
    blocks: list[dict[str, object]] = []
    blocks_by_id: dict[str, dict[str, object]] = {}

    events: Iterable[PortalTurnEvent] = PortalTurnEvent.objects.filter(turn_id=turn_id).order_by("seq")

    for evt in events:
        event_type = str(evt.type or "").strip().lower()
        payload = evt.payload if isinstance(evt.payload, dict) else {}

        if event_type == "block_start":
            block = payload.get("block") if isinstance(payload, dict) else None
            if not isinstance(block, dict):
                continue
            block_id = str(block.get("block_id") or block.get("blockId") or "").strip()
            if not block_id:
                continue
            block_copy = copy.deepcopy(block)
            if block_id in blocks_by_id:
                existing = blocks_by_id[block_id]
                existing.clear()
                existing.update(block_copy)
            else:
                blocks.append(block_copy)
                blocks_by_id[block_id] = blocks[-1]
            continue

        if event_type == "block_delta":
            block_id = str(payload.get("block_id") or payload.get("blockId") or "").strip()
            if not block_id:
                continue
            ops = payload.get("ops")
            if not isinstance(ops, list):
                continue
            block = blocks_by_id.get(block_id)
            if not block:
                continue
            apply_block_ops(block, ops)
            continue

        if event_type == "block_end":
            continue

        if event_type in {"block_tool_use", "block_tool_result"}:
            block = payload.get("block") if isinstance(payload, dict) else None
            if not isinstance(block, dict):
                continue
            block_id = str(block.get("block_id") or block.get("blockId") or "").strip()
            if not block_id:
                continue
            block_copy = copy.deepcopy(block)
            if block_id in blocks_by_id:
                existing = blocks_by_id[block_id]
                existing.clear()
                existing.update(block_copy)
            else:
                blocks.append(block_copy)
                blocks_by_id[block_id] = blocks[-1]
            continue

    return blocks
