"""
Query Intent Classification Module

This module provides intelligent query intent classification for RAG retrieval,
replacing simple keyword matching with proper linguistic analysis.

The classifier distinguishes between:
- ENUMERATE: "list all products", "show me every service"
- SPECIFIC_LOOKUP: "Acme package details", "what is the premium tier price"
- COMPARE: "compare basic vs premium", "difference between plan A and plan B"
- AGGREGATE: "total cost", "average response time across all tickets"
- EXPLORATORY: "what do you offer", "tell me about your services"

This enables the retrieval layer to select appropriate strategies per intent.
"""

from typing import Optional
import re
import logging
import hashlib

from apps.rag.lexicon.tenant import normalize_lexicon_text, tokenize_lexicon_text
from apps.rag.query.classifier_entities import QueryClassifierEntityMixin
from apps.rag.query.classifier_hints import QueryClassifierHintMixin
from apps.rag.query.classifier_patterns import QueryClassifierPatternMixin
from apps.rag.query.classifier_scoring import QueryClassifierScoringMixin
from apps.rag.query.classifier_types import QueryClassification, QueryIntent

logger = logging.getLogger(__name__)

# Module-level cache for query classifications to ensure deterministic results
# across repeated calls with the same query
_classification_cache: dict[str, "QueryClassification"] = {}
_CLASSIFICATION_CACHE_MAX_SIZE = 1000



class QueryClassifier(
    QueryClassifierPatternMixin,
    QueryClassifierScoringMixin,
    QueryClassifierEntityMixin,
    QueryClassifierHintMixin,
):
    """
    Intelligent query intent classifier for RAG systems.

    Uses linguistic patterns and contextual signals to classify user queries
    into intent types, enabling intent-aware retrieval strategies.

    This replaces the broken keyword-matching logic that disabled comprehensive
    intent when ANY row label matched query tokens.
    """


    @staticmethod
    def _normalize_context_terms(values: object, *, max_items: int = 300) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple, set)):
            return tuple()
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            token = normalize_lexicon_text(str(value or ""))
            if not token:
                continue
            if token in seen:
                continue
            seen.add(token)
            normalized.append(token)
            if len(normalized) >= max_items:
                break
        return tuple(normalized)

    @staticmethod
    def _context_signature(context: dict) -> str:
        if not context:
            return ""
        parts: list[str] = []
        tenant_id = str(context.get("tenant_id") or "").strip().lower()
        if tenant_id:
            parts.append(f"tenant_id:{tenant_id}")
        for key in ("document_entities", "table_schemas", "tenant_entity_terms", "tenant_attribute_terms"):
            values = QueryClassifier._normalize_context_terms(context.get(key), max_items=200)
            if not values:
                continue
            parts.append(f"{key}:{'|'.join(values)}")
        if not parts:
            return ""
        digest = hashlib.sha1("||".join(parts).encode("utf-8")).hexdigest()[:16]
        return digest

    @staticmethod
    def _phrase_in_query(phrase: str, query_lower: str) -> bool:
        phrase_normalized = normalize_lexicon_text(str(phrase or ""))
        query_normalized = normalize_lexicon_text(str(query_lower or ""))
        if not phrase_normalized or not query_normalized:
            return False

        def _contains(candidate: str) -> bool:
            escaped = re.escape(candidate)
            if not escaped:
                return False
            pattern = rf"(?<!\w){escaped}(?!\w)"
            return bool(re.search(pattern, query_normalized))

        if _contains(phrase_normalized):
            return True

        # Lightweight singular/plural tolerance for lexicon phrase matching.
        words = phrase_normalized.split()
        if not words:
            return False
        last = words[-1]
        variants: set[str] = set()
        if len(last) > 2:
            if last.endswith("ies"):
                variants.add(last[:-3] + "y")
            if last.endswith("s") and not last.endswith("ss"):
                variants.add(last[:-1])
            else:
                variants.add(last + "s")
            if not last.endswith("ies") and last.endswith("y") and len(last) > 3:
                variants.add(last[:-1] + "ies")
        for variant in variants:
            probe = " ".join(words[:-1] + [variant]).strip()
            if probe and _contains(probe):
                return True
        return False

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
        context = context or {}
        tenant_entity_terms = self._normalize_context_terms(context.get("tenant_entity_terms"), max_items=300)
        tenant_attribute_terms = self._normalize_context_terms(context.get("tenant_attribute_terms"), max_items=500)

        # Check cache for deterministic results on repeated queries + relevant context.
        context_sig = self._context_signature(context)
        normalized_query = normalize_lexicon_text(query) or query.strip().lower()
        cache_key = normalized_query
        if context_sig:
            cache_key = f"{cache_key}::{context_sig}"
        if cache_key in _classification_cache:
            cached = _classification_cache[cache_key]
            logger.debug(f"Query classification cache hit for '{query[:50]}...'")
            return cached

        query_lower = normalized_query
        token_list = list(tokenize_lexicon_text(query_lower, max_tokens=256))
        tokens = set(token_list)

        # Update known entities from context
        if context.get('document_entities'):
            self.known_entity_names.update(context['document_entities'])
        if tenant_entity_terms:
            self.known_entity_names.update(tenant_entity_terms)

        # Extract entity names first, then infer generic entity type/attributes.
        entity_names = self._extract_entity_names(query, tenant_entity_terms=tenant_entity_terms)
        entity_type = self._extract_entity_type(
            token_list,
            entity_names,
            tenant_entity_terms=tenant_entity_terms,
            query_lower=query_lower,
        )
        attributes = self._extract_attributes(
            token_list,
            entity_type,
            entity_names,
            tenant_attribute_terms=tenant_attribute_terms,
            query_lower=query_lower,
        )

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
        retrieval_hints = self._generate_retrieval_hints(
            intent,
            entity_type,
            entity_names,
            attributes=attributes,
            token_list=token_list,
            query_lower=query_lower,
        )

        classification = QueryClassification(
            intent=intent,
            entity_type=entity_type,
            entity_names=entity_names,
            attributes=attributes,
            scope=scope,
            confidence=confidence,
            reasoning=reasoning,
            retrieval_hints=retrieval_hints,
            source="heuristic",
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
