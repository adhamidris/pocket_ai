from __future__ import annotations

from dataclasses import dataclass

from apps.rag.lexicon.tenant import normalize_lexicon_text, tokenize_lexicon_text

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

_GENERIC_CONTEXT_TOKENS = {
    "amount",
    "amounts",
    "charge",
    "charges",
    "cost",
    "costs",
    "fee",
    "fees",
    "price",
    "prices",
    "pricing",
    "rate",
    "rates",
    "tariff",
    "tariffs",
    "رسوم",
}


def _tokenize(text: str) -> list[str]:
    normalized = normalize_lexicon_text(text)
    base = normalized if normalized else (text or "").lower()
    return [token for token in tokenize_lexicon_text(base, max_tokens=256) if token]


def _significant_tokens(text: str) -> set[str]:
    return {token for token in _tokenize(text) if len(token) >= 3 and token not in _STOPWORDS}


def _context_overlap_tokens(text: str) -> set[str]:
    return _significant_tokens(text) - _GENERIC_CONTEXT_TOKENS


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
    reason: str = ""

    def as_dict(self) -> dict:
        """Convert to dictionary for logging/serialization."""
        return {
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "context_injected": self.context_injected,
            "rewrite_strategy": self.rewrite_strategy,
            "confidence": self.confidence,
            "reason": self.reason,
        }
