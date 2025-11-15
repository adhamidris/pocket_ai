"""
Shared typing helpers for the MCP orchestrator stack.

Keeping these interfaces in a dedicated module lets us avoid circular imports
between the orchestrator, prompts, and tool dispatcher modules while the new
system takes shape.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Mapping, Sequence


JsonDict = Mapping[str, object]
Message = Mapping[str, object]


from apps.services.llm_provider import BaseMcpProvider


class ToolConstraintError(RuntimeError):
    """Raised when a tool invocation violates business constraints."""


class ChunkReadBudgetExceeded(ToolConstraintError):
    """Raised when a tool tries to exceed the configured chunk-read budget."""


@dataclasses.dataclass
class ToolExecutionContext:
    """
    Shared execution context for MCP tools.

    Carries per-turn limits (like chunk-read budgets) and any warnings that
    should be bubbled up to diagnostics when the orchestrator assembles the
    final response.
    """

    max_chunk_reads_per_turn: int | None = None
    chunk_reads_used: int = 0
    ingestion_warnings: list[JsonDict] = dataclasses.field(default_factory=list)
    knowledge_results: list[dict[str, object]] = dataclasses.field(default_factory=list)
    knowledge_reads: list[dict[str, object]] = dataclasses.field(default_factory=list)
    tool_trace: list[dict[str, object]] = dataclasses.field(default_factory=list)

    def reserve_chunk_reads(self, count: int) -> None:
        """Ensure the requested chunk reads do not exceed the per-turn budget."""

        if count <= 0:
            return
        if self.max_chunk_reads_per_turn is None:
            self.chunk_reads_used += count
            return
        projected = self.chunk_reads_used + count
        if projected > self.max_chunk_reads_per_turn:
            raise ChunkReadBudgetExceeded(
                f"Chunk read budget exceeded (requested {projected}, max {self.max_chunk_reads_per_turn})."
            )
        self.chunk_reads_used = projected

    def add_ingestion_warning(self, warning: Mapping[str, object]) -> None:
        """Record an ingestion warning so the orchestrator can surface it later."""

        self.ingestion_warnings.append(dict(warning))

    def add_knowledge_result(self, result: Mapping[str, object]) -> None:
        self.knowledge_results.append(dict(result))

    def add_knowledge_read(self, read: Mapping[str, object]) -> None:
        self.knowledge_reads.append(dict(read))

    def add_tool_trace(self, trace: Mapping[str, object]) -> None:
        self.tool_trace.append(dict(trace))
@dataclasses.dataclass
class KnowledgeToolResult:
    """Structured record of knowledge snippets returned by tool calls."""

    snippets: tuple[dict[str, object], ...] = dataclasses.field(default_factory=tuple)
    source_tool: str | None = None
    note: str | None = None
