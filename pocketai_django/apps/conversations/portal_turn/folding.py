from __future__ import annotations

import copy
import json
import uuid
from typing import Iterable

from apps.conversations.models import PortalTurnEvent
from apps.conversations.portal_turn.events import get_portal_redis_client, portal_turn_redis_stream_key
from apps.conversations.rich_blocks import apply_block_ops


def _fold_events(events: Iterable[tuple[str, dict]]) -> list[dict[str, object]]:
    """Replay portal turn events into ordered `content_blocks`."""
    blocks: list[dict[str, object]] = []
    blocks_by_id: dict[str, dict[str, object]] = {}

    for event_type_raw, payload_raw in events:
        event_type = str(event_type_raw or "").strip().lower()
        payload = payload_raw if isinstance(payload_raw, dict) else {}

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


def fold_turn_events(*, turn_id: uuid.UUID) -> list[dict[str, object]]:
    """Replay portal turn events from Postgres into ordered `content_blocks`."""
    events = (
        (str(row[0] or "").strip(), row[1] if isinstance(row[1], dict) else {})
        for row in PortalTurnEvent.objects.filter(turn_id=turn_id).order_by("seq").values_list("type", "payload")
    )
    return _fold_events(events)


def fold_turn_events_from_redis(*, turn_id: uuid.UUID, limit: int = 25_000) -> list[dict[str, object]]:
    """
    Replay portal turn events from Redis Streams into ordered `content_blocks`.

    Phase 5: used as a fallback when the worker needs to finalize a turn but does not
    have in-memory builder state (e.g. after a crash while status=FINALIZING).
    """
    conn = get_portal_redis_client(socket_timeout_seconds=1.0)
    if conn is None:
        return []

    stream_key = portal_turn_redis_stream_key(turn_id=turn_id)
    count_limit = max(1, int(limit or 0))

    def _iter_events() -> Iterable[tuple[str, dict]]:
        fetched = 0
        last_id = "0-0"
        while fetched < count_limit:
            try:
                entries = conn.xrange(stream_key, min=f"({last_id}", max="+", count=500)
            except Exception:
                break
            if not entries:
                break
            for entry_id, fields in entries:
                entry_id_str = (
                    entry_id.decode("utf-8", errors="replace") if isinstance(entry_id, (bytes, bytearray)) else str(entry_id)
                )
                last_id = entry_id_str
                fetched += 1
                if fetched > count_limit:
                    return

                raw_type = fields.get(b"type") if isinstance(fields, dict) else None
                if raw_type is None and isinstance(fields, dict):
                    raw_type = fields.get("type")  # type: ignore[index]
                event_type = (
                    raw_type.decode("utf-8", errors="replace") if isinstance(raw_type, (bytes, bytearray)) else str(raw_type or "")
                ).strip()

                raw_payload = fields.get(b"payload") if isinstance(fields, dict) else None
                if raw_payload is None and isinstance(fields, dict):
                    raw_payload = fields.get("payload")  # type: ignore[index]
                payload_text = (
                    raw_payload.decode("utf-8", errors="replace") if isinstance(raw_payload, (bytes, bytearray)) else str(raw_payload or "")
                )
                try:
                    payload_obj = json.loads(payload_text) if payload_text else {}
                except Exception:
                    payload_obj = {}
                yield event_type, payload_obj if isinstance(payload_obj, dict) else {}

    return _fold_events(_iter_events())

