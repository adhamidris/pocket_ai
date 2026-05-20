from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping


def _clip_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = str(value)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _json_safe(value: object, *, fallback: object | None = None) -> object:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return fallback if fallback is not None else str(value)


def _stable_digest(value: object) -> str:
    safe_value = _json_safe(value, fallback=str(value))
    encoded = json.dumps(safe_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8", errors="ignore")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _extract_json_object(text: str) -> dict[str, object] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"\s*```$", "", raw).strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return None


def _coerce_list(value: object, *, limit: int = 100, item_limit: int = 500) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, Mapping):
            text = str(item.get("id") or item.get("identity") or item.get("message_id") or item.get("title") or item)
        else:
            text = str(item or "")
        text = _clip_text(text.strip(), item_limit)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _merge_unique(existing: object, incoming: object, *, limit: int = 200, item_limit: int = 500) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*_coerce_list(incoming, limit=limit, item_limit=item_limit), *_coerce_list(existing, limit=limit, item_limit=item_limit)]:
        if item in seen:
            continue
        seen.add(item)
        merged.append(item)
        if len(merged) >= limit:
            break
    return merged


def _recursive_contains_force_final(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key or "").strip().lower()
            if key_text in {"stage", "reason", "status"} and str(item or "").strip().lower() == "force_final":
                return True
            if "force_final" in key_text:
                return True
            if _recursive_contains_force_final(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_recursive_contains_force_final(item) for item in value)
    elif isinstance(value, str):
        return "force_final" in value.strip().lower()
    return False


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


def _format_email_address(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        email = str(value.get("email") or value.get("address") or value.get("value") or "").strip()
        name = str(value.get("name") or value.get("label") or "").strip()
        if email and name:
            return f"{name} <{email}>"
        return email or name
    return str(value).strip()


def _format_email_list(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            rendered = _format_email_address(item)
            if rendered:
                parts.append(rendered)
        return ", ".join(parts)
    return _format_email_address(value)


def _build_email_approval_preview(arguments: Mapping[str, object]) -> dict[str, object] | None:
    if not isinstance(arguments, Mapping):
        return None
    to_value = _format_email_list(arguments.get("to") or arguments.get("recipients") or arguments.get("recipient"))
    cc_value = _format_email_list(arguments.get("cc"))
    bcc_value = _format_email_list(arguments.get("bcc"))
    from_value = _format_email_list(arguments.get("from") or arguments.get("sender") or arguments.get("sender_email"))
    subject = str(arguments.get("subject") or arguments.get("title") or "").strip()
    body = str(
        arguments.get("body")
        or arguments.get("body_html")
        or arguments.get("bodyHtml")
        or arguments.get("body_text")
        or arguments.get("bodyText")
        or arguments.get("message")
        or arguments.get("content")
        or ""
    ).strip()
    attachments = arguments.get("attachments")
    attachment_count = len(attachments) if isinstance(attachments, list) else 0

    fields: list[dict[str, str]] = []
    if to_value:
        fields.append({"label": "To", "value": _clip_text(to_value, 320)})
    if cc_value:
        fields.append({"label": "Cc", "value": _clip_text(cc_value, 320)})
    if bcc_value:
        fields.append({"label": "Bcc", "value": _clip_text(bcc_value, 320)})
    if from_value:
        fields.append({"label": "From", "value": _clip_text(from_value, 240)})
    if subject:
        fields.append({"label": "Subject", "value": _clip_text(subject, 240)})
    if attachment_count:
        label = "Attachment" if attachment_count == 1 else "Attachments"
        fields.append({"label": label, "value": f"{attachment_count} file(s)"})

    preview: dict[str, object] = {"type": "email", "title": "Email draft", "fields": fields}
    if body:
        preview["body"] = _clip_text(body, 1400)
    if not fields and not body:
        return None
    return preview


def _build_phone_call_approval_preview(arguments: Mapping[str, object]) -> dict[str, object] | None:
    if not isinstance(arguments, Mapping):
        return None
    phone_number = str(arguments.get("phone_number") or arguments.get("phoneNumber") or "").strip()
    objective = str(arguments.get("objective") or "").strip()
    call_type = str(arguments.get("call_type") or arguments.get("callType") or "").strip()
    language = str(arguments.get("language") or "").strip()
    max_duration = arguments.get("max_duration_minutes") or arguments.get("maxDurationMinutes")
    try:
        max_duration_value = int(max_duration) if max_duration is not None else None
    except (TypeError, ValueError):
        max_duration_value = None

    fields: list[dict[str, str]] = []
    if phone_number:
        fields.append({"label": "To", "value": _clip_text(phone_number, 80)})
    if objective:
        fields.append({"label": "Objective", "value": _clip_text(objective, 360)})
    if call_type:
        fields.append({"label": "Type", "value": _clip_text(call_type, 80)})
    if language:
        fields.append({"label": "Language", "value": _clip_text(language, 40)})
    if max_duration_value:
        fields.append({"label": "Max duration", "value": f"{max_duration_value} min"})

    context_items = arguments.get("context_items") or arguments.get("contextItems") or []
    context_lines: list[str] = []
    if isinstance(context_items, list):
        for item in context_items[:8]:
            line = ""
            if isinstance(item, Mapping):
                for key in ("label", "title", "name", "summary", "note", "value"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        line = value.strip()
                        break
                if not line:
                    try:
                        line = json.dumps(item, ensure_ascii=False)
                    except Exception:
                        line = str(item)
            elif isinstance(item, str):
                line = item.strip()
            elif item is not None:
                line = str(item).strip()
            if line:
                context_lines.append(_clip_text(line, 220))

    preview: dict[str, object] = {"type": "phone_call", "title": "Phone call", "fields": fields}
    if context_lines:
        preview["body"] = _clip_text("\n".join(context_lines), 1400)
    if not fields and not context_lines:
        return None
    return preview


def _build_approval_preview(
    tool_name: str,
    approval_event: Mapping[str, object] | None,
    pending_tool_call: Mapping[str, object] | None,
) -> dict[str, object] | None:
    tool_norm = str(tool_name or "").strip().lower()
    kind = ""
    if isinstance(approval_event, Mapping):
        kind = str(approval_event.get("kind") or "").strip().lower()
    arguments = None
    if isinstance(pending_tool_call, Mapping):
        arguments = pending_tool_call.get("arguments")
    if not isinstance(arguments, Mapping) and isinstance(approval_event, Mapping):
        candidate = approval_event.get("input")
        if isinstance(candidate, Mapping):
            arguments = candidate
    if not isinstance(arguments, Mapping):
        return None
    if "email" in tool_norm or kind == "email":
        return _build_email_approval_preview(arguments)
    if tool_norm in {"initiate_phone_call", "phone_call"} or "phone" in tool_norm or kind in {"phone", "voice"}:
        return _build_phone_call_approval_preview(arguments)
    return None


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
    for key in ("id", "agent_id", "name", "status", "visibility", "trigger_type", "next_trigger_at", "last_triggered_at"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = _clip_text(value.strip(), 240)
    trigger_config = task.get("trigger_config")
    if isinstance(trigger_config, Mapping):
        compact_trigger: dict[str, object] = {}
        for key in ("type", "cron", "timezone"):
            value = trigger_config.get(key)
            if isinstance(value, str) and value.strip():
                compact_trigger[key] = _clip_text(value.strip(), 120)
        if compact_trigger:
            out["trigger_config"] = compact_trigger
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
        tasks = output.get("tasks")
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

    if normalized in {"draft_task", "update_task", "request_task_activation", "pause_task"}:
        task = _sanitize_task_payload(output.get("task"))
        if task:
            out["task"] = task
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
