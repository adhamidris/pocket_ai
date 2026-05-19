"""
Search knowledge pagination cursor helpers.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core import signing
from django.core.cache import cache

from apps.conversations.models import Conversation

from ..types import ToolExecutionContext

# search_knowledge pagination (cursor) helpers
SEARCH_KNOWLEDGE_CURSOR_SALT = "mcp.search_knowledge.cursor.v1"
SEARCH_KNOWLEDGE_CURSOR_CACHE_PREFIX = "mcp:search_knowledge:cursor:v1"
SEARCH_KNOWLEDGE_CURSOR_HANDLE_CACHE_PREFIX = "mcp:search_knowledge:cursor_handle:v1"


def _search_cursor_cache_key(*, conversation: Conversation, session_id: str) -> str:
    return (
        f"{SEARCH_KNOWLEDGE_CURSOR_CACHE_PREFIX}:"
        f"{conversation.business_profile_id}:"
        f"{conversation.id}:"
        f"{session_id}"
    )


def _search_cursor_handle_cache_key(*, conversation: Conversation, handle: str) -> str:
    return (
        f"{SEARCH_KNOWLEDGE_CURSOR_HANDLE_CACHE_PREFIX}:"
        f"{conversation.business_profile_id}:"
        f"{conversation.id}:"
        f"{handle}"
    )


def _encode_search_cursor(*, session_id: str, offset: int) -> str:
    payload = {"sid": str(session_id), "o": int(offset)}
    return signing.dumps(payload, salt=SEARCH_KNOWLEDGE_CURSOR_SALT)


def _decode_search_cursor(token: str, *, max_age_seconds: int) -> dict[str, object] | None:
    try:
        decoded = signing.loads(token, salt=SEARCH_KNOWLEDGE_CURSOR_SALT, max_age=max_age_seconds)
    except Exception:
        return None
    return decoded if isinstance(decoded, dict) else None


def _search_cursor_ttl_seconds() -> int:
    try:
        cursor_ttl_seconds = int(getattr(settings, "MCP_SEARCH_PAGINATION_TTL_SECONDS", 3600) or 3600)
    except (TypeError, ValueError):
        cursor_ttl_seconds = 3600
    return max(60, cursor_ttl_seconds)


def _resolve_search_cursor_from_handle(
    context: ToolExecutionContext | None,
    conversation: Conversation,
    cursor_token: str | None,
) -> str | None:
    token = str(cursor_token or "").strip()
    if not token:
        return None
    cache_value = getattr(context, "search_cursor_handles", None)
    if isinstance(cache_value, dict):
        mapped = cache_value.get(token)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    if token.startswith("s_"):
        mapped = cache.get(_search_cursor_handle_cache_key(conversation=conversation, handle=token))
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    return token


def _store_search_cursor_handle(
    context: ToolExecutionContext | None,
    conversation: Conversation,
    cursor_signed: str | None,
    *,
    ttl_seconds: int | None = None,
) -> str | None:
    token = str(cursor_signed or "").strip()
    if not token:
        return None
    ttl = _search_cursor_ttl_seconds() if ttl_seconds is None else max(60, int(ttl_seconds))
    cache_value = getattr(context, "search_cursor_handles", None)
    reverse_cache_value = getattr(context, "search_cursor_reverse_handles", None)
    if not isinstance(cache_value, dict) or not isinstance(reverse_cache_value, dict):
        return token

    existing_handle = reverse_cache_value.get(token)
    if isinstance(existing_handle, str) and existing_handle.strip():
        cached_token = cache_value.get(existing_handle.strip())
        if isinstance(cached_token, str) and cached_token == token:
            cache.set(
                _search_cursor_handle_cache_key(conversation=conversation, handle=existing_handle.strip()),
                token,
                ttl,
            )
            return existing_handle.strip()

    handle = f"s_{uuid.uuid4().hex[:20]}"
    cache_value[handle] = token
    reverse_cache_value[token] = handle
    cache.set(_search_cursor_handle_cache_key(conversation=conversation, handle=handle), token, ttl)

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
