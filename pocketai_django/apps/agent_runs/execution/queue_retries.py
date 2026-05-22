from __future__ import annotations

import hashlib
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context

from apps.agent_runs.execution.results import AgentRunProcessResult
from apps.agent_runs.models import AgentRun, AgentRunEventStream, AgentRunEventType, AgentRunStatus


class AgentRunQueueRetryMixin:

    def _requeue_stale_running_runs(self, *, limit: int) -> int:
        now = timezone.now()
        cutoff = now - timedelta(seconds=max(10.0, float(self.lease_seconds)))
        with tenant_bypass():
            qs = (
                AgentRun.objects.filter(status=AgentRunStatus.RUNNING)
                .filter(Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True, started_at__lt=cutoff))
                .order_by("started_at")
            )
            run_kind = str(getattr(self, "run_kind", "all") or "all").strip().lower()
            if run_kind == "sub_agent":
                qs = qs.filter(agentic_task_id__isnull=True)
            elif run_kind == "agentic_task":
                qs = qs.filter(agentic_task_id__isnull=False)
            stale = list(qs[: max(1, int(limit))])
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
