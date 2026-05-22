"""
Persistent task automation MCP tool handlers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Mapping

from apps.conversations.models import Conversation

from ..types import ToolExecutionContext


def _automation_payload(automation) -> dict[str, object]:
    instructions = automation.instructions if isinstance(automation.instructions, dict) else {}
    trigger_config = automation.trigger_config if isinstance(automation.trigger_config, dict) else {}
    compact_trigger: dict[str, object] = {}
    for key in ("type", "cron", "timezone"):
        value = trigger_config.get(key)
        if isinstance(value, str) and value.strip():
            compact_trigger[key] = value.strip()[:160]

    payload = {
        "id": str(automation.id),
        "agent_id": str(automation.agent_profile_id),
        "name": automation.name,
        "status": automation.status,
        "visibility": automation.visibility,
        "trigger_type": automation.trigger_type,
        "goal": str(instructions.get("goal") or ""),
        "trigger_config": compact_trigger,
        "next_trigger_at": automation.next_trigger_at.isoformat() if automation.next_trigger_at else None,
        "last_triggered_at": automation.last_triggered_at.isoformat() if automation.last_triggered_at else None,
        "last_error": automation.last_error or "",
    }
    return payload


def _task_trigger_config_from_args(arguments: Mapping[str, object], trigger_type: str, *, current: Mapping[str, object] | None = None) -> dict[str, object]:
    raw = arguments.get("trigger_config") if "trigger_config" in arguments else arguments.get("triggerConfig")
    source = raw if isinstance(raw, Mapping) else current if isinstance(current, Mapping) else {}
    if trigger_type == "schedule":
        out: dict[str, object] = {"type": "cron"}
        cron = str(source.get("cron") or "").strip()
        timezone_value = str(source.get("timezone") or "").strip()
        if cron:
            out["cron"] = cron[:120]
        if timezone_value:
            out["timezone"] = timezone_value[:120]
        return out
    return {}


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
            trigger_type=arguments.get("trigger_type") or arguments.get("triggerType"),
        )
        spec.setdefault("workflow_type", inferred_type)
        spec.setdefault("memory_shape", inferred_shape)

    return normalize_workflow_instructions(spec)


def _has_task_instruction_updates(arguments: Mapping[str, object]) -> bool:
    return "goal" in arguments


def _remember_automation_resource_ref(
    conversation: Conversation,
    automation,
    *,
    purpose: str,
    pending_activation: bool = False,
) -> None:
    metadata = dict(conversation.metadata) if isinstance(getattr(conversation, "metadata", None), Mapping) else {}
    refs_raw = metadata.get("resource_refs")
    refs = [dict(item) for item in refs_raw if isinstance(item, Mapping)] if isinstance(refs_raw, list) else []
    automation_id = str(getattr(automation, "id", "") or "").strip()
    if not automation_id:
        return

    refs = [
        ref
        for ref in refs
        if not (
            str(ref.get("type") or "").strip().lower() in {"automation", "task"}
            and str(ref.get("id") or "").strip() == automation_id
        )
    ]
    refs.insert(
        0,
        {
            "type": "automation",
            "id": automation_id,
            "name": str(getattr(automation, "name", "") or "")[:160],
            "status": str(getattr(automation, "status", "") or ""),
            "purpose": str(purpose or "reference")[:80],
            "agent_id": str(getattr(automation, "agent_profile_id", "") or ""),
            "trigger_type": str(getattr(automation, "trigger_type", "") or ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    metadata["resource_refs"] = refs[:12]
    if pending_activation:
        metadata["pending_automation_activation_id"] = automation_id
    elif str(metadata.get("pending_automation_activation_id") or "") == automation_id:
        metadata.pop("pending_automation_activation_id", None)

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
    from apps.automations.models import Automation

    qs = Automation.objects.select_related("agent_profile").filter(business_profile_id=conversation.business_profile_id)
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
    return {"tool": "list_tasks", "status": "ok", "tasks": [_automation_payload(item) for item in qs.order_by("-updated_at")[:limit]]}


def _draft_task_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.agent_runs.models import AgentRunVisibility
    from apps.automations.models import Automation, AutomationStatus, AutomationTriggerType

    agent, error = _resolve_task_agent(conversation, arguments.get("agent_id") or arguments.get("agentId"))
    if error:
        return {"tool": "draft_task", **error}
    name = str(arguments.get("name") or "").strip()
    goal = str(arguments.get("goal") or "").strip()
    if not name or not goal:
        return {"tool": "draft_task", "status": "error", "error_code": "validation_failed", "error": "name and goal are required."}
    trigger_type = str(arguments.get("trigger_type") or arguments.get("triggerType") or AutomationTriggerType.SCHEDULE).strip().lower()
    if trigger_type not in {choice for choice, _ in AutomationTriggerType.choices}:
        return {"tool": "draft_task", "status": "error", "error_code": "validation_failed", "error": "Invalid trigger_type."}
    visibility = str(arguments.get("visibility") or AgentRunVisibility.INITIATOR).strip().lower()
    if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
        visibility = AgentRunVisibility.INITIATOR
    instructions = _task_instruction_spec_from_args(arguments)
    workflow_type = str(instructions.get("workflow_type") or "general")
    memory_shape = str(instructions.get("memory_shape") or "general")
    description = str(arguments.get("description") or "").strip()[:4000]
    automation = Automation.objects.create(
        business_profile_id=conversation.business_profile_id,
        agent_profile=agent,
        created_by=getattr(conversation, "owner_user", None) or getattr(agent, "user", None),
        name=name[:160],
        description=description,
        status=AutomationStatus.DRAFT,
        visibility=visibility,
        trigger_type=trigger_type,
        trigger_config=_task_trigger_config_from_args(arguments, trigger_type),
        instructions=instructions,
        metadata={
            "source": "chat_task_draft",
            "activation_requires_user_approval": True,
            "workflow_type": workflow_type,
            "memory_shape": memory_shape,
        },
    )
    _remember_automation_resource_ref(
        conversation,
        automation,
        purpose="pending activation",
        pending_activation=True,
    )
    return {
        "tool": "draft_task",
        "status": "ok",
        "task": _automation_payload(automation),
        "activation_required": True,
        "hint": "Task draft saved. Ask the user to approve activation before calling request_task_activation with approved=true.",
    }


def _update_task_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.agent_runs.models import AgentRunVisibility
    from apps.automations.models import Automation, AutomationTriggerType

    try:
        task_id = uuid.UUID(str(arguments.get("task_id") or arguments.get("taskId") or ""))
    except (TypeError, ValueError):
        return {"tool": "update_task", "status": "error", "error_code": "validation_failed", "error": "task_id must be a UUID."}
    automation = Automation.objects.filter(id=task_id, business_profile_id=conversation.business_profile_id).first()
    if automation is None:
        return {"tool": "update_task", "status": "error", "error_code": "not_found", "error": "Task not found."}
    updates: list[str] = []
    if "name" in arguments:
        automation.name = str(arguments.get("name") or "").strip()[:160]
        updates.append("name")
    if "description" in arguments:
        automation.description = str(arguments.get("description") or "").strip()[:4000]
        updates.append("description")
    if _has_task_instruction_updates(arguments):
        current = dict(automation.instructions or {}) if isinstance(automation.instructions, dict) else {}
        automation.instructions = _task_instruction_spec_from_args(arguments, current=current)
        metadata = dict(automation.metadata or {}) if isinstance(automation.metadata, dict) else {}
        if automation.instructions.get("workflow_type"):
            metadata["workflow_type"] = automation.instructions.get("workflow_type")
        if automation.instructions.get("memory_shape"):
            metadata["memory_shape"] = automation.instructions.get("memory_shape")
        automation.metadata = metadata
        updates.append("instructions")
        updates.append("metadata")
    if "trigger_type" in arguments or "triggerType" in arguments:
        trigger_type = str(arguments.get("trigger_type") or arguments.get("triggerType") or "").strip().lower()
        if trigger_type not in {choice for choice, _ in AutomationTriggerType.choices}:
            return {"tool": "update_task", "status": "error", "error_code": "validation_failed", "error": "Invalid trigger_type."}
        automation.trigger_type = trigger_type
        updates.append("trigger_type")
    if "trigger_config" in arguments or "triggerConfig" in arguments or "trigger_type" in arguments or "triggerType" in arguments:
        automation.trigger_config = _task_trigger_config_from_args(arguments, automation.trigger_type, current=automation.trigger_config)
        updates.append("trigger_config")
    if "visibility" in arguments:
        visibility = str(arguments.get("visibility") or "").strip().lower()
        if visibility not in {choice for choice, _ in AgentRunVisibility.choices}:
            return {"tool": "update_task", "status": "error", "error_code": "validation_failed", "error": "Invalid visibility."}
        automation.visibility = visibility
        updates.append("visibility")
    if updates:
        automation.save(update_fields=sorted(set([*updates, "updated_at"])))
        purpose = "pending activation" if automation.status == "draft" else "updated"
        _remember_automation_resource_ref(
            conversation,
            automation,
            purpose=purpose,
            pending_activation=automation.status == "draft",
        )
    return {"tool": "update_task", "status": "ok", "task": _automation_payload(automation)}


def _request_task_activation_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from django.utils import timezone as django_timezone
    from apps.automations.models import Automation, AutomationStatus, AutomationTriggerType
    from apps.automations.scheduling import CronScheduleError, compute_next_automation_schedule_at

    try:
        task_id = uuid.UUID(str(arguments.get("task_id") or arguments.get("taskId") or ""))
    except (TypeError, ValueError):
        return {"tool": "request_task_activation", "status": "error", "error_code": "validation_failed", "error": "task_id must be a UUID."}
    automation = Automation.objects.filter(id=task_id, business_profile_id=conversation.business_profile_id).first()
    if automation is None:
        return {"tool": "request_task_activation", "status": "error", "error_code": "not_found", "error": "Task not found."}
    if not bool(arguments.get("approved")):
        return {
            "tool": "request_task_activation",
            "status": "needs_user",
            "task": _automation_payload(automation),
            "prompt": "Please approve activating this persistent task before it starts running.",
        }
    next_trigger_at = None
    if automation.trigger_type == AutomationTriggerType.SCHEDULE:
        try:
            next_trigger_at = compute_next_automation_schedule_at("cron", dict(automation.trigger_config or {}), after=django_timezone.now())
        except CronScheduleError as exc:
            return {"tool": "request_task_activation", "status": "error", "error_code": "validation_failed", "error": str(exc)}
    automation.status = AutomationStatus.ACTIVE
    automation.next_trigger_at = next_trigger_at
    automation.save(update_fields=["status", "next_trigger_at", "updated_at"])
    _remember_automation_resource_ref(
        conversation,
        automation,
        purpose="active",
        pending_activation=False,
    )
    return {"tool": "request_task_activation", "status": "ok", "task": _automation_payload(automation), "activated": True}


def _pause_task_handler(
    arguments: Mapping[str, object],
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
) -> Mapping[str, object]:
    del context
    from apps.automations.models import Automation, AutomationStatus

    try:
        task_id = uuid.UUID(str(arguments.get("task_id") or arguments.get("taskId") or ""))
    except (TypeError, ValueError):
        return {"tool": "pause_task", "status": "error", "error_code": "validation_failed", "error": "task_id must be a UUID."}
    automation = Automation.objects.filter(id=task_id, business_profile_id=conversation.business_profile_id).first()
    if automation is None:
        return {"tool": "pause_task", "status": "error", "error_code": "not_found", "error": "Task not found."}
    metadata = dict(automation.metadata or {}) if isinstance(automation.metadata, dict) else {}
    if arguments.get("reason"):
        metadata["last_pause_reason"] = str(arguments.get("reason"))[:500]
    automation.status = AutomationStatus.PAUSED
    automation.next_trigger_at = None
    automation.metadata = metadata
    automation.save(update_fields=["status", "next_trigger_at", "metadata", "updated_at"])
    _remember_automation_resource_ref(
        conversation,
        automation,
        purpose="paused",
        pending_activation=False,
    )
    return {"tool": "pause_task", "status": "ok", "task": _automation_payload(automation)}
