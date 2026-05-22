from __future__ import annotations

from typing import Mapping


class ToolContextUsageMixin:

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

