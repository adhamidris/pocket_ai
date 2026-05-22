"""
Compatibility exports for shared MCP contracts.

New code should import from the focused modules in this package. This bridge
keeps older imports stable while the codebase is being organized.
"""

from __future__ import annotations

from typing import Mapping

from apps.llm.llm_provider import BaseMcpProvider

from .errors import (
    CharacterBudgetExceeded,
    ChunkPageBudgetExceeded,
    ChunkReadBudgetExceeded,
    ReadBudgetExceeded,
    SearchBudgetExceeded,
    ToolConstraintError,
    ToolRateLimitExceeded,
)
from .execution_context import ToolExecutionContext
from .knowledge import KnowledgeToolResult


JsonDict = Mapping[str, object]
Message = Mapping[str, object]

__all__ = [
    "BaseMcpProvider",
    "CharacterBudgetExceeded",
    "ChunkPageBudgetExceeded",
    "ChunkReadBudgetExceeded",
    "JsonDict",
    "KnowledgeToolResult",
    "Message",
    "ReadBudgetExceeded",
    "SearchBudgetExceeded",
    "ToolConstraintError",
    "ToolExecutionContext",
    "ToolRateLimitExceeded",
]
