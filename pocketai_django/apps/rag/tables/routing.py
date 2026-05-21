from __future__ import annotations

import uuid
from typing import Mapping, Sequence

from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.contracts import ChunkResult


class TableRoutingMixin:

    def _route_chunk_hits(
        self,
        hits: Sequence[ChunkResult],
        *,
        table_intent: bool,
        table_context: Mapping[str, object] | None = None,
    ) -> tuple[tuple[ChunkResult, ...], tuple[ChunkResult, ...], dict[str, object]]:
        if not hits:
            return tuple(), tuple(), {"index_route": "empty", "index_route_table_hits": 0, "index_route_text_hits": 0}
        table_hits: list[ChunkResult] = []
        text_hits: list[ChunkResult] = []
        for hit in hits:
            index_type = self._chunk_index_type(hit.chunk)
            if index_type == "table":
                table_hits.append(hit)
            else:
                text_hits.append(hit)
        table_context = table_context or {}
        specific_tokens = set(table_context.get("specific_tokens") or ())
        query_tokens = set(table_context.get("query_tokens") or ())

        filtered_count = 0
        weak_count = 0
        strong_count = 0
        if table_hits and table_intent and specific_tokens:
            for hit in table_hits:
                match_info = self._table_chunk_match_info(
                    hit.chunk,
                    query_tokens=query_tokens,
                    specific_tokens=specific_tokens,
                )
                hit.diagnostics.update(match_info)
                if match_info.get("specific_match_strong"):
                    strong_count += 1
                elif match_info.get("specific_match"):
                    weak_count += 1
                else:
                    filtered_count += 1

        raw_bias = str(table_context.get("modality_bias") or "").strip().lower()
        if raw_bias not in {"table", "text", "mixed"}:
            raw_bias = "table" if table_intent else "text"

        if table_hits and text_hits:
            table_biased = raw_bias == "table" or (raw_bias == "mixed" and table_intent)
            if table_biased:
                primary_hits = self._interleave_chunk_hits(table_hits, text_hits)
                secondary_hits = tuple(text_hits)
                route = "mixed_primary_table_biased"
                dominant_modality = "table"
            else:
                primary_hits = self._interleave_chunk_hits(text_hits, table_hits)
                secondary_hits = tuple(table_hits)
                route = "mixed_primary_text_biased"
                dominant_modality = "text"
        elif table_hits:
            primary_hits = tuple(table_hits)
            secondary_hits = tuple()
            route = "mixed_primary_table_only"
            dominant_modality = "table"
        elif text_hits:
            primary_hits = tuple(text_hits)
            secondary_hits = tuple()
            route = "mixed_primary_text_only"
            dominant_modality = "text"
        else:
            primary_hits = tuple(hits)
            secondary_hits = tuple()
            route = "mixed_fallback_all"
            dominant_modality = "unknown"

        diagnostics = {
            "index_route": route,
            "index_route_table_hits": len(table_hits),
            "index_route_text_hits": len(text_hits),
            "index_route_modality_bias": raw_bias,
            "index_route_dominant_modality": dominant_modality,
            "index_route_mixed": bool(table_hits and text_hits),
        }
        if specific_tokens:
            diagnostics.update(
                {
                    "table_specific_filtered": filtered_count,
                    "table_specific_strong_hits": strong_count,
                    "table_specific_weak_hits": weak_count,
                }
            )
        return tuple(primary_hits), secondary_hits, diagnostics

    def _table_parent_hits(
        self,
        business_profile,
        hits: Sequence[ChunkResult],
        *,
        limit: int = 3,
    ) -> tuple[ChunkResult, ...]:
        if not hits:
            return tuple()
        table_ids: set[str] = set()
        hit_ids: set[uuid.UUID] = {hit.chunk_id for hit in hits}
        for hit in hits:
            meta = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
            if not meta.get("is_table_chunk"):
                continue
            if str(meta.get("table_chunk_role") or "") != "row":
                continue
            table_id = meta.get("table_id")
            if table_id:
                table_ids.add(str(table_id))
        if not table_ids:
            return tuple()
        parents = (
            KnowledgeUploadChunk.objects.filter(
                upload__business_profile=business_profile,
                metadata__table_id__in=list(table_ids),
                metadata__table_chunk_role="parent",
            )
            .select_related("upload")
            .order_by("chunk_index")[: max(1, int(limit))]
        )
        parent_hits: list[ChunkResult] = []
        for chunk in parents:
            if chunk.id in hit_ids:
                continue
            parent_hits.append(ChunkResult(chunk=chunk, source_stage="table_parent"))
        return tuple(parent_hits)
