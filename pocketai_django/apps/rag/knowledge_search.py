from __future__ import annotations

from apps.rag.contracts import (
    AliasSearchResult,
    ChunkResult,
    HybridSearchResult,
    KnowledgeSearchResult,
    KnowledgeSnippet,
    QueryTraits,
)
from apps.rag.knowledge_search_service import KnowledgeSearchService
from apps.rag.query_normalizer import QueryNormalizer

__all__ = [
    "AliasSearchResult",
    "ChunkResult",
    "HybridSearchResult",
    "KnowledgeSearchResult",
    "KnowledgeSearchService",
    "KnowledgeSnippet",
    "QueryNormalizer",
    "QueryTraits",
]
