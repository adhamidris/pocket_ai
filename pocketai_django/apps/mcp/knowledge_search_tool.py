"""
search_knowledge MCP tool handler compatibility bridge.
"""

from __future__ import annotations

from .knowledge_search.handler import (
    _coerce_str,
    _knowledge_service,
    _portal_file_embedding_service,
    _search_knowledge_handler,
)

__all__ = [
    "_coerce_str",
    "_knowledge_service",
    "_portal_file_embedding_service",
    "_search_knowledge_handler",
]
