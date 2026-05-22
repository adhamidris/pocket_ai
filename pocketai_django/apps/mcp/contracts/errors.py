from __future__ import annotations

class ToolConstraintError(RuntimeError):
    """Raised when a tool invocation violates business constraints."""


class ChunkReadBudgetExceeded(ToolConstraintError):
    """Raised when a tool tries to exceed the configured chunk-read budget."""


class ChunkPageBudgetExceeded(ToolConstraintError):
    """Raised when a tool exhausts the per-turn page window budget."""


class CharacterBudgetExceeded(ToolConstraintError):
    """Raised when the character/token budget is exhausted."""


class ToolRateLimitExceeded(ToolConstraintError):
    """Raised when a business/tool rate limit is exceeded."""


class SearchBudgetExceeded(ToolConstraintError):
    """Raised when search_knowledge calls per turn exceed the limit (Phase 4)."""


class ReadBudgetExceeded(ToolConstraintError):
    """Raised when read_knowledge calls per turn exceed the limit (future/agentic enforcement)."""


