from __future__ import annotations

import re
import uuid
from typing import Sequence

from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

from apps.accounts.models import KnowledgeVisibility
from apps.knowledge.models import KnowledgeUploadChunk, KnowledgeUploadTableCell, KnowledgeUploadTableRow
from apps.rag.contracts import KNOWLEDGE_READ_STATE_SUMMARY, KnowledgeSnippet
from apps.rag.observability.logging import rag_log


def _rag_log(stage: str, detail=None, *, indent: int = 0, context=None) -> None:
    rag_log(stage, detail=detail, indent=indent, context=context)


class TableDirectSnippetMixin:

    def _table_search_snippets(
        self,
        *,
        business_profile,
        query_text: str,
        limit: int,
        matched_columns: set[str],
        allowed_upload_ids: Sequence[uuid.UUID] | None = None,
        allowed_explicit_upload_ids: Sequence[uuid.UUID] | None = None,
        comprehensive_intent: bool = False,
    ) -> tuple[KnowledgeSnippet, ...]:
        normalized_query = (query_text or "").strip()
        if not normalized_query:
            return tuple()
        row_cap = self._table_row_result_cap_for_business(business_profile, requested=limit)

        # DEBUG: Log entry into _table_search_snippets
        _rag_log(
            "table.search_snippets_entry",
            {
                "query": normalized_query[:100],
                "comprehensive_intent_received": comprehensive_intent,
                "row_cap": row_cap,
                "limit": limit,
                "matched_columns": list(matched_columns)[:10] if matched_columns else [],
            },
            indent=3,
            context={"business": business_profile.id},
        )
        total_keywords = (
            "total",
            "sum",
            "overall",
            "اجمالي",
            "إجمالي",
            "الاجمالي",
            "المجموع",
        )

        def _normalize_label(value: str | None) -> str:
            return re.sub(r"\s+", " ", (value or "").strip().lower())

        def _is_total_column(label: str | None) -> bool:
            normalized = _normalize_label(label)
            if not normalized:
                return False
            return any(keyword in normalized for keyword in total_keywords)

        def _parse_numeric(value: str | None) -> float | None:
            if not isinstance(value, str):
                return None
            text = value.strip()
            if not text:
                return None
            cleaned = re.sub(r"[^\d\-,\.]", "", text)
            cleaned = cleaned.replace(",", "")
            if not cleaned or cleaned in {"-", "."}:
                return None
            try:
                return float(cleaned)
            except ValueError:
                return None

        def _format_total(value: float | None, raw: str | None = None) -> str | None:
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            if value is None:
                return None
            rounded = round(value)
            if abs(value - rounded) < 1e-6:
                return f"{rounded:,}"
            return f"{value:,.2f}".rstrip("0").rstrip(".")

        def _is_numeric_value(value: str | None) -> bool:
            return _parse_numeric(value) is not None
        cell_qs = KnowledgeUploadTableCell.objects.filter(table__upload__business_profile=business_profile).exclude(
            table__upload__visibility=KnowledgeVisibility.INTERNAL,
        )
        cell_qs = self._filter_queryable_table_uploads(
            cell_qs,
            format_lookup="table__upload__ingestion_metadata__format",
        )
        if allowed_upload_ids is not None:
            if not allowed_upload_ids:
                return tuple()
            cell_qs = cell_qs.filter(table__upload_id__in=allowed_upload_ids)
        else:
            clauses: list[Q] = []
            if allowed_explicit_upload_ids:
                clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
            if clauses:
                clause = clauses[0]
                for extra in clauses[1:]:
                    clause |= extra
                cell_qs = cell_qs.filter(clause)
        if matched_columns:
            column_filter = Q()
            for column in matched_columns:
                column_filter |= Q(column_key__iexact=column) | Q(column_key__icontains=column)
            cell_qs = cell_qs.filter(column_filter)
        # NOTE: We intentionally do not apply per-token AND filters here.
        # Requiring every cell to contain all query tokens can drop valid rows
        # when entity names and generic terms are split across columns.
        # We rely on TrigramSimilarity over raw_text for fuzzy row matching.
        cell_qs = (
            cell_qs.annotate(sim=TrigramSimilarity("raw_text", normalized_query))
            .filter(sim__gte=self.table_similarity_threshold)
            .order_by("-sim")
            .select_related("row__table__upload")
        )
        # LLM-driven mode: always collect more candidates for table diversity
        # Let snippet limit (RAG_MAX_SNIPPETS_PER_SEARCH) be the only hard cap
        candidate_limit = row_cap * 5  # Always use higher limit
        top_cells = list(cell_qs[:candidate_limit])
        row_priority: list[uuid.UUID] = []
        cell_diag: dict[uuid.UUID, dict[str, object]] = {}
        # Track table_id for each row to enable round-robin diversification
        row_to_table: dict[uuid.UUID, uuid.UUID] = {}

        if top_cells:
            for cell in top_cells:
                row = cell.row
                if not row or not row.id or row.id in cell_diag:
                    continue
                table = getattr(row, "table", None)
                upload = getattr(table, "upload", None) if table else None
                if not upload or upload.business_profile_id != business_profile.id:
                    continue
                similarity = float(getattr(cell, "sim", 0.0) or 0.0)
                cell_diag[row.id] = {
                    "column_key": cell.column_key,
                    "value": cell.raw_text,
                    "similarity": similarity,
                    "table_id": table.id,
                }
                row_priority.append(row.id)
                row_to_table[row.id] = table.id
                # LLM-driven mode: never break early, collect all candidates
                # Table diversity will be applied after collection
        else:
            # Fallback: match on row.raw_text when no individual cell is similar enough.
            row_qs = KnowledgeUploadTableRow.objects.filter(
                table__upload__business_profile=business_profile,
            ).exclude(
                table__upload__visibility=KnowledgeVisibility.INTERNAL,
            )
            row_qs = self._filter_queryable_table_uploads(
                row_qs,
                format_lookup="table__upload__ingestion_metadata__format",
            )
            if allowed_upload_ids is not None:
                row_qs = row_qs.filter(table__upload_id__in=allowed_upload_ids)
            else:
                clauses = []
                if allowed_explicit_upload_ids:
                    clauses.append(Q(table__upload_id__in=allowed_explicit_upload_ids))
                if clauses:
                    clause = clauses[0]
                    for extra in clauses[1:]:
                        clause |= extra
                    row_qs = row_qs.filter(clause)
            row_qs = (
                row_qs.annotate(sim=TrigramSimilarity("raw_text", normalized_query))
                .filter(sim__gte=self.table_similarity_threshold)
                .order_by("-sim")
                .select_related("table__upload")
            )
            fallback_limit = row_cap * 5 if comprehensive_intent else row_cap
            top_rows = list(row_qs[:fallback_limit])
            for row in top_rows:
                if not row or not row.id or row.id in cell_diag:
                    continue
                table = getattr(row, "table", None)
                upload = getattr(table, "upload", None) if table else None
                if not upload or upload.business_profile_id != business_profile.id:
                    continue
                similarity = float(getattr(row, "sim", 0.0) or 0.0)
                cell_diag[row.id] = {
                    "column_key": None,
                    "value": row.raw_text,
                    "similarity": similarity,
                    "table_id": table.id,
                }
                row_priority.append(row.id)
                row_to_table[row.id] = table.id
                # LLM-driven mode: never break early, collect all candidates
                # Table diversity will be applied after collection

        if not row_priority:
            _rag_log(
                "table.search_snippets_no_candidates",
                {"comprehensive_intent": comprehensive_intent, "query": normalized_query[:50]},
                indent=3,
                context={"business": business_profile.id},
            )
            return tuple()

        # DEBUG: Log collected candidates BEFORE diversification
        table_distribution_before: dict[str, int] = {}
        for rid in row_priority:
            tid = row_to_table.get(rid)
            if tid:
                key = str(tid)[:8]
                table_distribution_before[key] = table_distribution_before.get(key, 0) + 1
        _rag_log(
            "table.candidates_collected",
            {
                "comprehensive_intent": comprehensive_intent,
                "total_candidates": len(row_priority),
                "distinct_tables": len(set(row_to_table.values())),
                "table_distribution": table_distribution_before,
                "row_cap": row_cap,
            },
            indent=3,
            context={"business": business_profile.id},
        )

        # LLM-driven mode: ALWAYS apply round-robin diversification across tables
        # This ensures results are spread across multiple tables for full coverage
        if len(row_to_table) > 0:  # Always diversify, regardless of intent
            distinct_tables = set(row_to_table.values())
            _rag_log(
                "table.diversification_check",
                {
                    "comprehensive_intent": comprehensive_intent,
                    "distinct_tables_count": len(distinct_tables),
                    "will_diversify": len(distinct_tables) > 1,
                },
                indent=3,
                context={"business": business_profile.id},
            )
            if len(distinct_tables) > 1:
                # Group rows by table, preserving similarity order within each table
                rows_by_table: dict[uuid.UUID, list[uuid.UUID]] = {}
                for row_id in row_priority:
                    table_id = row_to_table.get(row_id)
                    if table_id:
                        if table_id not in rows_by_table:
                            rows_by_table[table_id] = []
                        rows_by_table[table_id].append(row_id)

                # Round-robin selection across tables
                diversified_priority: list[uuid.UUID] = []
                table_queues = {tid: list(rows) for tid, rows in rows_by_table.items()}
                table_order = list(table_queues.keys())  # Stable order

                while len(diversified_priority) < row_cap and table_queues:
                    for table_id in list(table_order):
                        if table_id not in table_queues:
                            continue
                        queue = table_queues[table_id]
                        if queue:
                            diversified_priority.append(queue.pop(0))
                            if len(diversified_priority) >= row_cap:
                                break
                        if not queue:
                            del table_queues[table_id]

                # Log diversification result with table distribution AFTER
                table_distribution_after: dict[str, int] = {}
                for rid in diversified_priority:
                    tid = row_to_table.get(rid)
                    if tid:
                        key = str(tid)[:8]
                        table_distribution_after[key] = table_distribution_after.get(key, 0) + 1
                _rag_log(
                    "table.diversification_applied",
                    {
                        "before_count": len(row_priority),
                        "after_count": len(diversified_priority),
                        "distinct_tables": len(distinct_tables),
                        "distribution_before": table_distribution_before,
                        "distribution_after": table_distribution_after,
                    },
                    indent=3,
                    context={"business": business_profile.id},
                )
                row_priority = diversified_priority
            else:
                _rag_log(
                    "table.diversification_skipped",
                    {"reason": "only_one_table", "distinct_tables": len(distinct_tables)},
                    indent=3,
                    context={"business": business_profile.id},
                )
        else:
            _rag_log(
                "table.diversification_not_applicable",
                {
                    "comprehensive_intent": comprehensive_intent,
                    "row_to_table_count": len(row_to_table),
                },
                indent=3,
                context={"business": business_profile.id},
            )
        rows = (
            KnowledgeUploadTableRow.objects.filter(id__in=row_priority)
            .select_related("table__upload__business_profile")
            .prefetch_related("cells")
        )
        row_map = {row.id: row for row in rows}
        ingestion_diag_cache: dict[uuid.UUID, dict[str, object]] = {}
        row_chunk_map: dict[tuple[str, int], KnowledgeUploadChunk] = {}
        row_keys: set[tuple[str, int]] = set()
        row_upload_ids: set[uuid.UUID] = set()
        for row in row_map.values():
            if not row or row.row_index is None:
                continue
            table = getattr(row, "table", None)
            upload_id = getattr(table, "upload_id", None) if table else None
            if not table or not upload_id:
                continue
            row_keys.add((str(table.id), int(row.row_index)))
            row_upload_ids.add(upload_id)
        if row_keys:
            table_ids = {table_id for table_id, _ in row_keys}
            row_indices = {row_index for _, row_index in row_keys}
            row_chunks = (
                KnowledgeUploadChunk.objects.filter(
                    business_profile=business_profile,
                    upload_id__in=row_upload_ids,
                    metadata__table_chunk_role="row",
                    metadata__table_id__in=list(table_ids),
                    metadata__table_row_index__in=list(row_indices),
                )
                .only("id", "chunk_index", "metadata", "upload_id")
            )
            for chunk in row_chunks:
                chunk_meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
                table_id = str(chunk_meta.get("table_id") or "")
                row_index = chunk_meta.get("table_row_index")
                try:
                    row_index_int = int(row_index)
                except (TypeError, ValueError):
                    continue
                if not table_id:
                    continue
                key = (table_id, row_index_int)
                if key not in row_chunk_map:
                    row_chunk_map[key] = chunk
        snippets: list[KnowledgeSnippet] = []
        for row_id in row_priority:
            row = row_map.get(row_id)
            if not row:
                continue
            table = row.table
            upload = getattr(table, "upload", None)
            if (
                not table
                or not upload
                or upload.business_profile_id != business_profile.id
                or upload.visibility == KnowledgeVisibility.INTERNAL
            ):
                continue
            business_name = getattr(upload.business_profile, "name", None)
            cells = sorted(row.cells.all(), key=lambda c: c.column_index)
            structured: list[dict[str, str]] = []
            for cell in cells:
                column_label = cell.column_key or f"column_{cell.column_index + 1}"
                cell_value = cell.raw_text or ""
                structured.append(
                    {
                        "column": column_label,
                        "value": cell_value,
                    }
                )
            visible_structured = [entry for entry in structured if not _is_total_column(entry.get("column"))]
            if not visible_structured:
                visible_structured = structured
            summary_candidates = [
                (entry["column"], entry["value"])
                for entry in visible_structured
                if entry["value"]
            ]
            base_candidates = summary_candidates[:8]
            highlight_candidates = [
                item
                for item in summary_candidates[8:]
                if _is_numeric_value(item[1])
            ]
            display_parts: list[str] = []
            seen_parts: set[str] = set()
            for column, value in base_candidates + highlight_candidates:
                if not value:
                    continue
                text = f"{column}: {value}"
                if text in seen_parts:
                    continue
                seen_parts.add(text)
                display_parts.append(text)
            summary = "; ".join(display_parts) or (row.raw_text or "")
            content_parts = [f"{column}: {value}" for column, value in summary_candidates]
            content = "\n".join(content_parts) or (row.raw_text or summary)
            structured_table = {
                "title": table.title or table.section_heading or "Table",
                "columns": [entry["column"] for entry in visible_structured],
                "rows": [[entry["value"] for entry in visible_structured]],
                "metadata": {
                    "sheet_name": (table.metadata or {}).get("sheet_name"),
                    "row_index": row.row_index,
                    "table_order_index": table.order_index,
                },
            }
            ingestion_diag = ingestion_diag_cache.get(upload.id)
            if ingestion_diag is None:
                ingestion_diag = self._table_ingestion_diagnostics(upload)
                ingestion_diag_cache[upload.id] = ingestion_diag
            table_truncated = bool(ingestion_diag.get("table_truncated"))
            structured_table["metadata"]["table_truncated"] = table_truncated
            diag = dict(cell_diag.get(row_id, {}))
            diag.update(
                {
                    "table_id": str(table.id),
                    "row_index": row.row_index,
                    "table_truncated": table_truncated,
                    "table_total_rows": ingestion_diag.get("total_rows"),
                    "table_indexed_rows": ingestion_diag.get("indexed_rows"),
                    "table_row_cap": ingestion_diag.get("row_cap"),
                    "table_partial_tables": ingestion_diag.get("partial_tables"),
                    "truncated_rows": ingestion_diag.get("truncated_rows"),
                    "truncated_columns": ingestion_diag.get("truncated_columns"),
                    "truncated_tables": ingestion_diag.get("truncated_tables"),
                }
            )
            entity_hint = diag.get("value") or diag.get("column_key") or structured_table["title"]
            row_chunk = row_chunk_map.get((str(table.id), int(row.row_index)))
            snippet_id = row_chunk.id if row_chunk else row.id
            snippet_chunk_id = row_chunk.id if row_chunk else None
            snippet_chunk_index = row_chunk.chunk_index if row_chunk else None
            snippets.append(
                KnowledgeSnippet(
                    id=snippet_id,
                    title=structured_table["title"],
                    summary=summary or structured_table["title"],
                    source="table_direct",
                    content=content,
                    public_label=structured_table["title"],
                    structured_tables=(structured_table,),
                    issues=tuple(),
                    page_summaries=tuple(),
                    read_state=KNOWLEDGE_READ_STATE_SUMMARY,
                    topic_hints=tuple(),
                    is_pinned=False,
                    upload_id=table.upload_id,
                    chunk_id=snippet_chunk_id,
                    chunk_index=snippet_chunk_index,
                    entity_type=table.section_heading or "table_row",
                    entity_name=entity_hint,
                    entity_business=business_name,
                    is_table_chunk=True,
                    table_id=str(table.id),
                    page_number=row.page_number or (table.page.page_number if table.page else None),
                    aliases=tuple(),
                    search_stage="table_direct",
                    confidence_score=self._clamp_unit(self._safe_float(diag.get("similarity"), default=0.0)),
                    truncated=False,
                    source_diagnostics=diag,
                    partial_index=table_truncated,
                    structured_table_count=1,
                    issue_count=0,
                    structured_table_hint=diag.get("column_key"),
                )
            )
        return tuple(snippets)
