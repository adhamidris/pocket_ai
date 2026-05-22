from __future__ import annotations

from typing import Mapping


class ToolContextVisibilityMixin:

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
