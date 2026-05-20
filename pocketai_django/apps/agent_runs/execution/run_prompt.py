from __future__ import annotations

import textwrap
from typing import Mapping


def build_seed_prompt(
    *,
    goal: str,
    criteria_lines: list[str],
    constraints: object,
    metadata_snapshot: Mapping[str, object],
    workflow_runtime_context: str,
) -> str:
    trigger_context_summary = ""
    raw_trigger = metadata_snapshot.get("trigger") if isinstance(metadata_snapshot, Mapping) else None
    if isinstance(raw_trigger, str) and isinstance(metadata_snapshot.get("message"), Mapping):
        raw_trigger = {"type": raw_trigger, **dict(metadata_snapshot.get("message") or {})}
    elif isinstance(raw_trigger, str) and raw_trigger:
        raw_trigger = {"type": raw_trigger}
    if isinstance(raw_trigger, Mapping) and raw_trigger:
        lines: list[str] = []
        preferred_keys = [
            "type",
            "provider",
            "email_account_id",
            "message_id",
            "thread_id",
            "from",
            "to",
            "subject",
            "date",
            "snippet",
        ]
        for key in preferred_keys:
            value = raw_trigger.get(key)
            if value is None or value == "":
                continue
            text = str(value).strip()
            if not text:
                continue
            if key == "snippet":
                text = text[:800].rstrip()
            else:
                text = text[:240].rstrip()
            lines.append(f"- {key}: {text}")
        if lines:
            trigger_context_summary = "<trigger_context>\n" + "\n".join(lines) + "\n</trigger_context>\n"

    run_report_contract = textwrap.dedent(
        """
        Final response contract:
        - Prefer returning a concise JSON object with key `run_report`.
        - Shape:
          {
            "run_report": {
              "objective": "...",
              "status": "completed|no_change|needs_approval|needs_user|failed",
              "findings": ["..."],
              "actions_taken": [{"tool": "...", "status": "..."}],
              "evidence_refs": [],
              "confidence": 0.0,
              "changed_entities": [{"type": "...", "identity": "...", "state": {}}],
              "notification_candidate": {"kind": "run_result|approval|user_input|failure", "priority": "low|normal|high", "title": "...", "body": "...", "payload": {}},
              "recommended_next_step": "...",
              "memory_update": {
                "current_summary": "...",
                "inspected_items": [],
                "ignored_items": [],
                "notified_items": [],
                "failed_items": [],
                "completed_steps": [],
                "pending_next_steps": [],
                "decisions": [],
                "blockers": [],
                "artifacts": [],
                "sources_covered": [],
                "actions_taken": [],
                "approvals": [],
                "touched_entities": [],
                "rollback_notes": []
              }
            }
          }
        - If nothing materially changed from workflow memory/state, set status=no_change and notification_candidate=null.
        - For recurring tasks, changed_entities must be stable across runs for the same real-world item and same state.
        - Always include memory_update with only compact identifiers/summaries needed by the next run; do not include raw email/document bodies.
        """
    ).strip()

    workflow_runtime_note = f"{workflow_runtime_context}\n\n" if workflow_runtime_context else ""
    return (
        "You are running a background task (agent run).\n"
        f"Goal: {goal}\n"
        f"Success criteria: {criteria_lines}\n"
        f"Constraints: {constraints or {}}\n"
        f"{trigger_context_summary}"
        f"{workflow_runtime_note}"
        "Instructions:\n"
        "- Work autonomously.\n"
        "- You may delegate a bounded subtask with start_agent_run when it protects your context window or parallelizes substantial work; wait for the delegated result before finalizing.\n"
        "- If you require missing information from the user, call request_user_input with concise questions and stop.\n"
        "- If a tool call is pending approval, ask the user to approve/deny and stop.\n"
        "- Do not claim actions happened unless they were executed via tools.\n"
        "- Keep internal steps/tool chatter out of the final response.\n"
        f"{run_report_contract}\n"
    ).strip()
