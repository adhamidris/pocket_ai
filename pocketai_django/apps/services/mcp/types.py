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


class ChunkPageBudgetExceeded(ToolConstraintError):
    """Raised when a tool exhausts the per-turn page window budget."""


class CharacterBudgetExceeded(ToolConstraintError):
    """Raised when the character/token budget is exhausted."""


@dataclasses.dataclass
class ToolExecutionContext:
    """
    Shared execution context for MCP tools.

    Carries per-turn limits (like chunk-read budgets) and any warnings that
    should be bubbled up to diagnostics when the orchestrator assembles the
    final response.
    
    This is the "state bag" that flows through all tool calls in a single turn.
    It accumulates:
        - Knowledge results (snippets from search/read/aggregate)
        - Tool traces (diagnostics per tool call)
        - Coverage ledger (what knowledge parts were covered)
        - Budget usage (chunk reads, pages, characters consumed)
        - Identifier gates (security decisions)
        - Ingestion warnings (partial/risky data flags)
        - Table cache (cached aggregate rows for reuse)
        - Search history (for duplicate search short-circuiting)
    
    Why shared context:
        - Tools need to know what's already been read/searched
        - Budgets must be enforced across all tools in a turn
        - Identifier gates must be consistent across search/read calls
        - Coverage ledger helps model understand what's available
        
    Used by:
        - McpOrchestratorService._execute_turn (created at turn start)
        - All MCP tool handlers (search_knowledge, read_document, table_aggregate)
        - Planner pass (for tool context note)
    """

    max_chunk_reads_per_turn: int | None = None
    chunk_reads_used: int = 0
    max_chunk_pages_per_turn: int | None = None
    chunk_pages_used: int = 0
    char_budget_per_turn: int | None = None
    characters_used: int = 0
    char_budget_per_minute: int | None = None
    minute_budget_reserver: Callable[[int], None] | None = None
    ingestion_warnings: list[JsonDict] = dataclasses.field(default_factory=list)
    knowledge_results: list[dict[str, object]] = dataclasses.field(default_factory=list)
    knowledge_reads: list[dict[str, object]] = dataclasses.field(default_factory=list)
    tool_trace: list[dict[str, object]] = dataclasses.field(default_factory=list)
    coverage_ledger: list[dict[str, object]] = dataclasses.field(default_factory=list)
    identifier_gate: object | None = None
    identifier_checks: list[dict[str, object]] = dataclasses.field(default_factory=list)
    identifier_filters: list[dict[str, object]] = dataclasses.field(default_factory=list)
    identifier_hashes: dict[str, str] = dataclasses.field(default_factory=dict)
    table_aggregate_rows: list[dict[str, object]] = dataclasses.field(default_factory=list)
    table_row_cache: dict[str, list[dict[str, object]]] = dataclasses.field(default_factory=dict)
    search_history: list[dict[str, object]] = dataclasses.field(default_factory=list)

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

    def reserve_chunk_pages(self, count: int) -> None:
        if count <= 0:
            return
        if self.max_chunk_pages_per_turn is None:
            self.chunk_pages_used += count
            return
        projected = self.chunk_pages_used + count
        if projected > self.max_chunk_pages_per_turn:
            raise ChunkPageBudgetExceeded(
                f"Chunk page budget exceeded (requested {projected}, max {self.max_chunk_pages_per_turn})."
            )
        self.chunk_pages_used = projected

    def reserve_characters(self, count: int) -> None:
        if count <= 0:
            return
        if self.char_budget_per_turn is not None:
            projected = self.characters_used + count
            if projected > self.char_budget_per_turn:
                raise CharacterBudgetExceeded(
                    f"Character budget exceeded (requested {projected}, max {self.char_budget_per_turn})."
                )
            self.characters_used = projected
        if self.char_budget_per_minute and self.minute_budget_reserver:
            self.minute_budget_reserver(count)

    def add_ingestion_warning(self, warning: Mapping[str, object]) -> None:
        """
        Record an ingestion warning so the orchestrator can surface it later.
        
        Warnings indicate potential data quality issues (partial uploads, risky content, etc.).
        These are surfaced in message metadata and diagnostics for transparency.
        
        Why warnings:
        - Alerts user to potential data limitations
        - Helps debug knowledge base issues
        - Provides context for answer quality
        """
        self.ingestion_warnings.append(dict(warning))

    def add_knowledge_result(self, result: Mapping[str, object]) -> None:
        """
        Add knowledge snippet to results (from search/read/aggregate tools).
        
        Knowledge results are used to:
        - Build citations in final answer
        - Provide context to planner pass
        - Track what knowledge was accessed
        """
        self.knowledge_results.append(dict(result))

    def add_knowledge_read(self, read: Mapping[str, object]) -> None:
        """
        Record a knowledge read operation (which document/page was read).
        
        Tracks actual reads (not just search results) for:
        - Coverage ledger (what's been read)
        - Budget tracking (read counts)
        - Planner context (what knowledge is available)
        """
        self.knowledge_reads.append(dict(read))

    def add_tool_trace(self, trace: Mapping[str, object]) -> None:
        """
        Record tool call diagnostics for observability.
        
        Tool traces include:
        - Tool name, arguments, status
        - Error codes, hints, modes
        - Token budgets, throttle flags
        - Duration, result counts
        
        Used for:
        - Debugging tool call issues
        - Performance monitoring
        - Answer quality diagnostics
        """
        self.tool_trace.append(dict(trace))

    def add_coverage_entry(self, entry: Mapping[str, object]) -> None:
        """
        Add entry to coverage ledger (high-level view of knowledge coverage).
        
        Coverage ledger helps model understand:
        - What knowledge parts have been accessed
        - What's available but not yet read
        - Coverage gaps (missing information)
        
        Used in planner pass to provide context about knowledge availability.
        """
        self.coverage_ledger.append(dict(entry))
@dataclasses.dataclass
class KnowledgeToolResult:
    """
    Structured record of knowledge snippets returned by tool calls.
    
    Returned by knowledge tools (search_knowledge, read_document, table_aggregate)
    to provide consistent structure for orchestrator processing.
    
    Fields:
        - snippets: Tuple of snippet dicts (immutable, prevents accidental mutation)
        - source_tool: Tool name that produced these snippets (for diagnostics)
        - note: Optional note about the results (e.g., "read_required", "throttled")
    
    Why dataclass:
        - Type safety (structured data vs raw dicts)
        - Immutable snippets (prevents accidental mutation)
        - Clear contract for tool return values
        - Easy to extend with new fields
    
    Used by:
        - Tool handlers (return KnowledgeToolResult)
        - Orchestrator (processes results, builds citations)
        - Planner pass (includes in tool context note)
    """
    snippets: tuple[dict[str, object], ...] = dataclasses.field(default_factory=tuple)
    source_tool: str | None = None
    note: str | None = None
