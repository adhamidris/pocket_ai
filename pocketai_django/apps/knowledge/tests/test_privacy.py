from __future__ import annotations

from django.test import SimpleTestCase

from apps.knowledge.privacy import redact_free_text


class RedactFreeTextTests(SimpleTestCase):
    """Tests for redact_free_text UUID-safe PII masking."""

    # ── Phone numbers should still be redacted ──────────────────────

    def test_us_phone_with_parens(self):
        self.assertIn("[PHONE]", redact_free_text("Call (555) 123-4567"))

    def test_us_phone_with_dots(self):
        self.assertIn("[PHONE]", redact_free_text("Call 555.123.4567"))

    def test_us_phone_with_spaces(self):
        self.assertIn("[PHONE]", redact_free_text("Call 555 123 4567"))

    def test_international_phone(self):
        self.assertIn("[PHONE]", redact_free_text("Call +1 (555) 123-4567"))

    # ── UUIDs must be preserved intact ──────────────────────────────

    def test_digit_heavy_uuid_preserved(self):
        uuid = "b955489c-3eab-4159-8135-1d013b55c2e9"
        result = redact_free_text(uuid)
        self.assertEqual(result, uuid)
        self.assertNotIn("[PHONE]", result)

    def test_letter_heavy_uuid_preserved(self):
        uuid = "abcdefab-abcd-abcd-abcd-abcdefabcdef"
        result = redact_free_text(uuid)
        self.assertEqual(result, uuid)
        self.assertNotIn("[PHONE]", result)

    # ── Mixed text with both UUIDs and phone numbers ────────────────

    def test_mixed_uuid_and_phone(self):
        text = "ID: b955489c-3eab-4159-8135-1d013b55c2e9, phone: +1 (555) 123-4567"
        result = redact_free_text(text)
        self.assertIn("b955489c-3eab-4159-8135-1d013b55c2e9", result)
        self.assertIn("[PHONE]", result)
        self.assertNotIn("555", result.split("phone:")[1])

    # ── Other PII types still work ──────────────────────────────────

    def test_email_redacted(self):
        result = redact_free_text("contact john.doe@example.com please")
        self.assertIn("j***@example.com", result)
        self.assertNotIn("john.doe@example.com", result)

    def test_ssn_redacted(self):
        result = redact_free_text("SSN is 123-45-6789")
        self.assertIn("[SSN]", result)
        self.assertNotIn("123-45-6789", result)

    def test_iban_redacted(self):
        result = redact_free_text("IBAN: GB29NWBK60161331926819")
        self.assertIn("[IBAN]", result)
        self.assertNotIn("GB29NWBK60161331926819", result)

    # ── Edge cases ──────────────────────────────────────────────────

    def test_empty_string(self):
        self.assertEqual(redact_free_text(""), "")

    def test_none_input(self):
        # redact_free_text expects str, but the guard handles falsy values.
        self.assertEqual(redact_free_text(""), "")
