from __future__ import annotations

from typing import Mapping, Sequence

from apps.knowledge.models import KnowledgeUploadChunk
from apps.rag.contracts import (
    ChunkResult,
    KnowledgeSnippet,
    KNOWLEDGE_READ_STATE_FULL,
    KNOWLEDGE_READ_STATE_PREVIEW,
    KNOWLEDGE_READ_STATE_SUMMARY,
)


class SnippetBuilderMixin:
    def _chunk_to_snippet(
        self,
        chunk: KnowledgeUploadChunk,
        *,
        result: ChunkResult | None = None,
        search_stage: str | None = None,
        content_mode: str = "abstract",
        table_row_sample: tuple[Mapping[str, object], ...] | None = None,
        query_text: str | None = None,
        query_tokens: Sequence[str] | None = None,
    ) -> KnowledgeSnippet:
        upload = chunk.upload
        label = self._public_label(upload)
        chunk_number = (chunk.chunk_index or 0) + 1 if chunk.chunk_index is not None else None
        title = f"{label} – chunk {chunk_number}" if chunk_number else label or "Document"
        summary = self._summarize_chunk(chunk)
        sample_text = ""
        evidence_span_text = ""

        chunk_metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        is_table_chunk_flag = bool(chunk_metadata.get("is_table_chunk"))

        if table_row_sample and is_table_chunk_flag:
            pairs: list[str] = []
            first = table_row_sample[0] if table_row_sample else None
            if isinstance(first, Mapping) and isinstance(first.get("columns"), list) and isinstance(first.get("rows"), list):
                cols = [str(c) for c in (first.get("columns") or [])]
                rows = first.get("rows") or []
                first_row = rows[0] if rows else None
                if isinstance(first_row, list):
                    for col, val in zip(cols, first_row):
                        value = str(val or "").strip()
                        label = str(col or "").strip()
                        if not value or not label:
                            continue
                        pairs.append(f"{label}: {value}")
                        if len(pairs) >= 8:
                            break
            else:
                for entry in table_row_sample:
                    if not isinstance(entry, Mapping):
                        continue
                    col = entry.get("column") or ""
                    val = entry.get("value") or ""
                    combined = f"{col}: {val}".strip(": ")
                    if combined:
                        pairs.append(combined)
                        if len(pairs) >= 8:
                            break
            if pairs:
                sample_text = "; ".join(pairs)[:500]

        if sample_text and is_table_chunk_flag:
            summary = sample_text[:500]

        if (not is_table_chunk_flag) and (query_text or query_tokens):
            evidence_span_text = self._best_text_evidence_span(
                chunk.content or "",
                query_text=query_text or "",
                tokens=tuple(query_tokens or ()),
                max_chars=280,
            )
            if evidence_span_text:
                summary = evidence_span_text

        truncated = False
        if content_mode == "abstract" and not is_table_chunk_flag:
            content = summary
            read_state = KNOWLEDGE_READ_STATE_SUMMARY
        elif content_mode == "abstract" and is_table_chunk_flag:
            content, truncated = self._trim_with_flag(chunk.content, max_chars=self.search_preview_char_limit)
            read_state = KNOWLEDGE_READ_STATE_FULL if content and len(content) >= self.table_ready_threshold else KNOWLEDGE_READ_STATE_PREVIEW
        else:
            content, truncated = self._trim_with_flag(chunk.content, max_chars=self.search_preview_char_limit)
            read_state = KNOWLEDGE_READ_STATE_PREVIEW if truncated else self._read_state_for_content(content, chunk_metadata)

        entity_type = chunk_metadata.get("entity_type")
        entity_name = chunk_metadata.get("entity_name")
        entity_business = chunk_metadata.get("entity_business")
        is_table_chunk = bool(chunk_metadata.get("is_table_chunk"))
        evidence_group_id = str(chunk_metadata.get("evidence_group_id") or "").strip() or None
        evidence_type = str(chunk_metadata.get("evidence_type") or "").strip() or None
        representation = str(chunk_metadata.get("representation") or "").strip().lower() or None
        aliases = tuple(chunk_metadata.get("aliases") or ())
        table_count, issue_count = self._structured_counts(upload)
        structured_preview: tuple[Mapping[str, object], ...] = tuple(table_row_sample or ())
        table_hint = None
        if table_count and not structured_preview:
            label_name = "tables" if table_count != 1 else "table"
            table_hint = f"{table_count} structured {label_name} available via load_document"

        source_stage = search_stage or (result.source_stage if result else None)
        confidence = self._public_confidence_score(result)
        diagnostics = dict(result.diagnostics) if result else {}
        if result and result.vector_distance is not None:
            diagnostics.setdefault("vector_distance", result.vector_distance)
        if is_table_chunk:
            for key in (
                "table_id",
                "table_order_index",
                "table_row_index",
                "table_page_number",
                "table_title",
                "table_quality_score",
                "table_is_decorative",
                "table_row_contract_version",
                "table_row_observed_value_columns",
                "table_row_qualifier_columns",
                "table_row_scope_dimension_columns",
                "table_row_inferred_scope_columns",
                "table_row_scope_reason",
                "table_row_scope_confidence",
                "table_row_fee_value",
                "table_row_evidence_cell_ids",
            ):
                if key in diagnostics:
                    continue
                value = chunk_metadata.get(key)
                if value is None or value == "":
                    continue
                diagnostics[key] = value
            ingestion_meta = upload.ingestion_metadata if isinstance(getattr(upload, "ingestion_metadata", None), Mapping) else {}
            format_hint = str(ingestion_meta.get("format") or "").strip().lower()
            if format_hint in {"pdf", "docx"}:
                diagnostics["table_read_only"] = True
        diagnostics["structured_table_count"] = table_count
        diagnostics["issue_count"] = issue_count
        trunc_metrics = self._truncation_metrics(upload)
        if trunc_metrics:
            diagnostics.update(trunc_metrics)
        if evidence_span_text:
            diagnostics["evidence_span"] = True
            diagnostics["evidence_span_chars"] = len(evidence_span_text)
        partial_flag = bool(trunc_metrics.get("partial_index")) if trunc_metrics else False

        return KnowledgeSnippet(
            id=chunk.id,
            title=title,
            summary=summary,
            source=upload.get_source_type_display(),
            content=content,
            content_mode=content_mode,
            public_label=label,
            structured_tables=structured_preview,
            issues=tuple(),
            page_summaries=tuple(),
            read_state=read_state,
            topic_hints=tuple(),
            is_pinned=False,
            upload_id=upload.id,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            entity_type=entity_type,
            entity_name=entity_name,
            entity_business=entity_business,
            is_table_chunk=is_table_chunk,
            table_id=str(chunk_metadata.get("table_id") or "") or None,
            evidence_group_id=evidence_group_id,
            evidence_type=evidence_type,
            representation=representation,
            aliases=aliases,
            search_stage=source_stage,
            confidence_score=confidence,
            truncated=truncated,
            source_diagnostics=diagnostics,
            partial_index=partial_flag,
            structured_table_count=table_count,
            issue_count=issue_count,
            structured_table_hint=table_hint,
        )
