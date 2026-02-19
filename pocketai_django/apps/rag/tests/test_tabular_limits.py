from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.mcp.types import ToolRateLimitExceeded
from apps.rag.tabular_limits import ToolRateLimit, enforce_tool_rate_limit
from core.cache_resilience import CacheUnavailableError


@override_settings(MCP_DISABLE_TOOL_RATE_LIMITS=False)
class TabularRateLimitHardeningTests(SimpleTestCase):
    def test_rate_limit_fails_closed_when_cache_unavailable(self) -> None:
        business = SimpleNamespace(id=uuid.uuid4())
        limit = ToolRateLimit(calls_per_minute=10, window_seconds=60, scope="business")

        with mock.patch("apps.rag.tabular_limits.reserve_counter", side_effect=CacheUnavailableError("redis down")):
            with self.assertRaises(ToolRateLimitExceeded):
                enforce_tool_rate_limit(
                    business_profile=business,
                    tool="dataset_query",
                    rate_limit=limit,
                )

    def test_rate_limit_still_enforces_threshold(self) -> None:
        business = SimpleNamespace(id=uuid.uuid4())
        limit = ToolRateLimit(calls_per_minute=3, window_seconds=60, scope="business")

        with mock.patch("apps.rag.tabular_limits.reserve_counter", return_value=4):
            with self.assertRaises(ToolRateLimitExceeded):
                enforce_tool_rate_limit(
                    business_profile=business,
                    tool="dataset_query",
                    rate_limit=limit,
                )
