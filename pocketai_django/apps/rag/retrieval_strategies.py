"""
Retrieval Strategy Pattern for RAG

This module implements the Strategy Pattern for intent-aware retrieval in the RAG system.
Each strategy encapsulates the retrieval logic for a specific query intent type.

Strategies:
- EnumerationStrategy: For "list all" queries requiring high recall & table diversification
- SpecificLookupStrategy: For targeted queries requiring precision
- ComparisonStrategy: For comparing multiple specific entities  
- AggregateStrategy: For aggregation/calculation queries needing full coverage
- ExploratoryStrategy: For open-ended queries needing broad coverage

The StrategyRouter selects the appropriate strategy based on QueryClassification.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Mapping, MutableMapping, Optional, Sequence

from apps.rag.query_classifier import QueryClassification, QueryIntent

if TYPE_CHECKING:
    from apps.rag.ai_orchestrator import (
        AliasSearchResult,
        ChunkResult,
        KnowledgeSearchResult,
        KnowledgeSearchService,
        QueryTraits,
    )

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RetrievalContext:
    """
    Context object containing all parameters needed for retrieval.
    
    This encapsulates the search parameters so strategies don't need
    to know about the full orchestrator interface.
    """
    business_profile: Any
    query: str
    traits: "QueryTraits"
    classification: QueryClassification
    table_context: Mapping[str, Any]
    
    # Search configuration
    limit: int
    alias_result: Optional["AliasSearchResult"] = None
    session_cache: Optional[MutableMapping[str, object]] = None
    identifier_filter: Optional[Mapping[str, str]] = None
    
    # Scope filters
    allowed_upload_ids: Optional[Sequence[uuid.UUID]] = None
    allowed_explicit_upload_ids: Optional[Sequence[uuid.UUID]] = None
    
    # Feature flags snapshot
    feature_state: Any = None
    
    # Request tracking
    request_id: Optional[uuid.UUID] = None


@dataclasses.dataclass
class RetrievalHints:
    """
    Hints for the retrieval layer based on strategy decisions.
    
    These hints influence how the underlying search methods behave
    without requiring changes to their interfaces.
    """
    snippet_limit_multiplier: float = 1.0
    diversify_tables: bool = False
    prefer_table_headers: bool = False
    include_all_tables: bool = False
    min_tables_coverage: str = "default"  # "default", "all", or a number
    filter_by_entity_names: bool = False
    entity_names_filter: Sequence[str] = dataclasses.field(default_factory=list)
    comprehensive_intent: bool = False
    expand_table_rows: bool = True
    
    def to_dict(self) -> dict[str, Any]:
        """Convert hints to a dictionary for diagnostics."""
        return {
            "snippet_limit_multiplier": self.snippet_limit_multiplier,
            "diversify_tables": self.diversify_tables,
            "prefer_table_headers": self.prefer_table_headers,
            "include_all_tables": self.include_all_tables,
            "min_tables_coverage": self.min_tables_coverage,
            "filter_by_entity_names": self.filter_by_entity_names,
            "entity_names_filter": list(self.entity_names_filter),
            "comprehensive_intent": self.comprehensive_intent,
            "expand_table_rows": self.expand_table_rows,
        }


@dataclasses.dataclass
class StrategyResult:
    """
    Result from a retrieval strategy execution.
    
    Contains the retrieval hints and any strategy-specific diagnostics.
    The actual search execution is still performed by the orchestrator
    using these hints.
    """
    hints: RetrievalHints
    diagnostics: dict[str, Any] = dataclasses.field(default_factory=dict)
    
    # Strategy can optionally modify the effective limit
    effective_limit: Optional[int] = None


class RetrievalStrategy(ABC):
    """
    Abstract base class for retrieval strategies.
    
    Each strategy encapsulates the retrieval logic for a specific intent type,
    determining how the search should be configured (limits, diversification, etc.).
    
    Strategies don't perform the actual search - they return RetrievalHints
    that the orchestrator uses to configure the search.
    """
    
    def __init__(self, search_service: "KnowledgeSearchService"):
        """
        Initialize the strategy with a reference to the search service.
        
        Args:
            search_service: The KnowledgeSearchService instance for accessing
                           configuration and helper methods.
        """
        self.search_service = search_service
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Return the strategy name for logging/diagnostics."""
        pass
    
    @abstractmethod
    def get_snippet_multiplier(self) -> float:
        """
        Return the snippet limit multiplier for this strategy.
        
        Higher values mean more snippets are retrieved:
        - 1.0: Standard retrieval
        - 1.5: Slightly expanded (comparison)
        - 2.0-2.5: Broad coverage (exploratory, aggregate)
        - 3.0: Maximum coverage (enumeration)
        """
        pass
    
    @abstractmethod
    def compute_hints(self, context: RetrievalContext) -> RetrievalHints:
        """
        Compute retrieval hints based on the query context.
        
        Args:
            context: The retrieval context with query, classification, etc.
            
        Returns:
            RetrievalHints with configuration for the search.
        """
        pass
    
    def execute(self, context: RetrievalContext) -> StrategyResult:
        """
        Execute the strategy and return results with hints.
        
        This is the main entry point called by the StrategyRouter.
        Default implementation calls compute_hints and wraps in StrategyResult.
        
        Args:
            context: The retrieval context.
            
        Returns:
            StrategyResult with hints and diagnostics.
        """
        hints = self.compute_hints(context)
        
        # Compute effective limit based on multiplier
        effective_limit = int(context.limit * hints.snippet_limit_multiplier)
        
        diagnostics = {
            "strategy": self.name,
            "intent": context.classification.intent.value,
            "confidence": context.classification.confidence,
            "base_limit": context.limit,
            "effective_limit": effective_limit,
            "multiplier": hints.snippet_limit_multiplier,
        }
        
        logger.debug(
            f"Strategy '{self.name}' computed hints: "
            f"multiplier={hints.snippet_limit_multiplier}, "
            f"diversify={hints.diversify_tables}, "
            f"comprehensive={hints.comprehensive_intent}"
        )
        
        return StrategyResult(
            hints=hints,
            diagnostics=diagnostics,
            effective_limit=effective_limit,
        )


class EnumerationStrategy(RetrievalStrategy):
    """
    Strategy for ENUMERATE intent queries.
    
    Used for queries like:
    - "List all credit cards"
    - "Show me every product"
    - "What are all the plans available?"
    
    Characteristics:
    - High recall (3x snippet multiplier)
    - Table diversification (spread results across tables)
    - Prefer table headers for entity discovery
    - Include all tables that might contain relevant entities
    - No row expansion (keep preview chunks for full structure)
    """
    
    @property
    def name(self) -> str:
        return "enumeration"
    
    def get_snippet_multiplier(self) -> float:
        return 3.0
    
    def compute_hints(self, context: RetrievalContext) -> RetrievalHints:
        return RetrievalHints(
            snippet_limit_multiplier=self.get_snippet_multiplier(),
            diversify_tables=True,
            prefer_table_headers=True,
            include_all_tables=True,
            min_tables_coverage="all",
            comprehensive_intent=True,
            expand_table_rows=False,  # Keep preview chunks for full table structure
        )


class SpecificLookupStrategy(RetrievalStrategy):
    """
    Strategy for SPECIFIC_LOOKUP intent queries.
    
    Used for queries like:
    - "Gold card annual fee"
    - "What is the interest rate for Platinum?"
    - "Tell me about the Classic card benefits"
    
    Characteristics:
    - Precision-focused (standard 1x multiplier)
    - Filter by entity names when detected
    - Expand table rows to get specific values
    - No diversification needed
    """
    
    @property
    def name(self) -> str:
        return "specific_lookup"
    
    def get_snippet_multiplier(self) -> float:
        return 1.0
    
    def compute_hints(self, context: RetrievalContext) -> RetrievalHints:
        entity_names = context.classification.entity_names or []
        
        return RetrievalHints(
            snippet_limit_multiplier=self.get_snippet_multiplier(),
            diversify_tables=False,
            prefer_table_headers=False,
            include_all_tables=False,
            filter_by_entity_names=bool(entity_names),
            entity_names_filter=entity_names,
            comprehensive_intent=False,
            expand_table_rows=True,  # Expand to get specific cell values
        )


class ComparisonStrategy(RetrievalStrategy):
    """
    Strategy for COMPARE intent queries.
    
    Used for queries like:
    - "Compare Gold vs Platinum card"
    - "Difference between Classic and Premium"
    - "Which is better: Plan A or Plan B?"
    
    Characteristics:
    - Multiple entity retrieval (1.5x multiplier)
    - Filter by mentioned entity names
    - Some table diversification for complete comparison
    - Expand rows to get comparable values
    """
    
    @property
    def name(self) -> str:
        return "comparison"
    
    def get_snippet_multiplier(self) -> float:
        return 1.5
    
    def compute_hints(self, context: RetrievalContext) -> RetrievalHints:
        entity_names = context.classification.entity_names or []
        
        return RetrievalHints(
            snippet_limit_multiplier=self.get_snippet_multiplier(),
            diversify_tables=True,  # Ensure we get data for both entities
            prefer_table_headers=False,
            include_all_tables=False,
            filter_by_entity_names=bool(entity_names),
            entity_names_filter=entity_names,
            comprehensive_intent=False,
            expand_table_rows=True,
        )


class AggregateStrategy(RetrievalStrategy):
    """
    Strategy for AGGREGATE intent queries.
    
    Used for queries like:
    - "Total fees for all cards"
    - "How many products do you have?"
    - "Average interest rate across cards"
    
    Characteristics:
    - Full coverage needed (2.5x multiplier)
    - Must include all tables for accurate aggregation
    - Comprehensive intent for complete data
    - Keep preview chunks for full table structure
    """
    
    @property
    def name(self) -> str:
        return "aggregate"
    
    def get_snippet_multiplier(self) -> float:
        return 2.5
    
    def compute_hints(self, context: RetrievalContext) -> RetrievalHints:
        return RetrievalHints(
            snippet_limit_multiplier=self.get_snippet_multiplier(),
            diversify_tables=True,
            prefer_table_headers=True,
            include_all_tables=True,
            min_tables_coverage="all",
            comprehensive_intent=True,
            expand_table_rows=False,  # Need full table structure for aggregation
        )


class ExploratoryStrategy(RetrievalStrategy):
    """
    Strategy for EXPLORATORY intent queries.
    
    Used for open-ended queries like:
    - "What credit cards do you have?"
    - "Tell me about your products"
    - "What can you help me with?"
    
    Characteristics:
    - Broad coverage (2x multiplier)
    - Table diversification for variety
    - Balance between depth and breadth
    """
    
    @property
    def name(self) -> str:
        return "exploratory"
    
    def get_snippet_multiplier(self) -> float:
        return 2.0
    
    def compute_hints(self, context: RetrievalContext) -> RetrievalHints:
        return RetrievalHints(
            snippet_limit_multiplier=self.get_snippet_multiplier(),
            diversify_tables=True,
            prefer_table_headers=False,
            include_all_tables=False,
            comprehensive_intent=False,
            expand_table_rows=True,
        )


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
