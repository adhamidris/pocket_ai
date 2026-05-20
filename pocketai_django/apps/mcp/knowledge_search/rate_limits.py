from __future__ import annotations

from typing import Mapping

from django.conf import settings

from apps.conversations.models import Conversation
from apps.rag.tabular_limits import ToolRateLimit, enforce_tool_rate_limit

from ..types import ToolRateLimitExceeded


def _enforce_search_rate_limit(conversation: Conversation) -> Mapping[str, object] | None:
    window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
    try:
        calls_per_minute = int(getattr(settings, "MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE", 120) or 0)
    except (TypeError, ValueError):
        calls_per_minute = 120
    calls_per_minute = 0 if calls_per_minute < 0 else calls_per_minute
    try:
        enforce_tool_rate_limit(
            business_profile=conversation.business_profile,
            tool="search_knowledge",
            rate_limit=ToolRateLimit(
                calls_per_minute=None if calls_per_minute <= 0 else calls_per_minute,
                window_seconds=window_seconds,
                scope="business",
            ),
        )
    except ToolRateLimitExceeded as exc:
        return {
            "tool": "search_knowledge",
            "status": "throttled",
            "error": "rate_limited",
            "error_code": "rate_limited",
            "snippets": [],
            "throttle_notice": {"type": "rate_limited", "message": str(exc)},
        }
    return None
