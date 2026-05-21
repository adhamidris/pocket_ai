from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag.decision.intent_fallback import IntentFallbackService
from apps.rag.query.classifier import QueryClassification, QueryIntent


class _StubProvider:
    def __init__(self, payload):
        self.payload = payload

    def generate(self, bundle, **kwargs):  # noqa: ANN001
        del bundle, kwargs
        return self.payload


class IntentFallbackServiceTests(SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.heuristic = QueryClassification(
            intent=QueryIntent.EXPLORATORY,
            confidence=0.32,
            entity_type=None,
            entity_names=[],
            attributes=[],
            scope="unknown",
            reasoning="heuristic",
            retrieval_hints={},
        )

    def test_classify_parses_provider_response_text_payload(self) -> None:
        provider = _StubProvider(
            {
                "response_text": (
                    '{"intent":"specific_lookup","confidence":0.81,'
                    '"entity_type":"service request",'
                    '"entity_names":["SR-9001"],'
                    '"attributes":["resolution time"],'
                    '"scope":"specific","reasoning":"Identifier-like lookup."}'
                ),
                "actions": [],
                "extractions": [],
            }
        )
        service = IntentFallbackService(provider_loader=lambda: provider)
        result = service.classify(
            query="SR-9001 resolution time",
            heuristic=self.heuristic,
            tenant_entity_terms=("service request",),
            tenant_attribute_terms=("resolution time",),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.intent, QueryIntent.SPECIFIC_LOOKUP)
        self.assertEqual(result.source, "llm_fallback")
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.entity_type, "service request")
        self.assertIn("SR-9001", result.entity_names)
        self.assertIn("resolution time", result.attributes)

    def test_classify_returns_none_when_payload_is_unusable(self) -> None:
        provider = _StubProvider({"response_text": "not-json"})
        service = IntentFallbackService(provider_loader=lambda: provider)
        result = service.classify(
            query="show me details",
            heuristic=self.heuristic,
        )
        self.assertIsNone(result)
