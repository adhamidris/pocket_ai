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
from dataclasses import dataclass
from typing import Mapping, Sequence

from django.conf import settings

from apps.rag.tenant_lexicon import TenantLexiconService, normalize_lexicon_text, tokenize_lexicon_text

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "show",
    "tell",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "to",
    "what",
    "with",
    "you",
    "your",
    "ما",
    "ماذا",
    "كيف",
    "من",
    "في",
    "على",
    "عن",
    "الى",
    "إلى",
    "او",
    "أو",
    "و",
    "هذا",
    "هذه",
    "ذلك",
    "تلك",
    "كل",
    "جميع",
}


def _tokenize(text: str) -> list[str]:
    normalized = normalize_lexicon_text(text)
    base = normalized if normalized else (text or "").lower()
    return [token for token in tokenize_lexicon_text(base, max_tokens=256) if token]


def _significant_tokens(text: str) -> set[str]:
    return {token for token in _tokenize(text) if len(token) >= 3 and token not in _STOPWORDS}


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

    # Tenant-scoped lexicon hints (Phase 4).
    tenant_entity_terms: tuple[str, ...] = ()
    tenant_attribute_terms: tuple[str, ...] = ()


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
        tokens = _tokenize(query)
        token_count = len(tokens)
        query_significant = {token for token in tokens if len(token) >= 3 and token not in _STOPWORDS}

        # Short queries are more likely to be follow-ups, but shortness alone is not enough.
        if token_count <= self.MAX_FOLLOWUP_QUERY_LENGTH:
            confidence += 0.10
        if token_count <= 6:
            confidence += 0.10
        if token_count <= 3:
            confidence += 0.10

        # Check for explicit follow-up indicators
        for pattern in self._followup_patterns:
            if pattern.search(query):
                confidence += 0.35
                break

        # Check for pronouns that reference previous context
        for pattern in self._pronoun_patterns:
            if pattern.search(query):
                confidence += 0.25
                break

        title_overlap = 0
        if context.primary_document_title:
            title_tokens = _significant_tokens(context.primary_document_title)
            title_overlap = len(title_tokens & query_significant) if title_tokens else 0
            if title_overlap >= 2:
                confidence += 0.25
            elif title_overlap == 1:
                confidence += 0.15

        prev_overlap = 0
        if context.previous_queries:
            prev_tokens = _significant_tokens(context.previous_queries[-1])
            prev_overlap = len(prev_tokens & query_significant) if prev_tokens else 0
            if prev_overlap >= 2:
                confidence += 0.25
            elif prev_overlap == 1:
                confidence += 0.15

        lexicon_overlap = self._lexicon_overlap(query_significant, context)
        if lexicon_overlap >= 2:
            confidence += 0.2
        elif lexicon_overlap == 1:
            confidence += 0.1

        # Guardrail: avoid rewriting "new topic" short queries with no overlap or markers.
        # After a document is read, the system also applies document affinity search; we
        # keep rewriting conservative to prevent over-biasing unrelated queries.
        if confidence >= self.min_confidence and token_count <= self.MAX_FOLLOWUP_QUERY_LENGTH:
            has_marker = any(pattern.search(query) for pattern in self._followup_patterns) or any(
                pattern.search(query) for pattern in self._pronoun_patterns
            )
            if not has_marker and title_overlap == 0 and prev_overlap == 0 and lexicon_overlap == 0:
                confidence = min(confidence, self.min_confidence - 0.01)

        is_followup = confidence >= self.min_confidence
        return is_followup, min(confidence, 1.0)

    @staticmethod
    def _lexicon_overlap(query_significant: set[str], context: RewriteContext) -> int:
        if not query_significant:
            return 0

        def _term_tokens(values: Sequence[str]) -> set[str]:
            tokens: set[str] = set()
            for value in values:
                for token in _tokenize(str(value or "")):
                    if len(token) >= 3 and token not in _STOPWORDS:
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


def build_rewrite_context_from_tool_context(tool_context, *, conversation=None) -> RewriteContext:
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

    previous_queries: list[str] = []

    # Prefer conversation-level customer messages so follow-up detection works across turns.
    if conversation is not None:
        try:
            from apps.conversations.models import ConversationSender

            raw_messages = list(
                conversation.messages.filter(sender=ConversationSender.CUSTOMER)
                .order_by("-sent_at", "-created_at")
                .values_list("body", flat=True)[:6]
            )
            raw_messages.reverse()  # chronological
            if raw_messages:
                # Drop the most recent customer message (current query) so we only use prior context.
                raw_messages = raw_messages[:-1]
            for body in raw_messages[-5:]:
                if isinstance(body, str) and body.strip():
                    previous_queries.append(body.strip())
        except Exception:
            previous_queries = []

    # Fallback to in-turn search history when conversation messages are unavailable.
    if not previous_queries:
        search_history = getattr(tool_context, "search_history", [])
        for entry in search_history[-5:]:  # Last 5 queries
            query = entry.get("query") if isinstance(entry, Mapping) else None
            if query:
                previous_queries.append(str(query))

    tenant_entity_terms: tuple[str, ...] = ()
    tenant_attribute_terms: tuple[str, ...] = ()
    business_profile = getattr(conversation, "business_profile", None) if conversation is not None else None
    if business_profile is not None:
        try:
            snapshot = TenantLexiconService().get_snapshot(business_profile=business_profile, use_cache=True)
            if isinstance(snapshot, Mapping):
                entity_values = snapshot.get("entity_terms")
                if isinstance(entity_values, (list, tuple, set)):
                    tenant_entity_terms = tuple(str(value).strip() for value in entity_values if str(value).strip())[:200]
                attribute_values = snapshot.get("attribute_terms")
                if isinstance(attribute_values, (list, tuple, set)):
                    tenant_attribute_terms = tuple(
                        str(value).strip() for value in attribute_values if str(value).strip()
                    )[:200]
        except Exception:
            tenant_entity_terms = ()
            tenant_attribute_terms = ()

    return RewriteContext(
        primary_document_title=doc_context.get("primary_document_title"),
        primary_upload_id=doc_context.get("primary_upload_id"),
        referenced_documents=tuple(doc_context.get("referenced_upload_ids", [])),
        previous_queries=tuple(previous_queries),
        tenant_entity_terms=tenant_entity_terms,
        tenant_attribute_terms=tenant_attribute_terms,
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
