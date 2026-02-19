"""
Unit tests for Query Intent Classification.

Tests cover:
- ENUMERATE intent detection ("list all X", "show me every Y")
- SPECIFIC_LOOKUP intent detection ("Gold card fees")
- COMPARE intent detection ("compare X vs Y")
- AGGREGATE intent detection ("total fees", "how many")
- EXPLORATORY intent fallback
- Edge cases and regression tests for the credit card scenario
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag import query_classifier as query_classifier_module
from apps.rag.query_classifier import (
    QueryClassifier,
    QueryClassification,
    QueryIntent,
    classify_query,
)


class TestQueryIntentEnumerate(SimpleTestCase):
    """Test ENUMERATE intent classification."""

    def setUp(self):
        self.classifier = QueryClassifier()

    def test_list_all_pattern(self):
        """'list all X' should be classified as ENUMERATE."""
        queries = [
            "list all credit cards",
            "list all products",
            "list all available cards",
            "list all the credit cards and their fees",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.ENUMERATE,
                f"Expected ENUMERATE for '{query}', got {result.intent}"
            )
            self.assertEqual(result.scope, "all")
            self.assertTrue(result.requires_full_coverage())

    def test_show_me_all_pattern(self):
        """'show me all X' should be classified as ENUMERATE."""
        queries = [
            "show me all credit cards",
            "show all cards",
            "show me every card available",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.ENUMERATE,
                f"Expected ENUMERATE for '{query}', got {result.intent}"
            )

    def test_every_pattern(self):
        """'every X' patterns should be classified as ENUMERATE."""
        queries = [
            "what are every card's fees",
            "show every credit card",
            "give me every product",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.ENUMERATE,
                f"Expected ENUMERATE for '{query}', got {result.intent}"
            )

    def test_complete_list_pattern(self):
        """'complete list' patterns should be classified as ENUMERATE."""
        queries = [
            "give me a complete list of cards",
            "show the complete list of products",
            "full list of credit cards",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.ENUMERATE,
                f"Expected ENUMERATE for '{query}', got {result.intent}"
            )

    def test_what_are_all_pattern(self):
        """'what are all the X' should be classified as ENUMERATE."""
        result = self.classifier.classify("what are all the credit cards available")
        self.assertEqual(result.intent, QueryIntent.ENUMERATE)

    def test_enumerate_retrieval_hints(self):
        """ENUMERATE queries should have correct retrieval hints."""
        result = self.classifier.classify("list all credit cards")
        self.assertTrue(result.retrieval_hints.get('diversify_tables'))
        self.assertTrue(result.retrieval_hints.get('increase_snippet_limit'))
        self.assertTrue(result.retrieval_hints.get('include_all_tables'))
        self.assertEqual(result.retrieval_hints.get('snippet_limit_multiplier'), 3.0)


class TestQueryIntentSpecificLookup(SimpleTestCase):
    """Test SPECIFIC_LOOKUP intent classification."""

    def setUp(self):
        self.classifier = QueryClassifier(
            known_entity_names=['Gold', 'Platinum', 'Classic', 'White', 'Titanium']
        )

    def test_specific_card_query(self):
        """Queries for specific card should be SPECIFIC_LOOKUP."""
        queries = [
            "what is the Gold card annual fee",
            "Gold card fees",
            "fees for the Platinum card",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.SPECIFIC_LOOKUP,
                f"Expected SPECIFIC_LOOKUP for '{query}', got {result.intent}"
            )
            self.assertEqual(result.scope, "specific")

    def test_the_x_card_pattern(self):
        """'the X card' pattern should be SPECIFIC_LOOKUP."""
        result = self.classifier.classify("what is the interest rate for the Gold card")
        self.assertEqual(result.intent, QueryIntent.SPECIFIC_LOOKUP)
        self.assertIn("Gold", result.entity_names)

    def test_specific_not_triggered_by_all(self):
        """SPECIFIC_LOOKUP should not trigger when 'all' is present."""
        result = self.classifier.classify("list all Gold cards")  # Not a typical query but tests logic
        # Should lean towards ENUMERATE due to "list all"
        self.assertNotEqual(result.intent, QueryIntent.SPECIFIC_LOOKUP)


class TestQueryIntentCompare(SimpleTestCase):
    """Test COMPARE intent classification."""

    def setUp(self):
        self.classifier = QueryClassifier(
            known_entity_names=['Gold', 'Platinum', 'Classic']
        )

    def test_compare_pattern(self):
        """Explicit 'compare' should be COMPARE."""
        queries = [
            "compare Gold and Platinum cards",
            "compare the fees of Gold vs Classic",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.COMPARE,
                f"Expected COMPARE for '{query}', got {result.intent}"
            )
            self.assertEqual(result.scope, "subset")

    def test_vs_pattern(self):
        """'X vs Y' should be COMPARE."""
        queries = [
            "Gold vs Platinum",
            "Gold versus Platinum fees",
            "Classic vs. Gold card benefits",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.COMPARE,
                f"Expected COMPARE for '{query}', got {result.intent}"
            )

    def test_difference_between_pattern(self):
        """'difference between X and Y' should be COMPARE."""
        result = self.classifier.classify("what is the difference between Gold and Platinum cards")
        self.assertEqual(result.intent, QueryIntent.COMPARE)

    def test_compare_retrieval_hints(self):
        """COMPARE queries should have correct retrieval hints."""
        result = self.classifier.classify("compare Gold vs Platinum")
        self.assertEqual(result.retrieval_hints.get('snippet_limit_multiplier'), 1.5)


class TestQueryIntentAggregate(SimpleTestCase):
    """Test AGGREGATE intent classification."""

    def setUp(self):
        self.classifier = QueryClassifier()

    def test_total_pattern(self):
        """'total X' should be AGGREGATE."""
        queries = [
            "what is the total annual fee",
            "total fees charged",
            "sum of interest rates",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.AGGREGATE,
                f"Expected AGGREGATE for '{query}', got {result.intent}"
            )
            self.assertEqual(result.scope, "all")
            self.assertTrue(result.requires_full_coverage())

    def test_how_many_pattern(self):
        """'how many X' should be AGGREGATE."""
        queries = [
            "how many credit cards are there",
            "how many products do you have",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.AGGREGATE,
                f"Expected AGGREGATE for '{query}', got {result.intent}"
            )

    def test_average_pattern(self):
        """'average X' should be AGGREGATE."""
        result = self.classifier.classify("what is the average annual fee")
        self.assertEqual(result.intent, QueryIntent.AGGREGATE)

    def test_aggregate_retrieval_hints(self):
        """AGGREGATE queries should have correct retrieval hints."""
        result = self.classifier.classify("how many cards are there")
        self.assertTrue(result.retrieval_hints.get('increase_snippet_limit'))
        self.assertEqual(result.retrieval_hints.get('snippet_limit_multiplier'), 2.5)


class TestQueryIntentExploratory(SimpleTestCase):
    """Test EXPLORATORY intent classification (fallback)."""

    def setUp(self):
        self.classifier = QueryClassifier()

    def test_vague_query(self):
        """Vague queries should be EXPLORATORY."""
        queries = [
            "tell me about your cards",
            "what do you have",
            "credit cards information",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.EXPLORATORY,
                f"Expected EXPLORATORY for '{query}', got {result.intent}"
            )

    def test_exploratory_table_diversification(self):
        """EXPLORATORY should enable table diversification."""
        result = self.classifier.classify("tell me about your products")
        self.assertTrue(result.requires_table_diversification())


class TestCreditCardRegressionScenario(SimpleTestCase):
    """
    Regression tests for the specific credit card scenario.

    These tests ensure the fix for "list all credit cards" returns all cards
    instead of just a subset.
    """

    def setUp(self):
        # Card names from the actual PDF
        known_cards = [
            'Gold', 'Platinum', 'Classic', 'White', 'E-Commerce', 'Cash Back',
            'Mileseverywhere', 'Titanium', 'World', 'World Elite', 'Heya',
            'CIB noon', 'CIB talabat', 'Swype'
        ]
        self.classifier = QueryClassifier(known_entity_names=known_cards)

    def test_list_all_credit_cards_original_query(self):
        """
        THE ORIGINAL BUG: 'list me all credit cards and their issuance fees'
        should be ENUMERATE, not SPECIFIC_LOOKUP.

        The old code would set comprehensive_intent=False because 'credit' and
        'cards' matched row labels. This test ensures the fix works.
        """
        query = "list me all credit cards and their issuance fees"
        result = self.classifier.classify(query)

        self.assertEqual(
            result.intent, QueryIntent.ENUMERATE,
            f"Critical regression: '{query}' should be ENUMERATE, got {result.intent}"
        )
        self.assertEqual(result.scope, "all")
        self.assertTrue(result.requires_full_coverage())
        self.assertTrue(result.requires_table_diversification())

        # Should have high confidence
        self.assertGreaterEqual(result.confidence, 0.5)

        # Retrieval hints should enable full coverage
        self.assertTrue(result.retrieval_hints.get('include_all_tables'))
        self.assertEqual(result.retrieval_hints.get('snippet_limit_multiplier'), 3.0)

    def test_any_more_followup(self):
        """'any more?' followup should be EXPLORATORY (seeking more results)."""
        result = self.classifier.classify("any more?")
        # This is ambiguous but should lean towards exploratory
        self.assertIn(result.intent, [QueryIntent.EXPLORATORY, QueryIntent.ENUMERATE])

    def test_specific_card_from_pdf(self):
        """Specific card queries should still work correctly."""
        queries_and_expected = [
            ("Gold card fees", QueryIntent.SPECIFIC_LOOKUP),
            ("what is the Platinum card annual fee", QueryIntent.SPECIFIC_LOOKUP),
            ("Titanium card interest rate", QueryIntent.SPECIFIC_LOOKUP),
        ]
        for query, expected in queries_and_expected:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, expected,
                f"Expected {expected} for '{query}', got {result.intent}"
            )

    def test_compare_cards_from_pdf(self):
        """Compare queries should work correctly."""
        result = self.classifier.classify("compare Gold vs Platinum card fees")
        self.assertEqual(result.intent, QueryIntent.COMPARE)

    def test_list_all_with_attribute(self):
        """'list all X and their Y' should be ENUMERATE."""
        queries = [
            "list all credit cards and their annual fees",
            "show all cards with their interest rates",
            "give me all products and their prices",
        ]
        for query in queries:
            result = self.classifier.classify(query)
            self.assertEqual(
                result.intent, QueryIntent.ENUMERATE,
                f"Expected ENUMERATE for '{query}', got {result.intent}"
            )


class TestEntityExtraction(SimpleTestCase):
    """Test entity name and type extraction."""

    def setUp(self):
        self.classifier = QueryClassifier(
            known_entity_names=['Gold', 'Platinum', 'Classic']
        )

    def test_extract_known_entity(self):
        """Known entity names should be extracted."""
        result = self.classifier.classify("Gold card fees")
        self.assertIn("Gold", result.entity_names)

    def test_extract_entity_type(self):
        """Entity type should be extracted."""
        result = self.classifier.classify("list all credit cards")
        self.assertEqual(result.entity_type, "credit card")

    def test_extract_attributes(self):
        """Attributes should be extracted."""
        result = self.classifier.classify("annual fees for all cards")
        self.assertIn("fees", result.attributes)
        self.assertIn("annual", result.attributes)

    def test_extract_generic_entity_type_from_plural_phrase(self):
        """Entity type extraction should work for non-banking phrases."""
        result = self.classifier.classify("list all maintenance requests")
        self.assertEqual(result.entity_type, "maintenance request")

    def test_extract_generic_attributes_without_domain_dictionary(self):
        """Attribute extraction should work via query structure, not fixed domain vocab."""
        result = self.classifier.classify("list all support tickets and their resolution time")
        self.assertIn("resolution", result.attributes)
        self.assertIn("time", result.attributes)


class TestClassificationConfidence(SimpleTestCase):
    """Test confidence scores."""

    def setUp(self):
        self.classifier = QueryClassifier()

    def test_high_confidence_for_clear_enumerate(self):
        """Clear enumeration queries should have high confidence."""
        result = self.classifier.classify("list all credit cards")
        self.assertGreaterEqual(result.confidence, 0.5)

    def test_lower_confidence_for_ambiguous(self):
        """Ambiguous queries should have lower confidence."""
        result = self.classifier.classify("cards")
        self.assertLess(result.confidence, 0.8)


class TestConvenienceFunction(SimpleTestCase):
    """Test the classify_query convenience function."""

    def test_classify_query_function(self):
        """classify_query should work the same as classifier.classify."""
        result = classify_query("list all credit cards")
        self.assertEqual(result.intent, QueryIntent.ENUMERATE)


class TestSnippetMultiplier(SimpleTestCase):
    """Test snippet limit multiplier calculation."""

    def setUp(self):
        self.classifier = QueryClassifier()

    def test_enumerate_multiplier(self):
        """ENUMERATE should have 3x multiplier."""
        result = self.classifier.classify("list all cards")
        self.assertEqual(result.get_snippet_multiplier(), 3.0)

    def test_aggregate_multiplier(self):
        """AGGREGATE should have 2.5x multiplier."""
        result = self.classifier.classify("total fees")
        self.assertEqual(result.get_snippet_multiplier(), 2.5)

    def test_exploratory_multiplier(self):
        """EXPLORATORY should have 2x multiplier."""
        result = self.classifier.classify("tell me about cards")
        self.assertEqual(result.get_snippet_multiplier(), 2.0)

    def test_compare_multiplier(self):
        """COMPARE should have 1.5x multiplier."""
        result = self.classifier.classify("compare Gold vs Platinum")
        self.assertEqual(result.get_snippet_multiplier(), 1.5)

    def test_specific_multiplier(self):
        """SPECIFIC_LOOKUP should have 1x multiplier."""
        classifier = QueryClassifier(known_entity_names=['Gold'])
        result = classifier.classify("Gold card fees")
        self.assertEqual(result.get_snippet_multiplier(), 1.0)


class TestTenantLexiconAwareClassification(SimpleTestCase):
    def test_lexicon_terms_enrich_entity_and_attribute_extraction(self):
        classifier = QueryClassifier()
        result = classifier.classify(
            "list all service requests and their resolution time",
            context={
                "tenant_entity_terms": ["service request", "incident ticket"],
                "tenant_attribute_terms": ["resolution time", "priority score"],
            },
        )

        self.assertEqual(result.intent, QueryIntent.ENUMERATE)
        self.assertIn("service request", [item.lower() for item in result.entity_names])
        self.assertIn("resolution time", result.attributes)

    def test_context_sensitive_cache_does_not_leak_terms(self):
        classifier = QueryClassifier()
        query = "what is sla target"
        first = classifier.classify(
            query,
            context={"tenant_attribute_terms": ["sla target"]},
        )
        second = classifier.classify(
            query,
            context={"tenant_attribute_terms": []},
        )

        self.assertIn("sla target", first.attributes)
        self.assertNotIn("sla target", second.attributes)


class TestMultilingualClassification(SimpleTestCase):
    def setUp(self):
        self.classifier = QueryClassifier()

    def test_arabic_enumerate_query(self):
        result = self.classifier.classify("اعرض كل الطلبات المفتوحة")
        self.assertEqual(result.intent, QueryIntent.ENUMERATE)
        self.assertEqual(result.scope, "all")

    def test_arabic_compare_query(self):
        result = self.classifier.classify("قارن بين الخطة الأساسية والخطة المتقدمة")
        self.assertEqual(result.intent, QueryIntent.COMPARE)

    def test_arabic_aggregate_query(self):
        result = self.classifier.classify("ما إجمالي الطلبات هذا الشهر")
        self.assertEqual(result.intent, QueryIntent.AGGREGATE)

    def test_arabic_lexicon_phrase_match(self):
        result = self.classifier.classify(
            "ما اجمالي المبيعات؟",
            context={
                "tenant_attribute_terms": ["إِجْمالِيّ المُبيـعات"],
            },
        )
        self.assertIn("اجمالي المبيعات", result.attributes)


class TestTenantIsolationClassificationCache(SimpleTestCase):
    def setUp(self):
        query_classifier_module._classification_cache.clear()
        self.classifier = QueryClassifier()

    def test_cache_partitions_by_tenant_id_even_when_query_matches(self):
        query = "status update"
        self.classifier.classify(query, context={"tenant_id": "tenant-a"})
        self.classifier.classify(query, context={"tenant_id": "tenant-b"})

        self.assertEqual(len(query_classifier_module._classification_cache), 2)


class TestPhaseSixMultiIndustryCoverage(SimpleTestCase):
    def setUp(self):
        self.classifier = QueryClassifier()

    def test_retail_enumerate_with_lexicon_terms(self):
        result = self.classifier.classify(
            "list all purchase orders and their delivery status",
            context={
                "tenant_id": "retail-tenant",
                "tenant_entity_terms": ["purchase order", "shipment"],
                "tenant_attribute_terms": ["delivery status", "vendor name"],
            },
        )
        self.assertEqual(result.intent, QueryIntent.ENUMERATE)
        self.assertIn("purchase order", [item.lower() for item in result.entity_names])
        self.assertIn("delivery status", result.attributes)

    def test_healthcare_compare_intent(self):
        result = self.classifier.classify(
            "compare outpatient vs inpatient wait time",
            context={"tenant_id": "health-tenant"},
        )
        self.assertEqual(result.intent, QueryIntent.COMPARE)

    def test_arabic_aggregate_with_diacritic_variant_attribute(self):
        result = self.classifier.classify(
            "ما اجمالي المبيعات حسب الفرع",
            context={
                "tenant_id": "ops-tenant-ar",
                "tenant_attribute_terms": ["إِجْمالِيّ المُبيـعات"],
            },
        )
        self.assertEqual(result.intent, QueryIntent.AGGREGATE)
        self.assertIn("اجمالي المبيعات", result.attributes)

    def test_mixed_arabic_english_compare(self):
        result = self.classifier.classify("قارن basic vs premium الباقة")
        self.assertEqual(result.intent, QueryIntent.COMPARE)
