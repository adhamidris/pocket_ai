from __future__ import annotations

from .errors import (
    CharacterBudgetExceeded,
    ChunkPageBudgetExceeded,
    ChunkReadBudgetExceeded,
    ReadBudgetExceeded,
    SearchBudgetExceeded,
)


class ToolContextBudgetMixin:

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
