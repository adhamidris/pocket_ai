from __future__ import annotations

from apps.rag.retrieval.strategy_base import RetrievalStrategy
from apps.rag.retrieval.strategy_types import RetrievalContext, RetrievalHints


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
            prefer_section_context=context.classification.prefers_section_context(),
            modality_bias="mixed",
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
            prefer_section_context=context.classification.prefers_section_context(),
            modality_bias="mixed",
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
            prefer_section_context=context.classification.prefers_section_context(),
            modality_bias="mixed",
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
            prefer_section_context=context.classification.prefers_section_context(),
            modality_bias="mixed",
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
            prefer_section_context=context.classification.prefers_section_context(),
            modality_bias="mixed",
        )

