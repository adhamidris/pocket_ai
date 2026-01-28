from __future__ import annotations

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


class RunSpec(TypedDict, total=False):
    """
    Serializable specification for a background run.

    Stored in `AgentRunSpec.spec` and snapshot into `AgentRun.run_spec_snapshot`.
    """

    version: int
    goal: str
    success_criteria: list[str]
    tool_allowlist: list[str]
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


def normalize_run_spec(spec: object | None) -> dict[str, Any]:
    """
    Best-effort normalization to keep RunSpec payloads stable and JSON-serializable.

    This is intentionally conservative: it avoids complex transformations so the
    caller (orchestrator/worker) can remain the source of truth.
    """

    if not isinstance(spec, Mapping):
        return {}

    out: dict[str, Any] = dict(spec)
    tool_allowlist = out.get("tool_allowlist")
    if isinstance(tool_allowlist, (list, tuple)):
        out["tool_allowlist"] = [str(item).strip() for item in tool_allowlist if str(item or "").strip()]
    elif tool_allowlist is not None:
        out["tool_allowlist"] = []

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

