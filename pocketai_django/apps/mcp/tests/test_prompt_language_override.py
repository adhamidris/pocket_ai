from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.mcp import prompts


class _EmptyMessageQuerySet:
    def order_by(self, *_args, **_kwargs):
        return self

    def __getitem__(self, item):
        if isinstance(item, slice):
            return []
        raise TypeError("Only slicing is supported.")


def _conversation_with_language(language: str | None):
    metadata = {"ui_language": language} if language else {}
    return SimpleNamespace(
        agent_profile=None,
        business_profile=SimpleNamespace(name="Acme"),
        metadata=metadata,
        summary="",
        messages=_EmptyMessageQuerySet(),
    )


class PromptLanguageOverrideTests(SimpleTestCase):
    def test_build_messages_prefers_selected_english_over_arabic_script_detection(self) -> None:
        conversation = _conversation_with_language("en")

        messages = prompts.build_messages(
            conversation=conversation,
            user_message="مرحبا، أحتاج مساعدة",
        )
        system_text = str(messages[0]["content"])

        self.assertIn("The visitor selected English in the UI", system_text)
        self.assertNotIn("The visitor is writing in Arabic", system_text)

    def test_build_messages_prefers_selected_arabic_over_english_script_detection(self) -> None:
        conversation = _conversation_with_language("ar")

        messages = prompts.build_messages(
            conversation=conversation,
            user_message="Need account help",
        )
        system_text = str(messages[0]["content"])

        self.assertIn("The visitor selected Arabic in the UI", system_text)
        self.assertNotIn("The visitor is writing in English", system_text)

    def test_final_answer_messages_follow_selected_language_policy(self) -> None:
        conversation = _conversation_with_language("en")

        final_messages = prompts.build_final_answer_messages(
            conversation=conversation,
            user_message="مرحبا",
        )
        system_text = str(final_messages[0]["content"])

        self.assertIn("The visitor selected English in the UI", system_text)
        self.assertIn("Reply ONLY in English", system_text)
