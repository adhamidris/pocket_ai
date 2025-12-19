from __future__ import annotations

from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.conversations.portal import ChatPortalService


class ChatPortalIdentifierCaptureTests(SimpleTestCase):
    def setUp(self) -> None:
        self.service = ChatPortalService()

    def test_capture_email_and_phone(self) -> None:
        convo = SimpleNamespace(metadata={})
        updated = self.service._capture_customer_identifiers(
            conversation=convo,
            body="My email is test@example.com and phone is +1 (415) 555-1212",
            message_metadata={},
        )
        self.assertTrue(updated)
        identifiers = convo.metadata.get("customer_identifiers")
        self.assertEqual(identifiers.get("email"), "test@example.com")
        self.assertIn("phone", identifiers)

    def test_capture_labeled_ticket_id(self) -> None:
        convo = SimpleNamespace(metadata={})
        updated = self.service._capture_customer_identifiers(
            conversation=convo,
            body="ticket id TCK-1011",
            message_metadata={},
        )
        self.assertTrue(updated)
        identifiers = convo.metadata.get("customer_identifiers")
        self.assertEqual(identifiers.get("external_id"), "TCK-1011")

    def test_merge_existing_and_metadata(self) -> None:
        convo = SimpleNamespace(metadata={"customer_identifiers": {"email": "keep@example.com"}})
        updated = self.service._capture_customer_identifiers(
            conversation=convo,
            body="Here is my phone 415-555-0000",
            message_metadata={"customer_identifiers": {"customer_id": "C123"}},
        )
        self.assertTrue(updated)
        identifiers = convo.metadata.get("customer_identifiers")
        self.assertEqual(identifiers.get("email"), "keep@example.com")
        self.assertEqual(identifiers.get("customer_id"), "C123")
        self.assertIn("phone", identifiers)
