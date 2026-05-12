from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from django.db import IntegrityError, connection as db_connection, transaction
from django.utils import timezone

from core.tenancy import tenant_context

from apps.accounts.models import EmailAccountProvider, EmailAccountStatus
from apps.conversations.workflow_scheduling import CronScheduleError, compute_next_workflow_schedule_at
from apps.conversations.models import (
    AgentRun,
    AgentRunEvent,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunSource,
    AgentRunStatus,
    AgentWorkflow,
    AgentWorkflowDedupeKey,
    AgentWorkflowStatus,
    AgentWorkflowTriggerType,
    Conversation,
    ConversationChannel,
    ConversationMessage,
    ConversationSender,
    ConversationStatus,
)
from apps.conversations.workflow_contracts import normalize_workflow_instructions
from apps.integrations.email_accounts import ensure_fresh_email_credentials
from apps.integrations.gmail import GmailApiError, gmail_search_messages
from apps.integrations.microsoft_graph import GraphApiError, graph_list_messages

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentWorkflowProcessResult:
    workflow_id: str
    action: str
    run_id: str | None = None
    error: str | None = None
    triggered_run_ids: tuple[str, ...] = ()


def compute_next_workflow_trigger_at(trigger_type: str, trigger_config: object, *, after=None):
    if trigger_type != AgentWorkflowTriggerType.SCHEDULE:
        return None
    config = dict(trigger_config or {}) if isinstance(trigger_config, Mapping) else {}
    config.setdefault("type", "cron")
    return compute_next_workflow_schedule_at("cron", config, after=after or timezone.now())


def workflow_snapshot(workflow: AgentWorkflow) -> dict[str, Any]:
    instructions = workflow.instructions if isinstance(workflow.instructions, dict) else {}
    return normalize_workflow_instructions(
        {
            **instructions,
            "name": workflow.name,
            "description": workflow.description,
            "trigger_type": workflow.trigger_type,
            "trigger_config": workflow.trigger_config if isinstance(workflow.trigger_config, dict) else {},
            "source_config": workflow.source_config if isinstance(workflow.source_config, dict) else {},
            "destination_config": workflow.destination_config if isinstance(workflow.destination_config, dict) else {},
            "notification_config": workflow.notification_config if isinstance(workflow.notification_config, dict) else {},
            "review_mode": workflow.review_mode,
            "autonomy_mode": workflow.autonomy_mode,
        }
    )


def ensure_workflow_thread(workflow: AgentWorkflow) -> Conversation:
    if workflow.conversation_id and getattr(workflow, "conversation", None):
        return workflow.conversation
    if workflow.conversation_id:
        existing = Conversation.objects.filter(id=workflow.conversation_id, business_profile=workflow.business_profile).first()
        if existing:
            workflow.conversation = existing
            return existing
    conversation = Conversation.objects.create(
        business_profile=workflow.business_profile,
        agent_profile=workflow.agent_profile,
        owner_user=workflow.created_by or workflow.agent_profile.user,
        channel=ConversationChannel.API,
        status=ConversationStatus.LIVE,
        metadata={"type": "workflow_thread", "workflow_id": str(workflow.id), "workflow_name": workflow.name},
    )
    ConversationMessage.objects.create(
        conversation=conversation,
        sender=ConversationSender.SYSTEM,
        body=f"Task thread created for {workflow.name}.",
        metadata={"type": "workflow_thread_intro", "workflow_id": str(workflow.id)},
    )
    AgentWorkflow.objects.filter(id=workflow.id).update(conversation_id=conversation.id, updated_at=timezone.now())
    workflow.conversation = conversation
    workflow.conversation_id = conversation.id
    return conversation


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


class AgentWorkflowProcessingService:
    def process_next_due_workflow(self) -> AgentWorkflowProcessResult | None:
        now = timezone.now()
        qs = (
            AgentWorkflow.objects.select_related("business_profile", "agent_profile", "conversation", "email_account")
            .filter(status=AgentWorkflowStatus.ACTIVE)
            .filter(trigger_type__in=[AgentWorkflowTriggerType.SCHEDULE, AgentWorkflowTriggerType.EMAIL_INBOX])
            .filter(next_trigger_at__lte=now)
            .order_by("next_trigger_at", "created_at")
        )
        with transaction.atomic():
            for_update_kwargs: dict[str, Any] = {}
            if getattr(db_connection.features, "has_select_for_update_skip_locked", False):
                for_update_kwargs["skip_locked"] = True
            if getattr(db_connection.features, "has_select_for_update_of", False):
                for_update_kwargs["of"] = ("self",)
            workflow = qs.select_for_update(**for_update_kwargs).first()
            if workflow is None:
                return None
            if workflow.trigger_type == AgentWorkflowTriggerType.SCHEDULE:
                return self._trigger_scheduled(workflow, now=now)
            return self._poll_email_inbox(workflow, now=now)

    def _trigger_scheduled(self, workflow: AgentWorkflow, *, now) -> AgentWorkflowProcessResult:
        try:
            next_at = compute_next_workflow_trigger_at(workflow.trigger_type, workflow.trigger_config, after=now)
        except CronScheduleError as exc:
            AgentWorkflow.objects.filter(id=workflow.id).update(status=AgentWorkflowStatus.PAUSED, last_error=str(exc)[:1000], updated_at=now)
            return AgentWorkflowProcessResult(workflow_id=str(workflow.id), action="paused", error=str(exc)[:400])
        with tenant_context(workflow.business_profile_id):
            conversation = ensure_workflow_thread(workflow)
        run = AgentRun.objects.create(
            business_profile=workflow.business_profile,
            agent_profile=workflow.agent_profile,
            workflow=workflow,
            conversation=conversation,
            created_by=workflow.created_by,
            workflow_snapshot=workflow_snapshot(workflow),
            title=(workflow.name or "Scheduled task")[:200],
            source=AgentRunSource.SCHEDULE,
            status=AgentRunStatus.QUEUED,
            visibility=workflow.visibility,
            metadata={
                "workflow_id": str(workflow.id),
                "trigger": "schedule",
                "responsible_context": self._responsible_context_snapshot(workflow),
            },
            run_after=now,
        )
        append_run_event(run, label="Queued (workflow schedule)", payload={"workflow_id": str(workflow.id), "trigger": "schedule"})
        AgentWorkflow.objects.filter(id=workflow.id).update(last_triggered_at=now, next_trigger_at=next_at, updated_at=now)
        return AgentWorkflowProcessResult(workflow_id=str(workflow.id), action="triggered", run_id=str(run.id))

    def _poll_email_inbox(self, workflow: AgentWorkflow, *, now) -> AgentWorkflowProcessResult:
        account = workflow.email_account
        if account is None:
            AgentWorkflow.objects.filter(id=workflow.id).update(status=AgentWorkflowStatus.PAUSED, last_error="email account is required", updated_at=now)
            return AgentWorkflowProcessResult(workflow_id=str(workflow.id), action="paused", error="email account is required")
        if account.status != EmailAccountStatus.CONNECTED:
            AgentWorkflow.objects.filter(id=workflow.id).update(status=AgentWorkflowStatus.PAUSED, last_error="email account is not connected", updated_at=now)
            return AgentWorkflowProcessResult(workflow_id=str(workflow.id), action="paused", error="email account is not connected")
        config = workflow.source_config if isinstance(workflow.source_config, Mapping) else {}
        metadata = workflow.state if isinstance(workflow.state, Mapping) else {}
        query = str(config.get("query") or "newer_than:1d").strip()
        unread_only = bool(config.get("unreadOnly", True))
        cursor = metadata.get("cursor") if isinstance(metadata.get("cursor"), Mapping) else {}
        after = str(cursor.get("after") or "").strip() or (workflow.last_polled_at.isoformat() if workflow.last_polled_at else None)
        max_events = max(1, min(int(workflow.max_events_per_poll or 5), 25))
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
            next_at = now + timedelta(seconds=max(60, int(workflow.poll_interval_seconds or 300)))
            AgentWorkflow.objects.filter(id=workflow.id).update(
                error_count=int(workflow.error_count or 0) + 1,
                last_error=str(exc)[:1000],
                next_trigger_at=next_at,
                updated_at=now,
            )
            return AgentWorkflowProcessResult(workflow_id=str(workflow.id), action="backoff", error=str(exc)[:240])
        triggered: list[str] = []
        for message in messages or []:
            if len(triggered) >= max_events:
                break
            message_id = str((message or {}).get("id") or (message or {}).get("message_id") or "").strip()
            if not message_id:
                digest = hashlib.sha256(repr(message).encode("utf-8", errors="ignore")).hexdigest()[:32]
                message_id = f"digest:{digest}"
            if not self._record_dedupe_key(workflow, self._email_dedupe_key(account.provider, account.id, message_id)):
                continue
            run = AgentRun.objects.create(
                business_profile=workflow.business_profile,
                agent_profile=workflow.agent_profile,
                workflow=workflow,
                conversation=ensure_workflow_thread(workflow),
                created_by=workflow.created_by,
                workflow_snapshot=workflow_snapshot(workflow),
                title=(f"{workflow.name}: {(message or {}).get('subject') or 'New email'}")[:200],
                source=AgentRunSource.EMAIL_INBOX,
                status=AgentRunStatus.QUEUED,
                visibility=workflow.visibility,
                metadata={
                    "workflow_id": str(workflow.id),
                    "trigger": "email_inbox",
                    "message": message,
                    "responsible_context": self._responsible_context_snapshot(workflow),
                },
                run_after=now,
            )
            append_run_event(run, label="Queued (email inbox workflow)", payload={"workflow_id": str(workflow.id), "message_id": message_id})
            triggered.append(str(run.id))
        next_at = now + timedelta(seconds=max(60, int(workflow.poll_interval_seconds or 300)))
        AgentWorkflow.objects.filter(id=workflow.id).update(
            last_polled_at=now,
            last_triggered_at=now if triggered else workflow.last_triggered_at,
            next_trigger_at=next_at,
            error_count=0,
            last_error="",
            state={**dict(metadata), "last_email_poll": {"at": now.isoformat(), "triggered": len(triggered)}},
            updated_at=now,
        )
        return AgentWorkflowProcessResult(workflow_id=str(workflow.id), action="polled", triggered_run_ids=tuple(triggered))

    @staticmethod
    def _responsible_context_snapshot(workflow: AgentWorkflow) -> dict[str, object]:
        department_id = getattr(workflow, "department_id", None)
        return {
            "mode": "department" if department_id else "main_agent",
            "agent_id": str(workflow.agent_profile_id) if workflow.agent_profile_id else None,
            "department_id": str(department_id) if department_id else None,
        }

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

        raise GraphApiError("Email provider is not supported for workflows yet.")

    @staticmethod
    def _record_dedupe_key(workflow: AgentWorkflow, dedupe_key: str) -> bool:
        try:
            AgentWorkflowDedupeKey.objects.create(workflow=workflow, business_profile_id=workflow.business_profile_id, dedupe_key=dedupe_key[:255])
            return True
        except IntegrityError:
            return False
