from __future__ import annotations

import uuid
from typing import Sequence

from apps.rag.contracts import ChunkResult


class CandidateMergeMixin:

    def _merge_candidates(self, *groups: Sequence[ChunkResult]) -> list[ChunkResult]:
        """
        Merge candidates from multiple search pathways, keeping the best occurrence
        of each chunk for deterministic results.
        """
        best: dict[uuid.UUID, ChunkResult] = {}
        order: list[uuid.UUID] = []

        def _merge_score(hit: ChunkResult) -> float:
            vec_contrib = -(hit.vector_distance or 0.0) if hit.vector_distance else 0.0
            return hit.alias_confidence + hit.lexical_score + vec_contrib

        for group in groups:
            for hit in group:
                cid = hit.chunk_id
                if cid not in best:
                    order.append(cid)
                    best[cid] = hit
                else:
                    existing_score = _merge_score(best[cid])
                    new_score = _merge_score(hit)
                    if new_score > existing_score:
                        best[cid] = hit

        return [best[cid] for cid in order]

    @staticmethod
    def _vector_distance_stats(candidates: Sequence[ChunkResult]) -> dict[str, float]:
        distances = [
            float(hit.vector_distance)
            for hit in candidates
            if isinstance(hit.vector_distance, (int, float))
        ]
        if not distances:
            return {}
        average = sum(distances) / len(distances)
        similarities = [1.0 - distance for distance in distances]
        sim_average = sum(similarities) / len(similarities)
        clamped_scores = [max(-1.0, min(1.0, sim)) for sim in similarities]
        score_average = sum(clamped_scores) / len(clamped_scores)
        return {
            "vector_distance_min": min(distances),
            "vector_distance_max": max(distances),
            "vector_distance_mean": round(average, 5),
            "vector_similarity_min": round(min(similarities), 5),
            "vector_similarity_max": round(max(similarities), 5),
            "vector_similarity_mean": round(sim_average, 5),
            "vector_score_mean": round(score_average, 5),
        }
