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

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import re
import logging
import hashlib

from apps.rag.tenant_lexicon import normalize_lexicon_text, tokenize_lexicon_text
from apps.rag.text_utils import (
    PLURAL_BLACKLIST as _SHARED_PLURAL_BLACKLIST,
    is_plural_candidate,
    singularize,
)

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
        'whole', 'everything', 'comprehensive',
        'كل', 'جميع', 'كافه', 'كافة', 'الكل',
    }

    # List action verbs - verbs that often precede enumeration
    LIST_VERBS = {
        'list', 'show', 'display', 'enumerate', 'give', 'tell',
        'اعرض', 'عرض', 'هات', 'اعطني', 'اذكر',
    }

    ALL_SCOPE_TOKENS = {
        "all",
        "every",
        "each",
        "entire",
        "full",
        "whole",
        "كل",
        "جميع",
        "كافة",
        "كافه",
        "الكل",
    }

    # Comparison indicators
    COMPARE_PATTERNS = [
        r'\bcompare\b',
        r'\bvs\.?\b',
        r'\bversus\b',
        r'\bdifference\s+between\b',
        r'\bcompared\s+to\b',
        r'\bbetter\s+than\b',
        r'\bworse\s+than\b',
        r'\bقارن\b',
        r'\bمقارنه\b',
        r'\bمقارنة\b',
        r'\bالفرق\s+بين\b',
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
        r'\bاجمالي\b',
        r'\bإجمالي\b',
        r'\bالمجموع\b',
        r'\bمتوسط\b',
        r'\bكم\b',
    ]

    # Specific lookup indicators - signals for SPECIFIC_LOOKUP
    SPECIFIC_PATTERNS = [
        r'\bwhat\s+is\s+the\s+\w+\b',
        r'\bdetails?\s+for\s+(the\s+)?\w+\b',
        r'\binfo(?:rmation)?\s+for\s+(the\s+)?\w+\b',
    ]

    # Domain-agnostic control words used to isolate entity/attribute candidates.
    # These are structural query terms, not business-domain vocab.
    CONTROL_TOKENS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "how", "in", "is", "it", "its", "me", "of", "on", "or", "show",
        "tell", "that", "the", "their", "them", "these", "those", "this",
        "to", "what", "which", "who", "with", "you", "your", "about", "all",
        "every", "each", "any", "some", "list", "display", "give", "enumerate",
        "compare", "comparison", "versus", "vs", "between", "difference", "differ",
        "total", "sum", "average", "count", "minimum", "maximum", "highest", "lowest",
        "mean", "many", "more", "few", "several", "please",
        "ما", "ماذا", "كيف", "من", "عن", "مع", "في", "على", "الى", "إلى",
        "هذا", "هذه", "ذلك", "تلك", "كل", "جميع", "او", "أو", "و", "ثم",
        "اعرض", "عرض", "قارن", "مقارنة", "اجمالي", "إجمالي", "المجموع",
    }
    ENTITY_BOUNDARY_TOKENS = {
        "and", "or", "with", "without", "their", "its", "this", "that", "these",
        "those", "for", "from", "to", "in", "on", "at", "by", "of", "about",
        "regarding", "where", "when", "which", "who", "what", "how",
        "عن", "مع", "في", "على", "الى", "إلى", "من", "او", "أو", "و", "ثم",
        "التي", "الذي", "ما", "ماذا", "كيف",
    }
    ENTITY_NOISE_PREFIX_TOKENS = {
        "the", "a", "an", "all", "every", "each", "any", "some",
        "available", "current", "latest", "new", "existing", "active",
        "ال", "كل", "جميع", "كافة", "هذا", "هذه", "ذلك", "تلك",
    }
    ENTITY_CAPTURE_ANCHORS = {
        "all", "every", "each", "some", "many", "few", "several", "for", "about", "regarding",
        "كل", "جميع", "كافة", "عن",
    }
    PLURAL_BLACKLIST = _SHARED_PLURAL_BLACKLIST
    COMPARE_TOKENS = {
        "vs",
        "versus",
        "compare",
        "comparison",
        "between",
        "differ",
        "difference",
        "قارن",
        "مقارنة",
        "مقارنه",
        "الفرق",
    }
    AGGREGATE_TOKENS = {
        "total",
        "sum",
        "average",
        "count",
        "minimum",
        "maximum",
        "mean",
        "اجمالي",
        "إجمالي",
        "المجموع",
        "متوسط",
    }
    SECTION_SEEKING_PATTERNS = [
        r"\bwork\s+experience\b",
        r"\bwork\s+history\b",
        r"\bemployment\s+history\b",
        r"\bjob\s+titles?\b",
        r"\bjob\s+positions?\b",
        r"\bterms?\s+and\s+conditions\b",
    ]
    SECTION_COVERAGE_HEADWORDS = {
        "title",
        "titles",
        "role",
        "roles",
        "position",
        "positions",
        "responsibility",
        "responsibilities",
        "duty",
        "duties",
        "term",
        "terms",
        "clause",
        "clauses",
        "section",
        "sections",
        "benefit",
        "benefits",
        "fee",
        "fees",
        "charge",
        "charges",
        "service",
        "services",
        "product",
        "products",
        "job",
        "jobs",
        "experience",
        "experiences",
        "history",
        "employment",
        "skill",
        "skills",
        "qualification",
        "qualifications",
        "requirement",
        "requirements",
        "location",
        "locations",
    }

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
        if tokens & self.LIST_VERBS and tokens & self.ALL_SCOPE_TOKENS:
            score += 0.4

        # Plural-noun queries are often enumeration-style ("products", "services").
        if any(self._looks_like_plural_noun(word) for word in tokens):
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
        if tokens & self.COMPARE_TOKENS:
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
        if tokens & self.AGGREGATE_TOKENS:
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

        # Definite-article phrasing with an entity mention is typically specific.
        if entity_names and re.search(r"\bthe\s+\w+\b", query_lower):
            score += 0.2

        # Possessive phrasing ("X's details") usually targets a specific item.
        if re.search(r"'s\s+\w+", query_lower):
            score += 0.15

        return min(score, 1.0)

    @staticmethod
    def _singularize(token: str) -> str:
        return singularize(token)

    @classmethod
    def _looks_like_plural_noun(cls, token: str) -> bool:
        return is_plural_candidate(token)

    @staticmethod
    def _dedupe_preserve(values: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    @staticmethod
    def _tokenize_entity_name(name: str) -> list[str]:
        return list(tokenize_lexicon_text(name, max_tokens=32))

    def _collect_entity_phrase(self, tokens: list[str], start_idx: int) -> list[str]:
        phrase: list[str] = []
        for token in tokens[start_idx:]:
            if token in self.ENTITY_BOUNDARY_TOKENS:
                break
            if len(token) < 2:
                continue
            phrase.append(token)
            if len(phrase) >= 3:
                break
        while phrase and phrase[0] in self.ENTITY_NOISE_PREFIX_TOKENS:
            phrase.pop(0)
        if phrase:
            phrase[-1] = self._singularize(phrase[-1])
        return phrase

    def _extract_entity_type(
        self,
        token_list: list[str],
        entity_names: list[str],
        *,
        tenant_entity_terms: tuple[str, ...] = tuple(),
        query_lower: str = "",
    ) -> Optional[str]:
        """Extract a generic entity type phrase without domain-specific dictionaries."""
        if not token_list:
            return None

        entity_name_tokens: set[str] = set()
        for name in entity_names:
            entity_name_tokens.update(self._tokenize_entity_name(name))

        candidates: list[list[str]] = []

        for idx, token in enumerate(token_list):
            if token in self.ENTITY_CAPTURE_ANCHORS:
                phrase = self._collect_entity_phrase(token_list, idx + 1)
                if phrase:
                    candidates.append(phrase)

        # Known-entity anchored candidate: first descriptor token after the entity name.
        if entity_names:
            for name in entity_names:
                name_tokens = self._tokenize_entity_name(name)
                if not name_tokens:
                    continue
                span = len(name_tokens)
                for idx in range(0, len(token_list) - span + 1):
                    if token_list[idx:idx + span] == name_tokens:
                        phrase = self._collect_entity_phrase(token_list, idx + span)
                        if phrase:
                            candidates.append(phrase[:1])

        normalized: list[str] = []
        for parts in candidates:
            filtered = [part for part in parts if part not in entity_name_tokens and part not in self.CONTROL_TOKENS]
            if not filtered:
                continue
            normalized.append(" ".join(filtered))

        if not normalized:
            if tenant_entity_terms and query_lower:
                for term in tenant_entity_terms:
                    if self._phrase_in_query(term, query_lower):
                        return term
            return None

        ordered = self._dedupe_preserve(normalized)
        # Prefer shortest candidate to avoid leaking attribute terms into entity type.
        ordered.sort(key=lambda value: (len(value.split()), len(value)))
        return ordered[0]

    def _extract_attributes(
        self,
        token_list: list[str],
        entity_type: Optional[str],
        entity_names: list[str],
        *,
        tenant_attribute_terms: tuple[str, ...] = tuple(),
        query_lower: str = "",
    ) -> list[str]:
        """Extract attribute-like tokens using query structure (domain agnostic)."""
        entity_tokens: set[str] = set()
        if entity_type:
            entity_tokens.update(re.findall(r"\b\w+\b", entity_type.lower()))
        for name in entity_names:
            entity_tokens.update(self._tokenize_entity_name(name))

        attributes: list[str] = []
        for token in token_list:
            if len(token) < 3:
                continue
            if token in self.CONTROL_TOKENS:
                continue
            if token in entity_tokens:
                continue
            attributes.append(token)

        if tenant_attribute_terms and query_lower:
            for phrase in tenant_attribute_terms:
                if phrase in entity_tokens:
                    continue
                if self._phrase_in_query(phrase, query_lower):
                    attributes.insert(0, phrase)

        return self._dedupe_preserve(attributes)

    def _extract_entity_names(self, query: str, *, tenant_entity_terms: tuple[str, ...] = tuple()) -> list[str]:
        """Extract specific entity names from the query."""
        names = []
        query_lower = normalize_lexicon_text(query)

        # Check against known entity names
        for name in self.known_entity_names:
            if self._phrase_in_query(name, query_lower):
                names.append(name)

        if tenant_entity_terms:
            for term in tenant_entity_terms:
                if self._phrase_in_query(term, query_lower):
                    names.append(term)

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
        entity_names: list[str],
        *,
        attributes: list[str],
        token_list: list[str],
        query_lower: str,
    ) -> dict:
        """Generate hints for the retrieval layer."""
        prefer_section_context = self._should_prefer_section_context(
            intent,
            token_list=token_list,
            query_lower=query_lower,
            entity_type=entity_type,
            entity_names=entity_names,
            attributes=attributes,
        )
        section_focus_terms = self._section_focus_terms(
            entity_type=entity_type,
            attributes=attributes,
            query_lower=query_lower,
        )
        hints = {
            'diversify_tables': intent in (QueryIntent.ENUMERATE, QueryIntent.EXPLORATORY),
            'increase_snippet_limit': intent in (QueryIntent.ENUMERATE, QueryIntent.AGGREGATE),
            'filter_by_entity_names': bool(entity_names) and intent == QueryIntent.SPECIFIC_LOOKUP,
            'entity_names_filter': entity_names if intent == QueryIntent.SPECIFIC_LOOKUP else [],
            'prefer_table_headers': intent == QueryIntent.ENUMERATE,
            'include_all_tables': intent == QueryIntent.ENUMERATE,
            'prefer_section_context': prefer_section_context,
            'section_focus_terms': section_focus_terms if prefer_section_context else [],
            'modality_bias': 'mixed',
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

    def _section_focus_terms(
        self,
        *,
        entity_type: Optional[str],
        attributes: list[str],
        query_lower: str,
    ) -> list[str]:
        terms: list[str] = []
        if entity_type:
            terms.append(entity_type)
        for attr in attributes:
            cleaned = str(attr or "").strip().lower()
            if not cleaned:
                continue
            terms.append(cleaned)
            attr_tokens = [token for token in cleaned.split() if token]
            if len(attr_tokens) == 1:
                singular = self._singularize(attr_tokens[0])
                if singular and singular != cleaned:
                    terms.append(singular)
        for pattern in self.SECTION_SEEKING_PATTERNS:
            match = re.search(pattern, query_lower)
            if match:
                terms.append(match.group(0).strip().lower())
        return self._dedupe_preserve(terms)[:8]

    def _should_prefer_section_context(
        self,
        intent: QueryIntent,
        *,
        token_list: list[str],
        query_lower: str,
        entity_type: Optional[str],
        entity_names: list[str],
        attributes: list[str],
    ) -> bool:
        token_set = set(token_list)
        coverage_terms = token_set & self.SECTION_COVERAGE_HEADWORDS
        plural_attribute_signal = False
        for attr in attributes:
            attr_tokens = list(tokenize_lexicon_text(attr, max_tokens=12))
            if any(self._looks_like_plural_noun(token) for token in attr_tokens):
                plural_attribute_signal = True
                break

        phrase_signal = any(re.search(pattern, query_lower) for pattern in self.SECTION_SEEKING_PATTERNS)
        temporal_location_signal = bool(
            {"where", "when"} & token_set
            and {"work", "employment", "experience", "history", "job", "jobs"} & token_set
        )
        coverage_question_signal = bool(
            coverage_terms
            and (
                plural_attribute_signal
                or bool(token_set & self.ALL_SCOPE_TOKENS)
                or bool(token_set & self.LIST_VERBS)
                or "else" in token_set
                or phrase_signal
                or temporal_location_signal
            )
        )

        if intent in (QueryIntent.ENUMERATE, QueryIntent.AGGREGATE):
            return bool(attributes or entity_type or coverage_terms or phrase_signal)
        if intent == QueryIntent.COMPARE:
            return bool(coverage_terms and len(entity_names) <= 1 and (plural_attribute_signal or phrase_signal))
        return bool(coverage_question_signal or phrase_signal or temporal_location_signal)


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
