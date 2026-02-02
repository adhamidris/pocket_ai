from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import PortalTurn, PortalTurnStatus
from .portal_turn_runner import PortalTurnRunner


@dataclass
class PortalTurnProcessResult:
    turn_id: uuid.UUID
    status: str
    error: str | None = None
    requeued: bool = False


class PortalTurnProcessingService:
    """Skeleton runner for portal turns. Execution logic is added in later phases."""

    def __init__(self, *, lease_seconds: int = 60) -> None:
        self.lease_seconds = max(5, int(lease_seconds or 0))

    def process_next_turn(self) -> PortalTurnProcessResult | None:
        turn = self._claim_next_turn()
        if not turn:
            return None
        try:
            return self._execute_turn(turn)
        except Exception as exc:  # pragma: no cover - defensive skeleton
            return self._mark_failed(turn, str(exc))

    def _claim_next_turn(self) -> PortalTurn | None:
        now = timezone.now()
        lease_until = now + timedelta(seconds=self.lease_seconds)
        with transaction.atomic():
            candidate = (
                PortalTurn.objects.select_for_update(skip_locked=True)
                .filter(status__in=[PortalTurnStatus.STREAMING, PortalTurnStatus.FINALIZING])
                .filter(run_after__lte=now)
                .order_by("run_after", "started_at")
                .first()
            )
            if not candidate:
                return None
            PortalTurn.objects.filter(id=candidate.id).update(
                lease_expires_at=lease_until,
                updated_at=now,
            )
            candidate.lease_expires_at = lease_until
            return candidate

    def _execute_turn(self, turn: PortalTurn) -> PortalTurnProcessResult:
        """Execute the portal turn synchronously."""
        runner = PortalTurnRunner(turn=turn, conversation=turn.conversation)
        runner.run()
        return PortalTurnProcessResult(turn_id=turn.id, status=PortalTurnStatus.FINALIZED)

    def _defer_turn(self, turn: PortalTurn, *, delay_seconds: int, reason: str) -> None:
        delay = max(0, int(delay_seconds or 0))
        run_after = timezone.now() + timedelta(seconds=delay)
        next_meta = dict(turn.metadata or {})
        next_meta["defer_reason"] = reason
        PortalTurn.objects.filter(id=turn.id).update(run_after=run_after, metadata=next_meta, updated_at=timezone.now())

    def _mark_failed(self, turn: PortalTurn, error: str) -> PortalTurnProcessResult:
        PortalTurn.objects.filter(id=turn.id).update(
            status=PortalTurnStatus.FAILED,
            error_detail=str(error or ""),
            updated_at=timezone.now(),
        )
        return PortalTurnProcessResult(turn_id=turn.id, status=PortalTurnStatus.FAILED, error=error)
