"""
Context-Aware Query Rewriting for Conversation-Aware RAG.

This module implements query rewriting patterns for handling follow-up queries
in multi-turn conversations. Based on 2025 best practices from LangChain,
LlamaIndex, and academic research on conversational RAG.

Key Features:
- Pronoun resolution ("it" -> document name)
- Context injection ("fees for X" -> "Trade Bills: fees for X")
- Follow-up query detection
- Query expansion with previous entities

References:
- LangChain Conversational RAG: Memory management patterns
- LlamaIndex Context-Aware Filtering: Query expansion techniques
- CRAG (Corrective RAG): Query reformulation strategies
"""

from __future__ import annotations

import re
from typing import Sequence

from django.conf import settings

from apps.rag.query.rewrite_context_builder import build_rewrite_context_from_tool_context
from apps.rag.query.rewrite_types import (
    _GENERIC_CONTEXT_TOKENS,
    _STOPWORDS,
    RewriteContext,
    RewriteResult,
    _context_overlap_tokens,
    _significant_tokens,
    _tokenize,
)
from apps.rag.lexicon.tenant import normalize_lexicon_text



class ContextAwareQueryRewriter:
    """
    Historical query rewriter kept for compatibility with older imports.

    Document-title query injection is intentionally disabled. Search queries
    must stay exactly as the agent/user supplied them unless an explicit tool
    scope is provided elsewhere.
    """

    # Patterns that indicate a follow-up query
    FOLLOWUP_INDICATORS = [
        r"^(and|also|what about|how about|tell me about|show me)\b",
        r"^(the|their|its|these|those)\b",
        r"^(another|other|more|different)\b",
        r"^(same|similar)\b",
        r"\b(as well|too|additionally)\b",
        r"^(?:و\s+|ثم\s+)",
        r"^(?:و)?ماذا\s+عن\b",
        r"^(?:و)?وش\s+عن\b",
        r"^(مثل|نفس|ايضا|أيضا)\b",
    ]

    # Pronouns that reference previous context
    PRONOUN_PATTERNS = [
        r"\b(it|its|they|them|their|this|that|these|those)\b",
        r"\b(هذا|هذه|ذلك|تلك|هو|هي|هم|هن|له|لها|لهم)\b",
    ]

    # Minimum query length to consider for context injection
    MIN_QUERY_LENGTH = 3

    # Maximum query length for follow-up detection (longer queries are usually self-contained)
    MAX_FOLLOWUP_QUERY_LENGTH = 12

    def __init__(
        self,
        enabled: bool = True,
        prefix_mode: bool = True,
        min_confidence: float = 0.5,
    ):
        """
        Initialize the query rewriter.

        Args:
            enabled: Whether rewriting is enabled
            prefix_mode: If True, prepend document title; if False, append
            min_confidence: Minimum confidence to apply rewriting
        """
        self.enabled = enabled
        self.prefix_mode = prefix_mode
        self.min_confidence = min_confidence

        # Compile patterns for efficiency
        self._followup_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.FOLLOWUP_INDICATORS
        ]
        self._pronoun_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.PRONOUN_PATTERNS
        ]

    def rewrite(self, query: str, context: RewriteContext) -> RewriteResult:
        """
        Return the original query unchanged.

        Args:
            query: The user's query string
            context: Conversation context for rewriting

        Returns:
            RewriteResult with no context injection.
        """
        query = (query or "").strip()
        return RewriteResult(
            original_query=query,
            rewritten_query=query,
            context_injected=False,
            rewrite_strategy="disabled",
            confidence=0.0,
            reason="document_continuity_removed",
        )

    def _detect_followup(
        self, query: str, context: RewriteContext
    ) -> tuple[bool, float, str]:
        """
        Detect if a query is a follow-up to previous conversation context.

        Returns:
            Tuple of (is_followup, confidence)
        """
        confidence = 0.0
        tokens = _tokenize(query)
        token_count = len(tokens)
        query_significant = {token for token in tokens if len(token) >= 3 and token not in _STOPWORDS}
        query_context_tokens = query_significant - _GENERIC_CONTEXT_TOKENS

        # Short queries are more likely to be follow-ups, but shortness alone is not enough.
        if token_count <= self.MAX_FOLLOWUP_QUERY_LENGTH:
            confidence += 0.10
        if token_count <= 6:
            confidence += 0.10
        if token_count <= 3:
            confidence += 0.10

        # Check for explicit follow-up indicators
        has_followup_marker = False
        for pattern in self._followup_patterns:
            if pattern.search(query):
                confidence += 0.35
                has_followup_marker = True
                break

        # Check for pronouns that reference previous context
        has_pronoun_marker = False
        for pattern in self._pronoun_patterns:
            if pattern.search(query):
                confidence += 0.25
                has_pronoun_marker = True
                break

        title_overlap = 0
        if context.primary_document_title:
            title_tokens = _context_overlap_tokens(context.primary_document_title)
            title_overlap = len(title_tokens & query_context_tokens) if title_tokens else 0
            if title_overlap >= 2:
                confidence += 0.25
            elif title_overlap == 1:
                confidence += 0.15

        prev_overlap = 0
        if context.previous_queries:
            prev_tokens = _context_overlap_tokens(context.previous_queries[-1])
            prev_overlap = len(prev_tokens & query_context_tokens) if prev_tokens else 0
            if prev_overlap >= 2:
                confidence += 0.25
            elif prev_overlap == 1:
                confidence += 0.15

        lexicon_overlap = self._lexicon_overlap(query_context_tokens, context)
        if lexicon_overlap >= 2:
            confidence += 0.2
        elif lexicon_overlap == 1:
            confidence += 0.1

        has_marker = has_followup_marker or has_pronoun_marker
        if (
            not has_marker
            and query_context_tokens
            and title_overlap == 0
            and prev_overlap == 0
            and lexicon_overlap == 0
        ):
            return False, min(confidence, self.min_confidence - 0.01), "topic_shift"

        # Guardrail: avoid rewriting "new topic" short queries with no overlap or markers.
        # After a document is read, the system also applies document affinity search; we
        # keep rewriting conservative to prevent over-biasing unrelated queries.
        if confidence >= self.min_confidence and token_count <= self.MAX_FOLLOWUP_QUERY_LENGTH:
            if not has_marker and title_overlap == 0 and prev_overlap == 0 and lexicon_overlap == 0:
                confidence = min(confidence, self.min_confidence - 0.01)

        is_followup = confidence >= self.min_confidence
        return is_followup, min(confidence, 1.0), "followup" if is_followup else "low_confidence"

    @staticmethod
    def _lexicon_overlap(query_significant: set[str], context: RewriteContext) -> int:
        if not query_significant:
            return 0

        def _term_tokens(values: Sequence[str]) -> set[str]:
            tokens: set[str] = set()
            for value in values:
                for token in _tokenize(str(value or "")):
                    if len(token) >= 3 and token not in _STOPWORDS and token not in _GENERIC_CONTEXT_TOKENS:
                        tokens.add(token)
            return tokens

        lexicon_tokens = _term_tokens(context.tenant_entity_terms) | _term_tokens(context.tenant_attribute_terms)
        if not lexicon_tokens:
            return 0
        return len(query_significant & lexicon_tokens)

    def _query_mentions_document(self, query: str, context: RewriteContext) -> bool:
        """Check if the query already mentions the primary document."""
        if not context.primary_document_title:
            return False

        query_lower = normalize_lexicon_text(query) or query.lower()
        title_lower = normalize_lexicon_text(context.primary_document_title) or context.primary_document_title.lower()

        # Check for exact title match
        if title_lower in query_lower:
            return True

        # Check for significant word overlap (more than half of significant title words).
        # Require at least 2 overlapping tokens (where possible) to avoid treating generic
        # overlaps like "bills" as an explicit document mention.
        title_tokens = _significant_tokens(title_lower)
        query_tokens = _significant_tokens(query_lower)
        if title_tokens:
            overlap = title_tokens & query_tokens
            min_required = 2 if len(title_tokens) >= 2 else 1
            if len(overlap) >= min_required and len(overlap) >= len(title_tokens) / 2:
                return True

        return False

    def _inject_context(self, query: str, context: RewriteContext) -> str:
        """Inject document context into the query."""
        title = context.primary_document_title

        if self.prefix_mode:
            # Prefix format: "Document Name: query"
            return f"{title}: {query}"
        else:
            # Suffix format: "query (from Document Name)"
            return f"{query} (from {title})"




def get_query_rewriter() -> ContextAwareQueryRewriter:
    """
    Get a configured ContextAwareQueryRewriter instance.

    Reads configuration from Django settings:
    - RAG_CONTEXT_QUERY_REWRITE_ENABLED: Enable/disable rewriting (default: True)
    - RAG_CONTEXT_QUERY_REWRITE_MIN_CONFIDENCE: Minimum confidence (default: 0.5)
    - RAG_CONTEXT_QUERY_REWRITE_PREFIX_MODE: Use prefix mode (default: True)

    Returns:
        Configured ContextAwareQueryRewriter instance
    """
    # Document-title query rewriting has been removed from the active product
    # contract. The settings are intentionally ignored so old environment values
    # cannot re-enable implicit file lock-in.
    enabled = False

    try:
        min_confidence = float(
            getattr(settings, "RAG_CONTEXT_QUERY_REWRITE_MIN_CONFIDENCE", 0.5)
        )
    except (TypeError, ValueError):
        min_confidence = 0.5

    prefix_mode = str(
        getattr(settings, "RAG_CONTEXT_QUERY_REWRITE_PREFIX_MODE", "true")
    ).lower() in {"1", "true", "yes"}

    return ContextAwareQueryRewriter(
        enabled=enabled,
        prefix_mode=prefix_mode,
        min_confidence=min_confidence,
    )
