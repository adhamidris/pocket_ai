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

    def test_document_read_drives_primary_document(self) -> None:
        context = ToolExecutionContext()
        context.track_document_reference("doc-a", title="Doc A", stage="vector", confidence=0.8)
        self.assertEqual(context.primary_upload_id, "doc-a")
        self.assertFalse(context.has_strong_primary_document())

        context.track_document_read("doc-b", title="Doc B")
        self.assertEqual(context.primary_upload_id, "doc-b")
        self.assertTrue(context.has_strong_primary_document())
        self.assertEqual(context.document_context["doc-b"].get("read_count"), 1)

        # Search-only references should not override a read-confirmed primary.
        context.track_document_reference("doc-a", title="Doc A", stage="vector", confidence=0.9)
        self.assertEqual(context.primary_upload_id, "doc-b")

    def test_document_context_persists_read_count_and_primary(self) -> None:
        context = ToolExecutionContext()
        context.track_document_reference("doc-a", title="Doc A", stage="vector", confidence=0.8)
        context.track_document_read("doc-b", title="Doc B")
        persisted = context.get_document_context_for_persistence()

        hydrated = ToolExecutionContext()
        hydrated.hydrate_document_context(persisted)
        self.assertEqual(hydrated.primary_upload_id, "doc-b")
        self.assertTrue(hydrated.has_strong_primary_document())
        self.assertEqual(hydrated.document_context["doc-b"].get("read_count"), 1)

    def test_recent_search_refs_persist_and_hydrate(self) -> None:
        context = ToolExecutionContext()
        context.set_recent_search_refs(
            [
                {
                    "id": "03669f1f-7eab-4b7f-aff9-771dcd6bbea8",
                    "label": "Fees and Charges Credit Cards Eng_185 - chunk 9",
                    "kind": "table_chunk",
                    "type": "table",
                    "document_id": "6bfaa7d3-0969-4293-aa41-9674581daa14",
                },
                {"id": "03669f1f-7eab-4b7f-aff9-771dcd6bbea8", "label": "duplicate should drop"},
                {"id": "", "label": "missing id should drop"},
            ]
        )
        persisted = context.get_recent_search_refs_for_persistence()
        self.assertEqual(len(persisted.get("refs") or []), 1)
        self.assertTrue(context.recent_search_refs_updated)

        hydrated = ToolExecutionContext()
        hydrated.hydrate_recent_search_refs(persisted)
        self.assertEqual(len(hydrated.recent_search_refs), 1)
        self.assertEqual(
            hydrated.recent_search_refs[0].get("id"),
            "03669f1f-7eab-4b7f-aff9-771dcd6bbea8",
        )
        self.assertFalse(hydrated.recent_search_refs_updated)

    def test_recent_search_refs_can_be_cleared(self) -> None:
        context = ToolExecutionContext()
        context.set_recent_search_refs([{"id": "03669f1f-7eab-4b7f-aff9-771dcd6bbea8"}])
        self.assertEqual(len(context.recent_search_refs), 1)
        context.set_recent_search_refs([])
        self.assertEqual(context.recent_search_refs, [])
        self.assertTrue(context.recent_search_refs_updated)
