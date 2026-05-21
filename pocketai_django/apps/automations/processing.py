from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from django.db import IntegrityError, connection as db_connection, transaction
from django.utils import timezone

from apps.accounts.models import EmailAccountProvider, EmailAccountStatus
from apps.agent_runs.models import AgentRun, AgentRunEvent, AgentRunEventStream, AgentRunEventType, AgentRunSource, AgentRunStatus
from apps.automations.models import Automation, AutomationDedupeKey, AutomationStatus, AutomationTriggerType
from apps.automations.scheduling import CronScheduleError, compute_next_automation_schedule_at
from apps.conversations.instruction_contracts import normalize_workflow_instructions
from apps.integrations.accounts.email import ensure_fresh_email_credentials
from apps.integrations.providers.gmail import GmailApiError, gmail_search_messages
from apps.integrations.providers.microsoft_graph import GraphApiError, graph_list_messages

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutomationProcessResult:
    automation_id: str
    action: str
    run_id: str | None = None
    error: str | None = None
    triggered_run_ids: tuple[str, ...] = ()


def compute_next_automation_trigger_at(trigger_type: str, trigger_config: object, *, after=None):
    if trigger_type != AutomationTriggerType.SCHEDULE:
        return None
    config = dict(trigger_config or {}) if isinstance(trigger_config, Mapping) else {}
    config.setdefault("type", "cron")
    return compute_next_automation_schedule_at("cron", config, after=after or timezone.now())


def run_snapshot(automation: Automation) -> dict[str, Any]:
    instructions = automation.instructions if isinstance(automation.instructions, dict) else {}
    return normalize_workflow_instructions(
        {
            **instructions,
            "name": automation.name,
            "description": automation.description,
            "trigger_type": automation.trigger_type,
            "trigger_config": automation.trigger_config if isinstance(automation.trigger_config, dict) else {},
            "source_config": automation.source_config if isinstance(automation.source_config, dict) else {},
            "destination_config": automation.destination_config if isinstance(automation.destination_config, dict) else {},
            "notification_config": automation.notification_config if isinstance(automation.notification_config, dict) else {},
            "review_mode": automation.review_mode,
            "autonomy_mode": automation.autonomy_mode,
        }
    )


def append_run_event(run: AgentRun, *, label: str, payload: dict[str, object] | None = None) -> None:
    with transaction.atomic():
        locked = AgentRun.objects.select_for_update().get(id=run.id)
        next_index = AgentRunEvent.objects.filter(run=locked).count() + 1
        AgentRunEvent.objects.create(
            run=locked,
            sequence_index=next_index,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label=label[:240],
            payload=payload or {},
        )


class AutomationProcessingService:
    def process_next_due_automation(self) -> AutomationProcessResult | None:
        now = timezone.now()
        qs = (
            Automation.objects.select_related("business_profile", "agent_profile", "conversation", "email_account")
            .filter(status=AutomationStatus.ACTIVE)
            .filter(trigger_type__in=[AutomationTriggerType.SCHEDULE, AutomationTriggerType.EMAIL_INBOX])
            .filter(next_trigger_at__lte=now)
            .order_by("next_trigger_at", "created_at")
        )
        with transaction.atomic():
            for_update_kwargs: dict[str, Any] = {}
            if getattr(db_connection.features, "has_select_for_update_skip_locked", False):
                for_update_kwargs["skip_locked"] = True
            if getattr(db_connection.features, "has_select_for_update_of", False):
                for_update_kwargs["of"] = ("self",)
            automation = qs.select_for_update(**for_update_kwargs).first()
            if automation is None:
                return None
            if automation.trigger_type == AutomationTriggerType.SCHEDULE:
                return self._trigger_scheduled(automation, now=now)
            return self._poll_email_inbox(automation, now=now)

    def _trigger_scheduled(self, automation: Automation, *, now) -> AutomationProcessResult:
        try:
            next_at = compute_next_automation_trigger_at(automation.trigger_type, automation.trigger_config, after=now)
        except CronScheduleError as exc:
            Automation.objects.filter(id=automation.id).update(status=AutomationStatus.PAUSED, last_error=str(exc)[:1000], updated_at=now)
            return AutomationProcessResult(automation_id=str(automation.id), action="paused", error=str(exc)[:400])
        run = AgentRun.objects.create(
            business_profile=automation.business_profile,
            agent_profile=automation.agent_profile,
            automation=automation,
            conversation=None,
            created_by=automation.created_by,
            run_snapshot=run_snapshot(automation),
            title=(automation.name or "Scheduled task")[:200],
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.QUEUED,
            visibility=automation.visibility,
            metadata={
                "automation_id": str(automation.id),
                "trigger": "schedule",
            },
            run_after=now,
        )
        append_run_event(run, label="Queued (automation schedule)", payload={"automation_id": str(automation.id), "trigger": "schedule"})
        Automation.objects.filter(id=automation.id).update(last_triggered_at=now, next_trigger_at=next_at, updated_at=now)
        return AutomationProcessResult(automation_id=str(automation.id), action="triggered", run_id=str(run.id))

    def _poll_email_inbox(self, automation: Automation, *, now) -> AutomationProcessResult:
        account = automation.email_account
        if account is None:
            Automation.objects.filter(id=automation.id).update(status=AutomationStatus.PAUSED, last_error="email account is required", updated_at=now)
            return AutomationProcessResult(automation_id=str(automation.id), action="paused", error="email account is required")
        if account.status != EmailAccountStatus.CONNECTED:
            Automation.objects.filter(id=automation.id).update(status=AutomationStatus.PAUSED, last_error="email account is not connected", updated_at=now)
            return AutomationProcessResult(automation_id=str(automation.id), action="paused", error="email account is not connected")
        config = automation.source_config if isinstance(automation.source_config, Mapping) else {}
        metadata = automation.state if isinstance(automation.state, Mapping) else {}
        query = str(config.get("query") or "newer_than:1d").strip()
        unread_only = bool(config.get("unreadOnly", True))
        cursor = metadata.get("cursor") if isinstance(metadata.get("cursor"), Mapping) else {}
        after = str(cursor.get("after") or "").strip() or (automation.last_polled_at.isoformat() if automation.last_polled_at else None)
        max_events = max(1, min(int(automation.max_events_per_poll or 5), 25))
        try:
            account = ensure_fresh_email_credentials(account)
            messages = self._poll_email_provider(
                provider=account.provider,
                access_token=str((account.credentials or {}).get("access_token") or "").strip(),
                query=query,
                unread_only=unread_only,
                after=after,
                limit=max_events * 3,
            )
        except (GmailApiError, GraphApiError, ValueError) as exc:
            next_at = now + timedelta(seconds=max(60, int(automation.poll_interval_seconds or 300)))
            Automation.objects.filter(id=automation.id).update(
                error_count=int(automation.error_count or 0) + 1,
                last_error=str(exc)[:1000],
                next_trigger_at=next_at,
                updated_at=now,
            )
            return AutomationProcessResult(automation_id=str(automation.id), action="backoff", error=str(exc)[:240])
        triggered: list[str] = []
        for message in messages or []:
            if len(triggered) >= max_events:
                break
            message_id = str((message or {}).get("id") or (message or {}).get("message_id") or "").strip()
            if not message_id:
                digest = hashlib.sha256(repr(message).encode("utf-8", errors="ignore")).hexdigest()[:32]
                message_id = f"digest:{digest}"
            if not self._record_dedupe_key(automation, self._email_dedupe_key(account.provider, account.id, message_id)):
                continue
            run = AgentRun.objects.create(
                business_profile=automation.business_profile,
                agent_profile=automation.agent_profile,
                automation=automation,
                conversation=None,
                created_by=automation.created_by,
                run_snapshot=run_snapshot(automation),
                title=(f"{automation.name}: {(message or {}).get('subject') or 'New email'}")[:200],
                source=AgentRunSource.EMAIL_INBOX,
                status=AgentRunStatus.QUEUED,
                visibility=automation.visibility,
                metadata={
                    "automation_id": str(automation.id),
                    "trigger": "email_inbox",
                    "message": message,
                },
                run_after=now,
            )
            append_run_event(run, label="Queued (email inbox automation)", payload={"automation_id": str(automation.id), "message_id": message_id})
            triggered.append(str(run.id))
        next_at = now + timedelta(seconds=max(60, int(automation.poll_interval_seconds or 300)))
        Automation.objects.filter(id=automation.id).update(
            last_polled_at=now,
            last_triggered_at=now if triggered else automation.last_triggered_at,
            next_trigger_at=next_at,
            error_count=0,
            last_error="",
            state={**dict(metadata), "last_email_poll": {"at": now.isoformat(), "triggered": len(triggered)}},
            updated_at=now,
        )
        return AutomationProcessResult(automation_id=str(automation.id), action="polled", triggered_run_ids=tuple(triggered))

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
        if not access_token:
            raise ValueError("email account missing access_token")
        safe_limit = max(1, min(int(limit or 0), 25))
        provider_key = str(provider or "").strip().lower()

        if provider_key == EmailAccountProvider.GOOGLE:
            effective_query = query or "is:unread"
            if unread_only and "is:unread" not in effective_query:
                effective_query = f"{effective_query} is:unread".strip()
            payload = gmail_search_messages(access_token=access_token, query=effective_query, limit=safe_limit, include_snippets_limit=5)
            items = payload.get("results") if isinstance(payload.get("results"), list) else []
            return [item for item in items if isinstance(item, Mapping)]

        if provider_key == EmailAccountProvider.MICROSOFT:
            payload = graph_list_messages(access_token=access_token, limit=safe_limit, unread_only=unread_only, after=after)
            items = payload.get("results") if isinstance(payload.get("results"), list) else []
            return [item for item in items if isinstance(item, Mapping)]

        raise GraphApiError("Email provider is not supported for automations yet.")

    @staticmethod
    def _record_dedupe_key(automation: Automation, dedupe_key: str) -> bool:
        try:
            AutomationDedupeKey.objects.create(automation=automation, business_profile_id=automation.business_profile_id, dedupe_key=dedupe_key[:255])
            return True
        except IntegrityError:
            return False
