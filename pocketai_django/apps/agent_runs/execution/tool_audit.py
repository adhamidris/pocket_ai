from __future__ import annotations

from typing import Mapping

from apps.agent_runs.execution.audit import _clip_text


def _summarize_input_payload(payload: object) -> dict[str, object]:
    """
    Return a privacy-safe summary of tool arguments.

    Audit logs must avoid storing raw sensitive payloads (PII / document text).
    """

    if not isinstance(payload, Mapping):
        return {"redacted": True, "keys": []}
    keys = []
    for key in payload.keys():
        if not isinstance(key, str):
            continue
        key_norm = key.strip()
        if not key_norm:
            continue
        keys.append(key_norm[:80])
    keys = sorted(set(keys))
    return {
        "redacted": True,
        "keys": keys[:60],
        **({"keys_total": len(keys)} if len(keys) > 60 else {}),
    }


def _sanitize_tool_input(tool_name: str, payload: object) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        return _summarize_input_payload(payload)

    normalized = str(tool_name or "").strip().lower()

    if normalized == "email_search":
        out: dict[str, object] = {}
        query = str(payload.get("query") or "").strip()
        if query:
            out["query"] = _clip_text(query, 240)
        if "limit" in payload:
            try:
                out["limit"] = max(1, min(25, int(payload.get("limit") or 0)))
            except (TypeError, ValueError):
                pass
        return out or _summarize_input_payload(payload)

    safe_id_keys_by_tool = {
        "email_get_message": ("message_id",),
        "email_get_thread": ("thread_id",),
        "email_send_draft": ("draft_id",),
    }
    safe_id_keys = safe_id_keys_by_tool.get(normalized)
    if safe_id_keys:
        out = {}
        for key in safe_id_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = _clip_text(value.strip(), 240)
        return out or _summarize_input_payload(payload)

    if normalized in {"mcp_search_tools", "search_knowledge", "search_conversation_files"}:
        query = str(payload.get("query") or "").strip()
        if query:
            return {"query": _clip_text(query, 280)}

    if normalized == "request_user_input":
        out = {}
        prompt = str(payload.get("prompt") or "").strip()
        if prompt:
            out["prompt"] = _clip_text(prompt, 280)
        questions = payload.get("questions")
        if isinstance(questions, list):
            clean_questions = [_clip_text(str(q).strip(), 200) for q in questions[:5] if str(q or "").strip()]
            if clean_questions:
                out["questions"] = clean_questions
        return out or _summarize_input_payload(payload)

    if normalized == "list_tasks":
        out = {}
        for key in ("agent_id", "agentId", "status"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = _clip_text(value.strip(), 120)
        if "limit" in payload:
            try:
                out["limit"] = max(1, min(50, int(payload.get("limit") or 0)))
            except (TypeError, ValueError):
                pass
        return out or _summarize_input_payload(payload)

    return _summarize_input_payload(payload)


def _summarize_tool_result(tool_name: str, tool_result: object) -> dict[str, object]:
    """
    Return a compact, privacy-safe summary of a tool result for resume prompts.

    Avoid dumping raw payloads; keep only identifiers and short error hints.
    """
    summary: dict[str, object] = {"tool": tool_name}
    if not isinstance(tool_result, Mapping):
        return summary
    status = str(tool_result.get("status") or "").strip() or "ok"
    summary["status"] = status
    for key in (
        "error_code",
        "error",
        "hint",
        "draft_id",
        "draftId",
        "message_id",
        "messageId",
        "thread_id",
        "threadId",
        "artifact_id",
        "artifactId",
        "email_account_id",
        "emailAccountId",
        "provider",
    ):
        if key not in tool_result:
            continue
        value = tool_result.get(key)
        if value is None:
            continue
        text = _clip_text(value, 240)
        if text:
            # Normalize camelCase variants.
            normalized = (
                key.replace("Id", "_id")
                .replace("ID", "_id")
                .replace("draftId", "draft_id")
                .replace("messageId", "message_id")
                .replace("threadId", "thread_id")
                .replace("artifactId", "artifact_id")
                .replace("emailAccountId", "email_account_id")
            )
            summary[normalized] = text
    return summary


def _sanitize_remote_meta(remote: object) -> dict[str, object] | None:
    if not isinstance(remote, Mapping):
        return None
    out: dict[str, object] = {}
    conn_id = remote.get("connection_id")
    conn_name = remote.get("connection_name")
    remote_tool = remote.get("remote_tool") or remote.get("tool")
    if isinstance(conn_id, str) and conn_id.strip():
        out["connection_id"] = conn_id.strip()
    if isinstance(conn_name, str) and conn_name.strip():
        out["connection_name"] = _clip_text(conn_name.strip(), 240)
    if isinstance(remote_tool, str) and remote_tool.strip():
        out["remote_tool"] = _clip_text(remote_tool.strip(), 240)
    return out or None


def _sanitize_approval_meta(approval: object) -> dict[str, object] | None:
    """
    Store minimal approval metadata (no raw inputs/reasons).
    """

    if not isinstance(approval, Mapping):
        return None
    out: dict[str, object] = {}
    approval_id = approval.get("id")
    status = approval.get("status")
    mode = approval.get("mode")
    operation_type = approval.get("operation_type")
    expires_at = approval.get("expires_at")

    if isinstance(approval_id, str) and approval_id.strip():
        out["id"] = approval_id.strip()
    if isinstance(status, str) and status.strip():
        out["status"] = status.strip()[:64]
    if isinstance(mode, str) and mode.strip():
        out["mode"] = mode.strip()[:64]
    if isinstance(operation_type, str) and operation_type.strip():
        out["operation_type"] = operation_type.strip()[:64]
    if isinstance(expires_at, str) and expires_at.strip():
        out["expires_at"] = expires_at.strip()[:64]

    return out or None


def _sanitize_task_payload(task: object) -> dict[str, object] | None:
    if not isinstance(task, Mapping):
        return None
    out: dict[str, object] = {}
    for key in ("id", "agent_id", "active_conversation_id", "name", "status", "visibility", "next_trigger_at", "last_triggered_at"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = _clip_text(value.strip(), 240)
    if "schedule_enabled" in task:
        out["schedule_enabled"] = bool(task.get("schedule_enabled"))
    schedule_config = task.get("schedule_config")
    if isinstance(schedule_config, Mapping):
        compact_schedule: dict[str, object] = {}
        for key in ("type", "cron", "timezone"):
            value = schedule_config.get(key)
            if isinstance(value, str) and value.strip():
                compact_schedule[key] = _clip_text(value.strip(), 120)
        if compact_schedule:
            out["schedule_config"] = compact_schedule
    last_error = str(task.get("last_error") or "").strip()
    if last_error:
        out["last_error"] = _clip_text(last_error, 240)
    return out or None


def _sanitize_tool_output(tool_name: str, output: object) -> dict[str, object] | None:
    if not isinstance(output, Mapping):
        return None

    normalized = str(tool_name or "").strip().lower()
    status = str(output.get("status") or "").strip() or "ok"
    out: dict[str, object] = {"status": status}

    if status != "ok":
        error_code = str(output.get("error_code") or output.get("error") or "").strip()
        error = str(output.get("error") or "").strip()
        hint = str(output.get("hint") or "").strip()
        if error_code:
            out["error_code"] = _clip_text(error_code, 120)
        if error:
            out["error"] = _clip_text(error, 240)
        if hint:
            out["hint"] = _clip_text(hint, 240)
        return out

    # Email tools already return privacy-safe outputs in the MCP layer; keep a small allowlist.
    if normalized.startswith("email_"):
        safe_keys = {
            "status",
            "result_count",
            "message_ids",
            "thread_ids",
            "message_id",
            "thread_id",
            "draft_id",
            "message_count",
            "returned_messages",
            "truncated",
            "body_truncated",
        }
        for key in safe_keys:
            if key not in output:
                continue
            value = output.get(key)
            if key in {"truncated", "body_truncated"}:
                out[key] = bool(value)
            elif key in {"result_count", "message_count", "returned_messages"}:
                try:
                    out[key] = int(value or 0)
                except (TypeError, ValueError):
                    continue
            elif key in {"message_ids", "thread_ids"} and isinstance(value, list):
                out[key] = [str(v)[:160] for v in value[:10] if str(v or "").strip()]
            else:
                if isinstance(value, str) and value.strip():
                    out[key] = _clip_text(value.strip(), 240)
        return out

    if normalized == "request_user_input":
        questions = output.get("questions")
        if isinstance(questions, list):
            out["questions_count"] = len(questions)
        schema_payload = output.get("schema")
        if isinstance(schema_payload, Mapping) and schema_payload:
            out["schema_keys"] = sorted({str(k)[:80] for k in schema_payload.keys() if isinstance(k, str) and k.strip()})[:40]
        return out

    if normalized == "list_tasks":
        tasks = output.get("agentic_tasks")
        if isinstance(tasks, list):
            compact_tasks = []
            for task in tasks[:10]:
                compact = _sanitize_task_payload(task)
                if compact:
                    compact_tasks.append(compact)
            out["tasks"] = compact_tasks
            out["tasks_count"] = len(tasks)
            if len(tasks) > len(compact_tasks):
                out["truncated"] = True
        return out

    if normalized in {"draft_agentic_task", "update_agentic_task", "request_agentic_task_activation", "pause_agentic_task"}:
        task = _sanitize_task_payload(output.get("agentic_task"))
        if task:
            out["agentic_task"] = task
        if "activated" in output:
            out["activated"] = bool(output.get("activated"))
        return out

    # Generic output summary: keep only stable metadata and identifiers, drop any free-text content.
    for key in ("tool", "tool_id", "artifact_id", "memory_id"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = _clip_text(value.strip(), 240)

    remote_meta = _sanitize_remote_meta(output.get("remote"))
    if remote_meta:
        out["remote"] = remote_meta

    for key in ("is_error", "truncated", "prompt_compact", "body_truncated", "content_truncated", "review_required"):
        if key in output:
            out[key] = bool(output.get(key))

    for key in ("result_count", "results_count", "message_count", "returned_messages", "content_items_total"):
        if key in output:
            try:
                out[key] = int(output.get(key) or 0)
            except (TypeError, ValueError):
                continue

    for key in ("message_id", "thread_id", "draft_id"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = _clip_text(value.strip(), 240)
    for key in ("message_ids", "thread_ids"):
        value = output.get(key)
        if isinstance(value, list):
            out[key] = [str(v)[:160] for v in value[:10] if str(v or "").strip()]

    return out


def sanitize_tool_event_for_audit(event: Mapping[str, object]) -> dict[str, object]:
    """
    Privacy-safe persisted tool event payload.

    NOTE: the full tool output may still exist out-of-band (artifacts), but the
    executed log should remain safe to show to managers by default.
    """

    out: dict[str, object] = {"redacted": True}
    event_id = event.get("event_id")
    phase = event.get("phase")
    status = event.get("status")
    tool_call_id = event.get("tool_call_id")
    tool_name = event.get("tool_name")
    kind = event.get("kind")
    duration_ms = event.get("duration_ms")

    if isinstance(event_id, str) and event_id.strip():
        out["event_id"] = event_id.strip()[:128]
    if isinstance(phase, str) and phase.strip():
        out["phase"] = phase.strip()[:40]
    if isinstance(status, str) and status.strip():
        out["status"] = status.strip()[:64]
    if isinstance(tool_call_id, str) and tool_call_id.strip():
        out["tool_call_id"] = tool_call_id.strip()[:128]
    if isinstance(tool_name, str) and tool_name.strip():
        out["tool_name"] = tool_name.strip()[:200]
    if isinstance(kind, str) and kind.strip():
        out["kind"] = kind.strip()[:40]
    if duration_ms is not None:
        try:
            out["duration_ms"] = int(duration_ms)
        except (TypeError, ValueError):
            pass

    defaults_applied = event.get("defaults_applied")
    if isinstance(defaults_applied, list) and defaults_applied:
        out["defaults_applied"] = [str(v)[:80] for v in defaults_applied[:20] if str(v or "").strip()]

    remote_meta = _sanitize_remote_meta(event.get("remote"))
    if remote_meta:
        out["remote"] = remote_meta

    approval_meta = _sanitize_approval_meta(event.get("approval"))
    if approval_meta:
        out["approval"] = approval_meta

    if "input" in event:
        out["input"] = _sanitize_tool_input(str(tool_name or ""), event.get("input"))

    sanitized_output = _sanitize_tool_output(str(tool_name or ""), event.get("output"))
    if sanitized_output:
        out["output"] = sanitized_output

    return out
