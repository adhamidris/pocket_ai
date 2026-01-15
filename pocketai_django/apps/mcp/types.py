"""
Shared typing helpers for the MCP orchestrator stack.

Keeping these interfaces in a dedicated module lets us avoid circular imports
between the orchestrator, prompts, and tool dispatcher modules while the new
system takes shape.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Mapping, Sequence, Tuple


JsonDict = Mapping[str, object]
Message = Mapping[str, object]


from apps.llm.llm_provider import BaseMcpProvider


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
    
    @property
    def _effective_max_searches(self) -> int:
        """Get the effective max searches, checking Django settings first."""
        try:
            from django.conf import settings
            configured = getattr(settings, 'MCP_MAX_SEARCHES_PER_TURN', None)
            if configured is not None:
                return int(configured)
        except Exception:
            pass
        return self.max_searches_per_turn
    
    ingestion_warnings: list[JsonDict] = dataclasses.field(default_factory=list)
    knowledge_results: list[dict[str, object]] = dataclasses.field(default_factory=list)
    knowledge_reads: list[dict[str, object]] = dataclasses.field(default_factory=list)
    tool_trace: list[dict[str, object]] = dataclasses.field(default_factory=list)
    coverage_ledger: list[dict[str, object]] = dataclasses.field(default_factory=list)
    identifier_gate: object | None = None
    identifier_checks: list[dict[str, object]] = dataclasses.field(default_factory=list)
    identifier_filters: list[dict[str, object]] = dataclasses.field(default_factory=list)
    identifier_hashes: dict[str, str] = dataclasses.field(default_factory=dict)
    identifier_mapping_cache: dict[tuple[tuple[tuple[str, str], ...], str | None, str | None], dict[str, object]] = dataclasses.field(default_factory=dict)
    identifier_event_fingerprints: set[tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = dataclasses.field(default_factory=set)
    audit_event_fingerprints: set[tuple[str, str, str]] = dataclasses.field(default_factory=set)
    table_aggregate_rows: list[dict[str, object]] = dataclasses.field(default_factory=list)
    table_row_cache: dict[str, list[dict[str, object]]] = dataclasses.field(default_factory=dict)
    search_history: list[dict[str, object]] = dataclasses.field(default_factory=list)
    search_cache: dict[Tuple[str, int, str, str | None, str | None], dict[str, object]] = dataclasses.field(default_factory=dict)
    read_cache: dict[Tuple[str, int, str, int, int | None], dict[str, object]] = dataclasses.field(default_factory=dict)
    table_column_filters: dict[str, list[str]] = dataclasses.field(default_factory=dict)
    table_result_cache: dict[tuple, dict[str, object]] = dataclasses.field(default_factory=dict)
    table_result_cache_dirty: set[tuple] = dataclasses.field(default_factory=set)

    # Conversation-level seen-item tracking (for "are there more?" follow-ups)
    # IDs of chunks/snippets already shown to user in this conversation
    seen_chunk_ids: set[str] = dataclasses.field(default_factory=set)
    # IDs of table rows already shown (document_id:row_index format)
    seen_row_ids: set[str] = dataclasses.field(default_factory=set)
    # New chunks/rows shown THIS turn (will be persisted after turn)
    newly_shown_chunk_ids: set[str] = dataclasses.field(default_factory=set)
    newly_shown_row_ids: set[str] = dataclasses.field(default_factory=set)

    # =========================================================================
    # Document Context Tracking (Conversation-Aware RAG)
    # =========================================================================
    # Tracks which documents were referenced in search results for follow-up queries.
    # Enables query rewriting, document affinity routing, and ranking bonuses.

    # Set of all upload_ids referenced in this conversation
    referenced_upload_ids: set[str] = dataclasses.field(default_factory=set)

    # Primary document being discussed (most-referenced or most recent high-confidence)
    primary_upload_id: str | None = None

    # Detailed metadata per document: {upload_id: {"title": str, "search_count": int, ...}}
    document_context: dict[str, dict] = dataclasses.field(default_factory=dict)

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

    def reserve_search(self) -> None:
        """Ensure the search call does not exceed the per-turn search budget (Phase 4)."""
        
        effective_limit = self._effective_max_searches
        if effective_limit <= 0:
            # No limit configured (set MCP_MAX_SEARCHES_PER_TURN=0 to disable)
            self.searches_used += 1
            return
        
        self.searches_used += 1
        if self.searches_used > effective_limit:
            raise SearchBudgetExceeded(
                f"Search limit exceeded ({self.searches_used} calls this turn, max {effective_limit}). "
                "You have already searched the knowledge base this turn. Use read_knowledge to get more details "
                "from the snippets you received, or answer based on what you found."
            )

    def add_ingestion_warning(self, warning: Mapping[str, object]) -> None:
        """Record an ingestion warning so the orchestrator can surface it later."""

        self.ingestion_warnings.append(dict(warning))

    def add_knowledge_result(self, result: Mapping[str, object]) -> None:
        self.knowledge_results.append(dict(result))

    def add_knowledge_read(self, read: Mapping[str, object]) -> None:
        self.knowledge_reads.append(dict(read))

    def add_tool_trace(self, trace: Mapping[str, object]) -> None:
        self.tool_trace.append(dict(trace))

    def add_coverage_entry(self, entry: Mapping[str, object]) -> None:
        self.coverage_ledger.append(dict(entry))

    def mark_chunk_shown(self, chunk_id: str) -> None:
        """Record a chunk as shown to the user this turn."""
        if chunk_id:
            self.newly_shown_chunk_ids.add(str(chunk_id))

    def mark_row_shown(self, document_id: str, row_index: int) -> None:
        """Record a table row as shown to the user this turn."""
        if document_id is not None and row_index is not None:
            row_key = f"{document_id}:{row_index}"
            self.newly_shown_row_ids.add(row_key)

    def is_chunk_seen(self, chunk_id: str) -> bool:
        """Check if a chunk was already shown in a previous turn."""
        return str(chunk_id) in self.seen_chunk_ids if chunk_id else False

    def is_row_seen(self, document_id: str, row_index: int) -> bool:
        """Check if a table row was already shown in a previous turn."""
        if document_id is None or row_index is None:
            return False
        row_key = f"{document_id}:{row_index}"
        return row_key in self.seen_row_ids

    def get_all_shown_this_conversation(self) -> dict[str, set[str]]:
        """Return all items shown (previous turns + this turn) for persistence."""
        return {
            "chunk_ids": self.seen_chunk_ids | self.newly_shown_chunk_ids,
            "row_ids": self.seen_row_ids | self.newly_shown_row_ids,
        }

    # =========================================================================
    # Document Context Tracking Methods
    # =========================================================================

    def track_document_reference(
        self,
        upload_id: str,
        title: str | None = None,
        stage: str | None = None,
        confidence: float | None = None,
    ) -> None:
        """
        Track a document that was referenced in search results.

        Called after each search to build conversation-level document context.
        Updates primary_upload_id based on reference frequency and confidence.
        """
        if not upload_id:
            return

        upload_id = str(upload_id)
        self.referenced_upload_ids.add(upload_id)

        # Initialize or update document metadata
        if upload_id not in self.document_context:
            self.document_context[upload_id] = {
                "title": title or "",
                "search_count": 0,
                "stages": [],
                "confidences": [],
                "last_referenced_at": None,
            }

        meta = self.document_context[upload_id]
        meta["search_count"] = meta.get("search_count", 0) + 1
        if title and not meta.get("title"):
            meta["title"] = title
        if stage:
            stages = meta.get("stages", [])
            if stage not in stages:
                stages.append(stage)
            meta["stages"] = stages
        if confidence is not None:
            confidences = meta.get("confidences", [])
            confidences.append(float(confidence))
            # Keep only last 10 confidence scores
            meta["confidences"] = confidences[-10:]

        # Update primary document if this one has higher engagement
        self._update_primary_document(upload_id)

    def _update_primary_document(self, candidate_upload_id: str) -> None:
        """Update primary_upload_id based on document engagement metrics."""
        if not self.primary_upload_id:
            # First document referenced becomes primary
            self.primary_upload_id = candidate_upload_id
            return

        # Compare candidate with current primary
        candidate_meta = self.document_context.get(candidate_upload_id, {})
        primary_meta = self.document_context.get(self.primary_upload_id, {})

        candidate_count = candidate_meta.get("search_count", 0)
        primary_count = primary_meta.get("search_count", 0)

        # Switch primary if candidate has significantly more references
        if candidate_count > primary_count + 2:
            self.primary_upload_id = candidate_upload_id

    def get_primary_document_title(self) -> str | None:
        """Get the title of the primary document, if available."""
        if not self.primary_upload_id:
            return None
        meta = self.document_context.get(self.primary_upload_id, {})
        return meta.get("title") or None

    def get_document_context_for_query(self) -> dict:
        """
        Get document context dictionary for query rewriting and routing.

        Returns a dict suitable for passing to ContextAwareQueryRewriter.
        """
        return {
            "primary_upload_id": self.primary_upload_id,
            "primary_document_title": self.get_primary_document_title(),
            "referenced_upload_ids": list(self.referenced_upload_ids),
            "document_metadata": dict(self.document_context),
        }

    def get_document_context_for_persistence(self) -> dict:
        """
        Get document context for persistence to conversation.metadata.

        Returns a serializable dict that can be stored and rehydrated.
        """
        return {
            "primary_upload_id": self.primary_upload_id,
            "referenced_uploads": list(self.referenced_upload_ids)[-20:],  # Keep last 20
            "document_metadata": {
                k: {
                    "title": v.get("title", ""),
                    "search_count": v.get("search_count", 0),
                    "stages": v.get("stages", [])[-5:],  # Keep last 5 stages
                    # Don't persist confidences (transient)
                }
                for k, v in list(self.document_context.items())[-10:]  # Keep top 10 docs
            },
        }

    def hydrate_document_context(self, persisted: dict) -> None:
        """
        Restore document context from conversation.metadata.

        Called at the start of each turn to restore conversation state.
        """
        if not persisted:
            return

        self.primary_upload_id = persisted.get("primary_upload_id")
        self.referenced_upload_ids = set(persisted.get("referenced_uploads", []))

        doc_metadata = persisted.get("document_metadata", {})
        for upload_id, meta in doc_metadata.items():
            self.document_context[upload_id] = {
                "title": meta.get("title", ""),
                "search_count": meta.get("search_count", 0),
                "stages": meta.get("stages", []),
                "confidences": [],  # Not persisted
            }


@dataclasses.dataclass
class KnowledgeToolResult:
    """Structured record of knowledge snippets returned by tool calls."""

    snippets: tuple[dict[str, object], ...] = dataclasses.field(default_factory=tuple)
    source_tool: str | None = None
    note: str | None = None
