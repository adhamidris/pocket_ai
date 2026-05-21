from __future__ import annotations

from typing import Sequence

from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.contracts import ChunkResult, QueryTraits


class SearchAutoScoringMixin:
    @staticmethod
    def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _chunk_hits_are_weak(self, hits: Sequence[ChunkResult], traits: QueryTraits) -> bool:
        if not hits:
            return True
        sample = hits[: self.table_chunk_sample_limit]

        def _is_table_chunk(candidate: ChunkResult) -> bool:
            metadata = candidate.chunk.metadata if isinstance(candidate.chunk.metadata, dict) else {}
            return bool(metadata.get("is_table_chunk"))

        has_table_chunk = any(_is_table_chunk(hit) for hit in sample)
        best_rerank = max((hit.rerank_score or 0.0) for hit in sample)
        vector_distances = [hit.vector_distance for hit in sample if hit.vector_distance is not None]
        best_vector = min(vector_distances) if vector_distances else None
        if has_table_chunk and best_rerank >= self.table_rerank_floor:
            return False
        if best_rerank < self.table_rerank_floor:
            return True
        if best_vector is not None and best_vector > self.table_vector_floor:
            return True
        if not has_table_chunk and best_rerank < (self.table_rerank_floor * 1.2):
            return True
        top = hits[0]
        meta = top.chunk.metadata if isinstance(top.chunk.metadata, dict) else {}
        if meta.get("is_table_preview"):
            filler = self._filler_tokens_for_business(top.chunk.upload.business_profile)
            query_tokens = [
                t.lower() for t in traits.tokens if t and t.lower() not in filler and len(t) > 3
            ]
            text = (top.chunk.content or "").lower()
            missing = [t for t in query_tokens if t not in text]
            if query_tokens and len(missing) >= len(query_tokens) * 0.5:
                return True
        return False

    @staticmethod
    def _is_doc_table_preview_chunk(chunk: KnowledgeUploadChunk) -> bool:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        return bool(metadata.get("is_table_chunk")) and bool(metadata.get("is_table_preview"))

    @staticmethod
    def _chunk_index_type(chunk: KnowledgeUploadChunk) -> str:
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        raw = str(metadata.get("index_type") or "").strip().lower()
        if raw:
            return raw
        if metadata.get("is_table_chunk"):
            return "table"
        return "text"

    @staticmethod
    def _interleave_chunk_hits(
        text_hits: Sequence[ChunkResult],
        table_hits: Sequence[ChunkResult],
    ) -> tuple[ChunkResult, ...]:
        merged: list[ChunkResult] = []
        text_idx = 0
        table_idx = 0
        take_text = True
        while text_idx < len(text_hits) or table_idx < len(table_hits):
            if take_text and text_idx < len(text_hits):
                merged.append(text_hits[text_idx])
                text_idx += 1
            elif (not take_text) and table_idx < len(table_hits):
                merged.append(table_hits[table_idx])
                table_idx += 1
            elif text_idx < len(text_hits):
                merged.append(text_hits[text_idx])
                text_idx += 1
            elif table_idx < len(table_hits):
                merged.append(table_hits[table_idx])
                table_idx += 1
            take_text = not take_text
        return tuple(merged)

    @staticmethod
    def _safe_float(value: object, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _clamp_unit(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @classmethod
    def _aggregate_auto_scores(cls, scores: Sequence[float]) -> float:
        if not scores:
            return 0.0
        ordered = sorted((max(0.0, float(score)) for score in scores), reverse=True)
        weights = (1.0, 0.75, 0.55, 0.4, 0.3)
        limited = ordered[: len(weights)]
        weighted_sum = sum(value * weights[idx] for idx, value in enumerate(limited))
        weight_total = sum(weights[: len(limited)]) or 1.0
        coverage = min(1.0, len(ordered) / 3.0)
        return (weighted_sum / weight_total) * (0.75 + 0.25 * coverage)

    def _score_auto_mode_candidates(
        self,
        hits: Sequence[ChunkResult],
        *,
        query_tokens: Sequence[str] | None = None,
        specific_tokens: Sequence[str] | None = None,
    ) -> dict[str, object]:
        if not hits:
            return {
                "auto_score_version": "v2",
                "auto_score_sample_size": 0,
                "auto_score_table_hits": 0,
                "auto_score_text_hits": 0,
                "auto_table_score": 0.0,
                "auto_text_score": 0.0,
                "auto_score_margin": 0.0,
                "auto_table_signal_header_hits": 0,
                "auto_table_signal_specific_hits": 0,
                "auto_table_signal_strong_hits": 0,
                "auto_text_semantic_overlap_avg": 0.0,
            }

        normalized_query_tokens = tuple(
            str(token).strip().lower()
            for token in (query_tokens or ())
            if str(token).strip()
        )
        query_token_set = set(normalized_query_tokens)
        specific_token_set = {
            str(token).strip().lower()
            for token in (specific_tokens or ())
            if str(token).strip()
        }
        sample = tuple(hits[: max(1, int(self.table_chunk_sample_limit))])

        table_scores: list[float] = []
        text_scores: list[float] = []
        table_header_hits = 0
        table_specific_hits = 0
        table_strong_hits = 0
        text_semantic_scores: list[float] = []

        for hit in sample:
            chunk = hit.chunk
            metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            content = str(chunk.content or "")

            rerank = self._clamp_unit(self._safe_float(hit.rerank_score))
            lexical = self._clamp_unit(self._safe_float(hit.lexical_score))
            alias = self._clamp_unit(self._safe_float(hit.alias_confidence))
            recency = self._clamp_unit(self._safe_float(hit.recency_score))
            vector_distance = hit.vector_distance
            vector_signal = 0.0
            if isinstance(vector_distance, (int, float)):
                vector_signal = self._clamp_unit(1.0 - float(vector_distance))
            if lexical <= 0.0 and normalized_query_tokens:
                lexical = self._clamp_unit(self._lexical_score_text(content, normalized_query_tokens))

            base = max(
                rerank,
                (0.45 * lexical) + (0.2 * alias) + (0.2 * recency) + (0.15 * vector_signal),
            )

            index_type = self._chunk_index_type(chunk)
            if index_type == "table":
                match_info = hit.diagnostics if isinstance(hit.diagnostics, dict) else {}
                if not {"header_match", "specific_match", "specific_match_strong"} & set(match_info.keys()):
                    if query_token_set or specific_token_set:
                        computed = self._table_chunk_match_info(
                            chunk,
                            query_tokens=query_token_set,
                            specific_tokens=specific_token_set,
                        )
                        if isinstance(hit.diagnostics, dict):
                            hit.diagnostics.update(computed)
                        match_info = computed

                header_match = bool(match_info.get("header_match"))
                specific_match = bool(match_info.get("specific_match"))
                strong_match = bool(match_info.get("specific_match_strong"))
                ratio = self._clamp_unit(self._safe_float(match_info.get("specific_match_ratio")))
                role = str(metadata.get("table_chunk_role") or "").strip().lower()
                is_preview = bool(metadata.get("is_table_preview"))

                table_boost = 0.0
                if header_match:
                    table_boost += 0.18
                    table_header_hits += 1
                if specific_match:
                    table_boost += 0.24
                    table_specific_hits += 1
                if strong_match:
                    table_boost += 0.3
                    table_strong_hits += 1
                table_boost += 0.15 * ratio
                if role == "row":
                    table_boost += 0.08
                if is_preview:
                    table_boost -= 0.05
                table_scores.append(max(0.0, base + table_boost))
            else:
                semantic_overlap = self._clamp_unit(self._lexical_score_text(content, normalized_query_tokens))
                density = self._clamp_unit(min(1.0, len(content) / 600.0))
                text_boost = (0.28 * semantic_overlap) + (0.08 * density)
                text_scores.append(max(0.0, base + text_boost))
                text_semantic_scores.append(semantic_overlap)

        table_score = round(self._aggregate_auto_scores(table_scores), 6)
        text_score = round(self._aggregate_auto_scores(text_scores), 6)
        margin = round(abs(table_score - text_score), 6)
        text_semantic_avg = round(sum(text_semantic_scores) / len(text_semantic_scores), 6) if text_semantic_scores else 0.0

        return {
            "auto_score_version": "v2",
            "auto_score_sample_size": len(sample),
            "auto_score_table_hits": len(table_scores),
            "auto_score_text_hits": len(text_scores),
            "auto_table_score": table_score,
            "auto_text_score": text_score,
            "auto_score_margin": margin,
            "auto_table_signal_header_hits": table_header_hits,
            "auto_table_signal_specific_hits": table_specific_hits,
            "auto_table_signal_strong_hits": table_strong_hits,
            "auto_text_semantic_overlap_avg": text_semantic_avg,
        }
