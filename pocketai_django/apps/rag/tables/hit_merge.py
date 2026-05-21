from __future__ import annotations

from collections import Counter
from typing import Sequence

from apps.rag.contracts import ChunkResult


class TableHitMergeMixin:

    def _table_hit_metadata(self, hit: ChunkResult) -> dict[str, object]:
        return hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}

    def _table_hit_table_id(self, hit: ChunkResult) -> str:
        metadata = self._table_hit_metadata(hit)
        return str(metadata.get("table_id") or "").strip()

    def _is_table_row_hit(self, hit: ChunkResult) -> bool:
        metadata = self._table_hit_metadata(hit)
        return bool(metadata.get("is_table_chunk")) and str(metadata.get("table_chunk_role") or "").strip().lower() == "row"

    def _is_table_context_hit(self, hit: ChunkResult) -> bool:
        metadata = self._table_hit_metadata(hit)
        role = str(metadata.get("table_chunk_role") or "").strip().lower()
        return bool(metadata.get("is_table_preview")) or role == "parent"

    def _query_token_overlap_fraction(
        self,
        content: str | None,
        query_tokens: Sequence[str] | None,
    ) -> float:
        normalized_tokens = tuple(
            token.lower()
            for token in (query_tokens or ())
            if isinstance(token, str) and token.strip()
        )
        text = str(content or "").lower()
        if not text or not normalized_tokens:
            return 0.0
        matches = sum(1 for token in normalized_tokens if token in text)
        return matches / len(normalized_tokens)

    def _merge_expanded_table_hits(
        self,
        chunk_hits: Sequence[ChunkResult],
        expanded_rows: Sequence[ChunkResult],
        *,
        query_tokens: Sequence[str] | None = None,
    ) -> tuple[tuple[ChunkResult, ...], dict[str, int]]:
        existing_ids = {hit.chunk_id for hit in chunk_hits}
        new_rows = [row for row in expanded_rows if row.chunk_id not in existing_ids]
        if not new_rows:
            return tuple(chunk_hits), {
                "expanded_rows": 0,
                "relevant_rows": 0,
                "supplemental_rows": 0,
                "parent_chunks_suppressed": 0,
                "parent_chunks_limited": 0,
            }

        parent_chunks = [hit for hit in chunk_hits if self._is_table_context_hit(hit)]
        non_parent_chunks = [hit for hit in chunk_hits if not self._is_table_context_hit(hit)]
        seed_row_tables = Counter(
            self._table_hit_table_id(hit)
            for hit in non_parent_chunks
            if self._is_table_row_hit(hit) and self._table_hit_table_id(hit)
        )

        relevant_rows: list[ChunkResult] = []
        supplemental_rows: list[ChunkResult] = []
        for row in new_rows:
            overlap_fraction = self._query_token_overlap_fraction(row.chunk.content, query_tokens)
            table_id = self._table_hit_table_id(row)
            if overlap_fraction >= 0.5 or (table_id and seed_row_tables.get(table_id, 0)):
                relevant_rows.append(row)
            else:
                supplemental_rows.append(row)

        def _row_priority(hit: ChunkResult) -> tuple[int, float, float, float]:
            table_id = self._table_hit_table_id(hit)
            return (
                1 if table_id and seed_row_tables.get(table_id, 0) else 0,
                self._query_token_overlap_fraction(hit.chunk.content, query_tokens),
                float(hit.rerank_score or 0.0),
                float(hit.lexical_score or 0.0),
            )

        relevant_rows.sort(key=_row_priority, reverse=True)
        supplemental_rows.sort(key=_row_priority, reverse=True)

        merged_hits = tuple(relevant_rows) + tuple(non_parent_chunks) + tuple(supplemental_rows)
        row_evidence_by_table = Counter(
            self._table_hit_table_id(hit)
            for hit in merged_hits
            if self._is_table_row_hit(hit) and self._table_hit_table_id(hit)
        )

        retained_parents: list[ChunkResult] = []
        parent_chunks_suppressed = 0
        parent_chunks_limited = 0
        for parent_hit in parent_chunks:
            table_id = self._table_hit_table_id(parent_hit)
            if table_id and row_evidence_by_table.get(table_id, 0) >= 2:
                parent_chunks_suppressed += 1
                continue
            if len(retained_parents) >= self.table_row_expansion_max_parent_context:
                parent_chunks_limited += 1
                continue
            retained_parents.append(parent_hit)

        merged_hits = merged_hits + tuple(retained_parents)
        return merged_hits, {
            "expanded_rows": len(new_rows),
            "relevant_rows": len(relevant_rows),
            "supplemental_rows": len(supplemental_rows),
            "parent_chunks_suppressed": parent_chunks_suppressed,
            "parent_chunks_limited": parent_chunks_limited,
        }
