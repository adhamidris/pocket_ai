from __future__ import annotations

import uuid
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from .models import PortalTurn, PortalTurnEvent


def append_turn_event(*, turn_id: uuid.UUID, event_type: str, payload: dict | None = None) -> PortalTurnEvent:
    """Append a portal turn event with a monotonic sequence number."""
    payload_out = payload if isinstance(payload, dict) else {}
    with transaction.atomic():
        turn = PortalTurn.objects.select_for_update().filter(id=turn_id).first()
        if not turn:
            raise PortalTurn.DoesNotExist(f"PortalTurn not found: {turn_id}")
        seq = int(turn.last_event_seq or 0) + 1
        event = PortalTurnEvent.objects.create(
            turn=turn,
            seq=seq,
            type=str(event_type or "").strip() or "event",
            payload=payload_out,
        )
        PortalTurn.objects.filter(id=turn.id).update(last_event_seq=seq, updated_at=timezone.now())
    return event


def list_turn_events(*, turn_id: uuid.UUID, since_seq: int = 0, limit: int = 250) -> Iterable[PortalTurnEvent]:
    since = int(since_seq or 0)
    limit = max(1, min(int(limit or 0), 500))
    return PortalTurnEvent.objects.filter(turn_id=turn_id, seq__gt=since).order_by("seq")[:limit]
