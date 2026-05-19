from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeVisibility
from apps.core.logging_utils import LogEmoji, log_start, log_success
from apps.knowledge.datasets.cards import build_dataset_card_segment_payload
from apps.knowledge.ingestion.chunk_canonical import IngestionChunkCanonicalMixin
from apps.knowledge.ingestion.chunk_quality import IngestionChunkQualityMixin
from apps.knowledge.ingestion.chunk_residuals import IngestionChunkResidualsMixin
from apps.knowledge.ingestion.chunk_text_segments import IngestionChunkTextSegmentsMixin
from apps.knowledge.ingestion.contracts import PageLayout
from apps.knowledge.ingestion.signals import OCR_NORMALIZATION_VERSION
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadShadowChunk,
    KnowledgeUploadTable,
)
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


class IngestionChunksMixin(
    IngestionChunkTextSegmentsMixin,
    IngestionChunkQualityMixin,
    IngestionChunkCanonicalMixin,
    IngestionChunkResidualsMixin,
):

    def _build_chunks(
        self,
        upload: KnowledgeUpload,
        content: str,
        *,
        entities: Sequence[Mapping[str, Any]] | None = None,
        ingestion_metadata: Mapping[str, Any] | None = None,
        pages: Sequence[PageLayout] | None = None,
        format_hint: str | None = None,
        shadow_ingestion: bool = False,
    ) -> tuple[int, list[str], list[KnowledgeUploadChunk]]:
        """
        Build semantic chunks from either structured entities or sliding windows of text/tables.
        """
        from apps.knowledge.models import KnowledgeUploadTable

        entity_payloads = list(entities or [])
        feature_flags: Mapping[str, Any] = {}
        if isinstance(ingestion_metadata, Mapping):
            raw_flags = ingestion_metadata.get("feature_flags")
            if isinstance(raw_flags, Mapping):
                feature_flags = raw_flags
        alias_hygiene = bool(feature_flags.get("rag_alias_hygiene"))
        quality_filter_enabled = bool(feature_flags.get("rag_chunk_quality_filter"))
        dedupe_enabled = bool(feature_flags.get("rag_chunk_dedupe"))
        used_page_blocks = False
        if entity_payloads:
            segment_payloads = self._build_entity_segment_payloads(entity_payloads, alias_hygiene=alias_hygiene)
        else:
            segment_payloads = []
            flat_text_segment_payloads = self._build_flat_text_segment_payloads(
                content,
                alias_hygiene=alias_hygiene,
            )
            if pages:
                page_segments = self._build_text_segments_from_blocks(pages, alias_hygiene=alias_hygiene)
                if page_segments:
                    text_chunk_source_decision = self._text_chunk_source_decision(
                        pages=pages,
                        page_segments=page_segments,
                        flat_segments=flat_text_segment_payloads,
                    )
                    if isinstance(ingestion_metadata, dict):
                        ingestion_metadata["text_chunk_source_decision"] = text_chunk_source_decision
                    logger.info(
                        "chunk.text_source.decision upload=%s selected=%s reason=%s diagnostics=%s",
                        upload.id,
                        text_chunk_source_decision.get("selected_source"),
                        text_chunk_source_decision.get("reason"),
                        text_chunk_source_decision,
                    )
                    if text_chunk_source_decision.get("selected_source") == "flat_text":
                        segment_payloads.extend(flat_text_segment_payloads)
                    elif self._should_prefer_flat_text_segments(
                        pages=pages,
                        page_segments=page_segments,
                        flat_segments=flat_text_segment_payloads,
                    ):
                        segment_payloads.extend(flat_text_segment_payloads)
                    else:
                        segment_payloads.extend(page_segments)
                        used_page_blocks = True
                elif isinstance(ingestion_metadata, dict):
                    ingestion_metadata["text_chunk_source_decision"] = {
                        "pages_available": True,
                        "page_segment_count": 0,
                        "flat_segment_count": len(flat_text_segment_payloads),
                        "selected_source": "flat_text",
                        "reason": "no_page_segments",
                    }
            if not segment_payloads:
                if isinstance(ingestion_metadata, dict) and "text_chunk_source_decision" not in ingestion_metadata:
                    ingestion_metadata["text_chunk_source_decision"] = {
                        "pages_available": bool(pages),
                        "page_segment_count": 0,
                        "flat_segment_count": len(flat_text_segment_payloads),
                        "selected_source": "flat_text",
                        "reason": "flat_text_fallback_only",
                    }
                segment_payloads.extend(flat_text_segment_payloads)

            table_segment_payloads: list[dict[str, Any]] = []
            privacy_rules = self._table_privacy_rules(upload)
            schema_chunking = self.table_schema_chunking
            try:
                tables = (
                    KnowledgeUploadTable.objects.filter(upload=upload)
                    .order_by("order_index")
                    .prefetch_related("rows__cells")
                )
                for t in tables:
                    table_metadata = t.metadata if isinstance(t.metadata, dict) else {}
                    quality_score = table_metadata.get("quality_score")
                    is_decorative = table_metadata.get("is_decorative")
                    quality_signals = table_metadata.get("quality_signals")
                    strong_noise_signal = False
                    if isinstance(quality_signals, dict):
                        strong_noise_signal = bool(
                            quality_signals.get("card_mockup") or quality_signals.get("spaced_characters")
                        )
                    if is_decorative is True and strong_noise_signal:
                        logger.info(
                            "table.preview.skip_decorative upload=%s table=%s score=%s signals=%s",
                            upload.id,
                            getattr(t, "id", None),
                            quality_score,
                            list(quality_signals.keys()) if isinstance(quality_signals, dict) else None,
                        )
                        continue
                    if isinstance(quality_score, (int, float)) and quality_score <= 0.2:
                        logger.info(
                            "table.preview.skip_low_quality upload=%s table=%s score=%s",
                            upload.id,
                            getattr(t, "id", None),
                            quality_score,
                        )
                        continue
                    raw_schema = list(map(str, (t.column_schema or [])))
                    header_labels = self._table_header_labels_for_model(t, raw_schema)
                    column_map, hidden_columns = self._table_column_map_for_model(
                        header_labels, raw_schema, privacy_rules
                    )
                    if not column_map:
                        continue
                    title = t.title or f"Table {t.order_index}"
                    base_metadata: dict[str, Any] = {
                        "strategy": "table_schema",
                        "is_table_chunk": True,
                        "table_title": title,
                        "table_id": str(t.id),
                        "table_order_index": t.order_index,
                        "table_page_number": t.page.page_number if t.page else None,  # FIXED: t.page_number doesn't exist
                        "index_type": "table",
                        "region_role": "table",
                        "visibility": getattr(upload, "visibility", KnowledgeVisibility.PRIVATE),
                    }
                    if self.ocr_normalization_enabled:
                        base_metadata["ocr_normalized"] = True
                        base_metadata["ocr_normalization_version"] = OCR_NORMALIZATION_VERSION
                    if hidden_columns:
                        base_metadata["restricted_columns"] = hidden_columns[:8]
                    table_metadata = t.metadata if isinstance(t.metadata, dict) else {}
                    for key in ("entity_type", "entity_name", "entity_business"):
                        if table_metadata.get(key):
                            base_metadata[key] = table_metadata[key]
                    if "quality_score" in table_metadata:
                        base_metadata["table_quality_score"] = table_metadata["quality_score"]
                    if "is_decorative" in table_metadata:
                        base_metadata["table_is_decorative"] = table_metadata["is_decorative"]
                    if "quality_signals" in table_metadata:
                        base_metadata["table_quality_signals"] = table_metadata["quality_signals"]
                    if table_metadata.get("page_anchor"):
                        base_metadata["page_anchor"] = table_metadata["page_anchor"]

                    if schema_chunking:
                        parent_text, truncated = self._table_parent_markdown_from_model(
                            table=t,
                            column_map=column_map,
                            raw_schema=raw_schema,
                            privacy_rules=privacy_rules,
                            max_rows=self.table_parent_max_rows,
                            max_chars=self.table_parent_max_chars,
                        )
                        # DISABLED: Parent chunks create column-position ambiguity when LLM
                        # processes multiple tables with different column orders.
                        # Row chunks (key: value format) are semantically unambiguous.
                        # See: llm_confusion_diagnosis.md
                        # if parent_text:
                        #     parent_meta = dict(base_metadata)
                        #     parent_meta.update(
                        #         {
                        #             "content_source": "table_parent",
                        #             "table_chunk_role": "parent",
                        #             "is_table_preview": True,
                        #             "table_parent_truncated": truncated,
                        #         }
                        #     )
                        #     table_segment_payloads.append({"text": parent_text, "metadata": parent_meta})
                        row_payloads = self._table_row_chunk_payloads(
                            table=t,
                            column_map=column_map,
                            raw_schema=raw_schema,
                            privacy_rules=privacy_rules,
                            base_metadata=base_metadata,
                            max_rows=self.table_child_max_rows,
                        )
                        table_segment_payloads.extend(row_payloads)

                        # Two-tier table retrieval: emit a single summary chunk
                        # per row shard for primary search. Row chunks (above) are
                        # tagged search_tier="drill_down" and excluded from the
                        # primary search index, then pulled via expansion.
                        if self.table_summary_enabled and row_payloads:
                            row_label_entries: list[dict[str, Any]] = []
                            for payload in row_payloads:
                                payload_meta = payload.get("metadata")
                                if not isinstance(payload_meta, Mapping):
                                    continue
                                label = str(payload_meta.get("row_label") or "").strip()
                                if not label:
                                    continue
                                row_label_entries.append(
                                    {
                                        "label": label,
                                        "row_index": payload_meta.get("table_row_index"),
                                        "shard_index": payload_meta.get("table_row_shard_index"),
                                    }
                                )
                            summary_payloads = self._table_summary_chunk_payloads(
                                table=t,
                                column_map=column_map,
                                base_metadata=base_metadata,
                                total_data_rows=len(row_payloads),
                                row_label_entries=row_label_entries,
                            )
                            table_segment_payloads.extend(summary_payloads)
                    else:
                        cols = [entry[0] for entry in column_map]
                        tsv_lines: list[str] = []
                        header_line = "\t".join(cols) if cols else ""
                        if header_line:
                            tsv_lines.append(header_line)

                        data_row_count = 0
                        for r in t.rows.all():
                            if (r.metadata or {}).get("row_type") == "header":
                                continue
                            row_attributes = self._row_model_attributes(r, raw_schema)
                            if self._row_is_internal(row_attributes, privacy_rules):
                                continue
                            canonical_lookup = {
                                self._canonical_column_name(key, key): value
                                for key, value in row_attributes.items()
                            }
                            cells = [canonical_lookup.get(entry[1], "") for entry in column_map]
                            if any(cells):
                                tsv_lines.append("\t".join(cells))
                                data_row_count += 1
                            if data_row_count >= 12:
                                break

                        if len(tsv_lines) <= 1:
                            continue

                        if tsv_lines:
                            preface = []
                            if t.section_heading:
                                preface.append(f"[Section] {t.section_heading}")
                            preface.append(f"[Table] {title}")
                            table_block = "\n".join(preface + tsv_lines)
                            if len(table_block) <= 1500:
                                blocks = [table_block]
                            else:
                                blocks = []
                                current: list[str] = []
                                current_len = 0
                                for line in (preface + tsv_lines):
                                    if current_len + len(line) + 1 > 1500 and current:
                                        blocks.append("\n".join(current))
                                        current, current_len = [], 0
                                    current.append(line)
                                    current_len += len(line) + 1
                                if current:
                                    blocks.append("\n".join(current))

                            legacy_meta = dict(base_metadata)
                            legacy_meta.update(
                                {
                                    "content_source": "table_preview",
                                    "table_chunk_role": "preview",
                                    "is_table_preview": True,
                                }
                            )
                            alias_list = legacy_meta.get("aliases") or []
                            for block in blocks:
                                if not block:
                                    continue
                                block_text = self._append_identifier_line(block, alias_list) if alias_list else block
                                table_segment_payloads.append({"text": block_text, "metadata": dict(legacy_meta)})
            except Exception:
                table_segment_payloads = []

            segment_payloads.extend(table_segment_payloads)
            if segment_payloads:
                segment_payloads, residual_reconciliation = self._reconcile_table_residual_segments(segment_payloads)
                if isinstance(ingestion_metadata, dict):
                    ingestion_metadata["table_residual_reconciliation"] = residual_reconciliation

        dataset_card = build_dataset_card_segment_payload(upload=upload, ingestion_metadata=ingestion_metadata)
        if dataset_card:
            segment_payloads.append(dataset_card)

        canonical_projection_stats: dict[str, Any] = {}
        if segment_payloads:
            segment_payloads, canonical_projection_stats = self._canonicalize_segment_payloads(
                upload=upload,
                segment_payloads=segment_payloads,
            )
            if isinstance(ingestion_metadata, dict):
                ingestion_metadata["canonical_chunk_projection"] = canonical_projection_stats

        if self.evidence_grouping_enabled and segment_payloads:
            self._assign_evidence_group_metadata(upload=upload, segment_payloads=segment_payloads)

        quality_stats = {
            "evaluated": 0,
            "short_tokens": 0,
            "heading_only": 0,
            "low_unique_ratio": 0,
            "low_quality": 0,
            "filtered": 0,
            "deduped": 0,
        }
        scored_payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metrics = self._chunk_quality_metrics(text)
            metadata.update(metrics)
            payload["metadata"] = metadata
            if self._segment_is_text(metadata):
                quality_stats["evaluated"] += 1
                if metrics["chunk_quality_tokens"] < self.chunk_quality_min_tokens:
                    quality_stats["short_tokens"] += 1
                if metrics["chunk_heading_only"]:
                    quality_stats["heading_only"] += 1
                if metrics["chunk_quality_unique_ratio"] < self.chunk_quality_min_unique_ratio:
                    quality_stats["low_unique_ratio"] += 1
                if metrics["chunk_quality_score"] < self.chunk_quality_low_score:
                    quality_stats["low_quality"] += 1
            scored_payloads.append(payload)

        segment_payloads = scored_payloads

        if quality_filter_enabled and segment_payloads:
            filtered_payloads: list[dict[str, Any]] = []
            for payload in segment_payloads:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if self._segment_is_text(metadata) and not metadata.get("is_dataset_card"):
                    if self._payload_is_table_residual(metadata) or self._payload_is_table_annotation(metadata):
                        filtered_payloads.append(payload)
                        continue
                    if self._is_low_quality_text_chunk(metadata):
                        quality_stats["filtered"] += 1
                        continue
                filtered_payloads.append(payload)
            if not filtered_payloads:
                filtered_payloads = segment_payloads[:1]
            segment_payloads = filtered_payloads

        if dedupe_enabled and segment_payloads:
            deduped_payloads: list[dict[str, Any]] = []
            seen_fingerprints: set[str] = set()
            for payload in segment_payloads:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
                if self._segment_is_text(metadata) and not metadata.get("is_dataset_card"):
                    fingerprint = self._chunk_fingerprint(str(payload.get("text") or ""))
                    if fingerprint and fingerprint in seen_fingerprints:
                        quality_stats["deduped"] += 1
                        continue
                    if fingerprint:
                        seen_fingerprints.add(fingerprint)
                deduped_payloads.append(payload)
            if not deduped_payloads:
                deduped_payloads = segment_payloads[:1]
            segment_payloads = deduped_payloads

        if isinstance(ingestion_metadata, dict):
            ingestion_metadata["chunk_quality_stats"] = {
                **quality_stats,
                "min_tokens": self.chunk_quality_min_tokens,
                "min_unique_ratio": self.chunk_quality_min_unique_ratio,
                "low_score_threshold": self.chunk_quality_low_score,
            }

        KnowledgeUploadChunk.objects.filter(upload=upload).delete()
        if shadow_ingestion:
            KnowledgeUploadShadowChunk.objects.filter(upload=upload).delete()
        if not segment_payloads:
            return 0, [], []

        logger.info(
            "embed.start upload=%s segments=%s provider=%s model=%s",
            upload.id,
            len(segment_payloads),
            type(self.embedding_service).__name__ if self.embedding_service else None,
            getattr(self.embedding_service, "model", "local"),
        )

        total_segments = len(segment_payloads)
        inline_limit = total_segments
        if self.ingest_inline_chunk_limit:
            inline_limit = min(total_segments, self.ingest_inline_chunk_limit)
        embeddings: list[list[float] | None] = [None] * total_segments
        if self.embedding_service and inline_limit:
            try:
                inline_vectors = self.embedding_service.embed_texts(
                    [payload["text"] for payload in segment_payloads[:inline_limit]]
                )
                for idx, vector in enumerate(inline_vectors):
                    embeddings[idx] = self._normalize_embedding(vector)
            except EmbeddingProviderError as exc:
                logger.warning("Embedding generation failed upload=%s error=%s", upload.id, exc)
            except Exception:
                logger.exception("Unexpected embedding failure upload=%s", upload.id)

        got_vectors = len([v for v in embeddings if v])
        staged_vectors = total_segments - inline_limit if self.ingest_inline_chunk_limit else 0
        logger.info(
            "embed.inline upload=%s segments=%s inline=%s staged=%s got_vectors=%s",
            upload.id,
            total_segments,
            inline_limit,
            staged_vectors,
            got_vectors,
        )

        chunk_objects: list[KnowledgeUploadChunk] = []
        fallback_targets: list[KnowledgeUploadChunk] = []
        for index, payload in enumerate(segment_payloads):
            segment_text = payload.get("text") or ""
            vector = None
            if embeddings and index < len(embeddings):
                vector = embeddings[index]

            chunk_metadata = {
                "strategy": "json_entity"
                if entity_payloads
                else ("page_blocks_plus_tables" if used_page_blocks else "sliding_window_plus_tables"),
            }
            extra_meta = payload.get("metadata") or {}
            if isinstance(extra_meta, dict):
                chunk_metadata.update(extra_meta)
            chunk_metadata.setdefault("is_table_chunk", False)
            if entity_payloads:
                chunk_metadata.setdefault("index_type", "entity")
            else:
                chunk_metadata.setdefault("index_type", "text")
            self._finalize_alias_metadata(chunk_metadata)

            chunk = KnowledgeUploadChunk(
                upload=upload,
                business_profile=upload.business_profile,
                chunk_index=index,
                content=segment_text,
                token_count=len(segment_text.split()),
                embedding=vector,
                metadata=chunk_metadata,
            )
            if chunk.embedding is None:
                fallback_targets.append(chunk)
            chunk_objects.append(chunk)

        prewarmed = 0
        if (
            self.embedding_prewarm_limit
            and fallback_targets
            and len(fallback_targets) <= self.embedding_prewarm_limit
            and self.embedding_service
        ):
            try:
                vectors = self.embedding_service.embed_texts([chunk.content or "" for chunk in fallback_targets])
                for chunk, vector in zip(fallback_targets, vectors):
                    normalized = self._normalize_embedding(vector)
                    if normalized:
                        chunk.embedding = normalized
                        prewarmed += 1
            except EmbeddingProviderError as exc:
                logger.warning("Embedding prewarm failed upload=%s error=%s", upload.id, exc)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Embedding prewarm unexpected failure upload=%s", upload.id)
            if prewarmed:
                logger.info("embed.prewarm upload=%s chunks=%s", upload.id, prewarmed)
        if prewarmed:
            fallback_targets = [chunk for chunk in fallback_targets if chunk.embedding is None]

        backfilled = 0
        if fallback_targets:
            backfilled = self._apply_fallback_embeddings(upload, fallback_targets)
            if backfilled:
                logger.info(
                    "embed.fallback upload=%s requested=%s backfilled=%s",
                    upload.id,
                    len(fallback_targets),
                    backfilled,
                )
            else:
                logger.warning(
                    "embed.fallback_failed upload=%s requested=%s",
                    upload.id,
                    len(fallback_targets),
                )

        missing_chunk_ids = [str(chunk.id) for chunk in chunk_objects if chunk.embedding is None]

        shadow_objects: list[KnowledgeUploadShadowChunk] = []
        if shadow_ingestion:
            for chunk in chunk_objects:
                shadow_meta = dict(chunk.metadata or {})
                shadow_meta["shadow_index"] = True
                shadow_meta.setdefault("shadow_source", "baseline")
                shadow_objects.append(
                    KnowledgeUploadShadowChunk(
                        upload=chunk.upload,
                        business_profile=chunk.business_profile,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        token_count=chunk.token_count,
                        embedding=chunk.embedding,
                        metadata=shadow_meta,
                    )
                )

        # Explicitly set tenant context for RLS - ensures app.current_tenant is set
        # so PostgreSQL row-level security allows the inserts. Without this, RLS
        # silently discards the rows when bulk_create runs.
        business_id = upload.business_profile_id
        with tenant_context(business_id):
            KnowledgeUploadChunk.objects.bulk_create(chunk_objects, batch_size=100)
            # Verify chunks were actually persisted (RLS can silently discard)
            actual_count = KnowledgeUploadChunk.objects.filter(upload=upload).count()

        if actual_count != len(chunk_objects):
            logger.error(
                "chunks.persistence_mismatch upload=%s expected=%s actual=%s business=%s "
                "hint=RLS may have discarded inserts due to missing tenant context",
                upload.id,
                len(chunk_objects),
                actual_count,
                business_id,
            )
        logger.info(
            "chunks.persisted upload=%s count=%s actual=%s missing_embeddings=%s",
            upload.id,
            len(chunk_objects),
            actual_count,
            len(missing_chunk_ids),
        )
        # New emoji-enhanced logging
        log_success(
            logger,
            "CHUNKS COMMITTED",
            f"{actual_count} chunks persisted",
            {
                "upload_id": upload.id,
                "expected": len(chunk_objects),
                "missing_embeddings": len(missing_chunk_ids),
            },
            emoji=LogEmoji.SUCCESS,
        )
        if shadow_objects:
            shadow_missing = sum(1 for chunk in shadow_objects if chunk.embedding is None)
            with tenant_context(business_id):
                KnowledgeUploadShadowChunk.objects.bulk_create(shadow_objects, batch_size=100)
                shadow_actual = KnowledgeUploadShadowChunk.objects.filter(upload=upload).count()
            if shadow_actual != len(shadow_objects):
                logger.error(
                    "shadow.chunks.persistence_mismatch upload=%s expected=%s actual=%s business=%s",
                    upload.id,
                    len(shadow_objects),
                    shadow_actual,
                    business_id,
                )
            logger.info(
                "shadow.chunks.persisted upload=%s count=%s actual=%s missing_embeddings=%s",
                upload.id,
                len(shadow_objects),
                shadow_actual,
                shadow_missing,
            )
        if missing_chunk_ids:
            self._schedule_embedding_jobs(upload, missing_chunk_ids)
        return len(chunk_objects), missing_chunk_ids, chunk_objects
