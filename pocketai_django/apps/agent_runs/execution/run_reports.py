from __future__ import annotations

from typing import Mapping

from apps.agent_runs.execution.audit import _clip_text, _extract_json_object, _json_safe, _stable_digest
from apps.agent_runs.models import AgentRun, AgentRunNotification, AgentRunNotificationStatus, AgentRunStatus
from apps.automations.models import Automation, AutomationDedupeKey


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
                    "identity": str(run.automation_id or run.id),
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

    def _workflow_dedupe_key(self, *, automation: Automation | None, report: Mapping[str, object]) -> str:
        entities = report.get("changed_entities")
        candidate = entities if isinstance(entities, list) and entities else report.get("notification_candidate") or report
        return f"workflow_state:{_stable_digest(candidate)}"

    def _persist_run_report(
        self,
        *,
        run: AgentRun,
        report: Mapping[str, object],
        next_status: str,
        now,
    ) -> dict[str, object]:
        automation = getattr(run, "automation", None)
        dedupe_key = self._workflow_dedupe_key(automation=automation, report=report)
        duplicate = False
        if automation is not None and dedupe_key:
            duplicate = not self._record_workflow_dedupe_key(automation, dedupe_key)

        if automation is not None:
            self._update_workflow_state_from_report(
                automation=automation,
                run=run,
                report=report,
                dedupe_key=dedupe_key,
                duplicate=duplicate,
                now=now,
            )

        return {"dedupe_key": dedupe_key, "duplicate": duplicate}

    def _persist_run_notification(
        self,
        *,
        run: AgentRun,
        report: Mapping[str, object],
        dedupe_key: str,
        duplicate: bool,
        now,
    ) -> None:
        candidate = report.get("notification_candidate")
        if not isinstance(candidate, Mapping):
            return
        automation = getattr(run, "automation", None)
        target_conversation = getattr(automation, "conversation", None) if automation is not None else None
        if target_conversation is None:
            target_conversation = getattr(run, "conversation", None)
        status = (
            AgentRunNotificationStatus.SUPPRESSED
            if duplicate
            else AgentRunNotificationStatus.DELIVERED
            if target_conversation is not None
            else AgentRunNotificationStatus.CANDIDATE
        )
        defaults = {
            "business_profile": run.business_profile,
            "agent_profile": run.agent_profile,
            "owner_agent_profile": getattr(automation, "agent_profile", None) if automation is not None else run.agent_profile,
            "automation": automation,
            "target_conversation": target_conversation,
            "status": status,
            "kind": str(candidate.get("kind") or "run_result")[:48],
            "priority": str(candidate.get("priority") or "normal")[:24],
            "title": str(candidate.get("title") or run.title or "Run update")[:240],
            "body": _clip_text(candidate.get("body") or "", 8000),
            "payload": dict(candidate.get("payload") or {}) if isinstance(candidate.get("payload"), Mapping) else {},
            "delivered_at": now if status == AgentRunNotificationStatus.DELIVERED else None,
        }
        AgentRunNotification.objects.update_or_create(
            run=run,
            dedupe_key=(dedupe_key or "")[:255],
            defaults=defaults,
        )

    def _record_workflow_dedupe_key(self, automation: Automation, dedupe_key: str) -> bool:
        if not dedupe_key:
            return True
        try:
            AutomationDedupeKey.objects.create(
                business_profile=automation.business_profile,
                automation=automation,
                dedupe_key=dedupe_key[:255],
            )
            return True
        except Exception:
            return False
