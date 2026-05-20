from __future__ import annotations

import json
from typing import Mapping

from apps.agent_runs.execution.audit import _clip_text


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
