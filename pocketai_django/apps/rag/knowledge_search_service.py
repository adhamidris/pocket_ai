"""
Compatibility bridge for the KnowledgeSearchService implementation.

The implementation lives in apps.rag.search.service. Keep this module small so
legacy imports and test patch paths such as
`apps.rag.knowledge_search_service.build_embedding_service` continue to work.
"""

from __future__ import annotations

from apps.rag.embeddings import build_embedding_service
from apps.rag.search.service import KnowledgeSearchService

__all__ = ["KnowledgeSearchService", "build_embedding_service"]
