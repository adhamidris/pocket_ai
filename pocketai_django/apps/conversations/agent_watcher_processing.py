from __future__ import annotations

import dataclasses
import hashlib
import logging
from datetime import timedelta
from typing import Any, Mapping

from django.db import IntegrityError, connection as db_connection
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.tenancy import tenant_context

from apps.accounts.models import EmailAccountProvider, EmailAccountStatus
from apps.accounts.feature_flags import FeatureFlagService
from apps.integrations.email_accounts import ensure_fresh_email_credentials
from apps.integrations.gmail import GmailApiError, gmail_search_messages
from apps.integrations.microsoft_graph import GraphApiError, graph_list_messages
from apps.conversations.output_destinations import ensure_watcher_thread
from apps.conversations.models import (
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
    AgentWatcher,
    AgentWatcherDedupeKey,
    AgentWatcherStatus,
    AgentWatcherType,
)
from apps.conversations.run_contracts import normalize_run_spec


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class AgentWatcherProcessResult:
    watcher_id: str
    action: str
    triggered_run_ids: tuple[str, ...] = ()
    error: str | None = None


class AgentWatcherProcessingService:
    """
    Background worker service for polling AgentWatchers.

    V1 scope: email inbox polling with dedupe + per-watcher rate limits.
    """

    def __init__(
        self,
        *,
        lease_seconds: float = 60.0,
        max_retry_delay_seconds: float = 900.0,
        dedupe_ttl_days: int = 30,
    ) -> None:
        self.lease_seconds = float(lease_seconds or 60.0)
        self.max_retry_delay_seconds = float(max_retry_delay_seconds or 900.0)
        self.dedupe_ttl_days = max(1, int(dedupe_ttl_days or 30))

    def process_next_watcher(self) -> AgentWatcherProcessResult | None:
        watcher = self._claim_next_watcher()
        if watcher is None:
            return None
        try:
            return self._poll_watcher(watcher)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("agent_watcher.poll_failed watcher=%s", watcher.id)
            return self._schedule_backoff(watcher, f"poll failed: {exc}")

    def _claim_next_watcher(self) -> AgentWatcher | None:
        now = timezone.now()
        qs = (
            AgentWatcher.objects.select_related(
                "agent_profile",
                "business_profile",
                "conversation",
                "run_spec",
                "email_account",
            )
            .filter(status=AgentWatcherStatus.ACTIVE)
            .filter(Q(next_poll_at__lte=now) | Q(next_poll_at__isnull=True))
            .filter(Q(lease_expires_at__lt=now) | Q(lease_expires_at__isnull=True))
            .order_by("next_poll_at", "created_at")
        )

        supports_skip_locked = bool(
            getattr(db_connection.features, "has_select_for_update", False)
            and getattr(db_connection.features, "has_select_for_update_skip_locked", False)
        )
        supports_for_update_of = bool(getattr(db_connection.features, "has_select_for_update_of", False))
        supports_for_update = bool(getattr(db_connection.features, "has_select_for_update", False))

        with transaction.atomic():
            watcher = None
            if supports_for_update:
                for_update_kwargs: dict[str, Any] = {}
                if supports_skip_locked:
                    for_update_kwargs["skip_locked"] = True
                if supports_for_update_of:
                    for_update_kwargs["of"] = ("self",)
                watcher = qs.select_for_update(**for_update_kwargs).first()
            else:
                watcher = qs.first()
            if watcher is None:
                return None

            lease = now + timedelta(seconds=max(10.0, float(self.lease_seconds)))
            AgentWatcher.objects.filter(id=watcher.id).update(lease_expires_at=lease, updated_at=now)
            watcher.lease_expires_at = lease
            return watcher

    def _poll_watcher(self, watcher: AgentWatcher) -> AgentWatcherProcessResult:
        now = timezone.now()
        business_id = watcher.business_profile_id
        if not business_id:
            return self._pause_watcher(watcher, "watcher missing business_profile_id")

        poll_interval = max(60, min(int(watcher.poll_interval_seconds or 0) or 300, 24 * 60 * 60))
        max_events = max(1, min(int(watcher.max_events_per_poll or 0) or 5, 25))

        enabled = bool(getattr(FeatureFlagService.snapshot(watcher.business_profile), "sub_agents_v1", False))
        if not enabled:
            next_poll_at = now + timedelta(seconds=int(poll_interval))
            metadata = dict(watcher.metadata or {}) if isinstance(watcher.metadata, dict) else {}
            metadata["last_skip"] = {"at": now.isoformat(), "reason": "sub_agents_disabled"}
            with tenant_context(business_id):
                AgentWatcher.objects.filter(id=watcher.id).update(
                    next_poll_at=next_poll_at,
                    lease_expires_at=None,
                    metadata=metadata,
                    updated_at=now,
                )
            return AgentWatcherProcessResult(watcher_id=str(watcher.id), action="skipped_disabled")

        with tenant_context(business_id):
            if watcher.watcher_type != AgentWatcherType.EMAIL_INBOX:
                return self._pause_watcher(watcher, f"unsupported watcher_type '{watcher.watcher_type}'")

            if watcher.conversation_id is None:
                ensure_watcher_thread(watcher)

            account = watcher.email_account
            if account is None:
                return self._pause_watcher(watcher, "email watcher missing email_account")
            if account.status != EmailAccountStatus.CONNECTED:
                return self._pause_watcher(watcher, "email account not connected")

            try:
                account = ensure_fresh_email_credentials(account)
            except Exception:
                logger.exception("agent_watcher.email_oauth_refresh_failed watcher=%s account=%s", watcher.id, getattr(account, "id", None))
                return self._schedule_backoff(watcher, "email OAuth refresh failed")

            creds = account.credentials or {}
            access_token = str(creds.get("access_token") or "").strip()
            if not access_token:
                return self._pause_watcher(watcher, "email account missing access_token")

            config = watcher.watch_config if isinstance(getattr(watcher, "watch_config", None), Mapping) else {}
            query = str(config.get("query") or "").strip()
            unread_only = bool(config.get("unreadOnly", True))

            cursor = watcher.metadata if isinstance(getattr(watcher, "metadata", None), Mapping) else {}
            after = None
            if isinstance(cursor.get("cursor"), Mapping):
                after = str(cursor.get("cursor", {}).get("after") or "").strip() or None
            if after is None and watcher.last_polled_at:
                after = watcher.last_polled_at.isoformat()

            try:
                results = self._poll_email_provider(
                    provider=account.provider,
                    access_token=access_token,
                    query=query,
                    unread_only=unread_only,
                    after=after,
                    limit=max_events * 3,
                )
            except (GmailApiError, GraphApiError) as exc:
                logger.warning(
                    "agent_watcher.email_poll_failed watcher=%s provider=%s error=%s",
                    watcher.id,
                    account.provider,
                    str(exc)[:240],
                )
                return self._schedule_backoff(watcher, f"email poll failed: {str(exc)[:240]}")

            triggered: list[str] = []
            inspected = 0
            for summary in results:
                if len(triggered) >= max_events:
                    break
                if not isinstance(summary, Mapping):
                    continue
                inspected += 1
                message_id = str(summary.get("message_id") or summary.get("messageId") or "").strip()
                if not message_id:
                    continue
                dedupe_key = self._email_dedupe_key(account.provider, account.id, message_id)
                if not self._record_dedupe_key(watcher, dedupe_key):
                    continue

                run_id = self._spawn_email_run(watcher, account, summary, now=now)
                if run_id:
                    triggered.append(run_id)

            self._prune_dedupe_keys(watcher, now=now)
            self._update_watcher_success(watcher, now=now, poll_interval=poll_interval, inspected=inspected, triggered=len(triggered))
            return AgentWatcherProcessResult(watcher_id=str(watcher.id), action="polled", triggered_run_ids=tuple(triggered))

    @staticmethod
    def _email_dedupe_key(provider: str, account_id: object, message_id: str) -> str:
        raw = f"{provider}:{account_id}:{message_id}".encode("utf-8", errors="ignore")
        digest = hashlib.sha256(raw).hexdigest()[:32]
        return f"email:{digest}"

    def _poll_email_provider(
        self,
        *,
        provider: str,
        access_token: str,
        query: str,
        unread_only: bool,
        after: str | None,
        limit: int,
    ) -> list[Mapping[str, object]]:
        safe_limit = max(1, min(int(limit or 0), 25))
        provider_key = str(provider or "").strip().lower()

        if provider_key == EmailAccountProvider.GOOGLE:
            effective_query = query or "is:unread"
            if unread_only and "is:unread" not in effective_query:
                effective_query = f"{effective_query} is:unread".strip()
            payload = gmail_search_messages(access_token=access_token, query=effective_query, limit=safe_limit, include_snippets_limit=5)
            items = payload.get("results") if isinstance(payload.get("results"), list) else []
            out: list[Mapping[str, object]] = []
            for item in items:
                if isinstance(item, Mapping):
                    out.append(item)
            return out

        if provider_key == EmailAccountProvider.MICROSOFT:
            payload = graph_list_messages(access_token=access_token, limit=safe_limit, unread_only=unread_only, after=after)
            items = payload.get("results") if isinstance(payload.get("results"), list) else []
            out: list[Mapping[str, object]] = []
            for item in items:
                if isinstance(item, Mapping):
                    out.append(item)
            return out

        raise GraphApiError("Email provider is not supported for watchers yet.")

    def _record_dedupe_key(self, watcher: AgentWatcher, dedupe_key: str) -> bool:
        try:
            with transaction.atomic():
                AgentWatcherDedupeKey.objects.create(
                    watcher=watcher,
                    business_profile_id=watcher.business_profile_id,
                    dedupe_key=dedupe_key,
                )
            return True
        except IntegrityError:
            return False

    def _spawn_email_run(self, watcher: AgentWatcher, account, summary: Mapping[str, object], *, now) -> str | None:
        message_id = str(summary.get("message_id") or "").strip()
        thread_id = str(summary.get("thread_id") or "").strip()
        subject = str(summary.get("subject") or "").strip()
        from_email = str(summary.get("from") or "").strip()
        date_value = str(summary.get("date") or "").strip()
        snippet = str(summary.get("snippet") or "").strip()

        trigger_context: dict[str, object] = {
            "type": "email_inbox",
            "provider": str(getattr(account, "provider", "") or ""),
            "email_account_id": str(getattr(account, "id", "") or ""),
            "message_id": message_id,
            "thread_id": thread_id,
            "from": from_email,
            "subject": subject,
            "date": date_value,
        }
        if snippet:
            trigger_context["snippet"] = snippet[:400].rstrip()

        run = AgentRun.objects.create(
            business_profile=watcher.business_profile,
            agent_profile=watcher.agent_profile,
            conversation=watcher.conversation,
            created_by=watcher.created_by,
            run_spec=watcher.run_spec,
            run_spec_snapshot=normalize_run_spec(watcher.run_spec_snapshot),
            title=(f"{watcher.name}: {subject}" if subject else f"{watcher.name}: New email")[:200],
            source=AgentRunSource.WATCHER,
            status=AgentRunStatus.QUEUED,
            visibility=watcher.visibility,
            metadata={
                "watcher_id": str(watcher.id),
                "trigger": trigger_context,
                "destination_config": dict(watcher.destination_config or {}) if isinstance(getattr(watcher, "destination_config", None), dict) else {},
            },
            run_after=now,
        )
        AgentRunEvent.objects.create(
            run=run,
            sequence_index=1,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Queued (watcher)",
            payload={"watcher_id": str(watcher.id), "type": "email_inbox", "message_id": message_id},
        )
        return str(run.id)

    def _update_watcher_success(self, watcher: AgentWatcher, *, now, poll_interval: int, inspected: int, triggered: int) -> None:
        next_poll_at = now + timedelta(seconds=int(poll_interval))
        metadata = dict(watcher.metadata or {}) if isinstance(watcher.metadata, dict) else {}
        metadata["last_poll"] = {
            "at": now.isoformat(),
            "inspected": int(inspected),
            "triggered": int(triggered),
        }
        cursor = metadata.get("cursor")
        if not isinstance(cursor, dict):
            cursor = {}
        cursor["after"] = now.isoformat()
        metadata["cursor"] = cursor

        AgentWatcher.objects.filter(id=watcher.id).update(
            last_polled_at=now,
            next_poll_at=next_poll_at,
            lease_expires_at=None,
            error_count=0,
            last_error="",
            metadata=metadata,
            updated_at=now,
        )

    def _schedule_backoff(self, watcher: AgentWatcher, message: str) -> AgentWatcherProcessResult:
        now = timezone.now()
        error_count = max(0, int(watcher.error_count or 0)) + 1
        backoff_seconds = min(self.max_retry_delay_seconds, float(2 ** min(10, error_count)))
        # Deterministic-ish jitter to avoid thundering herds across identical schedules.
        seed = f"{watcher.id}:{error_count}".encode("utf-8", errors="ignore")
        digest = hashlib.sha256(seed).digest()
        jitter = int.from_bytes(digest[:2], "big") / 65536.0
        delay = backoff_seconds + jitter
        next_poll_at = now + timedelta(seconds=float(delay))

        metadata = dict(watcher.metadata or {}) if isinstance(watcher.metadata, dict) else {}
        attempts = metadata.get("attempts")
        if not isinstance(attempts, list):
            attempts = []
        attempts.append({"at": now.isoformat(), "error": (message or "")[:400]})
        metadata["attempts"] = attempts[-10:]

        AgentWatcher.objects.filter(id=watcher.id).update(
            next_poll_at=next_poll_at,
            lease_expires_at=None,
            error_count=min(255, int(error_count)),
            last_error=(message or "")[:800],
            metadata=metadata,
            updated_at=now,
        )
        return AgentWatcherProcessResult(watcher_id=str(watcher.id), action="backoff", error=(message or "")[:240])

    def _pause_watcher(self, watcher: AgentWatcher, message: str) -> AgentWatcherProcessResult:
        now = timezone.now()
        metadata = dict(watcher.metadata or {}) if isinstance(watcher.metadata, dict) else {}
        metadata["paused_reason"] = {"at": now.isoformat(), "message": (message or "")[:400]}
        AgentWatcher.objects.filter(id=watcher.id).update(
            status=AgentWatcherStatus.PAUSED,
            next_poll_at=None,
            lease_expires_at=None,
            last_error=(message or "")[:800],
            metadata=metadata,
            updated_at=now,
        )
        logger.warning("agent_watcher.paused watcher=%s error=%s", watcher.id, message)
        return AgentWatcherProcessResult(watcher_id=str(watcher.id), action="paused", error=(message or "")[:240])

    def _prune_dedupe_keys(self, watcher: AgentWatcher, *, now) -> None:
        cutoff = now - timedelta(days=int(self.dedupe_ttl_days))
        try:
            AgentWatcherDedupeKey.objects.filter(watcher=watcher, created_at__lt=cutoff).delete()
        except Exception:  # pragma: no cover - best effort only
            logger.exception("agent_watcher.dedupe_prune_failed watcher=%s", watcher.id)
