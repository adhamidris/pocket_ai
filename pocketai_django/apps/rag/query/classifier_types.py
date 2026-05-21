from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class QueryIntent(Enum):
    """
    Enumeration of query intent types.

    Each intent type maps to a different retrieval strategy:
    - ENUMERATE: Retrieve ALL items of a type (high recall, table diversification)
    - SPECIFIC_LOOKUP: Retrieve specific item(s) by name (precision-focused)
    - COMPARE: Retrieve multiple specific items for comparison
    - AGGREGATE: Retrieve all items for aggregation/calculation
    - EXPLORATORY: Open-ended query, needs broad coverage
    """
    ENUMERATE = "enumerate"
    SPECIFIC_LOOKUP = "specific_lookup"
    COMPARE = "compare"
    AGGREGATE = "aggregate"
    EXPLORATORY = "exploratory"


@dataclass
class QueryClassification:
    """
    Result of query intent classification.

    Attributes:
        intent: The classified intent type
        entity_type: The type of entity being queried (e.g., "credit card", "product")
        entity_names: Specific entity names mentioned (e.g., ["Gold", "Platinum"])
        attributes: Attributes being queried (e.g., ["fees", "interest rate"])
        scope: Query scope - "all", "specific", "subset"
        confidence: Classification confidence (0.0 to 1.0)
        reasoning: Brief explanation of classification decision
        retrieval_hints: Hints for the retrieval layer
    """
    intent: QueryIntent
    entity_type: Optional[str] = None
    entity_names: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)
    scope: str = "unknown"
    confidence: float = 0.0
    reasoning: str = ""
    retrieval_hints: dict = field(default_factory=dict)
    source: str = "heuristic"
    fallback_used: bool = False
    requires_clarification: bool = False
    clarification_question: str = ""

    def requires_full_coverage(self) -> bool:
        """Returns True if this query requires retrieving all matching items."""
        return self.intent in (QueryIntent.ENUMERATE, QueryIntent.AGGREGATE)

    def requires_table_diversification(self) -> bool:
        """Returns True if results should be diversified across tables."""
        return self.intent in (QueryIntent.ENUMERATE, QueryIntent.EXPLORATORY)

    def prefers_section_context(self) -> bool:
        """Returns True when section-aware text retrieval/ranking should be preferred."""
        return bool(self.retrieval_hints.get("prefer_section_context"))

    def modality_bias(self) -> str:
        """Returns the retrieval modality bias hint."""
        value = str(self.retrieval_hints.get("modality_bias") or "mixed").strip().lower()
        if value not in {"table", "text", "mixed"}:
            return "mixed"
        return value

    def get_snippet_multiplier(self) -> float:
        """Returns multiplier for snippet limit based on intent."""
        multipliers = {
            QueryIntent.ENUMERATE: 3.0,      # Need many snippets
            QueryIntent.AGGREGATE: 2.5,       # Need comprehensive coverage
            QueryIntent.EXPLORATORY: 2.0,     # Broad coverage
            QueryIntent.COMPARE: 1.5,         # Multiple specific items
            QueryIntent.SPECIFIC_LOOKUP: 1.0  # Focused results
        }
        return multipliers.get(self.intent, 1.0)
