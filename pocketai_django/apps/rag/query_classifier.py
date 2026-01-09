"""
Query Intent Classification Module

This module provides intelligent query intent classification for RAG retrieval,
replacing simple keyword matching with proper linguistic analysis.

The classifier distinguishes between:
- ENUMERATE: "list all credit cards", "show me every product"
- SPECIFIC_LOOKUP: "Gold card fees", "what is the interest rate for Platinum"
- COMPARE: "compare Gold vs Platinum", "difference between Classic and Premium"
- AGGREGATE: "total fees", "average interest rate across all cards"
- EXPLORATORY: "what credit cards do you have", "tell me about your cards"

This enables the retrieval layer to select appropriate strategies per intent.
"""

from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Optional
import hashlib
import re
import logging

logger = logging.getLogger(__name__)

# Module-level cache for query classifications to ensure deterministic results
# across repeated calls with the same query
_classification_cache: dict[str, "QueryClassification"] = {}
_CLASSIFICATION_CACHE_MAX_SIZE = 1000


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

    def requires_full_coverage(self) -> bool:
        """Returns True if this query requires retrieving all matching items."""
        return self.intent in (QueryIntent.ENUMERATE, QueryIntent.AGGREGATE)

    def requires_table_diversification(self) -> bool:
        """Returns True if results should be diversified across tables."""
        return self.intent in (QueryIntent.ENUMERATE, QueryIntent.EXPLORATORY)

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


class QueryClassifier:
    """
    Intelligent query intent classifier for RAG systems.

    Uses linguistic patterns and contextual signals to classify user queries
    into intent types, enabling intent-aware retrieval strategies.

    This replaces the broken keyword-matching logic that disabled comprehensive
    intent when ANY row label matched query tokens.
    """

    # Enumeration indicators - strong signals for ENUMERATE intent
    ENUMERATE_PATTERNS = [
        r'\b(list|show|display|give)\s+(me\s+)?(all|every|each)\b',
        r'\ball\s+(the\s+)?\w+s?\b',
        r'\bevery\s+(single\s+)?\w+\b',
        r'\bwhat\s+(are\s+)?(all|the)\s+\w+s\b',
        r'\bentire\s+(list|set|collection)\b',
        r'\bcomplete\s+(list|overview)\b',
        r'\bfull\s+list\b',
    ]

    # Enumerate keywords - words that signal enumeration intent
    ENUMERATE_KEYWORDS = {
        'all', 'every', 'each', 'entire', 'complete', 'full',
        'whole', 'everything', 'comprehensive'
    }

    # List action verbs - verbs that often precede enumeration
    LIST_VERBS = {'list', 'show', 'display', 'enumerate', 'give', 'tell'}

    # Comparison indicators
    COMPARE_PATTERNS = [
        r'\bcompare\b',
        r'\bvs\.?\b',
        r'\bversus\b',
        r'\bdifference\s+between\b',
        r'\bcompared\s+to\b',
        r'\bbetter\s+than\b',
        r'\bworse\s+than\b',
    ]

    # Aggregation indicators
    AGGREGATE_PATTERNS = [
        r'\btotal\b',
        r'\bsum\b',
        r'\baverage\b',
        r'\bcount\b',
        r'\bhow\s+many\b',
        r'\bminimum\b',
        r'\bmaximum\b',
        r'\bhighest\b',
        r'\blowest\b',
    ]

    # Specific lookup indicators - signals for SPECIFIC_LOOKUP
    SPECIFIC_PATTERNS = [
        r'\bthe\s+\w+\s+(card|product|plan|account)\b',
        r'\bfor\s+(the\s+)?\w+\s+(card|product)\b',
        r'\bwhat\s+is\s+the\s+\w+\b',
    ]

    # Common entity types in financial/product domains
    ENTITY_TYPES = {
        'credit card': ['card', 'cards', 'credit card', 'credit cards'],
        'product': ['product', 'products', 'item', 'items'],
        'fee': ['fee', 'fees', 'charge', 'charges', 'cost', 'costs'],
        'account': ['account', 'accounts'],
        'plan': ['plan', 'plans', 'tier', 'tiers'],
    }

    # Common attributes being queried
    ATTRIBUTES = {
        'fee', 'fees', 'charge', 'charges', 'cost', 'costs', 'price', 'prices',
        'rate', 'rates', 'interest', 'percentage', 'limit', 'limits',
        'benefit', 'benefits', 'feature', 'features', 'requirement', 'requirements',
        'annual', 'monthly', 'issuance', 'renewal', 'late', 'penalty'
    }

    def __init__(self, known_entity_names: Optional[list[str]] = None):
        """
        Initialize the classifier.

        Args:
            known_entity_names: Optional list of known entity names from the document
                               (e.g., ["Gold", "Platinum", "Classic"] for credit cards)
        """
        self.known_entity_names = set(known_entity_names or [])
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile regex patterns for efficiency."""
        self._enumerate_re = [re.compile(p, re.IGNORECASE) for p in self.ENUMERATE_PATTERNS]
        self._compare_re = [re.compile(p, re.IGNORECASE) for p in self.COMPARE_PATTERNS]
        self._aggregate_re = [re.compile(p, re.IGNORECASE) for p in self.AGGREGATE_PATTERNS]
        self._specific_re = [re.compile(p, re.IGNORECASE) for p in self.SPECIFIC_PATTERNS]

    def classify(self, query: str, context: Optional[dict] = None) -> QueryClassification:
        """
        Classify a query's intent.

        Args:
            query: The user's query string
            context: Optional context dict with:
                - 'document_entities': Known entity names from the document
                - 'previous_queries': Previous queries in the conversation
                - 'table_schemas': Available table column schemas

        Returns:
            QueryClassification with intent and metadata
        """
        # Check cache for deterministic results on repeated queries
        cache_key = query.strip().lower()
        if cache_key in _classification_cache:
            cached = _classification_cache[cache_key]
            logger.debug(f"Query classification cache hit for '{query[:50]}...'")
            return cached

        context = context or {}
        query_lower = query.lower()
        tokens = set(re.findall(r'\b\w+\b', query_lower))

        # Update known entities from context
        if context.get('document_entities'):
            self.known_entity_names.update(context['document_entities'])

        # Extract entity type and attributes
        entity_type = self._extract_entity_type(query_lower)
        attributes = self._extract_attributes(tokens)
        entity_names = self._extract_entity_names(query, tokens)

        # Check for each intent type
        enumerate_score = self._score_enumerate(query_lower, tokens)
        compare_score = self._score_compare(query_lower, tokens, entity_names)
        aggregate_score = self._score_aggregate(query_lower, tokens)
        specific_score = self._score_specific(query_lower, tokens, entity_names)

        # Determine intent based on scores
        scores = {
            QueryIntent.ENUMERATE: enumerate_score,
            QueryIntent.COMPARE: compare_score,
            QueryIntent.AGGREGATE: aggregate_score,
            QueryIntent.SPECIFIC_LOOKUP: specific_score,
        }

        # Log scores for debugging
        logger.debug(f"Intent scores for '{query}': {scores}")

        # Priority order for tie-breaking when scores are equal
        # (higher index = higher priority when scores tie)
        intent_priority = {
            QueryIntent.SPECIFIC_LOOKUP: 0,
            QueryIntent.COMPARE: 1,
            QueryIntent.AGGREGATE: 2,
            QueryIntent.ENUMERATE: 3,
        }

        # Sort by score descending, then by priority descending for stable tie-breaking
        sorted_intents = sorted(
            scores.keys(),
            key=lambda i: (scores[i], intent_priority.get(i, 0)),
            reverse=True,
        )
        max_intent = sorted_intents[0]
        max_score = scores[max_intent]

        # If no strong signal, default to EXPLORATORY
        if max_score < 0.3:
            intent = QueryIntent.EXPLORATORY
            confidence = 0.5
            reasoning = "No strong intent signals detected, defaulting to exploratory"
        else:
            intent = max_intent
            confidence = min(max_score, 1.0)
            reasoning = self._generate_reasoning(intent, query_lower, tokens)

        # Determine scope
        scope = self._determine_scope(intent, tokens, entity_names)

        # Generate retrieval hints
        retrieval_hints = self._generate_retrieval_hints(intent, entity_type, entity_names)

        classification = QueryClassification(
            intent=intent,
            entity_type=entity_type,
            entity_names=entity_names,
            attributes=list(attributes),
            scope=scope,
            confidence=confidence,
            reasoning=reasoning,
            retrieval_hints=retrieval_hints,
        )

        logger.info(
            f"Query classified: query='{query[:50]}...', "
            f"intent={intent.value}, confidence={confidence:.2f}, "
            f"scope={scope}, entity_type={entity_type}"
        )

        # Cache the classification for deterministic results on repeated queries
        # Limit cache size to prevent memory growth
        if len(_classification_cache) >= _CLASSIFICATION_CACHE_MAX_SIZE:
            # Remove oldest entries (first ~10% of cache)
            keys_to_remove = list(_classification_cache.keys())[:_CLASSIFICATION_CACHE_MAX_SIZE // 10]
            for key in keys_to_remove:
                _classification_cache.pop(key, None)
        _classification_cache[cache_key] = classification

        return classification

    def _score_enumerate(self, query_lower: str, tokens: set) -> float:
        """Score likelihood of ENUMERATE intent."""
        score = 0.0

        # Check for enumeration patterns (strong signal)
        for pattern in self._enumerate_re:
            if pattern.search(query_lower):
                score += 0.5
                break

        # Check for enumeration keywords
        enum_tokens = tokens & self.ENUMERATE_KEYWORDS
        if enum_tokens:
            score += 0.3 * len(enum_tokens)

        # Check for list verbs + "all/every"
        if tokens & self.LIST_VERBS and tokens & {'all', 'every', 'each'}:
            score += 0.4

        # Plural entity types suggest enumeration ("cards" vs "card")
        if any(word.endswith('s') and word[:-1] in ['card', 'product', 'fee', 'plan']
               for word in tokens):
            score += 0.1

        # "what are the" pattern
        if re.search(r'\bwhat\s+(are|is)\s+(the|all)\b', query_lower):
            score += 0.2

        return min(score, 1.0)

    def _score_compare(self, query_lower: str, tokens: set, entity_names: list) -> float:
        """Score likelihood of COMPARE intent."""
        score = 0.0

        # Check for comparison patterns
        for pattern in self._compare_re:
            if pattern.search(query_lower):
                score += 0.6
                break

        # Multiple entity names mentioned
        if len(entity_names) >= 2:
            score += 0.3

        # Comparison words
        if tokens & {'vs', 'versus', 'compare', 'comparison', 'between', 'differ', 'difference'}:
            score += 0.2

        return min(score, 1.0)

    def _score_aggregate(self, query_lower: str, tokens: set) -> float:
        """Score likelihood of AGGREGATE intent."""
        score = 0.0

        # Check for aggregation patterns
        for pattern in self._aggregate_re:
            if pattern.search(query_lower):
                score += 0.5
                break

        # Aggregation keywords
        if tokens & {'total', 'sum', 'average', 'count', 'minimum', 'maximum', 'mean'}:
            score += 0.4

        # "how many" pattern
        if re.search(r'\bhow\s+many\b', query_lower):
            score += 0.3

        return min(score, 1.0)

    def _score_specific(self, query_lower: str, tokens: set, entity_names: list) -> float:
        """Score likelihood of SPECIFIC_LOOKUP intent."""
        score = 0.0

        # Specific entity name mentioned (single)
        if len(entity_names) == 1:
            score += 0.5

        # "the X card" pattern
        for pattern in self._specific_re:
            if pattern.search(query_lower):
                score += 0.3
                break

        # No enumeration keywords present
        if not (tokens & self.ENUMERATE_KEYWORDS):
            score += 0.2

        # Definite article with singular noun
        if re.search(r'\bthe\s+\w+\s+(card|product|fee|plan)\b', query_lower):
            score += 0.2

        # Possessive patterns
        if re.search(r"'s\s+(fee|rate|limit|benefit)", query_lower):
            score += 0.2

        return min(score, 1.0)

    def _extract_entity_type(self, query_lower: str) -> Optional[str]:
        """Extract the entity type being queried."""
        for entity_type, keywords in self.ENTITY_TYPES.items():
            for keyword in keywords:
                if keyword in query_lower:
                    return entity_type
        return None

    def _extract_attributes(self, tokens: set) -> set:
        """Extract attributes being queried."""
        return tokens & self.ATTRIBUTES

    def _extract_entity_names(self, query: str, tokens: set) -> list[str]:
        """Extract specific entity names from the query."""
        names = []

        # Check against known entity names
        for name in self.known_entity_names:
            if name.lower() in query.lower():
                names.append(name)

        # Check for capitalized words that might be entity names
        # (excluding common words and query terms)
        common_words = {'the', 'a', 'an', 'all', 'every', 'what', 'which', 'how',
                       'is', 'are', 'do', 'does', 'can', 'could', 'would', 'should',
                       'list', 'show', 'give', 'tell', 'me', 'please', 'and', 'or',
                       'for', 'with', 'from', 'to', 'in', 'on', 'at', 'by'}

        # Find capitalized words in original query
        capitalized = re.findall(r'\b[A-Z][a-z]+\b', query)
        for word in capitalized:
            if word.lower() not in common_words and word not in names:
                names.append(word)

        return names

    def _determine_scope(self, intent: QueryIntent, tokens: set, entity_names: list) -> str:
        """Determine the scope of the query."""
        if intent == QueryIntent.ENUMERATE:
            return "all"
        elif intent == QueryIntent.AGGREGATE:
            return "all"
        elif intent == QueryIntent.COMPARE:
            return "subset"
        elif intent == QueryIntent.SPECIFIC_LOOKUP:
            return "specific"
        elif tokens & {'some', 'few', 'several'}:
            return "subset"
        else:
            return "unknown"

    def _generate_reasoning(self, intent: QueryIntent, query_lower: str, tokens: set) -> str:
        """Generate human-readable reasoning for the classification."""
        if intent == QueryIntent.ENUMERATE:
            if tokens & self.ENUMERATE_KEYWORDS:
                keywords = tokens & self.ENUMERATE_KEYWORDS
                return f"Enumeration keywords detected: {keywords}"
            return "Query pattern suggests listing/enumeration intent"

        elif intent == QueryIntent.COMPARE:
            return "Comparison pattern or multiple entities detected"

        elif intent == QueryIntent.AGGREGATE:
            return "Aggregation/calculation intent detected"

        elif intent == QueryIntent.SPECIFIC_LOOKUP:
            return "Specific entity lookup pattern detected"

        return "Classification based on overall query analysis"

    def _generate_retrieval_hints(
        self,
        intent: QueryIntent,
        entity_type: Optional[str],
        entity_names: list[str]
    ) -> dict:
        """Generate hints for the retrieval layer."""
        hints = {
            'diversify_tables': intent in (QueryIntent.ENUMERATE, QueryIntent.EXPLORATORY),
            'increase_snippet_limit': intent in (QueryIntent.ENUMERATE, QueryIntent.AGGREGATE),
            'filter_by_entity_names': bool(entity_names) and intent == QueryIntent.SPECIFIC_LOOKUP,
            'entity_names_filter': entity_names if intent == QueryIntent.SPECIFIC_LOOKUP else [],
            'prefer_table_headers': intent == QueryIntent.ENUMERATE,
            'include_all_tables': intent == QueryIntent.ENUMERATE,
        }

        if intent == QueryIntent.ENUMERATE:
            hints['min_tables_coverage'] = 'all'
            hints['snippet_limit_multiplier'] = 3.0
        elif intent == QueryIntent.AGGREGATE:
            hints['min_tables_coverage'] = 'all'
            hints['snippet_limit_multiplier'] = 2.5
        elif intent == QueryIntent.COMPARE:
            hints['snippet_limit_multiplier'] = 1.5
        else:
            hints['snippet_limit_multiplier'] = 1.0

        return hints


# Convenience function for quick classification
def classify_query(query: str, context: Optional[dict] = None) -> QueryClassification:
    """
    Classify a query's intent using default classifier.

    Args:
        query: The user's query string
        context: Optional context dict

    Returns:
        QueryClassification with intent and metadata
    """
    classifier = QueryClassifier()
    return classifier.classify(query, context)
