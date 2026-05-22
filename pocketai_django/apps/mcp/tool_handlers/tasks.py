"""Persistent Agentic Task MCP tool handlers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Mapping

from apps.conversations.models import Conversation

from ..types import ToolExecutionContext


def _agentic_task_payload(agentic_task) -> dict[str, object]:
    instructions = agentic_task.instructions if isinstance(agentic_task.instructions, dict) else {}
    schedule_config = agentic_task.schedule_config if isinstance(agentic_task.schedule_config, dict) else {}
    compact_schedule: dict[str, object] = {}
    for key in ("type", "cron", "timezone"):
        value = schedule_config.get(key)
        if isinstance(value, str) and value.strip():
            compact_schedule[key] = value.strip()[:160]

    payload = {
        "id": str(agentic_task.id),
        "agent_id": str(agentic_task.agent_profile_id),
        "active_conversation_id": str(agentic_task.active_conversation_id) if agentic_task.active_conversation_id else None,
        "name": agentic_task.name,
        "status": agentic_task.status,
        "visibility": agentic_task.visibility,
        "goal": str(instructions.get("goal") or ""),
        "schedule_enabled": bool(agentic_task.schedule_enabled),
        "schedule_config": compact_schedule,
        "next_trigger_at": agentic_task.next_trigger_at.isoformat() if agentic_task.next_trigger_at else None,
        "last_triggered_at": agentic_task.last_triggered_at.isoformat() if agentic_task.last_triggered_at else None,
        "last_error": agentic_task.last_error or "",
    }
    return payload


def _task_schedule_config_from_args(arguments: Mapping[str, object], *, current: Mapping[str, object] | None = None) -> dict[str, object]:
    raw = arguments.get("schedule_config") if "schedule_config" in arguments else arguments.get("scheduleConfig")
    source = raw if isinstance(raw, Mapping) else current if isinstance(current, Mapping) else {}
    out: dict[str, object] = {"type": "cron"}
    cron = str(source.get("cron") or "").strip()
    timezone_value = str(source.get("timezone") or "").strip()
    if cron:
        out["cron"] = cron[:120]
    if timezone_value:
        out["timezone"] = timezone_value[:120]
    return out


def _has_cron_schedule(schedule_config: Mapping[str, object] | None) -> bool:
    return bool(str((schedule_config or {}).get("cron") or "").strip())


def _first_present(arguments: Mapping[str, object], *keys: str) -> tuple[bool, object]:
    for key in keys:
        if key in arguments:
            return True, arguments.get(key)
    return False, None


def _task_instruction_spec_from_args(
    arguments: Mapping[str, object],
    *,
    current: Mapping[str, object] | None = None,
) -> dict[str, object]:
    from apps.conversations.instruction_contracts import infer_workflow_type_and_memory_shape, normalize_workflow_instructions

    spec: dict[str, object] = {"goal": str((current or {}).get("goal") or "").strip()[:6000]} if current else {}

    present, value = _first_present(arguments, "goal")
    if present:
        spec["goal"] = str(value or "").strip()[:6000]

    if not spec.get("workflow_type") or not spec.get("memory_shape"):
        inferred_type, inferred_shape = infer_workflow_type_and_memory_shape(
            goal=spec.get("goal") or arguments.get("goal"),
            schedule_type="schedule" if arguments.get("schedule_enabled") or arguments.get("scheduleEnabled") else "manual",
        )
        spec.setdefault("workflow_type", inferred_type)
        spec.setdefault("memory_shape", inferred_shape)

    return normalize_workflow_instructions(spec)


def _has_task_instruction_updates(arguments: Mapping[str, object]) -> bool:
    return "goal" in arguments


def _remember_agentic_task_resource_ref(
    conversation: Conversation,
    agentic_task,
    *,
    purpose: str,
    pending_activation: bool = False,
) -> None:
    metadata = dict(conversation.metadata) if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    refs_raw = metadata.get("resource_refs")
    refs = [dict(item) for item in refs_raw if isinstance(item, Mapping)] if isinstance(refs_raw, list) else []
    agentic_task_id = str(getattr(agentic_task, "id", "") or "").strip()
    if not agentic_task_id:
        return

    refs = [
        ref
        for ref in refs
        if not (
            str(ref.get("type") or "").strip().lower() in {"agentic_task", "task"}
            and str(ref.get("id") or "").strip() == agentic_task_id
        )
    ]
    refs.insert(
        0,
        {
            "type": "agentic_task",
            "id": agentic_task_id,
            "name": str(getattr(agentic_task, "name", "") or "")[:160],
            "status": str(getattr(agentic_task, "status", "") or ""),
            "purpose": str(purpose or "reference")[:80],
            "agent_id": str(getattr(agentic_task, "agent_profile_id", "") or ""),
            "schedule_enabled": bool(getattr(agentic_task, "schedule_enabled", False)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    metadata["resource_refs"] = refs[:12]
    if pending_activation:
        metadata["pending_agentic_task_activation_id"] = agentic_task_id
    elif str(metadata.get("pending_agentic_task_activation_id") or "") == agentic_task_id:
        metadata.pop("pending_agentic_task_activation_id", None)

    conversation.metadata = metadata
    conversation.save(update_fields=["metadata", "last_activity_at"])


def _resolve_task_agent(conversation: Conversation, raw_agent_id: object = None):
    from apps.accounts.models import AgentProfile

    if raw_agent_id:
        try:
            agent_id = uuid.UUID(str(raw_agent_id))
        except (TypeError, ValueError):
            return None, {"status": "error", "error_code": "validation_failed", "error": "agent_id must be a UUID."}
        agent = AgentProfile.objects.filter(id=agent_id, business_profile_id=conversation.business_profile_id).first()
        if agent is None:
            return None, {"status": "error", "error_code": "agent_not_found", "error": "Agent not found."}
        return agent, None
    agent = getattr(conversation, "agent_profile", None)
    if agent is None:
        return None, {"status": "error", "error_code": "missing_agent_profile", "error": "Conversation is not linked to an agent."}
    return agent, None


def _list_tasks_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.agentic_tasks.models import AgenticTask

    qs = AgenticTask.objects.select_related("agent_profile").filter(business_profile_id=conversation.business_profile_id)
    agent_id_raw = arguments.get("agent_id") or arguments.get("agentId")
    if agent_id_raw:
        try:
            qs = qs.filter(agent_profile_id=uuid.UUID(str(agent_id_raw)))
        except (TypeError, ValueError):
            return {"tool": "list_tasks", "status": "error", "error_code": "validation_failed", "error": "agent_id must be a UUID."}
    status = str(arguments.get("status") or "all").strip().lower()
    if status and status != "all":
        qs = qs.filter(status=status)
    limit = max(1, min(int(arguments.get("limit") or 20), 50))
    return {"tool": "list_tasks", "status": "ok", "agentic_tasks": [_agentic_task_payload(item) for item in qs.order_by("-updated_at")[:limit]]}


def _draft_agentic_task_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.agent_runs.models import AgentRunVisibility
    from apps.agentic_tasks.models import AgenticTask, AgenticTaskStatus

    agent, error = _resolve_task_agent(conversation, arguments.get("agent_id") or arguments.get("agentId"))
    if error:
        return {"tool": "draft_agentic_task", **error}
    name = str(arguments.get("name") or "").strip()
    goal = str(arguments.get("goal") or "").strip()
    if not name or not goal:
        return {"tool": "draft_agentic_task", "status": "error", "error_code": "validation_failed", "error": "name and goal are required."}
    visibility = str(arguments.get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
    if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
        visibility = AgentRunVisibility.INITIATOR
    schedule_config = _task_schedule_config_from_args(arguments)
    schedule_enabled = bool(arguments.get("schedule_enabled") if "schedule_enabled" in arguments else arguments.get("scheduleEnabled"))
    if schedule_enabled and not _has_cron_schedule(schedule_config):
        schedule_enabled = False
        schedule_config = {}
    instructions = _task_instruction_spec_from_args(arguments)
    workflow_type = str(instructions.get("workflow_type") or "general")
    memory_shape = str(instructions.get("memory_shape") or "general")
    description = str(arguments.get("description") or "").strip()[:4000]
    agentic_task = AgenticTask.objects.create(
        business_profile_id=conversation.business_profile_id,
        agent_profile=agent,
        created_by=getattr(conversation, "owner_user", None) or getattr(agent, "user", None),
        name=name[:160],
        description=description,
        status=AgenticTaskStatus.DRAFT,
        visibility=visibility,
        schedule_enabled=schedule_enabled,
        schedule_config=schedule_config,
        instructions=instructions,
        metadata={
            "source": "chat_task_draft",
            "activation_requires_user_approval": True,
            "workflow_type": workflow_type,
            "memory_shape": memory_shape,
        },
    )
    _remember_agentic_task_resource_ref(
        conversation,
        agentic_task,
        purpose="pending activation",
        pending_activation=True,
    )
    return {
        "tool": "draft_agentic_task",
        "status": "ok",
        "agentic_task": _agentic_task_payload(agentic_task),
        "activation_required": True,
        "hint": "Agentic Task draft saved. Ask the user to approve activation before calling request_agentic_task_activation with approved=true.",
    }


def _update_agentic_task_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.agent_runs.models import AgentRunVisibility
    from apps.agentic_tasks.models import AgenticTask

    try:
        task_id = uuid.UUID(str(arguments.get("agentic_task_id") or arguments.get("agenticTaskId") or arguments.get("task_id") or arguments.get("taskId") or ""))
    except (TypeError, ValueError):
        return {"tool": "update_agentic_task", "status": "error", "error_code": "validation_failed", "error": "agentic_task_id must be a UUID."}
    agentic_task = AgenticTask.objects.filter(id=task_id, business_profile_id=conversation.business_profile_id).first()
    if agentic_task is None:
        return {"tool": "update_agentic_task", "status": "error", "error_code": "not_found", "error": "Agentic Task not found."}
    updates: list[str] = []
    if "name" in arguments:
        agentic_task.name = str(arguments.get("name") or "").strip()[:160]
        updates.append("name")
    if "description" in arguments:
        agentic_task.description = str(arguments.get("description") or "").strip()[:4000]
        updates.append("description")
    if _has_task_instruction_updates(arguments):
        current = dict(agentic_task.instructions or {}) if isinstance(agentic_task.instructions, dict) else {}
        agentic_task.instructions = _task_instruction_spec_from_args(arguments, current=current)
        metadata = dict(agentic_task.metadata or {}) if isinstance(agentic_task.metadata, dict) else {}
        if agentic_task.instructions.get("workflow_type"):
            metadata["workflow_type"] = agentic_task.instructions.get("workflow_type")
        if agentic_task.instructions.get("memory_shape"):
            metadata["memory_shape"] = agentic_task.instructions.get("memory_shape")
        agentic_task.metadata = metadata
        updates.append("instructions")
        updates.append("metadata")
    if "schedule_enabled" in arguments or "scheduleEnabled" in arguments:
        agentic_task.schedule_enabled = bool(arguments.get("schedule_enabled") if "schedule_enabled" in arguments else arguments.get("scheduleEnabled"))
        updates.append("schedule_enabled")
    if "schedule_config" in arguments or "scheduleConfig" in arguments:
        agentic_task.schedule_config = _task_schedule_config_from_args(arguments, current=agentic_task.schedule_config)
        updates.append("schedule_config")
    if "visibility" in arguments:
        visibility = str(arguments.get("visibility") or "").strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return {"tool": "update_agentic_task", "status": "error", "error_code": "validation_failed", "error": "Invalid visibility."}
        agentic_task.visibility = visibility
        updates.append("visibility")
    if updates:
        agentic_task.save(update_fields=sorted(set([*updates, "updated_at"])))
        purpose = "pending activation" if agentic_task.status == "draft" else "updated"
        _remember_agentic_task_resource_ref(
            conversation,
            agentic_task,
            purpose=purpose,
            pending_activation=agentic_task.status == "draft",
        )
    return {"tool": "update_agentic_task", "status": "ok", "agentic_task": _agentic_task_payload(agentic_task)}


def _request_agentic_task_activation_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from django.utils import timezone as django_timezone
    from apps.agentic_tasks.models import AgenticTask, AgenticTaskStatus
    from apps.agentic_tasks.scheduling import CronScheduleError, compute_next_agentic_task_schedule_at

    try:
        task_id = uuid.UUID(str(arguments.get("agentic_task_id") or arguments.get("agenticTaskId") or arguments.get("task_id") or arguments.get("taskId") or ""))
    except (TypeError, ValueError):
        return {"tool": "request_agentic_task_activation", "status": "error", "error_code": "validation_failed", "error": "agentic_task_id must be a UUID."}
    agentic_task = AgenticTask.objects.filter(id=task_id, business_profile_id=conversation.business_profile_id).first()
    if agentic_task is None:
        return {"tool": "request_agentic_task_activation", "status": "error", "error_code": "not_found", "error": "Agentic Task not found."}
    if not bool(arguments.get("approved")):
        return {
            "tool": "request_agentic_task_activation",
            "status": "needs_user",
            "agentic_task": _agentic_task_payload(agentic_task),
            "prompt": "Please approve activating this Agentic Task before it starts running.",
        }
    next_trigger_at = None
    if agentic_task.schedule_enabled and not _has_cron_schedule(agentic_task.schedule_config if isinstance(agentic_task.schedule_config, dict) else {}):
        agentic_task.schedule_enabled = False
        agentic_task.schedule_config = {}
    if agentic_task.schedule_enabled:
        try:
            next_trigger_at = compute_next_agentic_task_schedule_at("cron", dict(agentic_task.schedule_config or {}), after=django_timezone.now())
        except CronScheduleError as exc:
            return {"tool": "request_agentic_task_activation", "status": "error", "error_code": "validation_failed", "error": str(exc)}
    agentic_task.status = AgenticTaskStatus.ACTIVE
    agentic_task.next_trigger_at = next_trigger_at
    agentic_task.save(update_fields=["status", "schedule_enabled", "schedule_config", "next_trigger_at", "updated_at"])
    _remember_agentic_task_resource_ref(
        conversation,
        agentic_task,
        purpose="active",
        pending_activation=False,
    )
    return {"tool": "request_agentic_task_activation", "status": "ok", "agentic_task": _agentic_task_payload(agentic_task), "activated": True}


def _pause_agentic_task_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.agentic_tasks.models import AgenticTask, AgenticTaskStatus

    try:
        task_id = uuid.UUID(str(arguments.get("agentic_task_id") or arguments.get("agenticTaskId") or arguments.get("task_id") or arguments.get("taskId") or ""))
    except (TypeError, ValueError):
        return {"tool": "pause_agentic_task", "status": "error", "error_code": "validation_failed", "error": "agentic_task_id must be a UUID."}
    agentic_task = AgenticTask.objects.filter(id=task_id, business_profile_id=conversation.business_profile_id).first()
    if agentic_task is None:
        return {"tool": "pause_agentic_task", "status": "error", "error_code": "not_found", "error": "Agentic Task not found."}
    metadata = dict(agentic_task.metadata or {}) if isinstance(agentic_task.metadata, dict) else {}
    if arguments.get("reason"):
        metadata["last_pause_reason"] = str(arguments.get("reason"))[:500]
    agentic_task.status = AgenticTaskStatus.PAUSED
    agentic_task.next_trigger_at = None
    agentic_task.metadata = metadata
    agentic_task.save(update_fields=["status", "next_trigger_at", "metadata", "updated_at"])
    _remember_agentic_task_resource_ref(
        conversation,
        agentic_task,
        purpose="paused",
        pending_activation=False,
    )
    return {"tool": "pause_agentic_task", "status": "ok", "agentic_task": _agentic_task_payload(agentic_task)}
