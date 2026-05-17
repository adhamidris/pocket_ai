from __future__ import annotations

import uuid
from typing import Mapping

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.tenancy import tenant_bypass, tenant_context


def _parse_uuid(value: object) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value).strip())
    except (TypeError, ValueError):
        return None


def get_agent_run_id_from_conversation_metadata(conversation) -> uuid.UUID | None:
    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    return _parse_uuid(meta.get("agent_run_id") or meta.get("agentRunId"))


def get_actor_user_id_from_conversation_metadata(conversation) -> uuid.UUID | None:
    meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    return _parse_uuid(meta.get("actor_user_id") or meta.get("actorUserId"))


def get_agent_run_id_from_call_session_metadata(session) -> uuid.UUID | None:
    meta = session.metadata if isinstance(getattr(session, "metadata", None), Mapping) else {}
    return _parse_uuid(meta.get("agent_run_id") or meta.get("agentRunId"))


def resolve_agent_run(*, agent_run_id: uuid.UUID, business_id: object | None):
    from apps.agent_runs.models import AgentRun

    if not agent_run_id:
        return None
    ctx = tenant_context(business_id) if business_id else tenant_bypass()
    with ctx:
        return AgentRun.objects.filter(id=agent_run_id, business_profile_id=business_id).first()


def append_agent_run_event(
    *,
    run_id: uuid.UUID,
    business_id: object | None,
    stream: str,
    event_type: str,
    label: str = "",
    payload: dict[str, object] | None = None,
    update_run_fields: dict[str, object] | None = None,
) -> None:
    """
    Append an AgentRunEvent with deterministic per-run ordering.

    Optionally updates the AgentRun row in the same transaction to bump updated_at
    (and set status/result/etc).
    """

    from apps.agent_runs.models import AgentRun, AgentRunEvent

    ctx = tenant_context(business_id) if business_id else tenant_bypass()
    with ctx:
        with transaction.atomic():
            run = AgentRun.objects.select_for_update().filter(id=run_id).first()
            if not run:
                return

            next_index = AgentRunEvent.objects.filter(run=run).aggregate(max_index=Max("sequence_index")).get("max_index") or 0
            AgentRunEvent.objects.create(
                run=run,
                sequence_index=int(next_index) + 1,
                stream=stream,
                event_type=event_type,
                label=(label or "")[:240],
                payload=payload or {},
            )

            if update_run_fields is None:
                AgentRun.objects.filter(id=run.id).update(updated_at=timezone.now())
            else:
                patch = dict(update_run_fields)
                patch["updated_at"] = timezone.now()
                AgentRun.objects.filter(id=run.id).update(**patch)
