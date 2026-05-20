from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Mapping

from apps.conversations.models import Conversation

from ..models import McpToolOutputArtifact
from ..runtime.agentic_read_cursor import _sign_agentic_read_cursor_v2


def _read_artifact_segment(
    *,
    conversation: Conversation,
    cursor_payload_base: Callable[..., dict[str, object]],
    item_id: str,
    artifact_id: str,
    start_offset: int,
    budget_chars: int,
) -> tuple[str, dict[str, object] | None, bool, dict[str, object] | None]:
    """
    Read from a stored tool-output artifact (Phase 4 fallback).

    Artifact payload shape (response JSON):
      { "text": "...", "title": "...", "type": "text|table", "cursor_after": "opaque_or_null" }
    """

    try:
        artifact_uuid = uuid.UUID(str(artifact_id))
    except (TypeError, ValueError):
        return "", None, True, {"id": item_id, "error_code": "invalid_cursor", "hint": "Invalid artifact id."}

    artifact = (
        McpToolOutputArtifact.objects.filter(
            id=artifact_uuid,
            conversation=conversation,
            invoked_tool="read_knowledge",
        )
        .only("id", "response")
        .first()
    )
    if not artifact:
        return "", None, True, {"id": item_id, "error_code": "artifact_not_found", "hint": "Artifact not found for this conversation."}

    payload = artifact.response if isinstance(getattr(artifact, "response", None), Mapping) else {}
    full_text = payload.get("text")
    if not isinstance(full_text, str):
        full_text = str(full_text or "")
    if not full_text:
        return "", None, True, {"id": item_id, "error_code": "artifact_empty", "hint": "Artifact has no readable text."}

    title_override = payload.get("title")
    type_override = payload.get("type")

    start = max(0, int(start_offset or 0))
    remaining = max(0, int(budget_chars))
    out = full_text[start : start + remaining]
    end = start + len(out)

    cursor_after = payload.get("cursor_after")
    if end < len(full_text):
        cursor_next = {
            **cursor_payload_base(item_id=item_id, kind="artifact"),
            "artifact_id": str(artifact.id),
            "char_offset": int(end),
        }
        cursor_str = _sign_agentic_read_cursor_v2(cursor_next)
        return out, {"cursor": cursor_str}, False, {"title": title_override, "type": type_override}

    if isinstance(cursor_after, str) and cursor_after.strip():
        # Once the artifact stream is consumed, resume the original knowledge cursor (if any).
        return out, {"cursor": cursor_after.strip()}, False, {"title": title_override, "type": type_override}

    return out, None, True, {"title": title_override, "type": type_override}
