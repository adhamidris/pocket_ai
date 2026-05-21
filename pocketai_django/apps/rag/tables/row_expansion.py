from __future__ import annotations

import logging
import uuid
from typing import Sequence

from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.contracts import ChunkResult
from apps.rag.observability.logging import rag_log


logger = logging.getLogger(__name__)


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class TableRowExpansionMixin:

    def _expand_table_rows(
        self,
        business_profile,
        hits: Sequence[ChunkResult],
        *,
        max_rows_per_table: int | None = None,
        query_tokens: Sequence[str] | None = None,
    ) -> tuple[ChunkResult, ...]:
        """
        Hierarchical table retrieval: expand parent/preview chunks to row chunks.

        For any parent/preview chunks in hits, fetch associated row chunks.
        Row chunks contain actual answer data (e.g., "EGP 500") while parent chunks
        often contain OCR-corrupted markdown summaries.

        This enables "table discovery -> row expansion -> answer from rows" pattern.
        """
        if not hits:
            return tuple()

        max_rows = max_rows_per_table or self.table_row_expansion_limit
        table_ids: set[str] = set()
        hit_ids: set[uuid.UUID] = {hit.chunk_id for hit in hits}

        parent_count = 0
        preview_count = 0
        seeded_row_count = 0
        missing_table_id_count = 0
        best_seed_by_table: dict[str, ChunkResult] = {}

        def _result_strength(candidate: ChunkResult) -> float:
            values: list[float] = []
            for raw_value in (
                candidate.rerank_score,
                candidate.lexical_score,
                candidate.alias_confidence,
                candidate.recency_score,
            ):
                try:
                    values.append(float(raw_value))
                except (TypeError, ValueError):
                    continue
            return max(values) if values else 0.0

        for hit in hits:
            meta = hit.chunk.metadata if isinstance(hit.chunk.metadata, dict) else {}
            if not meta.get("is_table_chunk"):
                continue
            is_parent = meta.get("table_chunk_role") == "parent"
            is_preview = bool(meta.get("is_table_preview"))
            is_row = str(meta.get("table_chunk_role") or "").strip().lower() == "row"

            if is_parent:
                parent_count += 1
            if is_preview:
                preview_count += 1
            if is_row:
                seeded_row_count += 1

            if is_parent or is_preview or is_row:
                table_id = meta.get("table_id")
                if table_id:
                    table_id_str = str(table_id)
                    table_ids.add(table_id_str)
                    existing_best = best_seed_by_table.get(table_id_str)
                    if existing_best is None or _result_strength(hit) > _result_strength(existing_best):
                        best_seed_by_table[table_id_str] = hit
                elif is_parent or is_preview:
                    missing_table_id_count += 1
                    _rag_log(
                        "table.row_expansion.missing_table_id",
                        {
                            "chunk_id": str(hit.chunk_id),
                            "chunk_index": hit.chunk.chunk_index,
                            "upload_id": str(hit.chunk.upload_id),
                            "is_parent": is_parent,
                            "is_preview": is_preview,
                            "metadata_keys": list(meta.keys()),
                        },
                        indent=2,
                        context={"business": business_profile.id if business_profile else None},
                    )

        if not table_ids:
            if parent_count or preview_count:
                logger.warning(
                    "table.row_expansion.no_table_ids business=%s parent_count=%s preview_count=%s missing_table_id=%s",
                    business_profile.id if business_profile else None,
                    parent_count,
                    preview_count,
                    missing_table_id_count,
                )
            return tuple()

        _rag_log(
            "table.row_expansion.discovery",
            {
                "input_hits": len(hits),
                "parent_chunks": parent_count,
                "preview_chunks": preview_count,
                "seed_row_chunks": seeded_row_count,
                "discovered_tables": len(table_ids),
                "table_ids": list(table_ids)[:5],
                "missing_table_id_count": missing_table_id_count,
            },
            indent=2,
            context={"business": business_profile.id if business_profile else None},
        )

        row_chunks: list[KnowledgeUploadChunk] = []
        table_shard_hints: dict[str, int] = {}
        for table_id in table_ids:
            seed_hit = best_seed_by_table.get(table_id)
            seed_meta = seed_hit.chunk.metadata if seed_hit and isinstance(seed_hit.chunk.metadata, dict) else {}
            if not isinstance(seed_meta, dict):
                continue
            raw_hint = seed_meta.get("table_row_shard_index")
            try:
                if raw_hint is not None:
                    table_shard_hints[table_id] = int(raw_hint)
            except (TypeError, ValueError):
                continue

        try:
            for table_id in sorted(table_ids):
                per_table_qs = (
                    KnowledgeUploadChunk.objects.filter(
                        upload__business_profile=business_profile,
                        metadata__table_id=table_id,
                        metadata__table_chunk_role="row",
                    )
                    .select_related("upload")
                    .order_by("chunk_index")
                )
                selected_rows: list[KnowledgeUploadChunk] = []
                shard_hint = table_shard_hints.get(table_id)
                if shard_hint is not None:
                    selected_rows.extend(list(per_table_qs.filter(metadata__table_row_shard_index=shard_hint)[:max_rows]))
                    if len(selected_rows) < max_rows:
                        selected_ids = [row.id for row in selected_rows]
                        supplemental_qs = per_table_qs
                        if selected_ids:
                            supplemental_qs = supplemental_qs.exclude(id__in=selected_ids)
                        selected_rows.extend(list(supplemental_qs[: max_rows - len(selected_rows)]))
                else:
                    selected_rows.extend(list(per_table_qs[:max_rows]))
                row_chunks.extend(selected_rows)
        except Exception as exc:
            logger.error(
                "table.row_expansion.query_failed business=%s table_ids=%s error=%s",
                business_profile.id if business_profile else None,
                list(table_ids)[:5],
                str(exc)[:300],
            )
            return tuple()

        row_chunk_count = len(row_chunks)
        if row_chunk_count == 0:
            logger.warning(
                "table.row_expansion.no_rows_found business=%s table_count=%s table_ids=%s max_rows=%s",
                business_profile.id if business_profile else None,
                len(table_ids),
                list(table_ids),
                max_rows,
            )
            try:
                any_chunks = (
                    KnowledgeUploadChunk.objects.filter(
                        upload__business_profile=business_profile,
                        metadata__table_id__in=list(table_ids),
                    )
                    .values_list("id", "metadata__table_chunk_role")[:5]
                )
                logger.warning(
                    "table.row_expansion.debug_sample business=%s sample_chunks=%s",
                    business_profile.id if business_profile else None,
                    list(any_chunks),
                )
            except Exception:
                pass

        normalized_query_tokens = tuple(
            token.lower()
            for token in (query_tokens or ())
            if isinstance(token, str) and token.strip()
        )

        expanded: list[ChunkResult] = []
        duplicate_count = 0
        for chunk in row_chunks:
            if chunk.id in hit_ids:
                duplicate_count += 1
                continue
            row_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
            row_table_id = str(row_meta.get("table_id") or "").strip()
            seed_hit = best_seed_by_table.get(row_table_id) if row_table_id else None
            seed_meta = seed_hit.chunk.metadata if seed_hit and isinstance(seed_hit.chunk.metadata, dict) else {}
            parent_shard = seed_meta.get("table_row_shard_index") if isinstance(seed_meta, dict) else None
            row_shard = row_meta.get("table_row_shard_index") if isinstance(row_meta, dict) else None

            lexical_score = 0.0
            if normalized_query_tokens:
                lexical_score = self._lexical_overlap_score(chunk, normalized_query_tokens)

            base_lexical = float(seed_hit.lexical_score) if seed_hit is not None else 0.0
            base_alias = float(seed_hit.alias_confidence) if seed_hit is not None else 0.0
            base_recency = float(seed_hit.recency_score) if seed_hit is not None else 0.0
            base_rerank = float(seed_hit.rerank_score) if seed_hit is not None else 0.0
            vector_distance = seed_hit.vector_distance if seed_hit is not None else None
            row_diagnostics = dict(seed_hit.diagnostics) if seed_hit is not None else {}
            if seed_hit is not None:
                row_diagnostics["expanded_from_chunk_id"] = str(seed_hit.chunk_id)
                row_diagnostics["expanded_from_stage"] = str(seed_hit.source_stage)
            row_diagnostics["expanded_table_id"] = row_table_id
            if parent_shard is not None:
                row_diagnostics["expanded_parent_shard"] = parent_shard
            if row_shard is not None:
                row_diagnostics["expanded_row_shard"] = row_shard

            inherited_weight = 0.3
            effective_weight = max(0.05, inherited_weight * lexical_score) if lexical_score > 0 else 0.05
            expanded.append(
                ChunkResult(
                    chunk=chunk,
                    source_stage="table_row_expansion",
                    vector_distance=vector_distance,
                    lexical_score=max(base_lexical * effective_weight, lexical_score),
                    alias_confidence=base_alias * effective_weight,
                    recency_score=base_recency * effective_weight,
                    rerank_score=max(base_rerank * effective_weight, lexical_score),
                    diagnostics=row_diagnostics,
                )
            )

        _rag_log(
            "table.row_expansion.result",
            {
                "discovered_tables": len(table_ids),
                "tables_with_shard_hints": len(table_shard_hints),
                "row_chunks_found": row_chunk_count,
                "expanded_rows": len(expanded),
                "duplicates_skipped": duplicate_count,
            },
            indent=2,
            context={"business": business_profile.id if business_profile else None},
        )

        return tuple(expanded)
