"""
Shared typing helpers for the MCP orchestrator stack.

Keeping these interfaces in a dedicated module lets us avoid circular imports
between the orchestrator, prompts, and tool dispatcher modules while the new
system takes shape.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone as dt_timezone
import uuid
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


class ReadBudgetExceeded(ToolConstraintError):
    """Raised when read_knowledge calls per turn exceed the limit (future/agentic enforcement)."""


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

    # NEW: Read call tracking (agentic RAG budgeting)
    # Configurable via settings.MCP_MAX_READS_PER_TURN (default: 10)
    max_reads_per_turn: int = 10
    reads_used: int = 0
    
    @property
    def _effective_max_searches(self) -> int:
        """Get the effective max searches, checking Django settings first."""
        explicit_value = self._explicit_budget_value("max_searches_per_turn")
        if explicit_value is not None:
            return explicit_value
        try:
            from django.conf import settings
            configured = getattr(settings, 'MCP_MAX_SEARCHES_PER_TURN', None)
            if configured is not None:
                return int(configured)
        except Exception:
            pass
        return self.max_searches_per_turn

    @property
    def _effective_max_reads(self) -> int:
        """Get the effective max reads, checking Django settings first."""
        explicit_value = self._explicit_budget_value("max_reads_per_turn")
        if explicit_value is not None:
            return explicit_value
        try:
            from django.conf import settings
            configured = getattr(settings, "MCP_MAX_READS_PER_TURN", None)
            if configured is not None:
                return int(configured)
        except Exception:
            pass
        return self.max_reads_per_turn

    def _explicit_budget_value(self, field_name: str) -> int | None:
        """
        Return an instance-level budget override when the caller supplied one.

        Runtime contexts normally use dataclass defaults and should follow
        Django settings. Tests and focused callers may pass a lower limit
        directly; that must not be silently widened by global settings.
        """

        try:
            field_info = self.__dataclass_fields__[field_name]  # type: ignore[attr-defined]
            default_value = field_info.default
            current_value = getattr(self, field_name)
            if current_value != default_value:
                return int(current_value)
        except Exception:
            return None
        return None
    
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

        projected = self.searches_used + 1
        if projected > effective_limit:
            raise SearchBudgetExceeded(
                f"Search limit exceeded ({projected} calls this turn, max {effective_limit}). "
                "Do not call search_knowledge again this turn. Use read_knowledge on the refs or snippets you received; "
                "if the existing evidence is enough, answer from it; otherwise ask one concise clarification question."
            )
        self.searches_used = projected

    def reserve_read(self) -> None:
        """
        Track a read_knowledge call against the per-turn read budget.

        Layer 2 uses this only for budgeting visibility (tool responses). Enforcement
        can be enabled later without changing the budget contract.
        """
        projected = self.reads_used + 1
        try:
            from django.conf import settings
            enforce = bool(getattr(settings, "MCP_ENFORCE_READ_BUDGET", False))
        except Exception:
            enforce = False
        if not enforce:
            self.reads_used = projected
            return

        effective_limit = self._effective_max_reads
        # No limit configured (set MCP_MAX_READS_PER_TURN=0 to disable)
        if effective_limit > 0 and projected > effective_limit:
            raise ReadBudgetExceeded(
                f"Read limit exceeded ({projected} calls this turn, max {effective_limit}). "
                "Batch IDs into a single read_knowledge call and answer from collected evidence."
            )
        self.reads_used = projected

    def budget_snapshot(self) -> dict[str, object]:
        """
        Lightweight per-turn budget telemetry for the LLM.

        This is intentionally small and stable. Tool responses should embed this
        object so the model can plan within limits without relying on injected
        system messages.
        """

        searches_used = int(getattr(self, "searches_used", 0) or 0)
        reads_used = int(getattr(self, "reads_used", 0) or 0)

        max_searches = int(self._effective_max_searches or 0)
        max_reads = int(self._effective_max_reads or 0)

        searches_remaining: int | None = None
        if max_searches > 0:
            searches_remaining = max(0, max_searches - searches_used)

        reads_remaining: int | None = None
        if max_reads > 0:
            reads_remaining = max(0, max_reads - reads_used)

        chars_used = int(getattr(self, "characters_used", 0) or 0)
        chars_budget: int | None = None
        if getattr(self, "char_budget_per_turn", None) is not None:
            try:
                chars_budget = int(getattr(self, "char_budget_per_turn") or 0)
            except (TypeError, ValueError):
                chars_budget = None

        warnings: list[str] = []
        next_action: str | None = None
        if searches_remaining == 0:
            warnings.append(
                "Search budget exhausted. Do not call search_knowledge again this turn; use read_knowledge on existing refs, answer from available evidence, or ask one concise clarification question."
            )
            next_action = "read_existing_refs_or_answer_or_ask_clarification"
        if searches_remaining == 1:
            warnings.append(
                "Last search available this turn. Prefer read_knowledge on current refs before spending it; use it only for a true new topic or clearly missing evidence."
            )
            next_action = "read_existing_refs_before_another_search"
        if reads_remaining == 1:
            warnings.append("Last read available this turn. Batch ids carefully.")
        warning = " ".join(warnings) if warnings else None

        snapshot = {
            "searches_used": searches_used,
            "searches_remaining": searches_remaining,
            "reads_used": reads_used,
            "reads_remaining": reads_remaining,
            "chars_used": chars_used,
            "chars_budget": chars_budget,
            "warning": warning,
        }
        if next_action:
            snapshot["next_action"] = next_action
        return snapshot

    def record_llm_usage(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        stage: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> None:
        self.llm_usage["prompt_tokens"] = int(self.llm_usage.get("prompt_tokens", 0)) + max(0, prompt_tokens)
        self.llm_usage["completion_tokens"] = int(self.llm_usage.get("completion_tokens", 0)) + max(0, completion_tokens)
        self.llm_usage["total_tokens"] = int(self.llm_usage.get("total_tokens", 0)) + max(0, total_tokens)
        entry: dict[str, object] = {
            "prompt_tokens": max(0, prompt_tokens),
            "completion_tokens": max(0, completion_tokens),
            "total_tokens": max(0, total_tokens),
        }
        if stage:
            entry["stage"] = stage
        if model:
            entry["model"] = model
        if provider:
            entry["provider"] = provider
        self.llm_usage_entries.append(entry)

    def add_prompt_budget_entry(self, entry: Mapping[str, object]) -> int:
        """Record prompt-size telemetry for a single provider call (no raw text)."""

        self.prompt_budget_entries.append(dict(entry))
        if len(self.prompt_budget_entries) > 25:
            self.prompt_budget_entries = self.prompt_budget_entries[-25:]
        return len(self.prompt_budget_entries) - 1

    def update_prompt_budget_entry(self, index: int, patch: Mapping[str, object]) -> None:
        """Patch an existing prompt telemetry entry (best-effort)."""

        if index < 0 or index >= len(self.prompt_budget_entries):
            return
        current = self.prompt_budget_entries[index]
        if not isinstance(current, dict):
            current = {}
            self.prompt_budget_entries[index] = current
        current.update(dict(patch))

    def add_ingestion_warning(self, warning: Mapping[str, object]) -> None:
        """Record an ingestion warning so the orchestrator can surface it later."""

        self.ingestion_warnings.append(dict(warning))

    def add_retrieval_candidate(self, result: Mapping[str, object]) -> None:
        entry = dict(result)
        identity = (
            str(entry.get("id") or entry.get("chunk_id") or "").strip(),
            str(entry.get("search_stage") or "").strip(),
            str(entry.get("upload_id") or "").strip(),
        )
        for existing in self.retrieval_candidates:
            existing_identity = (
                str(existing.get("id") or existing.get("chunk_id") or "").strip(),
                str(existing.get("search_stage") or "").strip(),
                str(existing.get("upload_id") or "").strip(),
            )
            if existing_identity == identity:
                return
        self.retrieval_candidates.append(entry)

    def add_model_visible_ref(self, result: Mapping[str, object]) -> None:
        entry = dict(result)
        identity = (
            str(entry.get("id") or "").strip(),
            str(entry.get("document_id") or entry.get("upload_id") or "").strip(),
            str(entry.get("kind") or entry.get("type") or "").strip(),
        )
        for existing in self.model_visible_refs:
            existing_identity = (
                str(existing.get("id") or "").strip(),
                str(existing.get("document_id") or existing.get("upload_id") or "").strip(),
                str(existing.get("kind") or existing.get("type") or "").strip(),
            )
            if existing_identity == identity:
                return
        self.model_visible_refs.append(entry)
        self.knowledge_results.append(dict(entry))

    def add_read_evidence(self, evidence: Mapping[str, object]) -> None:
        entry = dict(evidence)
        identity = (
            str(entry.get("id") or "").strip(),
            str(entry.get("type") or "").strip(),
            str(entry.get("next_cursor") or "").strip(),
        )
        for existing in self.read_evidence:
            existing_identity = (
                str(existing.get("id") or "").strip(),
                str(existing.get("type") or "").strip(),
                str(existing.get("next_cursor") or "").strip(),
            )
            if existing_identity == identity:
                return
        self.read_evidence.append(entry)

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
    # Recent Search Ref Tracking (Cross-Turn Read Continuity)
    # =========================================================================

    @staticmethod
    def _clip_text(value: object, *, limit: int) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: max(1, limit - 3)].rstrip() + "..."

    @staticmethod
    def _normalize_recent_ref_entry(ref: Mapping[str, object]) -> dict[str, object] | None:
        ref_id = str(ref.get("id") or "").strip()
        if not ref_id:
            return None
        normalized: dict[str, object] = {"id": ref_id}
        label = str(ref.get("label") or ref.get("title") or "").strip()
        if label:
            normalized["label"] = ToolExecutionContext._clip_text(label, limit=180)
        document = str(ref.get("document") or ref.get("document_name") or "").strip()
        if document:
            normalized["document"] = ToolExecutionContext._clip_text(document, limit=180)
        kind = str(ref.get("kind") or "").strip().lower()
        if kind:
            normalized["kind"] = kind
        ref_type = str(ref.get("type") or "").strip().lower()
        if ref_type:
            normalized["type"] = ref_type
        document_id = str(ref.get("document_id") or ref.get("upload_id") or "").strip()
        if document_id:
            normalized["document_id"] = document_id
        preview = str(ref.get("preview") or "").strip()
        if preview:
            normalized["preview"] = ToolExecutionContext._clip_text(preview, limit=220)
        return normalized

    def set_recent_search_refs(self, refs: Sequence[Mapping[str, object]] | None, *, limit: int = 12) -> None:
        normalized: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for item in refs or ():
            if not isinstance(item, Mapping):
                continue
            normalized_item = self._normalize_recent_ref_entry(item)
            if not normalized_item:
                continue
            ref_id = str(normalized_item.get("id") or "").strip()
            if not ref_id or ref_id in seen_ids:
                continue
            seen_ids.add(ref_id)
            normalized.append(normalized_item)
            if len(normalized) >= max(1, int(limit)):
                break
        self.recent_search_refs = normalized
        self.recent_search_refs_updated = True

    def get_recent_search_refs_for_persistence(self) -> dict[str, object]:
        refs = [dict(item) for item in (self.recent_search_refs or [])][:12]
        return {"refs": refs}

    def hydrate_recent_search_refs(self, persisted: Mapping[str, object] | Sequence[Mapping[str, object]] | None) -> None:
        refs_payload: Sequence[Mapping[str, object]] | None = None
        if isinstance(persisted, Mapping):
            refs = persisted.get("refs")
            if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes, bytearray)):
                refs_payload = refs  # type: ignore[assignment]
        elif isinstance(persisted, Sequence) and not isinstance(persisted, (str, bytes, bytearray)):
            refs_payload = persisted  # type: ignore[assignment]

        normalized: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for item in refs_payload or ():
            if not isinstance(item, Mapping):
                continue
            normalized_item = self._normalize_recent_ref_entry(item)
            if not normalized_item:
                continue
            ref_id = str(normalized_item.get("id") or "").strip()
            if not ref_id or ref_id in seen_ids:
                continue
            seen_ids.add(ref_id)
            normalized.append(normalized_item)
            if len(normalized) >= 12:
                break
        self.recent_search_refs = normalized
        self.recent_search_refs_updated = False

    # =========================================================================
    # Document Context Tracking Methods
    # =========================================================================

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(tz=dt_timezone.utc).isoformat()

    def has_strong_primary_document(self) -> bool:
        """
        Document continuity is disabled; there is no implicit primary document.
        """
        return False

    def track_document_reference(
        self,
        upload_id: str,
        title: str | None = None,
        stage: str | None = None,
        confidence: float | None = None,
        *,
        update_primary: bool = True,
    ) -> None:
        """
        Track a document that was referenced in search results.

        Called after each search for debug/reference bookkeeping only.
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
                "read_count": 0,
                "stages": [],
                "confidences": [],
                "last_referenced_at": None,
                "last_read_at": None,
            }

        meta = self.document_context[upload_id]
        meta["search_count"] = meta.get("search_count", 0) + 1
        meta["last_referenced_at"] = self._utc_now_iso()
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

    def track_document_read(self, upload_id: str, *, title: str | None = None) -> None:
        """
        Track an explicit document read.
        """
        if not upload_id:
            return
        upload_id = str(upload_id)
        self.referenced_upload_ids.add(upload_id)

        if upload_id not in self.document_context:
            self.document_context[upload_id] = {
                "title": title or "",
                "search_count": 0,
                "read_count": 0,
                "stages": [],
                "confidences": [],
                "last_referenced_at": None,
                "last_read_at": None,
            }

        meta = self.document_context[upload_id]
        if title and not meta.get("title"):
            meta["title"] = title
        meta["read_count"] = meta.get("read_count", 0) + 1
        meta["last_read_at"] = self._utc_now_iso()
        meta["last_referenced_at"] = meta.get("last_referenced_at") or meta["last_read_at"]

    def _update_primary_document(self, candidate_upload_id: str) -> None:
        """Primary document selection is disabled."""
        return

    def get_primary_document_title(self) -> str | None:
        """Document continuity is disabled; no primary document is exposed."""
        return None

    def get_document_context_for_query(self) -> dict:
        """
        Get document context dictionary for query rewriting and routing.

        Returns a dict suitable for passing to ContextAwareQueryRewriter.
        """
        return {
            "primary_upload_id": None,
            "primary_document_title": None,
            "referenced_upload_ids": list(self.referenced_upload_ids),
            "document_metadata": dict(self.document_context),
        }

    def get_document_context_for_persistence(self) -> dict:
        """
        Get document context for persistence to conversation.metadata.

        Returns a serializable dict that can be stored and rehydrated.
        """
        def _as_int(value: object) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        sorted_docs = sorted(
            self.document_context.items(),
            key=lambda item: (
                _as_int(item[1].get("read_count")),
                _as_int(item[1].get("search_count")),
                str(item[0]),
            ),
            reverse=True,
        )
        selected_ids: list[str] = []
        for upload_id, _ in sorted_docs:
            upload_id = str(upload_id)
            if upload_id in selected_ids:
                continue
            selected_ids.append(upload_id)
            if len(selected_ids) >= 10:
                break

        referenced_sorted = sorted(
            self.referenced_upload_ids,
            key=lambda upload_id: (
                _as_int(self.document_context.get(upload_id, {}).get("read_count")),
                _as_int(self.document_context.get(upload_id, {}).get("search_count")),
                str(upload_id),
            ),
            reverse=True,
        )[:20]

        return {
            "primary_upload_id": None,
            "referenced_uploads": referenced_sorted,  # Keep top 20 by engagement
            "document_metadata": {
                k: {
                    "title": v.get("title", ""),
                    "search_count": v.get("search_count", 0),
                    "read_count": v.get("read_count", 0),
                    "stages": v.get("stages", [])[-5:],  # Keep last 5 stages
                    "last_referenced_at": v.get("last_referenced_at"),
                    "last_read_at": v.get("last_read_at"),
                    # Don't persist confidences (transient)
                }
                for k in selected_ids
                for v in [self.document_context.get(k, {})]
            },
        }

    def hydrate_document_context(self, persisted: dict) -> None:
        """
        Restore document context from conversation.metadata.

        Called at the start of each turn to restore conversation state.
        """
        if not persisted:
            return

        self.primary_upload_id = None
        self.referenced_upload_ids = set(persisted.get("referenced_uploads", []))

        doc_metadata = persisted.get("document_metadata", {})
        for upload_id, meta in doc_metadata.items():
            self.document_context[upload_id] = {
                "title": meta.get("title", ""),
                "search_count": meta.get("search_count", 0),
                "read_count": meta.get("read_count", 0),
                "stages": meta.get("stages", []),
                "confidences": [],  # Not persisted
                "last_referenced_at": meta.get("last_referenced_at"),
                "last_read_at": meta.get("last_read_at"),
            }

    # =========================================================================
    # Query Refinement Tracking Methods (Phase 4)
    # =========================================================================

    def track_refinement(
        self,
        original_query: str,
        refined_query: str,
        reason: str,
        verdict: str = "mismatch",
        *,
        auto_applied: bool = False,
    ) -> None:
        """
        Track a query refinement attempt.

        Args:
            original_query: The original search query
            refined_query: The refined/suggested query
            reason: Why refinement was suggested (from critique)
            verdict: Critique verdict (mismatch, uncertain, etc.)
            auto_applied: Whether the refinement was auto-applied
        """
        self.refinement_history.append({
            "original": original_query,
            "refined": refined_query,
            "reason": reason,
            "verdict": verdict,
            "auto_applied": auto_applied,
            "timestamp": self._utc_now_iso(),
        })

    def get_refinement_count_for_query(self, query: str) -> int:
        """Count how many refinements have been attempted for a given query."""
        query_lower = (query or "").lower().strip()
        count = 0
        for entry in self.refinement_history:
            if (entry.get("original") or "").lower().strip() == query_lower:
                count += 1
        return count

    def can_refine_query(self, query: str) -> bool:
        """Check if more refinements are allowed for this query."""
        return self.get_refinement_count_for_query(query) < self.max_refinements_per_query

    def get_refinement_context_for_prompt(self) -> str | None:
        """
        Build a context string about refinement history for the LLM prompt.

        Returns None if no refinements have been attempted.
        """
        if not self.refinement_history:
            return None

        lines = ["Query refinements attempted this turn:"]
        for entry in self.refinement_history[-5:]:  # Last 5 refinements
            original = entry.get("original", "")
            refined = entry.get("refined", "")
            reason = entry.get("reason", "")
            auto_applied = entry.get("auto_applied", False)
            status = "auto-applied" if auto_applied else "suggested"
            lines.append(f"  - '{original}' → '{refined}' ({status}: {reason})")

        return "\n".join(lines)

    def get_pending_refinement_suggestion(self) -> dict | None:
        """
        Get the most recent refinement suggestion that wasn't auto-applied.

        Returns dict with 'original', 'refined', 'reason' or None.
        """
        for entry in reversed(self.refinement_history):
            if not entry.get("auto_applied"):
                return {
                    "original": entry.get("original"),
                    "refined": entry.get("refined"),
                    "reason": entry.get("reason"),
                }
        return None

    # =========================================================================
@dataclasses.dataclass
class KnowledgeToolResult:
    """Structured record of knowledge snippets returned by tool calls."""

    snippets: tuple[dict[str, object], ...] = dataclasses.field(default_factory=tuple)
    source_tool: str | None = None
    note: str | None = None
