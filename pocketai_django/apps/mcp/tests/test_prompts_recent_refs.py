from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.mcp.prompts import _recent_search_refs_note, _scope_resolution_note, build_messages


class _EmptyMessageQuerySet:
    def order_by(self, *_args, **_kwargs):
        return self

    def __getitem__(self, item):
        if isinstance(item, slice):
            return []
        raise TypeError("Only slicing is supported.")


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

    def test_scope_resolution_note_includes_base_and_resolved_query(self) -> None:
        conversation = SimpleNamespace(
            metadata={
                "mcp_scope_clarification": {
                    "resolution": {
                        "mode": "specific",
                        "base_query": "plus fees",
                        "resolved_query": "plus fees focus only on cheques fees",
                        "category": "cheques",
                    }
                }
            }
        )

        note = _scope_resolution_note(conversation)
        self.assertIsNotNone(note)
        assert note is not None
        self.assertIn("base_query=plus fees", note)
        self.assertIn("resolved_query=plus fees focus only on cheques fees", note)
        self.assertIn("selected_category=cheques", note)

    def test_build_messages_includes_scope_resolution_system_note(self) -> None:
        conversation = SimpleNamespace(
            agent_profile=None,
            business_profile=SimpleNamespace(name="Acme"),
            metadata={
                "mcp_scope_clarification": {
                    "resolution": {
                        "mode": "specific",
                        "base_query": "plus fees",
                        "resolved_query": "plus fees focus only on cheques fees",
                        "category": "cheques",
                    }
                }
            },
            summary="",
            messages=_EmptyMessageQuerySet(),
        )

        messages = build_messages(
            conversation=conversation,
            user_message="Do these fees address my first question?",
        )
        system_text = str(messages[0]["content"])

        self.assertIn("Active scope resolution from prior MCQ selection:", system_text)
        self.assertIn("base_query=plus fees", system_text)
        self.assertIn("resolved_query=plus fees focus only on cheques fees", system_text)
