from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.mcp.orchestrator import McpOrchestratorService
from apps.mcp.types import CharacterBudgetExceeded
from core.cache_resilience import CacheUnavailableError


class CharBudgetResilienceTests(SimpleTestCase):
    def test_minute_budget_fails_closed_when_cache_unavailable(self) -> None:
        service = McpOrchestratorService.__new__(McpOrchestratorService)
        service.char_budget_window_seconds = 60

        business = SimpleNamespace(id=uuid.uuid4())
        reserver = service._build_minute_budget_reserver(business, 500)
        self.assertIsNotNone(reserver)

        with mock.patch("apps.mcp.orchestrator.reserve_counter", side_effect=CacheUnavailableError("redis down")):
            with self.assertRaises(CharacterBudgetExceeded):
                reserver(50)

    def test_minute_budget_still_enforces_limit(self) -> None:
        service = McpOrchestratorService.__new__(McpOrchestratorService)
        service.char_budget_window_seconds = 60

        business = SimpleNamespace(id=uuid.uuid4())
        reserver = service._build_minute_budget_reserver(business, 100)
        self.assertIsNotNone(reserver)

        with mock.patch("apps.mcp.orchestrator.reserve_counter", return_value=120):
            with self.assertRaises(CharacterBudgetExceeded):
                reserver(30)
