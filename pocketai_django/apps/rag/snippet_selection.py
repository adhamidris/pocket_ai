from __future__ import annotations

from collections import OrderedDict
import uuid
from typing import Mapping, Sequence

from apps.rag.contracts import ChunkResult, KnowledgeSnippet
from apps.rag.query_normalizer import QueryNormalizer
from apps.rag.rag_logging import rag_log


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class SnippetSelectionMixin:
    def _public_confidence_score(self, result: ChunkResult | None) -> float | None:
        """Expose one comparable public relevance signal for prompt-visible snippets.

        Public confidence should reflect retrieval relevance, not freshness or
        other helper boosts that can make scores incomparable across result
        types. Keep recency out of the public score.
        """
        if result is None:
            return None

        score_candidates: list[float] = []
        rerank_score = self._clamp_unit(self._safe_float(getattr(result, "rerank_score", None), default=0.0))
        if rerank_score > 0.0:
            score_candidates.append(rerank_score)

        lexical_score = self._clamp_unit(self._safe_float(getattr(result, "lexical_score", None), default=0.0))
        if lexical_score > 0.0:
            score_candidates.append(lexical_score)

        alias_score = self._clamp_unit(self._safe_float(getattr(result, "alias_confidence", None), default=0.0))
        if alias_score > 0.0:
            score_candidates.append(alias_score)

        vector_distance = getattr(result, "vector_distance", None)
        if isinstance(vector_distance, (int, float)):
            vector_score = self._clamp_unit(1.0 - float(vector_distance))
            if vector_score > 0.0:
                score_candidates.append(vector_score)

        return max(score_candidates) if score_candidates else 0.0

    @staticmethod
    def _diversify_table_snippets(
        snippets: Sequence[KnowledgeSnippet],
        *,
        limit: int,
    ) -> list[KnowledgeSnippet]:
        diversified, _diagnostics = SnippetSelectionMixin._diversify_table_snippets_with_diagnostics(
            snippets,
            limit=limit,
        )
        return diversified

    @staticmethod
    def _diversify_table_snippets_with_diagnostics(
        snippets: Sequence[KnowledgeSnippet],
        *,
        limit: int,
    ) -> tuple[list[KnowledgeSnippet], dict[str, object]]:
        """Round-robin across table/document buckets before final clipping."""
        bucketed: OrderedDict[str, list[KnowledgeSnippet]] = OrderedDict()
        table_bucket_ids: set[str] = set()
        upload_bucket_ids: set[str] = set()

        for snippet in snippets:
            diagnostics = snippet.source_diagnostics if isinstance(snippet.source_diagnostics, Mapping) else {}
            table_id = str(snippet.table_id or diagnostics.get("table_id") or "").strip()
            upload_id = str(snippet.upload_id or "").strip()
            if upload_id:
                upload_bucket_ids.add(upload_id)

            if table_id:
                bucket_key = f"table:{table_id}"
                table_bucket_ids.add(table_id)
            elif upload_id:
                bucket_key = f"upload:{upload_id}"
            else:
                bucket_key = f"snippet:{snippet.id}"
            bucketed.setdefault(bucket_key, []).append(snippet)

        requested_limit = max(0, int(limit or 0))
        diagnostics_out: dict[str, object] = {
            "coverage_diversification_method": "table_document_round_robin_v2",
            "coverage_diversification_input_count": len(snippets),
            "coverage_diversification_limit": requested_limit,
            "coverage_diversification_bucket_count": len(bucketed),
            "coverage_diversification_table_buckets": len(table_bucket_ids),
            "coverage_diversification_upload_buckets": len(upload_bucket_ids),
            "coverage_diversification_applied": False,
            "coverage_diversification_output_count": 0,
        }

        if requested_limit <= 0 or not snippets:
            return [], diagnostics_out

        if len(bucketed) <= 1:
            result = list(snippets)[:requested_limit]
            diagnostics_out["coverage_diversification_output_count"] = len(result)
            return result, diagnostics_out

        result: list[KnowledgeSnippet] = []
        offsets: dict[str, int] = {key: 0 for key in bucketed}
        bucket_keys = list(bucketed.keys())

        while len(result) < requested_limit:
            added = False
            for key in bucket_keys:
                offset = offsets[key]
                bucket = bucketed[key]
                if offset >= len(bucket):
                    continue
                result.append(bucket[offset])
                offsets[key] = offset + 1
                added = True
                if len(result) >= requested_limit:
                    break
            if not added:
                break

        diagnostics_out["coverage_diversification_applied"] = True
        diagnostics_out["coverage_diversification_output_count"] = len(result)
        return result, diagnostics_out

    def _search_chunks(
        self,
        hits: Sequence[ChunkResult],
        *,
        limit: int,
        business_profile,
        pathway: str,
        query: str | None = None,
    ) -> Sequence[KnowledgeSnippet]:
        if not hits:
            return tuple()

        snippets: list[KnowledgeSnippet] = []
        per_upload_counts: dict[uuid.UUID, int] = {}
        query_text = (query or "").strip()
        query_tokens: tuple[str, ...] = tuple(
            token
            for token in QueryNormalizer._TOKEN_SPLIT.split(
                QueryNormalizer._normalize_query_text(query_text).lower()
            )
            if token
        )
        max_per_upload = max(limit, self._effective_chunk_cap(business_profile, pathway))
        for hit in hits:
            chunk = hit.chunk
            upload = chunk.upload
            current = per_upload_counts.get(upload.id, 0)
            if current >= max_per_upload:
                continue
            table_sample: tuple[Mapping[str, object], ...] | None = self._table_row_sample(
                chunk,
                max_columns=6,
                max_rows=1,
                query=query,
            )
            snippets.append(
                self._chunk_to_snippet(
                    chunk,
                    result=hit,
                    table_row_sample=table_sample,
                    query_text=query_text,
                    query_tokens=query_tokens,
                )
            )
            per_upload_counts[upload.id] = current + 1
            if len(snippets) >= limit:
                break
        return tuple(snippets)

    def _rrf_fusion_snippets(
        self,
        *,
        vector_snippets: Sequence[KnowledgeSnippet],
        table_snippets: Sequence[KnowledgeSnippet],
        k: int = 60,
    ) -> list[KnowledgeSnippet]:
        """
        Merge vector and table search results using Reciprocal Rank Fusion (RRF).

        RRF formula: score(d) = sum 1/(k + rank(d))
        """
        rrf_scores: dict[str, float] = {}
        snippet_map: dict[str, KnowledgeSnippet] = {}

        for rank, snippet in enumerate(vector_snippets, start=1):
            snippet_id = str(snippet.id)
            rrf_scores[snippet_id] = rrf_scores.get(snippet_id, 0.0) + 1.0 / (k + rank)
            if snippet_id not in snippet_map:
                snippet_map[snippet_id] = snippet

        for rank, snippet in enumerate(table_snippets, start=1):
            snippet_id = str(snippet.id)
            rrf_scores[snippet_id] = rrf_scores.get(snippet_id, 0.0) + 1.0 / (k + rank)
            if snippet_id not in snippet_map:
                snippet_map[snippet_id] = snippet

        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)

        merged: list[KnowledgeSnippet] = []
        for snippet_id in sorted_ids:
            snippet = snippet_map[snippet_id]
            merged.append(snippet)

        _rag_log(
            "parallel_table.rrf_fusion",
            {
                "vector_count": len(vector_snippets),
                "table_count": len(table_snippets),
                "merged_count": len(merged),
                "top_5_scores": [
                    {"id": sid[:8], "score": round(rrf_scores[sid], 4)}
                    for sid in sorted_ids[:5]
                ],
            },
            indent=2,
        )

        return merged
