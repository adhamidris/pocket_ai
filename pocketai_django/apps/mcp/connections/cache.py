from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def _is_cache_expired(tool_cache: Mapping[str, Any]) -> bool:
    """Check if a tool cache has expired based on its expires_at timestamp."""
    expires_at = tool_cache.get("expires_at")
    if not expires_at:
        # Legacy caches without expiration are considered valid (backwards compatibility)
        return False
    try:
        if isinstance(expires_at, str):
            # Parse ISO format timestamp
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        elif isinstance(expires_at, datetime):
            expiry = expires_at
        else:
            return False
        # Ensure timezone-aware comparison
        now = datetime.now(timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return now > expiry
    except (ValueError, TypeError):
        return False
