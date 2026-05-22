from __future__ import annotations

from typing import Mapping

from apps.agent_runs.execution.audit import _clip_text, _extract_json_object, _json_safe, _stable_digest
from apps.agent_runs.models import AgentRun, AgentRunStatus


class AgentRunReportMixin:

    def _build_run_report(
        self,
        *,
        run: AgentRun,
        next_status: str,
        response_text: str,
        tool_trace: list[object],
        pause_payload: Mapping[str, object] | None,
        approval_preview: Mapping[str, object] | None,
    ) -> dict[str, object]:
        parsed = _extract_json_object(response_text)
        candidate = parsed.get("run_report") if isinstance(parsed, Mapping) and isinstance(parsed.get("run_report"), Mapping) else parsed
        if not isinstance(candidate, Mapping):
            candidate = {}
        report: dict[str, object] = {
            "objective": str((run.run_snapshot or {}).get("goal") or run.title or "").strip(),
            "status": "completed" if next_status == AgentRunStatus.COMPLETED else next_status,
            "findings": [],
            "actions_taken": [],
            "evidence_refs": [],
            "confidence": 0.8,
            "changed_entities": [],
            "notification_candidate": None,
            "recommended_next_step": "",
            "memory_update": {},
        }
        for key in report.keys():
            if key in candidate:
                report[key] = _json_safe(candidate.get(key), fallback=report[key])
        if not report.get("findings") and response_text:
            report["findings"] = [_clip_text(response_text, 1600)]
        actions: list[object] = []
        for entry in tool_trace or []:
            if not isinstance(entry, Mapping):
                continue
            tool_name = entry.get("tool") or entry.get("tool_name")
            status = entry.get("status")
            if tool_name:
                actions.append({"tool": str(tool_name), "status": str(status or "")})
        if actions:
            report["actions_taken"] = actions
        if next_status in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_USER}:
            title = "Approval needed" if next_status == AgentRunStatus.WAITING_APPROVAL else "User input needed"
            body = _clip_text(response_text or title, 1800)
            report["notification_candidate"] = {
                "kind": "approval" if next_status == AgentRunStatus.WAITING_APPROVAL else "user_input",
                "priority": "high",
                "title": title,
                "body": body,
                "payload": {
                    "pause": dict(pause_payload or {}),
                    "approval_preview": dict(approval_preview or {}) if isinstance(approval_preview, Mapping) else {},
                },
            }
            if approval_preview:
                report["changed_entities"] = [
                    {
                        "type": "approval_request",
                        "identity": _stable_digest(approval_preview),
                        "state": approval_preview,
                    }
                ]
        elif not report.get("notification_candidate") and response_text:
            report["notification_candidate"] = {
                "kind": "run_result",
                "priority": "normal",
                "title": run.title or "Workflow update",
                "body": _clip_text(response_text, 1800),
                "payload": {},
            }
        if not report.get("changed_entities"):
            report["changed_entities"] = [
                {
                    "type": "run_response",
                    "identity": str(run.agentic_task_id or run.id),
                    "state": {"response_hash": _stable_digest(response_text or report)},
                }
            ]
        return report

    def _forced_final_trace(self, tool_trace: list[object]) -> dict[str, object] | None:
        for entry in reversed(tool_trace or []):
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("tool") or "").strip() != "__orchestrator__":
                continue
            if str(entry.get("status") or "").strip().lower() != "forced_final":
                continue
            return dict(entry)
        return None

    def _persist_run_report(
        self,
        *,
        run: AgentRun,
        report: Mapping[str, object],
        next_status: str,
        now,
    ) -> dict[str, object]:
        agentic_task = getattr(run, "agentic_task", None)
        if agentic_task is not None:
            self._update_workflow_state_from_report(
                agentic_task=agentic_task,
                run=run,
                report=report,
                now=now,
            )

        return {"persisted": bool(agentic_task is not None)}
