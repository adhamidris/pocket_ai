from __future__ import annotations

import json
import re
from typing import Mapping

from apps.conversations.models import AgentRun, AgentRunStatus


_JSON_DECODER = json.JSONDecoder()


def _clip_text(value: object, limit: int = 1000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _json_object_spans(text: str) -> list[tuple[int, int, Mapping[str, object]]]:
    spans: list[tuple[int, int, Mapping[str, object]]] = []
    if not text:
        return spans
    cursor = 0
    while cursor < len(text):
        start = text.find("{", cursor)
        if start < 0:
            break
        try:
            parsed, end_offset = _JSON_DECODER.raw_decode(text[start:])
        except ValueError:
            cursor = start + 1
            continue
        end = start + int(end_offset)
        if isinstance(parsed, Mapping):
            spans.append((start, end, parsed))
        cursor = max(start + 1, end)
    return spans


def _clean_text_fragment(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    spans = _json_object_spans(text)
    if spans:
        parts: list[str] = []
        cursor = 0
        for start, end, _parsed in spans:
            parts.append(text[cursor:start])
            cursor = end
        parts.append(text[cursor:])
        text = "".join(parts)
    text = re.sub(r"```(?:json)?\s*```", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _json_report_from_text(text: str) -> Mapping[str, object]:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    candidates: list[Mapping[str, object]] = []
    try:
        parsed = json.loads(stripped)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, Mapping):
        candidates.append(parsed)
    candidates.extend(parsed for _start, _end, parsed in _json_object_spans(stripped))
    for parsed_map in candidates:
        candidate = parsed_map.get("run_report") if isinstance(parsed_map.get("run_report"), Mapping) else parsed_map
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("findings") or candidate.get("objective") or candidate.get("status") or candidate.get("notification_candidate"):
            return candidate
    return {}


def _clean_report_text(value: object, limit: int) -> str:
    return _clip_text(_clean_text_fragment(value), limit)


def _run_report(run: AgentRun) -> Mapping[str, object]:
    result = _as_mapping(getattr(run, "result", None))
    report = result.get("run_report") or result.get("runReport")
    if isinstance(report, Mapping):
        return report
    response_text = str(result.get("response_text") or result.get("responseText") or "").strip()
    return _json_report_from_text(response_text)


def _list_strings(value: object, *, limit: int = 6, chars: int = 700) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value[:limit]:
        if isinstance(item, str):
            text = _clean_report_text(item, chars)
        elif isinstance(item, Mapping):
            text = _clean_report_text(" · ".join(str(v) for v in item.values() if v), chars)
        else:
            text = _clean_report_text(item, chars)
        if text:
            items.append(text)
    return items


def _actions(value: object, *, limit: int = 6) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, str]] = []
    for item in value[:limit]:
        if isinstance(item, str):
            label = _clip_text(item, 160)
            if label:
                actions.append({"label": label, "status": ""})
            continue
        if not isinstance(item, Mapping):
            continue
        label = _clip_text(item.get("label") or item.get("tool") or item.get("tool_name") or item.get("name"), 160)
        status = _clip_text(item.get("status"), 80)
        if label or status:
            actions.append({"label": label or "Action", "status": status})
    return actions


def _status_tone(status: str, report_status: str = "") -> str:
    normalized = (status or "").strip().lower()
    report_normalized = (report_status or "").strip().lower()
    if normalized in {AgentRunStatus.WAITING_APPROVAL, AgentRunStatus.WAITING_USER, AgentRunStatus.PAUSED}:
        return "attention"
    if normalized in {AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}:
        return "danger"
    if normalized in {AgentRunStatus.RUNNING, AgentRunStatus.QUEUED, AgentRunStatus.WAITING_CHILD, AgentRunStatus.WAITING_EXTERNAL}:
        return "active"
    if normalized == AgentRunStatus.COMPLETED:
        if report_normalized in {"failed", "error"}:
            return "danger"
        if report_normalized in {"needs_approval", "needs_user"}:
            return "attention"
        return "success"
    return "neutral"


def build_agent_run_display(run: AgentRun) -> dict[str, object]:
    result = _as_mapping(getattr(run, "result", None))
    report = _run_report(run)
    notification = _as_mapping(report.get("notification_candidate") or report.get("notification"))
    response_text = str(result.get("response_text") or result.get("responseText") or "").strip()
    clean_response_text = _clean_text_fragment(response_text)
    notification_body = _clean_text_fragment(notification.get("body"))
    notification_title = _clean_text_fragment(notification.get("title"))
    report_status = _clean_report_text(report.get("status"), 120)

    findings = _list_strings(report.get("findings"), limit=8, chars=700)
    actions = _actions(report.get("actions_taken") or report.get("actionsTaken"), limit=8)
    recommended_next_step = _clean_report_text(report.get("recommended_next_step") or report.get("recommendedNextStep"), 700)

    agent_message = ""
    if notification_body:
        agent_message = _clip_text(notification_body, 1000)
    elif clean_response_text:
        agent_message = _clip_text(clean_response_text.split("\n\n", 1)[0], 1000)
    elif findings:
        agent_message = findings[0]
    elif recommended_next_step:
        agent_message = recommended_next_step

    summary = ""
    if run.status == AgentRunStatus.FAILED and getattr(run, "error_detail", ""):
        summary = _clip_text(run.error_detail, 700)
    elif notification_title:
        summary = _clip_text(notification_title, 240)
    elif findings:
        summary = findings[0]
    elif agent_message:
        summary = agent_message
    elif run.status == AgentRunStatus.COMPLETED:
        summary = "Completed. No detailed report was recorded."
    elif run.status == AgentRunStatus.RUNNING:
        summary = "Running now."
    elif run.status == AgentRunStatus.QUEUED:
        summary = "Queued."
    else:
        summary = _clip_text(run.status.replace("_", " ").title(), 240)

    raw_debug: dict[str, object] = {}
    if response_text:
        raw_debug["responseText"] = _clip_text(response_text, 6000)
    if report:
        raw_debug["runReport"] = dict(report)
    tool_trace = result.get("tool_trace") or result.get("toolTrace")
    if isinstance(tool_trace, list) and tool_trace:
        raw_debug["toolTrace"] = tool_trace[:12]

    return {
        "summary": _clip_text(summary, 700),
        "agentMessage": _clip_text(agent_message, 1200),
        "findings": findings,
        "actionsTaken": actions,
        "recommendedNextStep": recommended_next_step,
        "statusTone": _status_tone(run.status, report_status),
        "reportStatus": report_status,
        "rawAvailable": bool(raw_debug),
        "rawDebug": raw_debug,
    }
