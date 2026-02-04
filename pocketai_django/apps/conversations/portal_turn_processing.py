from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context

from .models import PortalTurn, PortalTurnStatus
from .portal_turn_runner import PortalTurnRunner


@dataclass
class PortalTurnProcessResult:
    turn_id: uuid.UUID
    status: str
    error: str | None = None
    requeued: bool = False


class PortalTurnProcessingService:
    """
    DB-leased worker for portal turns.

    Why this exists:
    - Thread-based execution inside the web server does not scale horizontally.
    - A DB lease + `SELECT ... FOR UPDATE SKIP LOCKED` pattern allows multiple workers
      to safely pull turns without double-processing.
    """

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
        with tenant_bypass():
            with transaction.atomic():
                candidate = (
                    PortalTurn.objects.select_related("conversation")
                    .select_for_update(skip_locked=True)
                    .filter(status__in=[PortalTurnStatus.STREAMING, PortalTurnStatus.FINALIZING])
                    .filter(run_after__lte=now)
                    .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lt=now))
                    .filter(Q(metadata__execution_mode="worker") | Q(metadata__execution_mode__isnull=True))
                    .filter(attempt_count__lt=F("max_attempts"))
                    .order_by("run_after", "started_at")
                    .first()
                )
                if not candidate:
                    return None
                updated = PortalTurn.objects.filter(id=candidate.id).update(
                    lease_expires_at=lease_until,
                    attempt_count=F("attempt_count") + 1,
                    updated_at=now,
                )
                if not updated:
                    return None
                candidate.lease_expires_at = lease_until
                candidate.attempt_count = int(getattr(candidate, "attempt_count", 0) or 0) + 1
                return candidate

    def _execute_turn(self, turn: PortalTurn) -> PortalTurnProcessResult:
        """Execute the portal turn synchronously."""
        business_id = getattr(getattr(turn, "conversation", None), "business_profile_id", None)
        if business_id is None:  # pragma: no cover - defensive, should always exist
            with tenant_bypass():
                refreshed = PortalTurn.objects.select_related("conversation").filter(id=turn.id).first()
            if not refreshed or not refreshed.conversation_id:
                return PortalTurnProcessResult(turn_id=turn.id, status=PortalTurnStatus.FAILED, error="Turn missing conversation.")
            business_id = getattr(refreshed.conversation, "business_profile_id", None)

        with tenant_context(business_id):
            refreshed = PortalTurn.objects.select_related("conversation").filter(id=turn.id).first()
            if not refreshed:
                return PortalTurnProcessResult(turn_id=turn.id, status=PortalTurnStatus.FAILED, error="Turn not found.")
            runner = PortalTurnRunner(turn=refreshed, conversation=refreshed.conversation)
            runner.run()

        # Best-effort lease cleanup (status is already FINALIZED/CANCELLED/FAILED inside the runner).
        with tenant_bypass():
            PortalTurn.objects.filter(id=turn.id).update(lease_expires_at=None, updated_at=timezone.now())

        with tenant_bypass():
            latest = (
                PortalTurn.objects.filter(id=turn.id)
                .values_list("status", flat=True)
                .first()
            )
        return PortalTurnProcessResult(turn_id=turn.id, status=str(latest or PortalTurnStatus.FINALIZED))

    def _defer_turn(self, turn: PortalTurn, *, delay_seconds: int, reason: str) -> None:
        delay = max(0, int(delay_seconds or 0))
        run_after = timezone.now() + timedelta(seconds=delay)
        next_meta = dict(turn.metadata or {})
        next_meta["defer_reason"] = reason
        with tenant_bypass():
            PortalTurn.objects.filter(id=turn.id).update(run_after=run_after, metadata=next_meta, updated_at=timezone.now())

    def _mark_failed(self, turn: PortalTurn, error: str) -> PortalTurnProcessResult:
        with tenant_bypass():
            PortalTurn.objects.filter(id=turn.id).update(
                status=PortalTurnStatus.FAILED,
                error_detail=str(error or ""),
                lease_expires_at=None,
                updated_at=timezone.now(),
            )
        return PortalTurnProcessResult(turn_id=turn.id, status=PortalTurnStatus.FAILED, error=error)
