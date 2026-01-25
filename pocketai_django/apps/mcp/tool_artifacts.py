from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any, Mapping

from django.db import transaction
from django.utils import timezone

from apps.conversations.models import Conversation
from apps.mcp.models import McpToolOutputArtifact


def _clip_text(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = str(value)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _compact_json_value(
    value: object,
    *,
    max_depth: int,
    max_dict_keys: int,
    max_list_items: int,
    max_string_chars: int,
) -> object:
    if max_depth <= 0:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return "[truncated]"

    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _clip_text(value, max_string_chars)
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for idx, (key, val) in enumerate(value.items()):
            if idx >= max_dict_keys:
                out["__truncated_keys__"] = True
                break
            key_str = str(key)
            out[key_str] = _compact_json_value(
                val,
                max_depth=max_depth - 1,
                max_dict_keys=max_dict_keys,
                max_list_items=max_list_items,
                max_string_chars=max_string_chars,
            )
        return out
    if isinstance(value, list):
        items_out: list[object] = []
        for item in value[:max_list_items]:
            items_out.append(
                _compact_json_value(
                    item,
                    max_depth=max_depth - 1,
                    max_dict_keys=max_dict_keys,
                    max_list_items=max_list_items,
                    max_string_chars=max_string_chars,
                )
            )
        if len(value) > max_list_items:
            items_out.append({"__truncated_items__": True, "total_items": len(value)})
        return items_out
    if isinstance(value, tuple):
        return _compact_json_value(
            list(value),
            max_depth=max_depth,
            max_dict_keys=max_dict_keys,
            max_list_items=max_list_items,
            max_string_chars=max_string_chars,
        )
    return _clip_text(value, max_string_chars)


def build_prompt_view_for_remote_tool_result(tool_result: Mapping[str, object]) -> dict[str, object]:
    """
    Produce a compact, JSON-safe prompt_view for the LLM.

    This intentionally avoids dumping full structured payloads into context. The
    full output is stored as an artifact instead.
    """

    view: dict[str, object] = {}
    text = tool_result.get("text")
    if isinstance(text, str) and text.strip():
        view["text"] = _clip_text(text.strip(), 1600)

    content = tool_result.get("content")
    if isinstance(content, list) and content:
        content_out: list[dict[str, object]] = []
        for item in content[:6]:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            if not isinstance(item_type, str) or not item_type.strip():
                continue
            entry: dict[str, object] = {"type": item_type.strip()}
            if item_type == "text":
                entry["text"] = _clip_text(item.get("text"), 1200)
            elif item_type == "resource_link":
                uri = item.get("uri")
                if isinstance(uri, str) and uri.strip():
                    entry["uri"] = uri.strip()
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    entry["name"] = _clip_text(name.strip(), 200)
            elif item_type == "structured":
                data = item.get("data")
                entry["data"] = _compact_json_value(
                    data,
                    max_depth=3,
                    max_dict_keys=16,
                    max_list_items=6,
                    max_string_chars=240,
                )
            content_out.append(entry)
        if content_out:
            view["content"] = content_out
        if len(content) > len(content_out):
            view["content_truncated"] = True
            view["content_items_total"] = len(content)
    return view


def build_prompt_view_for_text_artifact(
    *,
    text: str,
    title: str | None = None,
    content_type: str | None = None,
    max_chars: int = 1600,
) -> dict[str, object]:
    """
    Compact prompt_view for large inline text/table reads.

    Keep this intentionally small; the full content lives in an artifact.
    """

    preview = _clip_text(text.strip(), max_chars) if isinstance(text, str) else ""
    view: dict[str, object] = {"text": preview}
    if title:
        view["title"] = _clip_text(str(title).strip(), 200)
    if content_type:
        view["type"] = _clip_text(str(content_type).strip(), 40)
    try:
        view["chars_total"] = len(text)
    except Exception:
        pass
    return view


def store_local_tool_output_artifact(
    *,
    conversation: Conversation,
    invoked_tool: str,
    request: Mapping[str, object],
    response: Mapping[str, object],
    status: str = "ok",
    is_error: bool = False,
    retention_days: int | None = None,
    max_per_conversation: int | None = None,
) -> str | None:
    """
    Store a local (first-party) tool output artifact (tenant-scoped).

    Best-effort retention:
    - prune artifacts older than `retention_days` (if set)
    - keep at most `max_per_conversation` newest artifacts for this tool (if set)
    """

    safe_invoked = str(invoked_tool or "")[:200]
    safe_status = str(status or "")[:48]

    try:
        with transaction.atomic():
            artifact = McpToolOutputArtifact.objects.create(
                conversation=conversation,
                invoked_tool=safe_invoked,
                status=safe_status,
                is_error=bool(is_error),
                request=_json_safe(request),
                response=_json_safe(response),
            )

            qs = McpToolOutputArtifact.objects.filter(conversation=conversation, invoked_tool=safe_invoked)

            if retention_days is not None:
                try:
                    days = int(retention_days)
                except (TypeError, ValueError):
                    days = 0
                if days > 0:
                    cutoff = timezone.now() - timedelta(days=days)
                    qs.filter(created_at__lt=cutoff).delete()

            if max_per_conversation is not None:
                try:
                    keep = int(max_per_conversation)
                except (TypeError, ValueError):
                    keep = 0
                if keep > 0:
                    keep_ids = list(qs.order_by("-created_at").values_list("id", flat=True)[:keep])
                    qs.exclude(id__in=keep_ids).delete()

            return str(artifact.id)
    except Exception:
        return None


def store_remote_tool_output_artifact(
    *,
    conversation: Conversation,
    tool_call_id: str,
    tool_event_id: str,
    invoked_tool: str,
    remote_event_payload: Mapping[str, object],
    tool_result: Mapping[str, object],
) -> str | None:
    remote = remote_event_payload.get("remote") if isinstance(remote_event_payload.get("remote"), Mapping) else None
    connection_id = None
    connection_name = ""
    remote_tool = ""
    if remote:
        raw_conn_id = remote.get("connection_id")
        if isinstance(raw_conn_id, str) and raw_conn_id.strip():
            try:
                connection_id = uuid.UUID(raw_conn_id.strip())
            except ValueError:
                connection_id = None
        raw_conn_name = remote.get("connection_name")
        if isinstance(raw_conn_name, str):
            connection_name = raw_conn_name.strip()
        raw_remote_tool = remote.get("remote_tool")
        if isinstance(raw_remote_tool, str):
            remote_tool = raw_remote_tool.strip()

    tool_id = str(remote_event_payload.get("tool_name") or "").strip()
    request_payload: dict[str, object] = {
        "remote": dict(remote) if remote else {},
        "input": remote_event_payload.get("input") if remote_event_payload.get("input") is not None else {},
    }

    # Ensure DB writes are atomic per tool call; never raise out of this helper.
    try:
        with transaction.atomic():
            artifact = McpToolOutputArtifact.objects.create(
                conversation=conversation,
                tool_call_id=str(tool_call_id or "")[:128],
                tool_event_id=str(tool_event_id or "")[:128],
                invoked_tool=str(invoked_tool or "")[:200],
                tool_id=tool_id[:240],
                remote_connection_id=connection_id,
                remote_connection_name=connection_name[:240],
                remote_tool=remote_tool[:240],
                status=str(tool_result.get("status") or "")[:48],
                is_error=bool(tool_result.get("is_error")),
                request=request_payload,
                response=_json_safe(tool_result),
            )
            return str(artifact.id)
    except Exception:
        return None


def _json_safe(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)
