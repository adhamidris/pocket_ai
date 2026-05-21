from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import (
    BusinessProfile,
    EmailAccountHealthJobStatus,
    EmailAccountProvider,
    EmailAccountStatus,
    RegistrationSession,
    User,
)
from apps.integrations.models import (
    EmailAccount,
    EmailAccountAuditEvent,
    EmailAccountHealthJob,
)
from apps.integrations.email.health_jobs import (
    EmailAccountHealthJobRunner,
    enqueue_email_account_health_job,
    _log_email_audit,
)


class EmailHealthJobTestMixin:
    """Common setup for email health job tests."""

    def setUp(self):
        self.user = User.objects.create(email="test@example.com")
        self.registration_session = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            name="Test Business",
            registration_session=self.registration_session,
        )
        self.email_account = EmailAccount.objects.create(
            business_profile=self.business,
            user=self.user,
            provider=EmailAccountProvider.GOOGLE,
            email_address="user@gmail.com",
            status=EmailAccountStatus.CONNECTED,
        )


class TestEnqueueEmailAccountHealthJob(EmailHealthJobTestMixin, TestCase):
    def test_creates_job_for_valid_account(self):
        job = enqueue_email_account_health_job(account=self.email_account, trigger="test")
        self.assertIsNotNone(job)
        self.assertEqual(job.email_account_id, self.email_account.id)
        self.assertEqual(job.business_profile_id, self.email_account.business_profile_id)
        self.assertEqual(job.status, EmailAccountHealthJobStatus.QUEUED)
        self.assertEqual(job.trigger, "test")

    def test_returns_none_for_disconnected_account(self):
        self.email_account.status = EmailAccountStatus.DISCONNECTED
        self.email_account.save()
        job = enqueue_email_account_health_job(account=self.email_account, trigger="test")
        self.assertIsNone(job)

    def test_returns_none_for_none_account(self):
        job = enqueue_email_account_health_job(account=None, trigger="test")
        self.assertIsNone(job)

    def test_cancels_existing_queued_jobs(self):
        # Create an existing queued job
        existing = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.QUEUED,
            trigger="old",
        )

        # Enqueue a new job
        new_job = enqueue_email_account_health_job(account=self.email_account, trigger="new")

        # Verify old job was cancelled
        existing.refresh_from_db()
        self.assertEqual(existing.status, EmailAccountHealthJobStatus.CANCELLED)
        self.assertEqual(new_job.status, EmailAccountHealthJobStatus.QUEUED)

    def test_run_after_delay(self):
        delay = timedelta(minutes=5)
        job = enqueue_email_account_health_job(account=self.email_account, trigger="delayed", run_after=delay)
        self.assertIsNotNone(job)
        self.assertIsNotNone(job.run_after)
        self.assertGreater(job.run_after, timezone.now())


class TestEmailAccountHealthJobRunner(EmailHealthJobTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.runner = EmailAccountHealthJobRunner(lease_seconds=60, max_retries=3)

    def test_claim_next_job_returns_none_when_empty(self):
        job = self.runner._claim_next_job()
        self.assertIsNone(job)

    def test_claim_next_job_claims_queued_job(self):
        created_job = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.QUEUED,
            trigger="test",
        )

        claimed = self.runner._claim_next_job()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, created_job.id)
        self.assertEqual(claimed.status, EmailAccountHealthJobStatus.RUNNING)
        self.assertIsNotNone(claimed.started_at)
        self.assertIsNotNone(claimed.lease_expires_at)

    def test_claim_skips_future_run_after_jobs(self):
        # Create a job scheduled for the future
        EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.QUEUED,
            trigger="future",
            run_after=timezone.now() + timedelta(hours=1),
        )

        claimed = self.runner._claim_next_job()
        self.assertIsNone(claimed)

    def test_requeue_job_with_backoff(self):
        job = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.RUNNING,
            trigger="test",
            attempt_count=0,
        )

        requeued = self.runner._requeue_job_with_backoff(job, "test error", reason="test_failure")
        self.assertTrue(requeued)

        job.refresh_from_db()
        self.assertEqual(job.status, EmailAccountHealthJobStatus.QUEUED)
        self.assertEqual(job.attempt_count, 1)
        self.assertIsNotNone(job.run_after)
        self.assertEqual(job.error_detail, "test error")

    def test_max_retries_fails_terminal(self):
        job = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.RUNNING,
            trigger="test",
            attempt_count=2,  # Will be incremented to 3, matching max_retries
            max_attempts=3,
        )

        requeued = self.runner._requeue_job_with_backoff(job, "test error", reason="test_failure")
        self.assertFalse(requeued)

        job.refresh_from_db()
        self.assertEqual(job.status, EmailAccountHealthJobStatus.FAILED)

    def test_mark_job_succeeded(self):
        job = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.RUNNING,
            trigger="test",
        )

        self.runner._mark_job_succeeded(job, payload={"test": "data"})

        job.refresh_from_db()
        self.assertEqual(job.status, EmailAccountHealthJobStatus.SUCCEEDED)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.payload, {"test": "data"})

    @patch("apps.integrations.email.health_jobs.ensure_fresh_email_credentials")
    @patch("apps.integrations.email.health_jobs._test_google_connection")
    def test_run_job_success_google(self, mock_test_google, mock_ensure_fresh):
        # Setup mocks
        mock_ensure_fresh.return_value = self.email_account
        mock_test_google.return_value = (True, "")

        # Set credentials
        self.email_account.credentials = {"access_token": "test_token", "refresh_token": "test_refresh"}
        self.email_account.save()

        job = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.RUNNING,
            trigger="test",
        )

        self.runner._run_job(job)

        job.refresh_from_db()
        self.email_account.refresh_from_db()

        self.assertEqual(job.status, EmailAccountHealthJobStatus.SUCCEEDED)
        self.assertEqual(self.email_account.status, EmailAccountStatus.CONNECTED)
        self.assertEqual(self.email_account.last_error, "")

    @patch("apps.integrations.email.health_jobs.ensure_fresh_email_credentials")
    @patch("apps.integrations.email.health_jobs._test_google_connection")
    def test_run_job_failure_requeues(self, mock_test_google, mock_ensure_fresh):
        # Setup mocks
        mock_ensure_fresh.return_value = self.email_account
        mock_test_google.return_value = (False, "API Error: 401")

        # Set credentials
        self.email_account.credentials = {"access_token": "test_token", "refresh_token": "test_refresh"}
        self.email_account.save()

        job = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.RUNNING,
            trigger="test",
            attempt_count=0,
        )

        self.runner._run_job(job)

        job.refresh_from_db()
        self.email_account.refresh_from_db()

        self.assertEqual(job.status, EmailAccountHealthJobStatus.QUEUED)  # Requeued for retry
        self.assertEqual(self.email_account.status, EmailAccountStatus.ERROR)
        self.assertIn("API Error", self.email_account.last_error)

    def test_run_job_cancels_disconnected_account(self):
        self.email_account.status = EmailAccountStatus.DISCONNECTED
        self.email_account.save()

        job = EmailAccountHealthJob.objects.create(
            business_profile=self.email_account.business_profile,
            email_account=self.email_account,
            status=EmailAccountHealthJobStatus.RUNNING,
            trigger="test",
        )

        self.runner._run_job(job)

        job.refresh_from_db()
        self.assertEqual(job.status, EmailAccountHealthJobStatus.CANCELLED)


class TestLogEmailAudit(EmailHealthJobTestMixin, TestCase):
    def test_creates_audit_event(self):
        event = _log_email_audit(
            business_id=self.email_account.business_profile_id,
            account=self.email_account,
            action="connected",
            description="Test connection",
            metadata={"provider": "google"},
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.action, "connected")
        self.assertEqual(event.description, "Test connection")
        self.assertEqual(event.metadata, {"provider": "google"})
        self.assertEqual(event.email_account_id, self.email_account.id)
        self.assertEqual(event.email_account_id_snapshot, self.email_account.id)

    def test_returns_none_for_invalid_business(self):
        event = _log_email_audit(
            business_id=None,
            account=self.email_account,
            action="connected",
            description="Test",
        )
        self.assertIsNone(event)


class TestRunOnce(EmailHealthJobTestMixin, TestCase):
    @patch("apps.integrations.email.health_jobs.ensure_fresh_email_credentials")
    @patch("apps.integrations.email.health_jobs._test_google_connection")
    def test_processes_multiple_jobs(self, mock_test_google, mock_ensure_fresh):
        mock_ensure_fresh.side_effect = lambda a: a
        mock_test_google.return_value = (True, "")

        # Set credentials
        self.email_account.credentials = {"access_token": "test_token", "refresh_token": "test_refresh"}
        self.email_account.save()

        # Create multiple jobs
        for i in range(3):
            EmailAccountHealthJob.objects.create(
                business_profile=self.email_account.business_profile,
                email_account=self.email_account,
                status=EmailAccountHealthJobStatus.QUEUED,
                trigger=f"test_{i}",
            )

        runner = EmailAccountHealthJobRunner()
        processed = runner.run_once(limit=10)

        # Due to deduplication, only 1 job should actually run (others cancelled)
        self.assertGreaterEqual(processed, 1)
        self.assertEqual(EmailAccountHealthJob.objects.filter(status=EmailAccountHealthJobStatus.SUCCEEDED).count(), 1)
