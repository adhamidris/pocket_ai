from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Any, Mapping

from django.conf import settings
from django.db import connection as db_connection
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context

from apps.agent_runs.execution.results import AgentRunProcessResult
from apps.agent_runs.models import AgentRun, AgentRunEvent, AgentRunEventStream, AgentRunEventType, AgentRunStatus


logger = logging.getLogger(__name__)


class AgentRunQueueMixin:

    def process_next_run(self) -> AgentRunProcessResult | None:
        self._expire_due_checkpoints(now=timezone.now())
        self._requeue_stale_running_runs(limit=self.max_stale_requeues_per_pass)
        run = self._claim_next_run()
        if not run:
            return None

        try:
            return self._execute_run(run)
        except Exception as exc:
            logger.exception("agent_run.execute_failed run=%s", run.id)
            return self._requeue_run_with_backoff(run, f"execution failed: {exc}", reason="execution_failed")

    def _defer_run(
        self,
        run: AgentRun,
        *,
        now,
        delay_seconds: float,
        label: str,
        reason: str,
        extra_payload: Mapping[str, object] | None = None,
    ) -> None:
        delay = max(1.0, float(delay_seconds or 0.0))
        run_after = now + timedelta(seconds=delay)
        AgentRun.objects.filter(id=run.id, status=AgentRunStatus.QUEUED).update(
            run_after=run_after,
            lease_expires_at=None,
            updated_at=now,
        )
        payload: dict[str, object] = {"reason": reason, "run_after": run_after.isoformat()}
        if extra_payload:
            payload.update(dict(extra_payload))
        self._append_event(
            run,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label=label,
            payload=payload,
        )

    def _claim_next_run(self) -> AgentRun | None:
        now = timezone.now()
        qs = (
            AgentRun.objects.filter(status=AgentRunStatus.QUEUED)
            .filter(Q(run_after__lte=now) | Q(run_after__isnull=True))
            .order_by("run_after", "created_at")
        )

        supports_skip_locked = bool(
            getattr(db_connection.features, "has_select_for_update", False)
            and getattr(db_connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(db_connection.features, "has_select_for_update_of", False))
        supports_for_update = bool(getattr(db_connection.features, "has_select_for_update", False))

        from django.db.models import Count

        scan_limit = max(1, int(self.claim_scan_limit or 1))
        max_running = max(0, int(self.max_running_per_business or 0))

        with tenant_bypass():
            with transaction.atomic():
                candidates: list[AgentRun] = []
                if supports_for_update:
                    for_update_kwargs: dict[str, Any] = {}
                    if supports_skip_locked:
                        for_update_kwargs["skip_locked"] = True
                    if supports_for_update_of:
                        for_update_kwargs["of"] = ("self",)
                    candidates = list(qs.select_for_update(**for_update_kwargs)[:scan_limit])
                else:
                    candidates = list(qs[:scan_limit])

                if not candidates:
                    return None

                business_ids = {run.business_profile_id for run in candidates if run.business_profile_id}

                running_by_business: dict[object, int] = {}
                if max_running > 0 and business_ids:
                    for row in (
                        AgentRun.objects.filter(status=AgentRunStatus.RUNNING, business_profile_id__in=business_ids)
                        .values("business_profile_id")
                        .annotate(count=Count("id"))
                    ):
                        bid = row.get("business_profile_id")
                        running_by_business[bid] = int(row.get("count") or 0)

                for run in candidates:
                    business_id = run.business_profile_id
                    if not business_id:
                        self._defer_run(
                            run,
                            now=now,
                            delay_seconds=self.capacity_backoff_seconds,
                            label="Queued (missing business)",
                            reason="missing_business_profile_id",
                        )
                        continue

                    if max_running > 0:
                        running = int(running_by_business.get(business_id, 0))
                        if running >= max_running:
                            self._defer_run(
                                run,
                                now=now,
                                delay_seconds=self.capacity_backoff_seconds,
                                label="Queued (capacity limit reached)",
                                reason="capacity_limit",
                                extra_payload={"running": running, "max": max_running},
                            )
                            continue
                        # Reserve a slot for this claim within this transaction.
                        running_by_business[business_id] = running + 1

                    lease = now + timedelta(seconds=max(10.0, float(self.lease_seconds)))
                    if supports_for_update:
                        run.status = AgentRunStatus.RUNNING
                        run.started_at = now
                        run.run_after = None
                        run.lease_expires_at = lease
                        run.save(update_fields=["status", "started_at", "run_after", "lease_expires_at", "updated_at"])
                    else:
                        updated = AgentRun.objects.filter(id=run.id, status=AgentRunStatus.QUEUED).update(
                            status=AgentRunStatus.RUNNING,
                            started_at=now,
                            run_after=None,
                            lease_expires_at=lease,
                        )
                        if not updated:
                            continue
                        run.refresh_from_db()

                    self._append_event(
                        run,
                        stream=AgentRunEventStream.SYSTEM,
                        event_type=AgentRunEventType.PROGRESS,
                        label="Started",
                        payload={"status": AgentRunStatus.RUNNING},
                    )
                    return run
                return None

    def _requeue_stale_running_runs(self, *, limit: int) -> int:
        now = timezone.now()
        cutoff = now - timedelta(seconds=max(10.0, float(self.lease_seconds)))
        with tenant_bypass():
            stale = list(
                AgentRun.objects.filter(status=AgentRunStatus.RUNNING)
                .filter(Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True, started_at__lt=cutoff))
                .order_by("started_at")[: max(1, int(limit))]
            )
        if not stale:
            return 0
        for run in stale:
            self._requeue_run_with_backoff(run, "auto-requeue: run lease expired", reason="lease_expired")
        return len(stale)

    def _job_retry_delay_seconds(self, run_id: str, attempt_count: int) -> float:
        normalized_attempt = max(1, int(attempt_count))
        base = min(self.max_retry_delay_seconds, float(2 ** min(10, normalized_attempt)))
        return base + self._deterministic_jitter(run_id, attempt_count)

    @staticmethod
    def _deterministic_jitter(run_id: str, attempt_count: int) -> float:
        """
        Deterministic jitter to avoid retry thundering herds without introducing non-determinism.
        """

        seed = f"{run_id}:{int(attempt_count)}".encode("utf-8", errors="ignore")
        digest = hashlib.sha256(seed).digest()
        # 0.0 .. < 1.0
        return int.from_bytes(digest[:2], "big") / 65536.0

    def _requeue_run_with_backoff(self, run: AgentRun, message: str, *, reason: str) -> AgentRunProcessResult:
        business_id = getattr(run, "business_profile_id", None)
        ctx = tenant_context(business_id) if business_id else tenant_bypass()
        with ctx:
            now = timezone.now()
            max_attempts = max(1, int(getattr(run, "max_attempts", 0) or 0) or self.max_retries_default)
            next_attempt = max(0, int(getattr(run, "attempt_count", 0) or 0)) + 1

            metadata = dict(run.metadata or {}) if isinstance(run.metadata, dict) else {}
            attempts = metadata.get("attempts")
            if not isinstance(attempts, list):
                attempts = []
            attempts.append(
                {
                    "attempt": next_attempt,
                    "at": now.isoformat(),
                    "reason": reason,
                    "error": (message or "")[:400],
                }
            )
            metadata["attempts"] = attempts[-10:]

            if next_attempt < max_attempts:
                delay = min(self.max_retry_delay_seconds, self._job_retry_delay_seconds(str(run.id), next_attempt))
                run_after = now + timedelta(seconds=float(delay))
                AgentRun.objects.filter(id=run.id).update(
                    status=AgentRunStatus.QUEUED,
                    attempt_count=next_attempt,
                    run_after=run_after,
                    lease_expires_at=None,
                    finished_at=None,
                    error_detail=(message or "")[:2000],
                    metadata=metadata,
                )
                self._append_event(
                    run,
                    stream=AgentRunEventStream.SYSTEM,
                    event_type=AgentRunEventType.PROGRESS,
                    label="Requeued",
                    payload={"reason": reason, "run_after": run_after.isoformat()},
                )
                return AgentRunProcessResult(run_id=str(run.id), status=AgentRunStatus.QUEUED, requeued=True, error=message)

            self._mark_run_failed_terminal(run, message, reason=reason, attempt_count=next_attempt, metadata=metadata)
            return AgentRunProcessResult(run_id=str(run.id), status=AgentRunStatus.FAILED, requeued=False, error=message)

    def _mark_run_failed_terminal(
        self,
        run: AgentRun,
        message: str,
        *,
        reason: str,
        attempt_count: int,
        metadata: dict[str, object] | None = None,
    ) -> None:
        now = timezone.now()
        AgentRun.objects.filter(id=run.id).update(
            status=AgentRunStatus.FAILED,
            attempt_count=attempt_count,
            run_after=None,
            lease_expires_at=None,
            finished_at=now,
            error_detail=(message or "")[:2000],
            metadata=metadata or {},
        )
        self._append_event(
            run,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.ERROR,
            label="Failed",
            payload={"reason": reason, "error": (message or "")[:500]},
        )

    def _append_event(
        self,
        run: AgentRun,
        *,
        stream: str,
        event_type: str,
        label: str,
        payload: Mapping[str, object] | None = None,
    ) -> AgentRunEvent:
        # Serialize event ordering via the run row lock so API-driven events (cancel, etc.)
        # can't collide with worker event writes.
        with transaction.atomic():
            locked_run = AgentRun.objects.select_for_update().get(id=run.id)
            next_index = (
                AgentRunEvent.objects.filter(run=locked_run).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
            )
            return AgentRunEvent.objects.create(
                run=locked_run,
                sequence_index=int(next_index) + 1,
                stream=stream,
                event_type=event_type,
                label=(label or "")[:240],
                payload=dict(payload or {}),
            )
