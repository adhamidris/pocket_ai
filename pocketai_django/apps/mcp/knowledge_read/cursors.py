from __future__ import annotations

import time
import uuid
from typing import Mapping

from ..runtime.agentic_read_cursor import (
    _AGENTIC_READ_CURSOR_V2_TTL_SECONDS,
    _verify_agentic_read_cursor_v2,
)


def _cursor_payload_base(*, conversation, item_id: str, kind: str) -> dict[str, object]:
    exp = int(time.time()) + _AGENTIC_READ_CURSOR_V2_TTL_SECONDS
    return {
        "v": 2,
        "exp": exp,
        "conversation_id": str(conversation.id),
        "business_id": str(conversation.business_profile_id),
        "item_id": item_id,
        "kind": kind,
    }

def _decode_cursor(conversation, item_id: str, cursor: str) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    try:
        payload = _verify_agentic_read_cursor_v2(cursor)
    except ValueError as exc:
        return None, {"id": item_id, "error_code": "invalid_cursor", "hint": str(exc) or "Invalid cursor."}
    if str(payload.get("conversation_id") or "") != str(conversation.id):
        return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different conversation."}
    if str(payload.get("business_id") or "") != str(conversation.business_profile_id):
        return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor is for a different business."}
    if str(payload.get("item_id") or "") != item_id:
        return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Cursor does not match the requested id."}
    if int(payload.get("v") or 0) != 2:
        return None, {"id": item_id, "error_code": "invalid_cursor", "hint": "Unsupported cursor version."}
    return payload, None

def _resolve_cursor_from_handle(context, cursor_token: str | None) -> str | None:
    token = str(cursor_token or "").strip()
    if not token:
        return None
    cache_value = getattr(context, "read_cursor_handles", None)
    if isinstance(cache_value, dict):
        mapped = cache_value.get(token)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    return token

def _store_cursor_handle(context, cursor_signed: str | None) -> str | None:
    token = str(cursor_signed or "").strip()
    if not token:
        return None
    cache_value = getattr(context, "read_cursor_handles", None)
    reverse_cache_value = getattr(context, "read_cursor_reverse_handles", None)
    if not isinstance(cache_value, dict) or not isinstance(reverse_cache_value, dict):
        return token

    existing_handle = reverse_cache_value.get(token)
    if isinstance(existing_handle, str) and existing_handle.strip():
        cached_token = cache_value.get(existing_handle.strip())
        if isinstance(cached_token, str) and cached_token == token:
            return existing_handle.strip()

    handle = f"c_{uuid.uuid4().hex[:20]}"
    cache_value[handle] = token
    reverse_cache_value[token] = handle

    while len(cache_value) > 500:
        oldest_handle = next(iter(cache_value))
        oldest_cursor = cache_value.pop(oldest_handle, None)
        if isinstance(oldest_cursor, str):
            reverse_cache_value.pop(oldest_cursor, None)
    while len(reverse_cache_value) > 500:
        oldest_cursor_key = next(iter(reverse_cache_value))
        oldest_handle_value = reverse_cache_value.pop(oldest_cursor_key, None)
        if isinstance(oldest_handle_value, str):
            cache_value.pop(oldest_handle_value, None)

    return handle
