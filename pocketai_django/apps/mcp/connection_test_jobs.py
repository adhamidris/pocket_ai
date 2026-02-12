from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from typing import Any

from django.db import connection as db_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.tenancy import tenant_context

from apps.accounts.models import (
    McpConnectionAuditAction,
    McpConnectionStatus,
)
from apps.mcp.models import McpConnection
from apps.mcp.connectors import mcp_connection_auth_headers
from apps.mcp.models import McpConnectionTestJob, McpConnectionTestJobStatus
from apps.mcp.remote_client import McpRemoteError, test_mcp_server

logger = logging.getLogger(__name__)


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.isdigit():
        return float(raw)
    try:
        when = parsedate_to_datetime(raw)
    except Exception:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt_timezone.utc)
    now = datetime.now(dt_timezone.utc)
    return max(0.0, (when - now).total_seconds())


def enqueue_mcp_connection_test_job(
    *,
    connection: McpConnection,
    trigger: str,
    run_after: datetime | None = None,
) -> McpConnectionTestJob | None:
    if not connection or not connection.business_profile_id:
        return None
    if str(connection.server_url or "").strip() == "__builtin__":
        return None
    if connection.status != McpConnectionStatus.ENABLED:
        return None

    now = timezone.now()
    trigger_value = str(trigger or "").strip()[:48]
    business_id = connection.business_profile_id
    with tenant_context(business_id):
        # Coalesce queued jobs for the same connection to avoid thrash.
        McpConnectionTestJob.objects.filter(
            connection=connection,
            status=McpConnectionTestJobStatus.QUEUED,
        ).update(
            status=McpConnectionTestJobStatus.CANCELLED,
            finished_at=now,
            error_detail="auto-cancel: superseded by newer enqueue",
            run_after=None,
            lease_expires_at=None,
        )
        job = McpConnectionTestJob.objects.create(
            business_profile=connection.business_profile,
            connection=connection,
            status=McpConnectionTestJobStatus.QUEUED,
            trigger=trigger_value,
            run_after=run_after,
        )
        return job


@dataclass
class McpConnectionTestJobRunner:
    lease_seconds: int = 120
    max_retries: int = 5
    idle_sleep_s: float = 1.5

    def run_forever(self, *, limit_per_tick: int = 25) -> None:
        while True:
            processed = self.run_once(limit=limit_per_tick)
            if processed <= 0:
                time.sleep(max(0.1, float(self.idle_sleep_s)))

    def run_once(self, *, limit: int = 25) -> int:
        processed = 0
        for _ in range(max(1, int(limit))):
            job = self._claim_next_job()
            if not job:
                break
            processed += 1
            try:
                self._run_job(job)
            except Exception:
                logger.exception("mcp_test_job_unhandled_error job=%s", job.id)
                self._mark_job_failed_terminal(job, "Unhandled error while running job.", reason="unhandled_error")
        return processed

    def _claim_next_job(self) -> McpConnectionTestJob | None:
        self._requeue_stale_running_jobs()

        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        qs = (
            McpConnectionTestJob.objects.filter(status=McpConnectionTestJobStatus.QUEUED)
            .filter(eligible)
            .select_related("connection", "business_profile")
            .order_by("created_at")
        )

        supports_skip_locked = bool(
            getattr(db_connection.features, "has_select_for_update", False)
            and getattr(db_connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(db_connection.features, "has_select_for_update_of", False))
        supports_for_update = bool(getattr(db_connection.features, "has_select_for_update", False))

        with transaction.atomic():
            job = None
            if supports_for_update:
                for_update_kwargs: dict[str, Any] = {}
                if supports_skip_locked:
                    for_update_kwargs["skip_locked"] = True
                if supports_for_update_of:
                    for_update_kwargs["of"] = ("self",)
                locked = qs.select_for_update(**for_update_kwargs)
                job = locked.first()
            else:
                job = qs.first()
            if not job:
                return None

            lease = now + timedelta(seconds=max(10, int(self.lease_seconds)))

            # Cancel duplicate queued jobs for this connection (keep the oldest/claimed one).
            cancelled = (
                McpConnectionTestJob.objects.filter(
                    connection_id=job.connection_id,
                    status=McpConnectionTestJobStatus.QUEUED,
                )
                .exclude(id=job.id)
                .update(
                    status=McpConnectionTestJobStatus.CANCELLED,
                    finished_at=now,
                    error_detail="auto-cancel: duplicate queued test job",
                    run_after=None,
                    lease_expires_at=None,
                )
            )
            if cancelled:
                logger.info("mcp_test_job_dedupe_cancelled connection=%s cancelled=%s", job.connection_id, cancelled)

            if supports_for_update:
                job.status = McpConnectionTestJobStatus.RUNNING
                job.started_at = now
                job.run_after = None
                job.lease_expires_at = lease
                job.save(update_fields=["status", "started_at", "run_after", "lease_expires_at", "updated_at"])
            else:
                updated = McpConnectionTestJob.objects.filter(id=job.id, status=McpConnectionTestJobStatus.QUEUED).update(
                    status=McpConnectionTestJobStatus.RUNNING,
                    started_at=now,
                    run_after=None,
                    lease_expires_at=lease,
                )
                if not updated:
                    return None
                job.refresh_from_db()
            return job

    def _requeue_stale_running_jobs(self, *, limit: int = 25) -> int:
        now = timezone.now()
        cutoff = now - timedelta(seconds=max(10, int(self.lease_seconds)))
        stale_jobs = list(
            McpConnectionTestJob.objects.filter(status=McpConnectionTestJobStatus.RUNNING)
            .filter(
                Q(lease_expires_at__lt=now)
                | Q(lease_expires_at__isnull=True, started_at__lt=cutoff)
            )
            .select_related("connection")
            .order_by("started_at")[: max(1, int(limit))]
        )
        if not stale_jobs:
            return 0
        for job in stale_jobs:
            self._requeue_job_with_backoff(job, "auto-requeue: job lease expired", reason="lease_expired")
        return len(stale_jobs)

    def _job_retry_delay_seconds(self, attempt_count: int) -> float:
        normalized_attempt = max(1, int(attempt_count))
        base = min(900.0, float(2 ** min(10, normalized_attempt)))
        jitter = random.uniform(0.0, 1.0)
        return base + jitter

    def _requeue_job_with_backoff(self, job: McpConnectionTestJob, message: str, *, reason: str) -> bool:
        now = timezone.now()
        max_attempts = max(1, int(getattr(job, "max_attempts", 0) or 0) or self.max_retries)
        next_attempt = max(0, int(getattr(job, "attempt_count", 0) or 0)) + 1
        payload = dict(job.payload or {})
        attempts = payload.get("attempts")
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
        payload["attempts"] = attempts[-10:]

        if next_attempt < max_attempts:
            delay = self._job_retry_delay_seconds(next_attempt)
            run_after = now + timedelta(seconds=float(delay))
            McpConnectionTestJob.objects.filter(id=job.id).update(
                status=McpConnectionTestJobStatus.QUEUED,
                attempt_count=next_attempt,
                run_after=run_after,
                lease_expires_at=None,
                finished_at=None,
                error_detail=message[:1000],
                payload=payload,
            )
            return True

        self._mark_job_failed_terminal(job, message, reason=reason)
        return False

    def _mark_job_failed_terminal(self, job: McpConnectionTestJob, message: str, *, reason: str) -> None:
        now = timezone.now()
        next_attempt = max(0, int(getattr(job, "attempt_count", 0) or 0)) + 1
        payload = dict(job.payload or {})
        attempts = payload.get("attempts")
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
        payload["attempts"] = attempts[-10:]
        McpConnectionTestJob.objects.filter(id=job.id).update(
            status=McpConnectionTestJobStatus.FAILED,
            attempt_count=next_attempt,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail=(message or "")[:1000],
            payload=payload,
        )

    def _mark_job_succeeded(self, job: McpConnectionTestJob, *, payload: dict[str, object] | None = None) -> None:
        now = timezone.now()
        McpConnectionTestJob.objects.filter(id=job.id).update(
            status=McpConnectionTestJobStatus.SUCCEEDED,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail="",
            payload=payload or {},
        )

    def _run_job(self, job: McpConnectionTestJob) -> None:
        from apps.api.mcp_connections import MCP_TOOL_CACHE_TTL_HOURS, _log_mcp_audit, _validate_mcp_server_url

        connection_obj = job.connection
        business = job.business_profile
        if not connection_obj or not business:
            self._mark_job_failed_terminal(job, "Job missing connection/business.", reason="missing_relation")
            return

        with tenant_context(business.id):
            # Refresh state at execution time (connection could have been updated).
            connection_obj.refresh_from_db()
            if connection_obj.status != McpConnectionStatus.ENABLED:
                McpConnectionTestJob.objects.filter(id=job.id).update(
                    status=McpConnectionTestJobStatus.CANCELLED,
                    finished_at=timezone.now(),
                    lease_expires_at=None,
                    run_after=None,
                    error_detail="cancelled: connection disabled",
                )
                return

            server_url, url_error = _validate_mcp_server_url(connection_obj.server_url)
            if url_error:
                self._mark_job_failed_terminal(job, url_error, reason="invalid_server_url")
                return

            headers = mcp_connection_auth_headers(connection_obj)

            try:
                session, tools = test_mcp_server(server_url=server_url or connection_obj.server_url, headers=headers)
            except McpRemoteError as exc:
                upstream_status = getattr(exc, "status_code", None)
                retry_after_value = getattr(exc, "retry_after", None)
                tested_at = timezone.now().isoformat()
                error_message = str(exc)

                metadata = dict(connection_obj.metadata or {})
                existing_cache = metadata.get("tool_cache") if isinstance(metadata.get("tool_cache"), dict) else {}
                existing_tools = existing_cache.get("tools") if isinstance(existing_cache.get("tools"), list) else None
                existing_expires_at = existing_cache.get("expires_at") if existing_cache else None
                existing_tool_count = (
                    len(existing_tools)
                    if isinstance(existing_tools, list)
                    else int(existing_cache.get("tool_count") or 0)
                    if str(existing_cache.get("tool_count") or "").isdigit()
                    else 0
                )

                next_cache = dict(existing_cache) if isinstance(existing_cache, dict) else {}
                next_cache.update(
                    {
                        "tested_at": tested_at,
                        "error": error_message[:500],
                        "status_code": upstream_status,
                        "retry_after": retry_after_value,
                    }
                )

                # Preserve last-known-good tools on transient failures.
                if isinstance(existing_tools, list) and existing_tools:
                    next_cache["tools"] = existing_tools
                    next_cache["tool_count"] = existing_tool_count
                    if existing_expires_at:
                        next_cache["expires_at"] = existing_expires_at
                else:
                    next_cache.pop("tools", None)
                    next_cache["tool_count"] = 0

                metadata["tool_cache"] = next_cache
                connection_obj.metadata = metadata
                connection_obj.save(update_fields=["metadata", "updated_at"])
                _log_mcp_audit(
                    business=business,
                    connection=connection_obj,
                    actor=None,
                    action=McpConnectionAuditAction.UPDATED,
                    description="MCP connection test failed (auto).",
                    metadata={"error": error_message[:500], "status_code": upstream_status, "trigger": job.trigger},
                )

                retry_after_s = _parse_retry_after_seconds(retry_after_value)
                if upstream_status == 429 and retry_after_s is not None:
                    run_after = timezone.now() + timedelta(seconds=float(min(3600.0, max(0.0, retry_after_s))))
                    McpConnectionTestJob.objects.filter(id=job.id).update(
                        status=McpConnectionTestJobStatus.QUEUED,
                        attempt_count=max(0, int(job.attempt_count or 0)) + 1,
                        run_after=run_after,
                        lease_expires_at=None,
                        error_detail=error_message[:1000],
                    )
                    return

                self._requeue_job_with_backoff(job, error_message, reason="test_failed")
                return

            tested_at = timezone.now().isoformat()
            tools_payload = [
                {
                    "name": tool.name,
                    "title": tool.title,
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                }
                for tool in tools
            ]

            metadata = dict(connection_obj.metadata or {})
            cache_expires_at = (timezone.now() + timedelta(hours=MCP_TOOL_CACHE_TTL_HOURS)).isoformat()
            metadata["tool_cache"] = {
                "tested_at": tested_at,
                "expires_at": cache_expires_at,
                "transport": session.transport,
                "protocol_version": session.protocol_version,
                "server_info": session.server_info.__dict__ if session.server_info else None,
                "tool_count": len(tools),
                "tools": tools_payload,
            }
            connection_obj.metadata = metadata
            connection_obj.save(update_fields=["metadata", "updated_at"])

            _log_mcp_audit(
                business=business,
                connection=connection_obj,
                actor=None,
                action=McpConnectionAuditAction.UPDATED,
                description="MCP connection tested successfully (auto).",
                metadata={"tool_count": len(tools), "transport": session.transport, "trigger": job.trigger},
            )
            self._mark_job_succeeded(
                job,
                payload={
                    "tested_at": tested_at,
                    "transport": session.transport,
                    "tool_count": len(tools),
                },
            )
