from __future__ import annotations

import logging
from typing import Mapping

from apps.agent_runs.execution.audit import _clip_text, _coerce_list, _json_safe, _merge_unique
from apps.agent_runs.models import AgentRun
from apps.agentic_tasks.models import AgenticTask
from apps.conversations.models import MemoryItem, MemoryKind, MemoryScope, MemoryStatus


logger = logging.getLogger(__name__)


class AgentRunWorkflowStateMixin:

    def _update_workflow_state_from_report(
        self,
        *,
        agentic_task: AgenticTask,
        run: AgentRun,
        report: Mapping[str, object],
        now,
    ) -> None:
        state = dict(agentic_task.state or {}) if isinstance(getattr(agentic_task, "state", None), Mapping) else {}
        history = state.get("recent_run_reports")
        if not isinstance(history, list):
            history = []
        compact_report = {
            "run_id": str(run.id),
            "at": now.isoformat(),
            "status": report.get("status"),
            "objective": _clip_text(report.get("objective"), 300),
            "confidence": report.get("confidence"),
            "recommended_next_step": _clip_text(report.get("recommended_next_step"), 500),
        }
        history.insert(0, compact_report)
        state["recent_run_reports"] = history[:20]
        state["last_run_report"] = compact_report
        state["last_changed_entities"] = report.get("changed_entities") if isinstance(report.get("changed_entities"), list) else []
        state["workflow_memory"] = self._merge_workflow_memory(
            agentic_task=agentic_task,
            run=run,
            previous=state.get("workflow_memory"),
            report=report,
            now=now,
        )
        AgenticTask.objects.filter(id=agentic_task.id).update(state=state, updated_at=now)
        agentic_task.state = state

    def _merge_workflow_memory(
        self,
        *,
        agentic_task: AgenticTask,
        run: AgentRun,
        previous: object,
        report: Mapping[str, object],
        now,
    ) -> dict[str, object]:
        memory = dict(previous or {}) if isinstance(previous, Mapping) else {}
        update = report.get("memory_update") if isinstance(report.get("memory_update"), Mapping) else {}
        instructions = agentic_task.instructions if isinstance(getattr(agentic_task, "instructions", None), Mapping) else {}
        metadata = agentic_task.metadata if isinstance(getattr(agentic_task, "metadata", None), Mapping) else {}
        memory_shape = str(instructions.get("memory_shape") or metadata.get("memory_shape") or "general").strip() or "general"
        memory["memory_shape"] = memory_shape
        memory["last_updated_at"] = now.isoformat()
        memory["last_run_id"] = str(run.id)

        scalar_keys = ("current_summary", "current_milestone", "last_run_summary")
        for key in scalar_keys:
            value = update.get(key)
            if value not in (None, "", [], {}):
                memory[key] = _clip_text(value, 1200)
        if "last_run_summary" not in memory:
            findings = report.get("findings") if isinstance(report.get("findings"), list) else []
            if findings:
                memory["last_run_summary"] = _clip_text("; ".join(str(item) for item in findings[:3]), 1200)
            else:
                memory["last_run_summary"] = _clip_text(report.get("recommended_next_step") or report.get("status") or "", 1200)

        list_key_map = {
            "inspected_items": ("inspected_items", "inspected", "read_items", "seen_items"),
            "ignored_items": ("ignored_items", "ignored"),
            "notified_items": ("notified_items", "notified"),
            "failed_items": ("failed_items", "failed"),
            "completed_steps": ("completed_steps", "completed"),
            "pending_next_steps": ("pending_next_steps", "pending_steps", "next_steps"),
            "decisions": ("decisions", "important_decisions"),
            "blockers": ("blockers", "open_blockers"),
            "artifacts": ("artifacts", "artifact_refs"),
            "sources_covered": ("sources_covered", "covered_sources"),
            "actions_taken": ("actions_taken", "actions"),
            "approvals": ("approvals", "approval_refs"),
            "touched_entities": ("touched_entities", "entities"),
            "rollback_notes": ("rollback_notes",),
        }
        for canonical, aliases in list_key_map.items():
            incoming: list[str] = []
            for alias in aliases:
                incoming.extend(_coerce_list(update.get(alias), limit=100, item_limit=500))
            if incoming:
                memory[canonical] = _merge_unique(memory.get(canonical), incoming, limit=200, item_limit=500)

        email_seen, email_failed = self._email_memory_from_tool_trace(report.get("actions_taken"), run)
        if email_seen:
            memory["inspected_items"] = _merge_unique(memory.get("inspected_items"), email_seen, limit=500, item_limit=255)
        if email_failed:
            memory["failed_items"] = _merge_unique(memory.get("failed_items"), email_failed, limit=500, item_limit=255)

        recent_updates = memory.get("recent_updates")
        if not isinstance(recent_updates, list):
            recent_updates = []
        recent_updates.insert(
            0,
            {
                "run_id": str(run.id),
                "at": now.isoformat(),
                "status": report.get("status"),
                "summary": _clip_text(memory.get("last_run_summary") or "", 600),
            },
        )
        memory["recent_updates"] = recent_updates[:10]

        self._upsert_workflow_memory_item(agentic_task=agentic_task, run=run, memory=memory, now=now)
        return _json_safe(memory, fallback={}) if isinstance(memory, dict) else {}

    def _email_memory_from_tool_trace(self, actions_taken: object, run: AgentRun) -> tuple[list[str], list[str]]:
        result = run.result if isinstance(getattr(run, "result", None), Mapping) else {}
        trace = result.get("tool_trace") if isinstance(result.get("tool_trace"), list) else []
        if not trace:
            trace = actions_taken if isinstance(actions_taken, list) else []
        inspected: list[str] = []
        failed: list[str] = []
        for entry in trace:
            if not isinstance(entry, Mapping):
                continue
            tool_name = str(entry.get("tool") or entry.get("tool_name") or "").strip().lower()
            if tool_name != "email_get_message":
                continue
            args = entry.get("arguments") if isinstance(entry.get("arguments"), Mapping) else {}
            summary = entry.get("output_summary") if isinstance(entry.get("output_summary"), Mapping) else {}
            message_id = str(summary.get("message_id") or args.get("message_id") or args.get("messageId") or "").strip()
            if not message_id:
                continue
            status = str(entry.get("status") or summary.get("status") or "").strip().lower()
            if status == "ok":
                inspected.append(message_id)
            else:
                error_code = str(entry.get("error_code") or summary.get("error_code") or status or "error").strip()
                failed.append(f"{message_id} ({error_code})")
        return inspected, failed

    def _upsert_workflow_memory_item(
        self,
        *,
        agentic_task: AgenticTask,
        run: AgentRun,
        memory: Mapping[str, object],
        now,
    ) -> None:
        content_parts = [
            str(memory.get("current_summary") or memory.get("last_run_summary") or "").strip(),
            f"inspected={len(_coerce_list(memory.get('inspected_items'), limit=1000))}",
            f"ignored={len(_coerce_list(memory.get('ignored_items'), limit=1000))}",
            f"notified={len(_coerce_list(memory.get('notified_items'), limit=1000))}",
            f"failed={len(_coerce_list(memory.get('failed_items'), limit=1000))}",
        ]
        content = " | ".join(part for part in content_parts if part)
        try:
            MemoryItem.objects.update_or_create(
                business_profile=agentic_task.business_profile,
                agentic_task=agentic_task,
                scope=MemoryScope.TASK,
                key="workflow_compact_journal",
                defaults={
                    "agent_profile": agentic_task.agent_profile,
                    "run": run,
                    "kind": MemoryKind.STATE_NOTE,
                    "content": _clip_text(content, 4000),
                    "payload": _json_safe(memory, fallback={}),
                    "status": MemoryStatus.ACTIVE,
                    "source_type": "agent_run_memory_writeback",
                    "source_id": run.id,
                    "updated_at": now,
                },
            )
        except Exception:  # pragma: no cover - memory writeback should not break run completion
            logger.exception("workflow_memory_item_upsert_failed agentic_task=%s run=%s", agentic_task.id, run.id)
