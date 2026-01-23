from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import connection as db_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.tenancy import tenant_context

from apps.accounts.models import (
    EmailAccount,
    EmailAccountAuditEvent,
    EmailAccountAuditAction,
    EmailAccountHealthJob,
    EmailAccountHealthJobStatus,
    EmailAccountProvider,
    EmailAccountStatus,
)
from apps.integrations.email_accounts import ensure_fresh_email_credentials
from apps.integrations.gmail import GmailApiError, gmail_get_message
from apps.integrations.microsoft_graph import GraphApiError, graph_get_profile


logger = logging.getLogger(__name__)


def enqueue_email_account_health_job(
    *,
    account: EmailAccount,
    trigger: str,
    run_after: timedelta | None = None,
) -> EmailAccountHealthJob | None:
    """
    Create a health check job for the given EmailAccount.

    Coalesces any existing queued jobs for this account to avoid thrash.
    Returns None if the account is not eligible (e.g. disconnected, no business).
    """
    if not account or not account.business_profile_id:
        return None
    if account.status == EmailAccountStatus.DISCONNECTED:
        return None

    now = timezone.now()
    trigger_value = str(trigger or "").strip()[:48]
    business_id = account.business_profile_id
    run_after_dt = now + run_after if run_after else None

    with tenant_context(business_id):
        # Coalesce queued jobs for the same account to avoid thrash.
        EmailAccountHealthJob.objects.filter(
            email_account=account,
            status=EmailAccountHealthJobStatus.QUEUED,
        ).update(
            status=EmailAccountHealthJobStatus.CANCELLED,
            finished_at=now,
            error_detail="auto-cancel: superseded by newer enqueue",
            run_after=None,
            lease_expires_at=None,
        )
        job = EmailAccountHealthJob.objects.create(
            business_profile=account.business_profile,
            email_account=account,
            status=EmailAccountHealthJobStatus.QUEUED,
            trigger=trigger_value,
            run_after=run_after_dt,
        )
        return job


def _log_email_audit(
    *,
    business_id: int,
    account: EmailAccount | None,
    action: str,
    description: str,
    metadata: dict[str, object] | None = None,
) -> EmailAccountAuditEvent | None:
    """
    Create an audit log entry for an email account event.

    IMPORTANT: Never include raw email content or OAuth tokens in metadata.
    """
    if not business_id:
        return None

    with tenant_context(business_id):
        event = EmailAccountAuditEvent.objects.create(
            business_profile_id=business_id,
            email_account=account,
            email_account_id_snapshot=account.id if account else None,
            action=action,
            description=description[:500] if description else "",
            metadata=metadata or {},
        )
        return event


def _test_google_connection(account: EmailAccount, access_token: str) -> tuple[bool, str]:
    """
    Test Google/Gmail connection by fetching a single message (or profile info).

    Returns (success, error_message).
    """
    try:
        # Try to list messages with limit 1 to verify access
        from apps.integrations.gmail import gmail_search_messages
        gmail_search_messages(access_token=access_token, query="", limit=1, include_snippets_limit=0)
        return True, ""
    except GmailApiError as exc:
        return False, str(exc)[:500]


def _test_microsoft_connection(account: EmailAccount, access_token: str) -> tuple[bool, str]:
    """
    Test Microsoft/Outlook connection by fetching profile info.

    Returns (success, error_message).
    """
    try:
        graph_get_profile(access_token=access_token)
        return True, ""
    except GraphApiError as exc:
        return False, str(exc)[:500]


@dataclass
class EmailAccountHealthJobRunner:
    """
    Background job runner for email account health checks.

    Follows the same pattern as McpConnectionTestJobRunner for consistency.
    """

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
                logger.exception("email_health_job_unhandled_error job=%s", job.id)
                self._mark_job_failed_terminal(job, "Unhandled error while running job.", reason="unhandled_error")
        return processed

    def _claim_next_job(self) -> EmailAccountHealthJob | None:
        self._requeue_stale_running_jobs()

        now = timezone.now()
        eligible = Q(run_after__isnull=True) | Q(run_after__lte=now)
        qs = (
            EmailAccountHealthJob.objects.filter(status=EmailAccountHealthJobStatus.QUEUED)
            .filter(eligible)
            .select_related("email_account", "business_profile")
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

            # Cancel duplicate queued jobs for this account (keep the oldest/claimed one).
            cancelled = (
                EmailAccountHealthJob.objects.filter(
                    email_account_id=job.email_account_id,
                    status=EmailAccountHealthJobStatus.QUEUED,
                )
                .exclude(id=job.id)
                .update(
                    status=EmailAccountHealthJobStatus.CANCELLED,
                    finished_at=now,
                    error_detail="auto-cancel: duplicate queued health job",
                    run_after=None,
                    lease_expires_at=None,
                )
            )
            if cancelled:
                logger.info("email_health_job_dedupe_cancelled account=%s cancelled=%s", job.email_account_id, cancelled)

            if supports_for_update:
                job.status = EmailAccountHealthJobStatus.RUNNING
                job.started_at = now
                job.run_after = None
                job.lease_expires_at = lease
                job.save(update_fields=["status", "started_at", "run_after", "lease_expires_at", "updated_at"])
            else:
                updated = EmailAccountHealthJob.objects.filter(id=job.id, status=EmailAccountHealthJobStatus.QUEUED).update(
                    status=EmailAccountHealthJobStatus.RUNNING,
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
            EmailAccountHealthJob.objects.filter(status=EmailAccountHealthJobStatus.RUNNING)
            .filter(
                Q(lease_expires_at__lt=now)
                | Q(lease_expires_at__isnull=True, started_at__lt=cutoff)
            )
            .select_related("email_account")
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

    def _requeue_job_with_backoff(self, job: EmailAccountHealthJob, message: str, *, reason: str) -> bool:
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
            EmailAccountHealthJob.objects.filter(id=job.id).update(
                status=EmailAccountHealthJobStatus.QUEUED,
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

    def _mark_job_failed_terminal(self, job: EmailAccountHealthJob, message: str, *, reason: str) -> None:
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
        EmailAccountHealthJob.objects.filter(id=job.id).update(
            status=EmailAccountHealthJobStatus.FAILED,
            attempt_count=next_attempt,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail=(message or "")[:1000],
            payload=payload,
        )

    def _mark_job_succeeded(self, job: EmailAccountHealthJob, *, payload: dict[str, object] | None = None) -> None:
        now = timezone.now()
        EmailAccountHealthJob.objects.filter(id=job.id).update(
            status=EmailAccountHealthJobStatus.SUCCEEDED,
            finished_at=now,
            lease_expires_at=None,
            run_after=None,
            error_detail="",
            payload=payload or {},
        )

    def _run_job(self, job: EmailAccountHealthJob) -> None:
        account = job.email_account
        business = job.business_profile
        if not account or not business:
            self._mark_job_failed_terminal(job, "Job missing account/business.", reason="missing_relation")
            return

        business_id = business.id
        with tenant_context(business_id):
            # Refresh state at execution time (account could have been updated).
            account.refresh_from_db()

            # If disconnected, cancel the job.
            if account.status == EmailAccountStatus.DISCONNECTED:
                EmailAccountHealthJob.objects.filter(id=job.id).update(
                    status=EmailAccountHealthJobStatus.CANCELLED,
                    finished_at=timezone.now(),
                    lease_expires_at=None,
                    run_after=None,
                    error_detail="cancelled: account disconnected",
                )
                return

            # Try to refresh credentials.
            try:
                account = ensure_fresh_email_credentials(account)
            except Exception as exc:
                error_msg = f"Token refresh failed: {exc}"
                logger.warning("email_health_token_refresh_failed account=%s error=%s", account.id, exc)
                self._update_account_error(account, error_msg)
                _log_email_audit(
                    business_id=business_id,
                    account=account,
                    action=EmailAccountAuditAction.ERROR,
                    description="Email account health check failed: token refresh error.",
                    metadata={"error": str(exc)[:400], "trigger": job.trigger},
                )
                self._requeue_job_with_backoff(job, error_msg, reason="token_refresh_failed")
                return

            # Get credentials for testing.
            credentials = account.credentials or {}
            access_token = str(credentials.get("access_token") or "").strip()
            if not access_token:
                error_msg = "No access token available for health check."
                self._update_account_error(account, error_msg)
                self._mark_job_failed_terminal(job, error_msg, reason="no_access_token")
                return

            # Test the connection based on provider.
            provider = str(account.provider or "").strip().lower()
            if provider == EmailAccountProvider.GOOGLE:
                success, error_msg = _test_google_connection(account, access_token)
            elif provider == EmailAccountProvider.MICROSOFT:
                success, error_msg = _test_microsoft_connection(account, access_token)
            else:
                error_msg = f"Unknown provider: {provider}"
                self._mark_job_failed_terminal(job, error_msg, reason="unknown_provider")
                return

            now = timezone.now()
            if not success:
                logger.warning("email_health_check_failed account=%s provider=%s error=%s", account.id, provider, error_msg)
                self._update_account_error(account, error_msg)
                _log_email_audit(
                    business_id=business_id,
                    account=account,
                    action=EmailAccountAuditAction.ERROR,
                    description="Email account health check failed.",
                    metadata={"error": error_msg[:400], "provider": provider, "trigger": job.trigger},
                )
                self._requeue_job_with_backoff(job, error_msg, reason="health_check_failed")
                return

            # Success - update account status to connected.
            account.status = EmailAccountStatus.CONNECTED
            account.last_error = ""
            account.last_health_checked_at = now
            meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
            meta["last_health_check_at"] = now.isoformat()
            meta["last_health_check_trigger"] = job.trigger
            account.metadata = meta
            account.save(update_fields=["status", "last_error", "last_health_checked_at", "metadata", "updated_at"])

            _log_email_audit(
                business_id=business_id,
                account=account,
                action=EmailAccountAuditAction.UPDATED,
                description="Email account health check succeeded.",
                metadata={"provider": provider, "trigger": job.trigger},
            )

            logger.info("email_health_check_succeeded account=%s provider=%s", account.id, provider)
            self._mark_job_succeeded(
                job,
                payload={
                    "checked_at": now.isoformat(),
                    "provider": provider,
                },
            )

    def _update_account_error(self, account: EmailAccount, error_msg: str) -> None:
        """Update account with error state."""
        account.status = EmailAccountStatus.ERROR
        account.last_error = (error_msg or "")[:500]
        meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        meta["last_health_check_error"] = error_msg[:400]
        meta["last_health_check_error_at"] = timezone.now().isoformat()
        account.metadata = meta
        account.save(update_fields=["status", "last_error", "metadata", "updated_at"])
