from __future__ import annotations

from collections.abc import Callable
from typing import Mapping

from apps.conversations.models import Conversation

from ..runtime.agentic_read_cursor import (
    _sign_agentic_read_cursor_v2,
    _verify_agentic_read_cursor_v2,
)
from ..runtime.tool_artifacts import store_local_tool_output_artifact


def _attach_artifact_for_item(
    item: dict[str, object],
    trace: dict[str, object],
    *,
    conversation: Conversation,
    cursor_payload_base: Callable[..., dict[str, object]],
    resolve_cursor_from_handle: Callable[[str | None], str | None],
    store_cursor_handle: Callable[[str | None], str | None],
    prompt_view_inline_min_chars: int,
    prompt_view_inline_max_chars: int,
    artifact_retention_days: int,
    artifact_max_per_conversation: int,
) -> bool:
    item_id = str(item.get("id") or "").strip()
    payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    if str(payload_obj.get("type") or "") != "text":
        return False
    full_text = payload_obj.get("text")
    if not item_id or not isinstance(full_text, str) or not full_text:
        return False
    if isinstance(item.get("artifact_id"), str) and str(item.get("artifact_id") or "").strip():
        return False

    preview_len = min(len(full_text), prompt_view_inline_max_chars)
    if len(full_text) >= prompt_view_inline_min_chars:
        preview_len = max(prompt_view_inline_min_chars, preview_len)
    preview_text = full_text[:preview_len]
    cursor_after_raw = item.get("next_cursor")
    cursor_after = resolve_cursor_from_handle(cursor_after_raw if isinstance(cursor_after_raw, str) else None)
    cursor_used_local = item.get("cursor_used")
    if isinstance(cursor_used_local, str) and cursor_used_local.strip():
        # Avoid nested artifacts: if this segment was already read from an artifact cursor,
        # we can always clip + continue with that cursor instead of creating a new artifact.
        try:
            used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
        except ValueError:
            used_payload = None
        if isinstance(used_payload, dict) and str(used_payload.get("kind") or "") == "artifact":
            return False

    artifact_payload: dict[str, object] = {
        "kind": "agentic_read_v2",
        "item_id": item_id,
        "title": item.get("title"),
        "type": item.get("type"),
        "text": full_text,
    }
    if isinstance(cursor_after, str) and cursor_after.strip():
        artifact_payload["cursor_after"] = cursor_after.strip()
    if isinstance(cursor_used_local, str) and cursor_used_local.strip():
        artifact_payload["cursor_used"] = cursor_used_local.strip()

    artifact_id = store_local_tool_output_artifact(
        conversation=conversation,
        invoked_tool="read_knowledge",
        request={"refs": [{"id": item_id, **({"cursor": cursor_used_local} if cursor_used_local else {})}]},
        response=artifact_payload,
        status="ok",
        is_error=False,
        retention_days=artifact_retention_days,
        max_per_conversation=artifact_max_per_conversation,
    )
    if not artifact_id:
        return False

    cursor_next = {
        **cursor_payload_base(item_id=item_id, kind="artifact"),
        "artifact_id": artifact_id,
        "char_offset": int(preview_len),
    }
    cursor_str = _sign_agentic_read_cursor_v2(cursor_next)

    item["artifact_id"] = artifact_id
    payload_obj = dict(payload_obj)
    payload_obj["text"] = preview_text
    item["payload"] = payload_obj
    item["chars"] = len(preview_text)
    item["next_cursor"] = store_cursor_handle(cursor_str) or cursor_str
    item["complete"] = False
    item["truncated"] = True

    trace["status"] = "artifact"
    trace["chars"] = len(full_text)
    trace["artifact_id"] = artifact_id
    return True
