"""
Unit tests for Retrieval Strategies

Tests the Strategy Pattern implementation for intent-aware RAG retrieval.
"""

import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.rag.query.classifier import QueryClassification, QueryIntent
from apps.rag.retrieval.strategies import (
    AggregateStrategy,
    ComparisonStrategy,
    EnumerationStrategy,
    ExploratoryStrategy,
    RetrievalContext,
    RetrievalHints,
    SpecificLookupStrategy,
    StrategyResult,
    StrategyRouter,
)


class MockSearchService:
    """Mock search service for testing strategies."""
    pass


class TestRetrievalHints(TestCase):
    """Tests for RetrievalHints dataclass."""
    
    def test_default_hints(self):
        """Test default hint values."""
        hints = RetrievalHints()
        
        self.assertEqual(hints.snippet_limit_multiplier, 1.0)
        self.assertFalse(hints.diversify_tables)
        self.assertFalse(hints.prefer_table_headers)
        self.assertFalse(hints.include_all_tables)
        self.assertEqual(hints.min_tables_coverage, "default")
        self.assertFalse(hints.filter_by_entity_names)
        self.assertEqual(list(hints.entity_names_filter), [])
        self.assertFalse(hints.comprehensive_intent)
        self.assertTrue(hints.expand_table_rows)
        self.assertFalse(hints.prefer_section_context)
        self.assertEqual(hints.modality_bias, "mixed")
    
    def test_hints_to_dict(self):
        """Test converting hints to dictionary."""
        hints = RetrievalHints(
            snippet_limit_multiplier=3.0,
            diversify_tables=True,
            comprehensive_intent=True,
        )
        
        result = hints.to_dict()
        
        self.assertEqual(result["snippet_limit_multiplier"], 3.0)
        self.assertTrue(result["diversify_tables"])
        self.assertTrue(result["comprehensive_intent"])
        self.assertEqual(result["modality_bias"], "mixed")


class TestEnumerationStrategy(TestCase):
    """Tests for EnumerationStrategy."""
    
    def setUp(self):
        self.search_service = MockSearchService()
        self.strategy = EnumerationStrategy(self.search_service)
    
    def test_name(self):
        """Test strategy name."""
        self.assertEqual(self.strategy.name, "enumeration")
    
    def test_snippet_multiplier(self):
        """Test high snippet multiplier for enumeration."""
        self.assertEqual(self.strategy.get_snippet_multiplier(), 3.0)
    
    def test_compute_hints_diversifies_tables(self):
        """Test that enumeration enables table diversification."""
        context = self._create_context(QueryIntent.ENUMERATE)
        hints = self.strategy.compute_hints(context)
        
        self.assertTrue(hints.diversify_tables)
        self.assertTrue(hints.include_all_tables)
        self.assertTrue(hints.comprehensive_intent)
        self.assertEqual(hints.min_tables_coverage, "all")
        self.assertEqual(hints.modality_bias, "mixed")
    
    def test_compute_hints_prefers_table_headers(self):
        """Test that enumeration prefers table headers for entity discovery."""
        context = self._create_context(QueryIntent.ENUMERATE)
        hints = self.strategy.compute_hints(context)
        
        self.assertTrue(hints.prefer_table_headers)
    
    def test_compute_hints_no_row_expansion(self):
        """Test that enumeration doesn't expand rows (keeps preview chunks)."""
        context = self._create_context(QueryIntent.ENUMERATE)
        hints = self.strategy.compute_hints(context)
        
        self.assertFalse(hints.expand_table_rows)
    
    def test_execute_returns_strategy_result(self):
        """Test execute returns proper StrategyResult."""
        context = self._create_context(QueryIntent.ENUMERATE, limit=5)
        result = self.strategy.execute(context)
        
        self.assertIsInstance(result, StrategyResult)
        self.assertEqual(result.effective_limit, 15)  # 5 * 3.0
        self.assertEqual(result.diagnostics["strategy"], "enumeration")
    
    def _create_context(self, intent: QueryIntent, limit: int = 10) -> RetrievalContext:
        """Create a test context with the given intent."""
        classification = QueryClassification(intent=intent, confidence=0.9)
        traits = MagicMock()
        traits.original = "list all credit cards"
        traits.normalized = "list all credit cards"
        
        return RetrievalContext(
            business_profile=MagicMock(),
            query="list all credit cards",
            traits=traits,
            classification=classification,
            table_context={},
            limit=limit,
        )


class TestSpecificLookupStrategy(TestCase):
    """Tests for SpecificLookupStrategy."""
    
    def setUp(self):
        self.search_service = MockSearchService()
        self.strategy = SpecificLookupStrategy(self.search_service)
    
    def test_name(self):
        """Test strategy name."""
        self.assertEqual(self.strategy.name, "specific_lookup")
    
    def test_snippet_multiplier(self):
        """Test standard snippet multiplier for specific lookups."""
        self.assertEqual(self.strategy.get_snippet_multiplier(), 1.0)
    
    def test_compute_hints_no_diversification(self):
        """Test that specific lookup doesn't diversify tables."""
        context = self._create_context(QueryIntent.SPECIFIC_LOOKUP)
        hints = self.strategy.compute_hints(context)
        
        self.assertFalse(hints.diversify_tables)
        self.assertFalse(hints.include_all_tables)
        self.assertFalse(hints.comprehensive_intent)
        self.assertEqual(hints.modality_bias, "mixed")
    
    def test_compute_hints_with_entity_names(self):
        """Test that entity names are used for filtering."""
        context = self._create_context(
            QueryIntent.SPECIFIC_LOOKUP,
            entity_names=["Gold", "Platinum"]
        )
        hints = self.strategy.compute_hints(context)
        
        self.assertTrue(hints.filter_by_entity_names)
        self.assertEqual(list(hints.entity_names_filter), ["Gold", "Platinum"])
    
    def test_compute_hints_expands_rows(self):
        """Test that specific lookup expands table rows."""
        context = self._create_context(QueryIntent.SPECIFIC_LOOKUP)
        hints = self.strategy.compute_hints(context)
        
        self.assertTrue(hints.expand_table_rows)

    def test_compute_hints_can_prefer_section_context(self):
        context = self._create_context(QueryIntent.SPECIFIC_LOOKUP)
        context.classification.retrieval_hints = {"prefer_section_context": True}
        hints = self.strategy.compute_hints(context)
        self.assertTrue(hints.prefer_section_context)
    
    def _create_context(
        self, 
        intent: QueryIntent, 
        entity_names: list[str] | None = None
    ) -> RetrievalContext:
        """Create a test context."""
        classification = QueryClassification(
            intent=intent, 
            confidence=0.9,
            entity_names=entity_names or [],
        )
        traits = MagicMock()
        traits.original = "Gold card annual fee"
        traits.normalized = "gold card annual fee"
        
        return RetrievalContext(
            business_profile=MagicMock(),
            query="Gold card annual fee",
            traits=traits,
            classification=classification,
            table_context={},
            limit=10,
        )


class TestComparisonStrategy(TestCase):
    """Tests for ComparisonStrategy."""
    
    def setUp(self):
        self.search_service = MockSearchService()
        self.strategy = ComparisonStrategy(self.search_service)
    
    def test_name(self):
        """Test strategy name."""
        self.assertEqual(self.strategy.name, "comparison")
    
    def test_snippet_multiplier(self):
        """Test expanded snippet multiplier for comparisons."""
        self.assertEqual(self.strategy.get_snippet_multiplier(), 1.5)
    
    def test_compute_hints_diversifies_for_comparison(self):
        """Test that comparison enables diversification for both entities."""
        context = self._create_context(
            QueryIntent.COMPARE,
            entity_names=["Gold", "Platinum"]
        )
        hints = self.strategy.compute_hints(context)
        
        self.assertTrue(hints.diversify_tables)
        self.assertTrue(hints.filter_by_entity_names)
        self.assertFalse(hints.comprehensive_intent)
        self.assertEqual(hints.modality_bias, "mixed")
    
    def _create_context(
        self, 
        intent: QueryIntent, 
        entity_names: list[str] | None = None
    ) -> RetrievalContext:
        """Create a test context."""
        classification = QueryClassification(
            intent=intent, 
            confidence=0.9,
            entity_names=entity_names or [],
        )
        traits = MagicMock()
        traits.original = "compare Gold vs Platinum"
        
        return RetrievalContext(
            business_profile=MagicMock(),
            query="compare Gold vs Platinum",
            traits=traits,
            classification=classification,
            table_context={},
            limit=10,
        )


class TestAggregateStrategy(TestCase):
    """Tests for AggregateStrategy."""
    
    def setUp(self):
        self.search_service = MockSearchService()
        self.strategy = AggregateStrategy(self.search_service)
    
    def test_name(self):
        """Test strategy name."""
        self.assertEqual(self.strategy.name, "aggregate")
    
    def test_snippet_multiplier(self):
        """Test high snippet multiplier for aggregation."""
        self.assertEqual(self.strategy.get_snippet_multiplier(), 2.5)
    
    def test_compute_hints_full_coverage(self):
        """Test that aggregation requires full coverage."""
        context = self._create_context(QueryIntent.AGGREGATE)
        hints = self.strategy.compute_hints(context)
        
        self.assertTrue(hints.diversify_tables)
        self.assertTrue(hints.include_all_tables)
        self.assertTrue(hints.comprehensive_intent)
        self.assertEqual(hints.min_tables_coverage, "all")
        self.assertFalse(hints.expand_table_rows)  # Need full table structure
    
    def _create_context(self, intent: QueryIntent) -> RetrievalContext:
        """Create a test context."""
        classification = QueryClassification(intent=intent, confidence=0.9)
        traits = MagicMock()
        traits.original = "total fees for all cards"
        
        return RetrievalContext(
            business_profile=MagicMock(),
            query="total fees for all cards",
            traits=traits,
            classification=classification,
            table_context={},
            limit=10,
        )


class TestExploratoryStrategy(TestCase):
    """Tests for ExploratoryStrategy."""
    
    def setUp(self):
        self.search_service = MockSearchService()
        self.strategy = ExploratoryStrategy(self.search_service)
    
    def test_name(self):
        """Test strategy name."""
        self.assertEqual(self.strategy.name, "exploratory")
    
    def test_snippet_multiplier(self):
        """Test broad snippet multiplier for exploration."""
        self.assertEqual(self.strategy.get_snippet_multiplier(), 2.0)
    
    def test_compute_hints_broad_coverage(self):
        """Test that exploration enables diversification but not full coverage."""
        context = self._create_context(QueryIntent.EXPLORATORY)
        hints = self.strategy.compute_hints(context)
        
        self.assertTrue(hints.diversify_tables)
        self.assertFalse(hints.include_all_tables)  # Not ALL tables
        self.assertFalse(hints.comprehensive_intent)
    
    def _create_context(self, intent: QueryIntent) -> RetrievalContext:
        """Create a test context."""
        classification = QueryClassification(intent=intent, confidence=0.5)
        traits = MagicMock()
        traits.original = "what credit cards do you have"
        
        return RetrievalContext(
            business_profile=MagicMock(),
            query="what credit cards do you have",
            traits=traits,
            classification=classification,
            table_context={},
            limit=10,
        )


class TestStrategyRouter(TestCase):
    """Tests for StrategyRouter."""
    
    def setUp(self):
        self.search_service = MockSearchService()
        self.router = StrategyRouter(self.search_service)
    
    def test_router_initializes_all_strategies(self):
        """Test that router initializes strategies for all intents."""
        for intent in QueryIntent:
            strategy = self.router.get_strategy(intent)
            self.assertIsNotNone(strategy)
    
    def test_get_strategy_enumerate(self):
        """Test router returns EnumerationStrategy for ENUMERATE intent."""
        strategy = self.router.get_strategy(QueryIntent.ENUMERATE)
        self.assertIsInstance(strategy, EnumerationStrategy)
    
    def test_get_strategy_specific_lookup(self):
        """Test router returns SpecificLookupStrategy for SPECIFIC_LOOKUP intent."""
        strategy = self.router.get_strategy(QueryIntent.SPECIFIC_LOOKUP)
        self.assertIsInstance(strategy, SpecificLookupStrategy)
    
    def test_get_strategy_compare(self):
        """Test router returns ComparisonStrategy for COMPARE intent."""
        strategy = self.router.get_strategy(QueryIntent.COMPARE)
        self.assertIsInstance(strategy, ComparisonStrategy)
    
    def test_get_strategy_aggregate(self):
        """Test router returns AggregateStrategy for AGGREGATE intent."""
        strategy = self.router.get_strategy(QueryIntent.AGGREGATE)
        self.assertIsInstance(strategy, AggregateStrategy)
    
    def test_get_strategy_exploratory(self):
        """Test router returns ExploratoryStrategy for EXPLORATORY intent."""
        strategy = self.router.get_strategy(QueryIntent.EXPLORATORY)
        self.assertIsInstance(strategy, ExploratoryStrategy)
    
    def test_route_by_classification(self):
        """Test routing based on QueryClassification."""
        classification = QueryClassification(
            intent=QueryIntent.ENUMERATE,
            confidence=0.9,
        )
        
        strategy = self.router.route(classification)
        
        self.assertIsInstance(strategy, EnumerationStrategy)
    
    def test_execute_returns_strategy_result(self):
        """Test execute method returns proper result."""
        classification = QueryClassification(
            intent=QueryIntent.ENUMERATE,
            confidence=0.9,
        )
        traits = MagicMock()
        traits.original = "list all cards"
        
        context = RetrievalContext(
            business_profile=MagicMock(),
            query="list all cards",
            traits=traits,
            classification=classification,
            table_context={},
            limit=5,
        )
        
        result = self.router.execute(context)
        
        self.assertIsInstance(result, StrategyResult)
        self.assertEqual(result.diagnostics["strategy"], "enumeration")
        self.assertEqual(result.effective_limit, 15)  # 5 * 3.0


class TestStrategyIntegration(TestCase):
    """Integration tests for strategy pattern with query classifier."""
    
    def setUp(self):
        self.search_service = MockSearchService()
        self.router = StrategyRouter(self.search_service)
    
    def test_list_all_query_uses_enumeration(self):
        """Test 'list all' query routes to enumeration strategy."""
        from apps.rag.query.classifier import classify_query
        
        classification = classify_query("list all credit cards")
        strategy = self.router.route(classification)
        
        # Should use enumeration for "list all" queries
        self.assertEqual(strategy.name, "enumeration")
        self.assertEqual(strategy.get_snippet_multiplier(), 3.0)
    
    def test_specific_query_uses_specific_lookup(self):
        """Test specific entity query routes to specific lookup strategy."""
        from apps.rag.query.classifier import classify_query
        
        classification = classify_query("Gold card annual fee")
        strategy = self.router.route(classification)
        
        # Should use specific lookup for entity queries
        # Note: Depending on classifier confidence, might be exploratory
        self.assertIn(strategy.name, ["specific_lookup", "exploratory"])
    
    def test_compare_query_uses_comparison(self):
        """Test comparison query routes to comparison strategy."""
        from apps.rag.query.classifier import classify_query
        
        classification = classify_query("compare Gold vs Platinum")
        strategy = self.router.route(classification)
        
        self.assertEqual(strategy.name, "comparison")
    
    def test_aggregate_query_uses_aggregate(self):
        """Test aggregate query routes to aggregate strategy."""
        from apps.rag.query.classifier import classify_query
        
        classification = classify_query("how many credit cards do you have")
        strategy = self.router.route(classification)
        
        self.assertEqual(strategy.name, "aggregate")
    
    def test_end_to_end_enumeration_hints(self):
        """Test end-to-end: query → classification → strategy → hints."""
        from apps.rag.query.classifier import classify_query
        
        # Classify the query
        classification = classify_query("show me every product")
        
        # Create context
        traits = MagicMock()
        traits.original = "show me every product"
        traits.normalized = "show me every product"
        
        context = RetrievalContext(
            business_profile=MagicMock(),
            query="show me every product",
            traits=traits,
            classification=classification,
            table_context={},
            limit=5,
        )
        
        # Execute strategy
        result = self.router.execute(context)
        
        # Verify enumeration behavior
        self.assertTrue(result.hints.diversify_tables)
        self.assertTrue(result.hints.comprehensive_intent)
        self.assertEqual(result.effective_limit, 15)  # 5 * 3.0
