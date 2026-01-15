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

import logging
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RewriteContext:
    """
    Context for query rewriting.

    Contains information from the conversation that can be used to
    rewrite follow-up queries for better retrieval.
    """

    # Primary document being discussed (most-referenced)
    primary_document_title: str | None = None
    primary_upload_id: str | None = None

    # All documents referenced in the conversation
    referenced_documents: tuple[str, ...] = ()

    # Previous queries in this conversation (for pattern detection)
    previous_queries: tuple[str, ...] = ()

    # Entities extracted from previous turns
    extracted_entities: tuple[str, ...] = ()


@dataclass(frozen=True)
class RewriteResult:
    """
    Result of query rewriting.

    Contains the rewritten query along with metadata about the rewrite
    for logging and debugging purposes.
    """

    original_query: str
    rewritten_query: str
    context_injected: bool
    rewrite_strategy: str  # "none", "prefix", "expansion", "entity_resolution"
    confidence: float = 1.0  # How confident we are this is a follow-up

    def as_dict(self) -> dict:
        """Convert to dictionary for logging/serialization."""
        return {
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "context_injected": self.context_injected,
            "rewrite_strategy": self.rewrite_strategy,
            "confidence": self.confidence,
        }


class ContextAwareQueryRewriter:
    """
    Rewrites queries using conversation context.

    This class implements follow-up query detection and context injection
    to improve retrieval accuracy in multi-turn conversations.

    Example:
        Turn 1: "What are the fees in Trade Bills EN?"
        Turn 2: "What about withdrawal fees?" (follow-up)

        Without rewriting: "What about withdrawal fees?"
            -> Matches many documents with "withdrawal" and "fees"

        With rewriting: "Trade Bills EN: What about withdrawal fees?"
            -> Strongly prefers Trade Bills document
    """

    # Patterns that indicate a follow-up query
    FOLLOWUP_INDICATORS = [
        r"^(and|also|what about|how about|tell me about|show me)\b",
        r"^(the|their|its|these|those)\b",
        r"^(another|other|more|different)\b",
        r"^(same|similar)\b",
        r"\b(as well|too|additionally)\b",
    ]

    # Pronouns that reference previous context
    PRONOUN_PATTERNS = [
        r"\b(it|its|they|them|their|this|that|these|those)\b",
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
        Rewrite a query with conversation context if appropriate.

        Args:
            query: The user's query string
            context: Conversation context for rewriting

        Returns:
            RewriteResult with original and (possibly) rewritten query
        """
        query = (query or "").strip()

        # Short-circuit if disabled or no context
        if not self.enabled:
            return RewriteResult(query, query, False, "disabled")

        if not context.primary_document_title:
            return RewriteResult(query, query, False, "no_context")

        if len(query) < self.MIN_QUERY_LENGTH:
            return RewriteResult(query, query, False, "query_too_short")

        # Check if query already mentions the primary document
        if self._query_mentions_document(query, context):
            return RewriteResult(query, query, False, "already_contextual")

        # Detect follow-up patterns
        is_followup, confidence = self._detect_followup(query, context)

        if not is_followup or confidence < self.min_confidence:
            return RewriteResult(query, query, False, "not_followup", confidence)

        # Apply context injection
        rewritten = self._inject_context(query, context)

        logger.debug(
            "Query rewritten: '%s' -> '%s' (strategy=%s, confidence=%.2f)",
            query,
            rewritten,
            "prefix" if self.prefix_mode else "suffix",
            confidence,
        )

        return RewriteResult(
            original_query=query,
            rewritten_query=rewritten,
            context_injected=True,
            rewrite_strategy="prefix" if self.prefix_mode else "suffix",
            confidence=confidence,
        )

    def _detect_followup(
        self, query: str, context: RewriteContext
    ) -> tuple[bool, float]:
        """
        Detect if a query is a follow-up to previous conversation context.

        Returns:
            Tuple of (is_followup, confidence)
        """
        confidence = 0.0
        tokens = query.lower().split()

        # Short queries are more likely to be follow-ups
        if len(tokens) <= self.MAX_FOLLOWUP_QUERY_LENGTH:
            confidence += 0.2

        # Check for explicit follow-up indicators
        for pattern in self._followup_patterns:
            if pattern.search(query):
                confidence += 0.4
                break

        # Check for pronouns that reference previous context
        for pattern in self._pronoun_patterns:
            if pattern.search(query):
                confidence += 0.3
                break

        # Check if query shares tokens with primary document title
        if context.primary_document_title:
            title_tokens = set(context.primary_document_title.lower().split())
            query_tokens = set(tokens)
            # If query shares NO tokens with document title, more likely a follow-up
            if not (title_tokens & query_tokens):
                confidence += 0.1

        # Check if this is the first query (not a follow-up by definition)
        if not context.previous_queries:
            confidence *= 0.5  # Reduce confidence for first query

        is_followup = confidence >= self.min_confidence
        return is_followup, min(confidence, 1.0)

    def _query_mentions_document(self, query: str, context: RewriteContext) -> bool:
        """Check if the query already mentions the primary document."""
        if not context.primary_document_title:
            return False

        query_lower = query.lower()
        title_lower = context.primary_document_title.lower()

        # Check for exact title match
        if title_lower in query_lower:
            return True

        # Check for significant word overlap (more than half of title words)
        title_words = set(title_lower.split())
        query_words = set(query_lower.split())
        overlap = title_words & query_words

        if len(overlap) >= len(title_words) / 2:
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


def build_rewrite_context_from_tool_context(tool_context) -> RewriteContext:
    """
    Build a RewriteContext from a ToolExecutionContext.

    This helper function extracts the relevant fields from ToolExecutionContext
    and creates a RewriteContext for query rewriting.

    Args:
        tool_context: ToolExecutionContext instance

    Returns:
        RewriteContext for query rewriting
    """
    if tool_context is None:
        return RewriteContext()

    # Get document context
    doc_context = tool_context.get_document_context_for_query()

    # Extract previous queries from search history
    previous_queries = []
    search_history = getattr(tool_context, "search_history", [])
    for entry in search_history[-5:]:  # Last 5 queries
        query = entry.get("query") if isinstance(entry, Mapping) else None
        if query:
            previous_queries.append(str(query))

    return RewriteContext(
        primary_document_title=doc_context.get("primary_document_title"),
        primary_upload_id=doc_context.get("primary_upload_id"),
        referenced_documents=tuple(doc_context.get("referenced_upload_ids", [])),
        previous_queries=tuple(previous_queries),
    )


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
    enabled = str(
        getattr(settings, "RAG_CONTEXT_QUERY_REWRITE_ENABLED", "true")
    ).lower() in {"1", "true", "yes"}

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
