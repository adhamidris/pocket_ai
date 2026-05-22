from __future__ import annotations

from collections.abc import Callable
from typing import Mapping

from django.conf import settings

from apps.conversations.models import Conversation

from ...runtime.agentic_read_cursor import (
    _sign_agentic_read_cursor_v2,
    _verify_agentic_read_cursor_v2,
)
from ...types import ToolExecutionContext
from ..response_building import _build_response, _payload_len_with_budget
from .output import _attach_artifact_for_item


def fit_read_response_to_prompt_limit(
    *,
    conversation: Conversation,
    context: ToolExecutionContext,
    contents: list[dict[str, object]],
    read: list[dict[str, object]],
    deferred: list[dict[str, object]],
    errors: list[dict[str, object]],
    max_chars: int,
    max_chars_allowed: int,
    prompt_output_limit: int,
    total_chars: int,
    cursor_payload_base: Callable[..., dict[str, object]],
    resolve_cursor_from_handle: Callable[[str | None], str | None],
    store_cursor_handle: Callable[[str | None], str | None],
) -> tuple[dict[str, object], int]:
    try:
        artifact_retention_days = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_RETENTION_DAYS", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS", 30)
            or 30
        )
    except (TypeError, ValueError):
        artifact_retention_days = 30
    artifact_retention_days = max(1, min(365, artifact_retention_days))
    try:
        artifact_max_per_conversation = int(
            getattr(settings, "MCP_READ_KNOWLEDGE_ARTIFACT_MAX_PER_CONVERSATION", None)
            or getattr(settings, "MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION", 200)
            or 200
        )
    except (TypeError, ValueError):
        artifact_max_per_conversation = 200
    artifact_max_per_conversation = max(0, min(5000, artifact_max_per_conversation))

    output_limit = max(0, int(prompt_output_limit))

    prompt_view_inline_min_chars = 200
    prompt_view_inline_max_chars = 1600
    if output_limit:
        # Reserve room for JSON framing + cursors + budget metadata when the prompt cap is small.
        prompt_view_inline_max_chars = min(
            prompt_view_inline_max_chars,
            max(prompt_view_inline_min_chars, output_limit // 2),
        )

    def build_response(*, total_chars_value: int, hint: str | None = None) -> dict[str, object]:
        return _build_response(
            contents=contents,
            read=read,
            deferred=deferred,
            errors=errors,
            max_chars=max_chars,
            max_chars_allowed=max_chars_allowed,
            context=context,
            total_chars_value=total_chars_value,
            hint=hint,
        )

    def attach_artifact_for_item(item: dict[str, object], trace: dict[str, object]) -> bool:
        return _attach_artifact_for_item(
            item,
            trace,
            conversation=conversation,
            cursor_payload_base=cursor_payload_base,
            resolve_cursor_from_handle=resolve_cursor_from_handle,
            store_cursor_handle=store_cursor_handle,
            prompt_view_inline_min_chars=prompt_view_inline_min_chars,
            prompt_view_inline_max_chars=prompt_view_inline_max_chars,
            artifact_retention_days=artifact_retention_days,
            artifact_max_per_conversation=artifact_max_per_conversation,
        )

    # Start with the raw v2 output; if it exceeds the prompt cap once budgets are added,
    # convert the largest content entries to artifacts until it fits.
    total_chars_final = int(total_chars)
    response_hint: str | None = None
    response_candidate = build_response(total_chars_value=total_chars_final)
    if output_limit and _payload_len_with_budget(response_candidate) > output_limit:
        read_by_id: dict[str, dict[str, object]] = {}
        for entry in read:
            if isinstance(entry, Mapping) and entry.get("id"):
                read_by_id[str(entry.get("id"))] = entry  # type: ignore[assignment]

        # Convert biggest items first (best shrink per artifact).
        contents_sorted = sorted(
            (item for item in contents if isinstance(item, Mapping)),
            key=lambda item: int(item.get("chars") or 0),
            reverse=True,
        )
        converted_any = False
        for item in contents_sorted:
            item_id = str(item.get("id") or "").strip()
            trace = read_by_id.get(item_id)
            if not trace or not isinstance(item, dict):
                continue
            if not attach_artifact_for_item(item, trace):
                continue
            converted_any = True
            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = build_response(total_chars_value=total_chars_final)
            if _payload_len_with_budget(response_candidate) <= output_limit:
                break

        if converted_any:
            response_hint = (
                "Some content was stored as an artifact to fit prompt limits. "
                "Use next_cursor to continue reading until complete."
            )

    response_candidate = build_response(total_chars_value=total_chars_final, hint=response_hint)
    if output_limit and response_hint and _payload_len_with_budget(response_candidate) > output_limit:
        # Hints are nice-to-have; when the prompt cap is very small, prefer staying under
        # the hard tool-output limit over including extra explanatory text.
        response_hint = None
        response_candidate = build_response(total_chars_value=total_chars_final)

    # If we're still above the prompt cap (e.g., cursor/budget overhead), clip artifact previews
    # until the JSON payload fits. This doesn't lose evidence because the full text is stored
    # in the artifact and `next_cursor` continues deterministically.
    if output_limit:
        guard_loops = 0
        payload_chars = _payload_len_with_budget(response_candidate)
        while payload_chars > output_limit and guard_loops < 20:
            guard_loops += 1
            artifact_streams: list[tuple[dict[str, object], str, int]] = []
            for item in contents:
                if not isinstance(item, dict):
                    continue
                payload_obj = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
                if str(payload_obj.get("type") or "") != "text" or not isinstance(payload_obj.get("text"), str):
                    continue
                artifact_id_direct = item.get("artifact_id")
                if isinstance(artifact_id_direct, str) and artifact_id_direct.strip():
                    artifact_streams.append((item, artifact_id_direct.strip(), 0))
                    continue
                cursor_used_local = item.get("cursor_used")
                if not (isinstance(cursor_used_local, str) and cursor_used_local.strip()):
                    continue
                try:
                    used_payload = _verify_agentic_read_cursor_v2(cursor_used_local.strip())
                except ValueError:
                    continue
                if str(used_payload.get("kind") or "") != "artifact":
                    continue
                artifact_id = str(used_payload.get("artifact_id") or "").strip()
                if not artifact_id:
                    continue
                try:
                    base_offset = int(used_payload.get("char_offset") or 0)
                except (TypeError, ValueError):
                    base_offset = 0
                artifact_streams.append((item, artifact_id, max(0, base_offset)))

            if not artifact_streams:
                break
            target, target_artifact_id, base_offset = max(
                artifact_streams,
                key=lambda it: len(
                    str(((it[0].get("payload") or {}) if isinstance(it[0].get("payload"), Mapping) else {}).get("text") or "")
                ),
            )
            current_payload = target.get("payload") if isinstance(target.get("payload"), Mapping) else {}
            current_text = current_payload.get("text")
            if not isinstance(current_text, str) or len(current_text) <= prompt_view_inline_min_chars:
                break

            excess = max(1, payload_chars - output_limit)
            new_len = max(prompt_view_inline_min_chars, len(current_text) - excess - 25)
            if new_len >= len(current_text):
                new_len = max(prompt_view_inline_min_chars, len(current_text) - 25)
            clipped_text = current_text[:new_len]
            current_payload = dict(current_payload)
            current_payload["text"] = clipped_text
            target["payload"] = current_payload
            target["chars"] = len(clipped_text)

            item_id = str(target.get("id") or "").strip()
            if item_id and target_artifact_id:
                cursor_next = {
                    **cursor_payload_base(item_id=item_id, kind="artifact"),
                    "artifact_id": target_artifact_id,
                    "char_offset": int(base_offset + len(clipped_text)),
                }
                cursor_signed = _sign_agentic_read_cursor_v2(cursor_next)
                target["next_cursor"] = store_cursor_handle(cursor_signed) or cursor_signed
                target["complete"] = False
                target["truncated"] = True

            total_chars_final = sum(int(entry.get("chars") or 0) for entry in contents if isinstance(entry, Mapping))
            response_candidate = build_response(total_chars_value=total_chars_final, hint=response_hint)
            payload_chars = _payload_len_with_budget(response_candidate)

    return response_candidate, total_chars_final
