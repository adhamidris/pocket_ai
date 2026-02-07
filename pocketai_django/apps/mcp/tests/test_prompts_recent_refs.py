from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.mcp.prompts import _recent_search_refs_note


class RecentSearchRefsPromptNoteTests(SimpleTestCase):
    def test_recent_search_refs_note_includes_exact_ids(self) -> None:
        conversation = SimpleNamespace(
            metadata={
                "mcp_recent_search_refs": {
                    "refs": [
                        {
                            "id": "03669f1f-7eab-4b7f-aff9-771dcd6bbea8",
                            "label": "Fees and Charges Credit Cards Eng_185 - chunk 9",
                            "kind": "table_chunk",
                        }
                    ]
                }
            }
        )

        note = _recent_search_refs_note(conversation)
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("read_knowledge", note)
        self.assertIn("03669f1f-7eab-4b7f-aff9-771dcd6bbea8", note)

    def test_recent_search_refs_note_absent_without_refs(self) -> None:
        conversation = SimpleNamespace(metadata={})
        self.assertIsNone(_recent_search_refs_note(conversation))
