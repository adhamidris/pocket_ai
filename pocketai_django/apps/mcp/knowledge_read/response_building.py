from __future__ import annotations

import json
from typing import Mapping, Sequence

from ..types import ToolExecutionContext


def _response_status(
    *,
    contents: Sequence[Mapping[str, object]],
    read: Sequence[Mapping[str, object]],
    deferred: Sequence[Mapping[str, object]],
    errors: Sequence[Mapping[str, object]],
) -> str:
    if errors or deferred or any(str(entry.get("status") or "") in {"truncated", "partial", "artifact"} for entry in read):
        return "truncated" if contents else "error"
    return "ok"

def _compact_read_summaries_for_prompt(entries: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """
    Keep read summaries lean for prompt injection.

    We preserve control-flow entries (truncated/error/deferred/covered/artifact/etc.)
    and only keep "full" entries when they carry actionable metadata (hint/cursor/artifact).
    """

    compact_entries: list[dict[str, object]] = []
    for entry in entries:
        status_raw = str(entry.get("status") or "").strip()
        status_key = status_raw.lower()
        has_hint = isinstance(entry.get("hint"), str) and bool(str(entry.get("hint") or "").strip())
        has_cursor = isinstance(entry.get("next_cursor"), str) and bool(str(entry.get("next_cursor") or "").strip())
        has_artifact = isinstance(entry.get("artifact_id"), str) and bool(str(entry.get("artifact_id") or "").strip())
        keep_entry = status_key != "full" or has_hint or has_cursor or has_artifact
        if not keep_entry:
            continue

        out: dict[str, object] = {}
        item_id = entry.get("id")
        if isinstance(item_id, str) and item_id.strip():
            out["id"] = item_id.strip()
        elif item_id is not None:
            out["id"] = item_id

        if status_raw:
            out["status"] = status_raw

        chars_value = entry.get("chars")
        if isinstance(chars_value, bool):
            chars_value = None
        if isinstance(chars_value, (int, float)):
            out["chars"] = int(chars_value)
        elif isinstance(chars_value, str) and chars_value.strip():
            try:
                out["chars"] = int(chars_value.strip())
            except (TypeError, ValueError):
                pass

        error_code = entry.get("error_code")
        if isinstance(error_code, str) and error_code.strip():
            out["error_code"] = error_code.strip()

        if has_artifact:
            out["artifact_id"] = str(entry.get("artifact_id")).strip()
        if has_cursor:
            out["next_cursor"] = str(entry.get("next_cursor")).strip()
        if has_hint:
            out["hint"] = str(entry.get("hint")).strip()

        if out:
            compact_entries.append(out)

    return compact_entries

def _build_response(
    *,
    contents: list[dict[str, object]],
    read: list[dict[str, object]],
    deferred: list[dict[str, object]],
    errors: list[dict[str, object]],
    max_chars: int,
    max_chars_allowed: int,
    context: ToolExecutionContext,
    total_chars_value: int,
    hint: str | None = None,
) -> dict[str, object]:
    read_out = _compact_read_summaries_for_prompt(read)
    payload: dict[str, object] = {
        "tool": "read_knowledge",
        "status": _response_status(
            contents=contents,
            read=read,
            deferred=deferred,
            errors=errors,
        ),
        "evidence": contents,
        "deferred": deferred,
        "max_chars": int(max_chars),
        "max_chars_allowed": int(max_chars_allowed),
        "total_chars": int(total_chars_value),
        "budget": context.budget_snapshot(),
    }
    if read_out:
        payload["read"] = read_out
    if errors:
        payload["errors"] = errors
    if hint:
        payload["hint"] = hint
    elif not contents and (deferred or errors):
        payload["hint"] = (
            "No content could be read. Ensure ids/cursors come from tool results, "
            "increase max_chars (up to max_chars_allowed), or retry with fewer items."
        )
    return payload

def _payload_len_with_budget(payload: Mapping[str, object]) -> int:
    try:
        return len(json.dumps(dict(payload), ensure_ascii=False, default=str))
    except Exception:
        return 0
