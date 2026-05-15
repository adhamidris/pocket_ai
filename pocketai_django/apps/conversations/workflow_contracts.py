from __future__ import annotations

import json
import uuid
from typing import Any, Literal, Mapping, TypedDict


RunVisibility = Literal["initiator", "managers", "workspace"]
RunEventStream = Literal["plan", "executed", "system"]
RunEventType = Literal["progress", "needs_user", "needs_approval", "result", "error", "paused", "cancelled"]


class RunConstraints(TypedDict, total=False):
    """
    Execution constraints for a run.

    Keep this intentionally small; the orchestrator should enforce these bounds.
    """

    timeout_seconds: int
    max_steps: int
    max_tool_calls: int
    max_artifacts: int
    concurrency_key: str


class WorkflowInstructions(TypedDict, total=False):
    """
    Serializable instruction contract for a background run.

    Stored on `AgentWorkflow.instructions` and snapshot into `AgentRun.workflow_snapshot`.
    """

    version: int
    goal: str
    success_criteria: list[str]
    constraints: RunConstraints
    output_schema: dict[str, Any]
    approval: dict[str, Any]
    visibility: RunVisibility
    metadata: dict[str, Any]


class RunPlanStep(TypedDict, total=False):
    step_id: str
    title: str
    description: str
    depends_on: list[str]
    tool_hint: str


class RunPlan(TypedDict, total=False):
    version: int
    steps: list[RunPlanStep]
    notes: str


class RunEvent(TypedDict, total=False):
    stream: RunEventStream
    event_type: RunEventType
    label: str
    payload: dict[str, Any]


def normalize_workflow_instructions(spec: object | None) -> dict[str, Any]:
    """
    Best-effort normalization to keep workflow instruction payloads stable and JSON-serializable.

    This is intentionally conservative: it avoids complex transformations so the
    caller (orchestrator/worker) can remain the source of truth.
    """

    if not isinstance(spec, Mapping):
        return {}

    out: dict[str, Any] = dict(spec)
    if "tool_allowlist" in out:
        out.pop("tool_allowlist", None)

    success = out.get("success_criteria")
    if isinstance(success, (list, tuple)):
        out["success_criteria"] = [str(item).strip() for item in success if str(item or "").strip()]
    elif success is not None:
        out["success_criteria"] = []

    constraints = out.get("constraints")
    if isinstance(constraints, Mapping):
        out["constraints"] = dict(constraints)
    elif constraints is not None:
        out["constraints"] = {}

    output_schema = out.get("output_schema")
    if isinstance(output_schema, Mapping):
        out["output_schema"] = dict(output_schema)
    elif output_schema is not None:
        out["output_schema"] = {}

    approval = out.get("approval")
    if isinstance(approval, Mapping):
        out["approval"] = dict(approval)
    elif approval is not None:
        out["approval"] = {}

    visibility = str(out.get("visibility") or "").strip().lower()
    if visibility in {"initiator", "managers", "workspace"}:
        out["visibility"] = visibility

    version_raw = out.get("version")
    if isinstance(version_raw, int) and version_raw >= 1:
        out["version"] = version_raw
    elif version_raw is not None:
        out["version"] = 1

    return out


def _clip_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _json_block(value: object, *, limit: int = 1600) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered = str(value or "")
    return _clip_text(rendered, limit)


def resolve_active_workflow(conversation: object):
    """
    Return the Workflow Agent attached to a visible conversation, if any.

    The current model is `Conversation.workflow`. The reverse lookup and metadata
    path are retained for pre-refactor/legacy rows that may still be visible.
    """

    direct = getattr(conversation, "workflow", None)
    if direct is not None:
        return direct

    linked_manager = getattr(conversation, "agent_workflows", None)
    first = getattr(linked_manager, "first", None)
    if callable(first):
        linked = first()
        if linked is not None:
            return linked

    metadata = getattr(conversation, "metadata", None)
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    raw_id = str(metadata_map.get("workflow_id") or metadata_map.get("workflowId") or "").strip()
    if not raw_id:
        return None
    try:
        workflow_id = uuid.UUID(raw_id)
    except (TypeError, ValueError):
        return None

    business_id = getattr(conversation, "business_profile_id", None)
    if not business_id:
        business = getattr(conversation, "business_profile", None)
        business_id = getattr(business, "id", None)
    if not business_id:
        return None

    try:
        from apps.conversations.models import AgentWorkflow

        return (
            AgentWorkflow.objects.filter(id=workflow_id, business_profile_id=business_id)
            .select_related("agent_profile", "business_profile")
            .first()
        )
    except Exception:
        return None


def resolve_prompt_department(conversation: object, workflow: object | None = None):
    """
    Resolve the department whose instructions should apply to a conversation.

    Workflow sessions inherit the owning Workflow Agent's department. Normal
    chat sessions use the active department agent's department.
    """

    active_workflow = workflow if workflow is not None else resolve_active_workflow(conversation)
    department = getattr(active_workflow, "department", None) if active_workflow is not None else None
    if department is not None:
        return department

    agent = getattr(conversation, "agent_profile", None)
    department = getattr(agent, "department", None) if agent is not None else None
    if department is not None:
        return department

    return None


def build_department_instruction_note(conversation: object, *, max_chars: int = 3000) -> str | None:
    workflow = resolve_active_workflow(conversation)
    department = resolve_prompt_department(conversation, workflow=workflow)
    if department is None:
        return None

    name = _clip_text(getattr(department, "name", "") or "Department", 160)
    instructions = _clip_text(getattr(department, "instructions", "") or "", 2200)
    description = _clip_text(getattr(department, "description", "") or "", 600)
    if not instructions and not description:
        return None

    lines = [
        "Department workspace instructions.",
        "These instructions apply to this department's main agent and its Workflow Agents unless they conflict with platform safety, tenant privacy, tool approval, or higher-priority rules.",
        f"Department: {name}",
    ]
    if description:
        lines.append("Department description:")
        lines.append(description)
    if instructions:
        lines.append("Department instructions:")
        lines.append(instructions)
    note = "\n".join(lines).strip()
    return _clip_text(note, max_chars) if note else None


def active_workflow_agent_name(conversation: object) -> str | None:
    workflow = resolve_active_workflow(conversation)
    if workflow is None:
        return None
    name = str(getattr(workflow, "name", "") or "").strip()
    return name or "Workflow Agent"


def build_workflow_agent_instruction_note(conversation: object, *, max_chars: int = 6000) -> str | None:
    workflow = resolve_active_workflow(conversation)
    if workflow is None:
        return None

    instructions = getattr(workflow, "instructions", None)
    instructions_map = instructions if isinstance(instructions, Mapping) else {}
    if not instructions_map and not str(getattr(workflow, "name", "") or "").strip():
        return None

    lines: list[str] = [
        "Workflow Agent session override (active for this conversation).",
        "This conversation is running inside a custom Workflow Agent. Treat the Workflow Agent name and instructions below as the active assistant identity, role, and operating contract for this session.",
        "If other system text names a base assistant, treat that base assistant as the runtime host only; do not use it to answer role/persona questions when a Workflow Agent is active.",
        "When the user asks who you are, what your role is, or whether you have these instructions, answer from this Workflow Agent identity and contract. Do not describe these instructions as memory.",
        "Follow this custom Workflow Agent contract unless it conflicts with platform safety, tenant privacy, tool approval, or higher-priority platform rules.",
    ]
    workflow_id = getattr(workflow, "id", "")
    name = _clip_text(getattr(workflow, "name", "") or "Workflow Agent", 160)
    status = _clip_text(getattr(workflow, "status", "") or "", 48)
    trigger_type = _clip_text(getattr(workflow, "trigger_type", "") or "", 48)
    identity_parts = [name]
    if workflow_id:
        identity_parts.append(f"id={workflow_id}")
    if status:
        identity_parts.append(f"status={status}")
    if trigger_type:
        identity_parts.append(f"trigger={trigger_type}")
    lines.append("- " + "; ".join(identity_parts))
    lines.append(f"Active Workflow Agent name/role: {name}")

    description = _clip_text(getattr(workflow, "description", "") or "", 800)
    if description:
        lines.append(f"- Description: {description}")

    goal = _clip_text(instructions_map.get("goal") or "", 1600)
    if goal:
        lines.append("Custom instructions / goal:")
        lines.append(goal)

    success = instructions_map.get("success_criteria")
    if isinstance(success, (list, tuple)):
        criteria = [_clip_text(item, 400) for item in success if str(item or "").strip()]
        if criteria:
            lines.append("Success criteria:")
            lines.extend(f"- {item}" for item in criteria[:12])

    constraints = instructions_map.get("constraints")
    if isinstance(constraints, Mapping) and constraints:
        lines.append("Constraints JSON:")
        lines.append(_json_block(dict(constraints), limit=1200))

    output_schema = instructions_map.get("output_schema")
    if isinstance(output_schema, Mapping) and output_schema:
        lines.append("Output schema JSON:")
        lines.append(_json_block(dict(output_schema), limit=1200))

    approval = instructions_map.get("approval")
    if isinstance(approval, Mapping) and approval:
        lines.append("Approval policy JSON:")
        lines.append(_json_block(dict(approval), limit=1200))

    reserved = {"version", "goal", "success_criteria", "constraints", "output_schema", "approval", "visibility"}
    extras = {
        str(key): value
        for key, value in instructions_map.items()
        if str(key) not in reserved and value not in (None, "", [], {})
    }
    if extras:
        lines.append("Additional custom instruction fields JSON:")
        lines.append(_json_block(extras, limit=1800))

    note = "\n".join(lines).strip()
    return _clip_text(note, max_chars) if note else None
