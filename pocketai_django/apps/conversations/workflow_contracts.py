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

    Stored on `AssistantWorkflow.instructions` and snapshot into `AgentRun.workflow_snapshot`.
    """

    version: int
    goal: str
    wake_up_prompt: str
    memory_instructions: str
    draft_summary: str
    clarification_questions: list[str]
    success_criteria: list[str]
    constraints: RunConstraints
    output_schema: dict[str, Any]
    approval: dict[str, Any]
    visibility: RunVisibility
    workflow_type: str
    memory_shape: str
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

    for key, limit in (
        ("goal", 6000),
        ("wake_up_prompt", 12000),
        ("executor_prompt", 12000),
        ("memory_instructions", 6000),
        ("draft_summary", 6000),
        ("custom_instructions", 6000),
    ):
        value = out.get(key)
        if value is None:
            continue
        text = str(value or "").strip()
        if text:
            out[key] = _clip_text(text, limit)
        else:
            out.pop(key, None)

    success = out.get("success_criteria")
    if isinstance(success, (list, tuple)):
        out["success_criteria"] = [str(item).strip() for item in success if str(item or "").strip()]
    elif success is not None:
        out["success_criteria"] = []

    questions = out.get("clarification_questions")
    if isinstance(questions, (list, tuple)):
        out["clarification_questions"] = [_clip_text(item, 300) for item in questions if str(item or "").strip()][:8]
    elif questions is not None:
        text = str(questions or "").strip()
        out["clarification_questions"] = [_clip_text(text, 300)] if text else []

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
    elif out:
        out["version"] = 2

    workflow_type = _normalize_slug(out.get("workflow_type"))
    if workflow_type:
        out["workflow_type"] = workflow_type
    memory_shape = _normalize_slug(out.get("memory_shape"))
    if memory_shape:
        out["memory_shape"] = memory_shape

    if "memory_instructions" not in out and out.get("memory_shape"):
        out["memory_instructions"] = build_default_memory_instructions(str(out.get("memory_shape") or ""))
    if "wake_up_prompt" not in out:
        wake_up_prompt = build_default_wake_up_prompt(out)
        if wake_up_prompt:
            out["wake_up_prompt"] = wake_up_prompt

    return out


def _normalize_slug(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = "".join(ch for ch in text if ch.isalnum() or ch == "_")
    return text[:64]


def infer_workflow_type_and_memory_shape(*, goal: object = "", trigger_type: object = "", source_config: object = None) -> tuple[str, str]:
    text = " ".join(
        [
            str(goal or ""),
            str(trigger_type or ""),
            json.dumps(source_config, ensure_ascii=False, sort_keys=True) if isinstance(source_config, Mapping) else "",
        ]
    ).lower()
    if any(token in text for token in ("email", "inbox", "gmail", "outlook", "message", "lead", "sales")):
        return "monitor", "email_monitor"
    if any(token in text for token in ("report", "digest", "summary", "weekly", "daily")):
        return "report", "report_digest"
    if any(token in text for token in ("update", "optimize", "seo", "maintain", "improve", "progress")):
        return "progressive", "progressive_work"
    if any(token in text for token in ("send", "create", "change", "publish", "approve", "action")):
        return "action", "action_workflow"
    return "general", "general"


def build_default_memory_instructions(memory_shape: str) -> str:
    shape = _normalize_slug(memory_shape)
    if shape == "email_monitor":
        return (
            "Maintain a compact tracker of inspected, ignored, failed, and notified message IDs. "
            "Use that tracker before searching or reading so recurring runs do not reprocess the same messages."
        )
    if shape == "progressive_work":
        return (
            "Maintain current milestone, completed steps, pending next steps, artifacts, decisions, and blockers. "
            "Use that tracker to continue the work instead of restarting it."
        )
    if shape == "report_digest":
        return (
            "Maintain prior report summaries, recurring metrics, trends, sources covered, decisions, and open questions. "
            "Use that tracker to avoid duplicate coverage and make each report incremental."
        )
    if shape == "action_workflow":
        return (
            "Maintain actions taken, approvals, touched entities, decisions, blockers, and rollback notes. "
            "Use that tracker to avoid repeating irreversible or already-completed actions."
        )
    return (
        "Maintain a compact summary of prior run outcomes, decisions, completed work, pending next steps, and blockers. "
        "Use that tracker to continue the workflow instead of starting from scratch."
    )


def build_default_wake_up_prompt(spec: Mapping[str, object]) -> str:
    goal = _clip_text(spec.get("goal") or "", 4000)
    custom = _clip_text(spec.get("custom_instructions") or "", 3000)
    memory = _clip_text(spec.get("memory_instructions") or "", 3000)
    success = spec.get("success_criteria")
    criteria = [str(item).strip() for item in success if str(item or "").strip()] if isinstance(success, (list, tuple)) else []
    if not any([goal, custom, memory, criteria]):
        return ""
    lines = [
        "Every time this workflow runs, execute the reusable instructions below.",
    ]
    if goal:
        lines.append("")
        lines.append("Objective:")
        lines.append(goal)
    if custom:
        lines.append("")
        lines.append("Execution instructions:")
        lines.append(custom)
    if criteria:
        lines.append("")
        lines.append("Success criteria:")
        lines.extend(f"- {_clip_text(item, 500)}" for item in criteria[:12])
    if memory:
        lines.append("")
        lines.append("Memory behavior:")
        lines.append(memory)
    lines.append("")
    lines.append("Before taking action, review workflow memory. Do not repeat completed work or reprocess tracked items unless there is a clear reason.")
    lines.append("At the end, return the user-facing result and a compact memory update for the next run.")
    return _clip_text("\n".join(lines).strip(), 12000)


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
    Return the Custom Assistant attached to a visible conversation, if any.

    The current model is `Conversation.workflow`. The reverse lookup and metadata
    path are retained for pre-refactor/legacy rows that may still be visible.
    """

    direct = getattr(conversation, "workflow", None)
    if direct is not None:
        return direct

    linked_manager = getattr(conversation, "assistant_workflows", None)
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
        from apps.conversations.models import AssistantWorkflow

        return (
            AssistantWorkflow.objects.filter(id=workflow_id, business_profile_id=business_id)
            .select_related("agent_profile", "business_profile")
            .first()
        )
    except Exception:
        return None


def active_workflow_agent_name(conversation: object) -> str | None:
    workflow = resolve_active_workflow(conversation)
    if workflow is None:
        return None
    name = str(getattr(workflow, "name", "") or "").strip()
    return name or "Custom Assistant"


def build_workflow_agent_instruction_note(conversation: object, *, max_chars: int = 6000) -> str | None:
    workflow = resolve_active_workflow(conversation)
    if workflow is None:
        return None

    instructions = getattr(workflow, "instructions", None)
    instructions_map = instructions if isinstance(instructions, Mapping) else {}
    if not instructions_map and not str(getattr(workflow, "name", "") or "").strip():
        return None

    lines: list[str] = [
        "Custom Assistant session override (active for this conversation).",
        "This conversation is running inside a Custom Assistant. Treat the Custom Assistant name and instructions below as the active assistant identity, role, and operating contract for this session.",
        "If other system text names a base assistant, treat that base assistant as the runtime host only; do not use it to answer role/persona questions when a Custom Assistant is active.",
        "When the user asks who you are, what your role is, or whether you have these instructions, answer from this Custom Assistant identity and contract. Do not describe these instructions as memory.",
        "Follow this Custom Assistant contract unless it conflicts with platform safety, tenant privacy, tool approval, or higher-priority platform rules.",
    ]
    workflow_id = getattr(workflow, "id", "")
    name = _clip_text(getattr(workflow, "name", "") or "Custom Assistant", 160)
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
    lines.append(f"Active Custom Assistant name/role: {name}")

    description = _clip_text(getattr(workflow, "description", "") or "", 800)
    if description:
        lines.append(f"- Description: {description}")

    goal = _clip_text(instructions_map.get("goal") or "", 1600)
    if goal:
        lines.append("Custom instructions / goal:")
        lines.append(goal)

    wake_up_prompt = _clip_text(instructions_map.get("wake_up_prompt") or instructions_map.get("executor_prompt") or "", 2400)
    if wake_up_prompt:
        lines.append("Reusable wake-up prompt:")
        lines.append(wake_up_prompt)

    memory_instructions = _clip_text(instructions_map.get("memory_instructions") or "", 1200)
    if memory_instructions:
        lines.append("Workflow memory instructions:")
        lines.append(memory_instructions)

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

    reserved = {
        "version",
        "goal",
        "wake_up_prompt",
        "executor_prompt",
        "memory_instructions",
        "draft_summary",
        "clarification_questions",
        "success_criteria",
        "constraints",
        "output_schema",
        "approval",
        "visibility",
    }
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
