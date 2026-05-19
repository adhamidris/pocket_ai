from __future__ import annotations

from django.test import SimpleTestCase

from apps.mcp.text.redaction import REDACTED_VALUE, redact_tool_input_payload


class McpToolInputRedactionTests(SimpleTestCase):
    def test_redacts_setup_fields_and_secretish_keys(self) -> None:
        payload = {
            "connection_string": "postgresql://user:pass@host:5432/db",
            "store_url": "https://shop.example",
            "query": "select 1",
            "token": "super-secret",
            "prompt_tokens": 12,
            "nested": {"consumer_secret": "still-secret", "value": 1},
        }

        redacted = redact_tool_input_payload(payload, sensitive_keys=["connection_string", "store_url"])
        self.assertIsInstance(redacted, dict)
        assert isinstance(redacted, dict)

        self.assertEqual(redacted["connection_string"], REDACTED_VALUE)
        self.assertEqual(redacted["store_url"], REDACTED_VALUE)
        self.assertEqual(redacted["token"], REDACTED_VALUE)
        self.assertEqual(redacted["prompt_tokens"], 12)
        self.assertEqual(redacted["query"], "select 1")
        self.assertEqual(redacted["nested"]["consumer_secret"], REDACTED_VALUE)
        self.assertEqual(redacted["nested"]["value"], 1)

