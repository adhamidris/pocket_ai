from __future__ import annotations

import uuid

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

    def test_scope_clarification_persists_and_hydrates(self) -> None:
        context = ToolExecutionContext()
        context.set_pending_scope_clarification(
            base_query="what are the fees for plus customers?",
            categories=["loan service fees", "outgoing transfers", "loan service fees"],
            question="Do you want one specific category or all related fees?",
        )
        self.assertTrue(context.scope_clarification_updated)
        pending = context.pending_scope_clarification or {}
        self.assertEqual(len(pending.get("categories") or []), 2)

        context.set_scope_resolution(
            mode="specific",
            base_query="what are the fees for plus customers?",
            resolved_query="what are the fees for plus customers? focus only on loan service fees",
            user_query="loan service fees",
            category="loan service fees",
        )
        self.assertIsNone(context.pending_scope_clarification)
        self.assertIsNotNone(context.scope_resolution)

        persisted = context.get_scope_clarification_for_persistence()
        self.assertIsNotNone(persisted)
        hydrated = ToolExecutionContext()
        hydrated.hydrate_scope_clarification(persisted)
        self.assertFalse(hydrated.scope_clarification_updated)
        self.assertEqual(
            (hydrated.scope_resolution or {}).get("mode"),
            "specific",
        )
        self.assertEqual(
            (hydrated.scope_resolution or {}).get("category"),
            "loan service fees",
        )
        self.assertEqual(
            (hydrated.scope_resolution or {}).get("categories"),
            ["loan service fees"],
        )

    def test_scope_clarification_pending_stores_category_refs(self) -> None:
        context = ToolExecutionContext()
        valid_ref = str(uuid.uuid4())
        context.set_pending_scope_clarification(
            base_query="what are the fees for plus customers?",
            categories=["loan service fees"],
            question="Pick one category.",
            category_refs={
                "loan_service_fees_key": {
                    "ref_ids": [valid_ref, "not-a-uuid"],
                    "source": "retrieval_candidates",
                    "fallback": "scoped_search",
                    "confidence": 1.7,
                },
                "empty_key": {},
            },
        )

        pending = context.pending_scope_clarification or {}
        self.assertEqual(pending.get("contract_version"), 1)
        category_refs = pending.get("category_refs") or {}
        self.assertEqual(set(category_refs.keys()), {"loan_service_fees_key"})
        mapped = category_refs.get("loan_service_fees_key") or {}
        self.assertEqual(mapped.get("ref_ids"), [valid_ref])
        self.assertEqual(mapped.get("source"), "retrieval_candidates")
        self.assertEqual(mapped.get("fallback"), "scoped_search")
        self.assertEqual(mapped.get("confidence"), 1.0)

    def test_scope_resolution_stores_mapped_refs_for_click_followups(self) -> None:
        context = ToolExecutionContext()
        valid_ref = str(uuid.uuid4())
        context.set_scope_resolution(
            mode="specific",
            base_query="what are the fees for plus customers?",
            resolved_query="what are the fees for plus customers? focus only on outgoing transfer fees",
            user_query="scope:category_key:outgoing_transfer_fees_key",
            category="outgoing transfer fees",
            categories=["outgoing transfer fees"],
            category_key="outgoing_transfer_fees_key",
            mapped_ref_ids=[valid_ref, "invalid-id"],
            mapped_refs={
                "ref_ids": [valid_ref, "still-invalid"],
                "source": "scope_state",
                "confidence": 4.2,
            },
            selection_source="category_key",
        )
        resolution = context.scope_resolution or {}
        self.assertEqual(resolution.get("category_key"), "outgoing_transfer_fees_key")
        self.assertEqual(resolution.get("selection_source"), "category_key")
        self.assertEqual(resolution.get("mapped_ref_ids"), [valid_ref])
        mapped_refs = resolution.get("mapped_refs") or {}
        self.assertEqual(mapped_refs.get("ref_ids"), [valid_ref])
        self.assertEqual(mapped_refs.get("source"), "scope_state")
        self.assertEqual(mapped_refs.get("confidence"), 1.0)
