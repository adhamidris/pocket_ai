from __future__ import annotations

import dataclasses
import logging

from django.conf import settings

from apps.accounts.models import BusinessProfile
from apps.knowledge.models import KnowledgeUpload
from apps.mcp.types import ToolRateLimitExceeded
from core.cache_resilience import CacheUnavailableError, reserve_counter


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ToolRateLimit:
    calls_per_minute: int | None
    window_seconds: int = 60
    scope: str = "business"  # business | upload


def enforce_tool_rate_limit(
    *,
    business_profile: BusinessProfile,
    tool: str,
    rate_limit: ToolRateLimit,
    upload: KnowledgeUpload | None = None,
) -> None:
    if bool(getattr(settings, "MCP_DISABLE_TOOL_RATE_LIMITS", False)):
        return

    calls_per_minute = rate_limit.calls_per_minute
    if calls_per_minute is None or calls_per_minute <= 0:
        return
    window_seconds = max(10, int(rate_limit.window_seconds or 60))

    key = f"mcp:rate:{tool}:{business_profile.id}"
    if rate_limit.scope == "upload" and upload is not None:
        key += f":{upload.id}"

    try:
        new_total = reserve_counter(
            key=key,
            window_seconds=window_seconds,
            amount=1,
            operation=f"tool_rate_limit:{tool}",
        )
    except CacheUnavailableError:
        logger.error(
            "tabular.rate_limit.unavailable tool=%s business=%s",
            tool,
            business_profile.id,
        )
        raise ToolRateLimitExceeded("Rate limiting is temporarily unavailable. Please retry in a moment.")
    except ToolRateLimitExceeded:
        raise
    except Exception:
        # Fail closed on unexpected limiter failures to avoid uncontrolled fanout under degraded cache state.
        logger.exception("tabular.rate_limit.failed tool=%s business=%s", tool, business_profile.id)
        raise ToolRateLimitExceeded("Rate limiting is temporarily unavailable. Please retry in a moment.")

    if int(new_total) > calls_per_minute:
        raise ToolRateLimitExceeded(
            f"Rate limit exceeded for {tool}. Try again in a moment or narrow the request."
        )
