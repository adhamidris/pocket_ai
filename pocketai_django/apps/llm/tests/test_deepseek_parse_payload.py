from __future__ import annotations

import json

from django.test import SimpleTestCase

from apps.llm.llm_provider import DeepSeekChatProvider


class DeepSeekParsePayloadTests(SimpleTestCase):
    def test_parse_payload_strips_json_code_fence(self) -> None:
        payload = {"response_text": "Hello", "actions": [], "extractions": []}
        content = f"```json\n{json.dumps(payload)}\n```"
        parsed = DeepSeekChatProvider._parse_payload(content)
        self.assertEqual(parsed.get("response_text"), "Hello")

    def test_parse_payload_extracts_embedded_json_object(self) -> None:
        payload = {"response_text": "Hi", "actions": [], "extractions": []}
        content = f"Sure — {json.dumps(payload)} Thanks."
        parsed = DeepSeekChatProvider._parse_payload(content)
        self.assertEqual(parsed.get("response_text"), "Hi")

