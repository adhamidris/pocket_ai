from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.rag.query.classifier import QueryClassification, QueryIntent
from apps.rag.retrieval.intent_strategies import (
    AggregateStrategy,
    ComparisonStrategy,
    EnumerationStrategy,
    ExploratoryStrategy,
    SpecificLookupStrategy,
)
from apps.rag.retrieval.strategy_base import RetrievalStrategy
from apps.rag.retrieval.strategy_types import RetrievalContext, StrategyResult

if TYPE_CHECKING:
    from apps.rag.knowledge_search import KnowledgeSearchService


logger = logging.getLogger(__name__)


class StrategyRouter:
    """
    Routes queries to the appropriate retrieval strategy based on intent.
    
    The router maintains instances of all strategies and selects the right one
    based on the QueryClassification.intent from the QueryClassifier.
    """
    
    def __init__(self, search_service: "KnowledgeSearchService"):
        """
        Initialize the router with all available strategies.
        
        Args:
            search_service: The KnowledgeSearchService for strategy initialization.
        """
        self.search_service = search_service
        
        # Initialize all strategies
        self._strategies: dict[QueryIntent, RetrievalStrategy] = {
            QueryIntent.ENUMERATE: EnumerationStrategy(search_service),
            QueryIntent.SPECIFIC_LOOKUP: SpecificLookupStrategy(search_service),
            QueryIntent.COMPARE: ComparisonStrategy(search_service),
            QueryIntent.AGGREGATE: AggregateStrategy(search_service),
            QueryIntent.EXPLORATORY: ExploratoryStrategy(search_service),
        }
        
        # Default strategy for unknown intents
        self._default_strategy = ExploratoryStrategy(search_service)
        
        logger.info(
            f"StrategyRouter initialized with {len(self._strategies)} strategies: "
            f"{[s.name for s in self._strategies.values()]}"
        )
    
    def get_strategy(self, intent: QueryIntent) -> RetrievalStrategy:
        """
        Get the strategy for a given intent.
        
        Args:
            intent: The QueryIntent from classification.
            
        Returns:
            The appropriate RetrievalStrategy instance.
        """
        strategy = self._strategies.get(intent, self._default_strategy)
        logger.debug(f"Selected strategy '{strategy.name}' for intent '{intent.value}'")
        return strategy
    
    def route(self, classification: QueryClassification) -> RetrievalStrategy:
        """
        Route to the appropriate strategy based on classification.
        
        This is the main entry point for strategy selection.
        
        Args:
            classification: The QueryClassification from the classifier.
            
        Returns:
            The appropriate RetrievalStrategy instance.
        """
        return self.get_strategy(classification.intent)
    
    def execute(self, context: RetrievalContext) -> StrategyResult:
        """
        Execute the appropriate strategy for the given context.
        
        Convenience method that routes and executes in one call.
        
        Args:
            context: The RetrievalContext with query and classification.
            
        Returns:
            StrategyResult with hints and diagnostics.
        """
        strategy = self.route(context.classification)
        return strategy.execute(context)
