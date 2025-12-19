from __future__ import annotations

from django.test import SimpleTestCase

from apps.mcp.types import CharacterBudgetExceeded, ToolExecutionContext


class ToolExecutionContextTests(SimpleTestCase):
    def test_reserve_characters_respects_turn_limit(self) -> None:
        context = ToolExecutionContext(char_budget_per_turn=100)
        context.reserve_characters(80)
        with self.assertRaises(CharacterBudgetExceeded):
            context.reserve_characters(30)

    def test_reserve_characters_respects_minute_limit(self) -> None:
        calls: list[int] = []

        def reserver(count: int) -> None:
            calls.append(count)
            if count > 10:
                raise CharacterBudgetExceeded("minute budget exceeded")

        context = ToolExecutionContext(char_budget_per_minute=100, minute_budget_reserver=reserver)
        context.reserve_characters(5)
        self.assertEqual(calls, [5])
        with self.assertRaises(CharacterBudgetExceeded):
            context.reserve_characters(20)
