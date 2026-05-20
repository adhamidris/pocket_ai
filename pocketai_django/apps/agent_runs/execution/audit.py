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


from apps.agent_runs.execution.approval_previews import (  # noqa: E402
    _build_approval_preview,
    _build_email_approval_preview,
    _build_phone_call_approval_preview,
    _format_email_address,
    _format_email_list,
)
from apps.agent_runs.execution.tool_audit import (  # noqa: E402
    _sanitize_approval_meta,
    _sanitize_remote_meta,
    _sanitize_task_payload,
    _sanitize_tool_input,
    _sanitize_tool_output,
    _summarize_input_payload,
    _summarize_tool_result,
    sanitize_tool_event_for_audit,
)
