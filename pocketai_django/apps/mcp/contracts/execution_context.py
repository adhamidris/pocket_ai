from __future__ import annotations

import dataclasses
from typing import Callable, Mapping, Tuple

from .context_budget import ToolContextBudgetMixin
from .context_documents import ToolContextDocumentsMixin
from .context_recent_refs import ToolContextRecentRefsMixin
from .context_refinement import ToolContextRefinementMixin
from .context_usage import ToolContextUsageMixin
from .context_visibility import ToolContextVisibilityMixin


JsonDict = Mapping[str, object]

@dataclasses.dataclass
class ToolExecutionContext(
    ToolContextBudgetMixin,
    ToolContextUsageMixin,
    ToolContextVisibilityMixin,
    ToolContextRecentRefsMixin,
    ToolContextDocumentsMixin,
    ToolContextRefinementMixin,
):
    """
    Shared execution context for MCP tools.

    Carries per-turn limits (like chunk-read budgets) and any warnings that
    should be bubbled up to diagnostics when the orchestrator assembles the
    final response.
    """

    max_chunk_reads_per_turn: int | None = None
    chunk_reads_used: int = 0
    max_chunk_pages_per_turn: int | None = None
    chunk_pages_used: int = 0
    char_budget_per_turn: int | None = None
    characters_used: int = 0
    char_budget_per_minute: int | None = None
    minute_budget_reserver: Callable[[int], None] | None = None
    
    # NEW: Search call enforcement (Phase 4)
    # Configurable via settings.MCP_MAX_SEARCHES_PER_TURN (default: 2)
    max_searches_per_turn: int = 2
    searches_used: int = 0

    # NEW: Read call tracking (agentic RAG budgeting)
    # Configurable via settings.MCP_MAX_READS_PER_TURN (default: 10)
    max_reads_per_turn: int = 10
    reads_used: int = 0

    ingestion_warnings: list[JsonDict] = dataclasses.field(default_factory=list)
    retrieval_candidates: list[dict[str, object]] = dataclasses.field(default_factory=list)
    model_visible_refs: list[dict[str, object]] = dataclasses.field(default_factory=list)
    read_evidence: list[dict[str, object]] = dataclasses.field(default_factory=list)
    knowledge_results: list[dict[str, object]] = dataclasses.field(default_factory=list)
    knowledge_reads: list[dict[str, object]] = dataclasses.field(default_factory=list)
    tool_trace: list[dict[str, object]] = dataclasses.field(default_factory=list)
    # Gateway-mode external MCP tool catalog (per turn).
    # tool_id -> {connection_id, connection_name, remote_tool, description, input_schema}
    mcp_gateway_catalog: dict[str, dict[str, object]] = dataclasses.field(default_factory=dict)
    coverage_ledger: list[dict[str, object]] = dataclasses.field(default_factory=list)
    audit_event_fingerprints: set[tuple[str, str, str]] = dataclasses.field(default_factory=set)
    table_row_cache: dict[str, list[dict[str, object]]] = dataclasses.field(default_factory=dict)
    search_history: list[dict[str, object]] = dataclasses.field(default_factory=list)
    search_cache: dict[Tuple[str, int, str, str | None, str | None], dict[str, object]] = dataclasses.field(default_factory=dict)
    read_cache: dict[Tuple[str, int, str, int, int | None], dict[str, object]] = dataclasses.field(default_factory=dict)
    # Most recent search_knowledge refs carried across turns so follow-up
    # read_knowledge calls can reuse exact IDs instead of guessing.
    recent_search_refs: list[dict[str, object]] = dataclasses.field(default_factory=list)
    recent_search_refs_updated: bool = False
    # Search-time table row anchor manifests keyed by promoted table ref id.
    # Used by read_knowledge v2 to start first table reads near matched rows.
    table_row_anchor_manifests: dict[str, dict[str, object]] = dataclasses.field(default_factory=dict)
    # Opaque cursor handles exposed to the model for read_knowledge continuation.
    # Maps handle -> signed cursor payload, and reverse map for stable reuse.
    read_cursor_handles: dict[str, str] = dataclasses.field(default_factory=dict)
    read_cursor_reverse_handles: dict[str, str] = dataclasses.field(default_factory=dict)
    # Opaque cursor handles exposed to the model for search_knowledge pagination.
    # Search still stores signed cursors internally, but the model sees short handles.
    search_cursor_handles: dict[str, str] = dataclasses.field(default_factory=dict)
    search_cursor_reverse_handles: dict[str, str] = dataclasses.field(default_factory=dict)
    llm_usage: dict[str, int] = dataclasses.field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    llm_usage_entries: list[dict[str, object]] = dataclasses.field(default_factory=list)
    # Prompt size telemetry per provider call (safe: counts/estimates only, no raw prompt text).
    prompt_budget_entries: list[dict[str, object]] = dataclasses.field(default_factory=list)
    preplan: dict[str, object] | None = None
    verification: dict[str, object] | None = None

    # Conversation-level seen-item tracking (for "are there more?" follow-ups)
    # IDs of chunks/snippets already shown to user in this conversation
    seen_chunk_ids: set[str] = dataclasses.field(default_factory=set)
    # IDs of table rows already shown (document_id:row_index format)
    seen_row_ids: set[str] = dataclasses.field(default_factory=set)
    # New chunks/rows shown THIS turn (will be persisted after turn)
    newly_shown_chunk_ids: set[str] = dataclasses.field(default_factory=set)
    newly_shown_row_ids: set[str] = dataclasses.field(default_factory=set)

    # Ref IDs already read this turn (for repeat-read detection / loop prevention)
    read_ref_ids_this_turn: set[str] = dataclasses.field(default_factory=set)
    # Tracks unresolved/non-UUID read ref attempts within the current turn so
    # wrapper-level guardrails can stop retry loops quickly.
    invalid_read_ref_attempts: dict[str, int] = dataclasses.field(default_factory=dict)
    # =========================================================================
    # Document Reference Tracking
    # =========================================================================
    # Tracks documents referenced/read for debug and persistence only.
    # This must not drive implicit query rewriting, document affinity routing, or
    # ranking bonuses.

    # Set of all upload_ids referenced in this conversation
    referenced_upload_ids: set[str] = dataclasses.field(default_factory=set)

    # Primary document being discussed (most-referenced or most recent high-confidence)
    primary_upload_id: str | None = None

    # Detailed metadata per document: {upload_id: {"title": str, "search_count": int, ...}}
    document_context: dict[str, dict] = dataclasses.field(default_factory=dict)

    # =========================================================================
    # Query Refinement Tracking (Phase 4: Multi-Turn Refinement Loop)
    # =========================================================================
    # Tracks query refinement attempts to enable multi-turn refinement and
    # provide context to the LLM about what refinements have been tried.

    # List of refinement attempts: [{"original": str, "refined": str, "reason": str, "verdict": str}, ...]
    refinement_history: list[dict[str, object]] = dataclasses.field(default_factory=list)

    # Max refinements allowed per original query (prevents infinite loops)
    max_refinements_per_query: int = 2

    # Latest user message for current turn.
    latest_user_message: str | None = None
    
