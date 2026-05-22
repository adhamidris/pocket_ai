from __future__ import annotations


class ToolContextRefinementMixin:

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
