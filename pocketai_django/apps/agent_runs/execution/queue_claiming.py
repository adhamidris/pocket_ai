from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from django.db import connection as db_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.tenancy import tenant_bypass

from apps.agent_runs.models import AgentRun, AgentRunEventStream, AgentRunEventType, AgentRunStatus


class AgentRunQueueClaimingMixin:

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
