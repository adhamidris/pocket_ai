from __future__ import annotations

from django.test import SimpleTestCase

from apps.mcp.sanitizer import extract_sentences


class SanitizerSentenceSplitTests(SimpleTestCase):
    def test_extract_sentences_keeps_decimal_values_intact(self) -> None:
        text = "Interest Rate: 3.99% and Late Payment Fee: EGP 150."

        sentences, remainder = extract_sentences(text)

        self.assertEqual(remainder, "")
        self.assertEqual(len(sentences), 1)
        self.assertEqual(sentences[0][0], text)
