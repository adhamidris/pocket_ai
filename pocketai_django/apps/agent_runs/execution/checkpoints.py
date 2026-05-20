from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from apps.agent_runs.execution.audit import _clip_text
from apps.agent_runs.models import (
    AgentRun,
    AgentRunCheckpoint,
    AgentRunCheckpointKind,
    AgentRunCheckpointStatus,
    AgentRunEventStream,
    AgentRunEventType,
    AgentRunStatus,
)
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender


class AgentRunCheckpointMixin:

    def _upsert_open_checkpoint(
        self,
        *,
        run: AgentRun,
        kind: str,
        title: str,
        prompt: str,
        payload: Mapping[str, object] | None,
        now,
        child_run: AgentRun | None = None,
    ) -> AgentRunCheckpoint:
        checkpoint = (
            AgentRunCheckpoint.objects.filter(run=run, kind=kind, status=AgentRunCheckpointStatus.OPEN)
            .order_by("-created_at")
            .first()
        )
        timeout_seconds = None
        automation = getattr(run, "automation", None)
        if automation is not None:
            config = automation.metadata if isinstance(getattr(automation, "metadata", None), Mapping) else {}
            timeout_seconds = config.get("checkpoint_timeout_seconds") or config.get("checkpointTimeoutSeconds")
        try:
            timeout_value = int(timeout_seconds) if timeout_seconds is not None else 86400
        except (TypeError, ValueError):
            timeout_value = 86400
        timeout_value = max(60, min(timeout_value, 60 * 60 * 24 * 30))
        expires_at = now + timedelta(seconds=timeout_value)
        values = {
            "business_profile": run.business_profile,
            "automation": run.automation,
            "conversation": run.conversation,
            "child_run": child_run,
            "title": _clip_text(title, 240),
            "prompt": _clip_text(prompt, 4000),
            "payload": dict(payload or {}),
            "expires_at": expires_at,
        }
        if checkpoint is None:
            checkpoint = AgentRunCheckpoint.objects.create(run=run, kind=kind, **values)
        else:
            for field, value in values.items():
                setattr(checkpoint, field, value)
            checkpoint.save(update_fields=[*values.keys(), "updated_at"])
        return checkpoint

    def _expire_due_checkpoints(self, *, now, limit: int = 25) -> int:
        due = list(
            AgentRunCheckpoint.objects.select_related("run")
            .filter(status=AgentRunCheckpointStatus.OPEN, expires_at__lte=now)
            .order_by("expires_at")[: max(1, int(limit))]
        )
        expired = 0
        for checkpoint in due:
            run = checkpoint.run
            checkpoint.status = AgentRunCheckpointStatus.EXPIRED
            checkpoint.resolved_at = now
            checkpoint.resolution = {"action": "expire", "reason": "timeout"}
            checkpoint.save(update_fields=["status", "resolved_at", "resolution", "updated_at"])
            AgentRun.objects.filter(id=run.id).update(
                status=AgentRunStatus.PAUSED,
                lease_expires_at=None,
                run_after=None,
                error_detail="checkpoint expired",
                updated_at=now,
            )
            self._append_event(
                run,
                stream=AgentRunEventStream.SYSTEM,
                event_type=AgentRunEventType.PAUSED,
                label="Checkpoint expired",
                payload={"checkpoint_id": str(checkpoint.id), "kind": checkpoint.kind},
            )
            expired += 1
        return expired

    def _resume_parent_if_child_finished(self, child: AgentRun, *, now) -> None:
        parent = getattr(child, "parent_run", None)
        if parent is None or parent.status != AgentRunStatus.WAITING_CHILD:
            return
        execution_conversation = None
        if parent.execution_conversation_id:
            execution_conversation = Conversation.objects.filter(
                id=parent.execution_conversation_id,
                business_profile_id=parent.business_profile_id,
            ).first()
        result = child.result if isinstance(getattr(child, "result", None), Mapping) else {}
        response_text = str(result.get("response_text") or "").strip()
        if execution_conversation is not None:
            ConversationMessage.objects.create(
                conversation=execution_conversation,
                sender=ConversationSender.CUSTOMER,
                body=(
                    f"Delegated run completed: {child.title or child.id}\n\n"
                    f"Status: {child.status}\n"
                    f"Result: {_clip_text(response_text or child.error_detail or '', 4000)}"
                ).strip(),
                metadata={"source": "agent_run_child_result", "agent_run_id": str(parent.id), "child_run_id": str(child.id)},
            )
            Conversation.objects.filter(id=execution_conversation.id).update(last_activity_at=now)
        AgentRunCheckpoint.objects.filter(
            run=parent,
            child_run=child,
            status=AgentRunCheckpointStatus.OPEN,
        ).update(
            status=AgentRunCheckpointStatus.RESOLVED,
            resolved_at=now,
            resolution={"action": "child_completed", "child_run_id": str(child.id), "child_status": child.status},
            updated_at=now,
        )
        meta = dict(parent.metadata or {}) if isinstance(getattr(parent, "metadata", None), Mapping) else {}
        meta.pop("pending_child_run_id", None)
        meta.pop("pending_checkpoint_id", None)
        AgentRun.objects.filter(id=parent.id).update(
            status=AgentRunStatus.QUEUED,
            run_after=now,
            lease_expires_at=None,
            metadata=meta,
            updated_at=now,
        )
        self._append_event(
            parent,
            stream=AgentRunEventStream.SYSTEM,
            event_type=AgentRunEventType.PROGRESS,
            label="Child run completed",
            payload={"child_run_id": str(child.id), "child_status": child.status},
        )
