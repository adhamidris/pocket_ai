from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from apps.rag.retrieval.strategy_types import RetrievalContext, RetrievalHints, StrategyResult

if TYPE_CHECKING:
    from apps.rag.knowledge_search import KnowledgeSearchService


logger = logging.getLogger(__name__)


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

