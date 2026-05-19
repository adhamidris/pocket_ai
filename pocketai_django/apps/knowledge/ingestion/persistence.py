from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Mapping, Sequence

from django.db import transaction
from django.utils import timezone

from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import KnowledgeIssueSeverity, KnowledgeStatus
from apps.knowledge.ingestion.aliases import CARD_NUMBER_PATTERN
from apps.knowledge.ingestion.contracts import (
    ExtractionResult,
    IssuePayload,
    KnowledgeIngestionError,
    PageBlockPayload,
    PageLayout,
)
from apps.knowledge.ingestion.lexicon_auto_learning import IngestionLexiconAutoLearningMixin
from apps.knowledge.ingestion.quality_report import IngestionQualityReportMixin
from apps.knowledge.ingestion.search_indexing import IngestionSearchIndexingMixin
from apps.knowledge.ingestion.text_utils import IngestionTextUtilsMixin
from apps.knowledge.models import (
    KnowledgeEntity,
    KnowledgeTableColumn,
    KnowledgeUpload,
    KnowledgeUploadIssue,
    KnowledgeUploadPage,
    KnowledgeUploadPageBlock,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
    KnowledgeUploadText,
)
from apps.rag.table_semantics import normalize_column_name
from apps.rag.quality_monitor import QualityMonitor


logger = logging.getLogger(__name__)


class IngestionPersistenceMixin(
    IngestionLexiconAutoLearningMixin,
    IngestionSearchIndexingMixin,
    IngestionQualityReportMixin,
):


    @staticmethod
    def _derive_table_section_heading_from_blocks(
        *,
        table_bbox: Mapping[str, Any],
        page_blocks: Sequence[PageBlockPayload],
        page_number: int | None,
        page_height: float | None,
    ) -> tuple[str, str | None]:
        """
        Best-effort extraction of a table "section heading" from the page text blocks.

        Motivation: many PDFs render headings (e.g. "Fees & Charges") as a separate
        block above the table. If we only index the table rows, broad queries like
        "plus fees" may never retrieve the table even though it's relevant.

        Heuristic: pick the closest *heading-like* block above the table bbox with
        sufficient horizontal overlap. Returns (heading_text, heading_anchor).
        """

        def _bbox_tuple(bbox: Mapping[str, Any]) -> tuple[float, float, float, float]:
            try:
                x0 = float(bbox.get("x0") or 0.0)
                y0 = float(bbox.get("y0") or 0.0)
                x1 = float(bbox.get("x1") or 0.0)
                y1 = float(bbox.get("y1") or 0.0)
            except Exception:
                return 0.0, 0.0, 0.0, 0.0
            return x0, y0, x1, y1

        def _heading_like(text: str) -> bool:
            if not text:
                return False
            if len(text) > 80:
                return False
            if "@" in text:
                return False
            lowered = text.lower()
            if "http://" in lowered or "https://" in lowered or "www." in lowered:
                return False
            if CARD_NUMBER_PATTERN.search(text):
                return False
            # Loose phone-number-like detector (avoid copying PII-ish headings).
            if re.search(r"\+?\d[\d\s().-]{8,}\d", text):
                return False

            alnum = [c for c in text if c.isalnum()]
            if alnum:
                digits = sum(1 for c in alnum if c.isdigit())
                if (digits / len(alnum)) > 0.3:
                    return False

            words = text.split()
            if not words or len(words) > 12:
                return False

            # Headings tend to be short fragments, not sentences.
            if text.endswith((".", "?", "!")):
                return False
            if text.count(".") >= 2:
                return False

            return True

        table_x0, table_y0, table_x1, table_y1 = _bbox_tuple(table_bbox)
        if table_x1 <= table_x0 or table_y1 <= table_y0:
            return "", None

        table_width = max(1.0, table_x1 - table_x0)
        max_gap = 200.0
        if page_height and page_height > 0:
            max_gap = max(40.0, float(page_height) * 0.25)

        candidates: list[tuple[float, float, int, str, str | None]] = []
        for block in page_blocks:
            raw_text = IngestionTextUtilsMixin._sanitize_text(getattr(block, "text", "")).strip()
            if not raw_text:
                continue
            if "\t" in raw_text or "|" in raw_text:
                # Likely a table-like block; don't use as a heading.
                continue

            text = raw_text.replace("\n", " ")
            text = re.sub(r"\s+", " ", text).strip()
            if not _heading_like(text):
                continue

            meta = getattr(block, "metadata", None) or {}
            region_role = str(meta.get("region_role") or "").strip().lower()
            if region_role in {"table", "figure", "decorative"}:
                continue

            bx0, by0, bx1, by1 = _bbox_tuple(getattr(block, "bbox", {}) or {})
            if bx1 <= bx0 or by1 <= by0:
                continue

            # Skip page headers/footers (reduce false associations).
            if page_height and page_height > 0:
                if by1 <= float(page_height) * 0.08:
                    continue
                if by0 >= float(page_height) * 0.92:
                    continue

            # Must be above (or barely overlapping) the table.
            if by1 > (table_y0 + 2.0):
                continue

            gap = table_y0 - by1
            if gap < -2.0 or gap > max_gap:
                continue

            # Require some horizontal overlap with the table region.
            overlap = min(bx1, table_x1) - max(bx0, table_x0)
            if overlap <= 0:
                continue
            block_width = max(1.0, bx1 - bx0)
            overlap_ratio = overlap / max(1.0, min(block_width, table_width))
            if overlap_ratio < 0.3:
                continue

            anchor = None
            if page_number is not None:
                try:
                    anchor = f"p{int(page_number)}-b{int(getattr(block, 'order_index', 0))}"
                except Exception:
                    anchor = f"p{page_number}-b0"

            # Prefer: closest block above table, then best overlap.
            candidates.append((float(gap), -float(overlap_ratio), len(text), text, anchor))

        if not candidates:
            return "", None

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        _gap, _overlap, _len, best_text, best_anchor = candidates[0]
        return best_text, best_anchor



    def _persist_structured_artifacts(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> dict[str, Any]:
        KnowledgeUploadPage.objects.filter(upload=upload).delete()
        KnowledgeTableColumn.objects.filter(upload=upload).delete()  # PHASE 2: Delete indexed columns
        KnowledgeUploadTable.objects.filter(upload=upload).delete()
        KnowledgeUploadIssue.objects.filter(upload=upload).delete()

        page_content_type_max = getattr(KnowledgeUploadPage._meta.get_field("content_type"), "max_length", 100) or 100
        block_section_heading_max = getattr(
            KnowledgeUploadPageBlock._meta.get_field("section_heading"),
            "max_length",
            255,
        ) or 255
        block_language_max = getattr(
            KnowledgeUploadPageBlock._meta.get_field("detected_language"),
            "max_length",
            32,
        ) or 32
        table_title_max = getattr(KnowledgeUploadTable._meta.get_field("title"), "max_length", 255) or 255
        table_section_heading_max = getattr(
            KnowledgeUploadTable._meta.get_field("section_heading"),
            "max_length",
            255,
        ) or 255
        issue_code_max = getattr(KnowledgeUploadIssue._meta.get_field("issue_code"), "max_length", 120) or 120
        cell_column_key_max = getattr(
            KnowledgeUploadTableCell._meta.get_field("column_key"),
            "max_length",
            160,
        ) or 160

        page_lookup: dict[int, KnowledgeUploadPage] = {}
        page_payload_lookup: dict[int, PageLayout] = {}
        block_objects: list[KnowledgeUploadPageBlock] = []
        page_summaries: list[dict[str, Any]] = []

        for page_payload in extraction.pages:
            page_payload_lookup[page_payload.page_number] = page_payload
            page_obj = KnowledgeUploadPage.objects.create(
                upload=upload,
                page_number=page_payload.page_number,
                width=page_payload.width,
                height=page_payload.height,
                rotation=page_payload.rotation,
                text_density=page_payload.text_density,
                has_ocr_content=page_payload.has_ocr_content,
                content_type=self._clamp_text(page_payload.content_type, page_content_type_max),
                metadata=page_payload.metadata,
            )
            page_lookup[page_payload.page_number] = page_obj
            synopsis = self._sanitize_text(self._page_synopsis_from_blocks(page_payload.blocks))
            headings: list[str] = []
            for block in page_payload.blocks:
                if not isinstance(block.section_heading, str):
                    continue
                normalized_heading = self._sanitize_text(block.section_heading).strip()
                if normalized_heading:
                    headings.append(normalized_heading)
            page_summaries.append(
                {
                    "page_number": page_payload.page_number,
                    "text_density": page_payload.text_density,
                    "has_ocr_content": page_payload.has_ocr_content,
                    "width": page_payload.width,
                    "height": page_payload.height,
                    "synopsis": synopsis,
                    "headings": headings[:3],
                }
            )
            for block_payload in page_payload.blocks:
                block_objects.append(
                    KnowledgeUploadPageBlock(
                        upload=upload,
                        page=page_obj,
                        block_type=block_payload.block_type,
                        order_index=block_payload.order_index,
                        text=self._sanitize_text(block_payload.text),
                        bbox=block_payload.bbox,
                        section_heading=self._clamp_text(block_payload.section_heading, block_section_heading_max),
                        heading_path=[
                            self._sanitize_text(item)
                            for item in (block_payload.heading_path or [])
                        ],
                        detected_language=self._clamp_text(block_payload.detected_language, block_language_max),
                        confidence=block_payload.confidence,
                        metadata=block_payload.metadata,
                    )
                )

        if block_objects:
            KnowledgeUploadPageBlock.objects.bulk_create(block_objects, batch_size=200)

        table_lookup: dict[tuple[int, int | None], KnowledgeUploadTable] = {}
        row_lookup: dict[tuple[uuid.UUID, int], KnowledgeUploadTableRow] = {}
        cell_lookup: dict[tuple[uuid.UUID, int], KnowledgeUploadTableCell] = {}
        table_summaries: list[dict[str, Any]] = []

        for table_payload in extraction.tables:
            # Assess table quality
            quality_assessment = self._assess_table_quality(table_payload)
            
            # Log warning for column misalignment (common Azure DI extraction error)
            if quality_assessment.get('signals', {}).get('column_misalignment'):
                misaligned_cols = quality_assessment['signals'].get('misaligned_columns', [])
                logger.warning(
                    "table.quality.column_misalignment upload=%s table=%s columns=%s "
                    "hint=Header cells empty but data cells have values; may cause wrong data attribution",
                    upload.id,
                    table_payload.order_index,
                    misaligned_cols,
                )
            
            # Merge quality data into table metadata
            table_metadata = dict(table_payload.metadata or {})
            table_metadata['quality_score'] = quality_assessment['quality_score']
            table_metadata['is_decorative'] = quality_assessment['is_decorative']
            table_metadata['quality_signals'] = quality_assessment['signals']
            if table_payload.page_number:
                table_metadata["page_anchor"] = f"p{table_payload.page_number}-t{table_payload.order_index}"
            else:
                table_metadata["page_anchor"] = f"t{table_payload.order_index}"
            
            raw_section_heading = (
                self._sanitize_text(table_payload.section_heading).strip()
                if isinstance(table_payload.section_heading, str)
                else ""
            )
            derived_section_heading = ""
            derived_section_heading_anchor: str | None = None
            if not raw_section_heading:
                page_payload = page_payload_lookup.get(table_payload.page_number or -1)
                derived_section_heading, derived_section_heading_anchor = (
                    self._derive_table_section_heading_from_blocks(
                        table_bbox=table_payload.bbox or {},
                        page_blocks=(page_payload.blocks if page_payload else []),
                        page_number=(page_payload.page_number if page_payload else None),
                        page_height=(page_payload.height if page_payload else None),
                    )
                )
                derived_section_heading = self._sanitize_text(derived_section_heading).strip()
                if derived_section_heading:
                    table_metadata["derived_section_heading"] = derived_section_heading
                    table_metadata["derived_section_heading_method"] = "page_block_above_table"
                    if derived_section_heading_anchor:
                        table_metadata["derived_section_heading_anchor"] = derived_section_heading_anchor

            section_heading = raw_section_heading or derived_section_heading

            page_obj = page_lookup.get(table_payload.page_number or -1)
            table_obj = KnowledgeUploadTable.objects.create(
                upload=upload,
                page=page_obj,
                source_block=None,
                title=self._clamp_text(
                    self._derive_table_title(table_payload, upload),
                    table_title_max,
                ),
                section_heading=self._clamp_text(section_heading, table_section_heading_max),
                order_index=table_payload.order_index,
                bbox=table_payload.bbox,
                column_schema=table_payload.column_schema,
                data_dictionary=table_payload.data_dictionary,
                metadata=table_metadata,  # Include quality metadata
            )
            table_lookup[(table_payload.order_index, table_payload.page_number)] = table_obj
            
            # PHASE 2: Index table columns for column-header search
            column_objects = []
            for idx, col_name in enumerate(table_payload.column_schema or []):
                if not col_name:
                    continue
                col_str = str(col_name).strip()
                if not col_str:
                    continue
                column_objects.append(
                    KnowledgeTableColumn(
                        table=table_obj,
                        upload=upload,
                        business_profile=upload.business_profile,
                        column_index=idx,
                        column_name=col_str[:255],
                        column_normalized=normalize_column_name(col_str)[:255],
                    )
                )
            if column_objects:
                KnowledgeTableColumn.objects.bulk_create(column_objects, ignore_conflicts=True)
            
            table_summaries.append(
                {
                    "order_index": table_payload.order_index,
                    "title": table_payload.title,
                    "page_number": table_payload.page_number,
                    "row_count": len(table_payload.rows),
                    "column_schema": table_payload.column_schema,
                    "quality_score": quality_assessment['quality_score'],  # NEW
                    "is_decorative": quality_assessment['is_decorative'],  # NEW
                }
            )
            for row_payload in table_payload.rows:
                row_text = self._table_cell_text(row_payload.raw_text)
                row_obj = KnowledgeUploadTableRow.objects.create(
                    table=table_obj,
                    row_index=row_payload.row_index,
                    page_number=row_payload.page_number,
                    bbox=row_payload.bbox,
                    raw_text=row_text,
                    metadata=row_payload.metadata,
                )
                row_lookup[(table_obj.id, row_payload.row_index)] = row_obj
                for cell_payload in row_payload.cells:
                    cell_text = self._table_cell_text(cell_payload.raw_text)
                    cell_obj = KnowledgeUploadTableCell.objects.create(
                        table=table_obj,
                        row=row_obj,
                        column_index=cell_payload.column_index,
                        column_key=self._clamp_text(cell_payload.column_key, cell_column_key_max),
                        raw_text=cell_text,
                        normalized_value=cell_payload.normalized_value,
                        bbox=cell_payload.bbox,
                        confidence=cell_payload.confidence,
                        metadata=cell_payload.metadata,
                    )
                    cell_lookup[(row_obj.id, cell_payload.column_index)] = cell_obj

        issue_objects: list[KnowledgeUploadIssue] = []
        issue_summaries: list[dict[str, Any]] = []
        valid_severities = set(KnowledgeIssueSeverity.values)

        for issue in extraction.issues:
            page_obj = page_lookup.get(issue.page_number) if issue.page_number is not None else None
            table_obj = None
            if issue.table_order_index is not None:
                table_obj = table_lookup.get((issue.table_order_index, issue.page_number))
                if table_obj is None:
                    for (order_idx, _), candidate in table_lookup.items():
                        if order_idx == issue.table_order_index:
                            table_obj = candidate
                            break
            row_obj = None
            if table_obj and issue.row_index is not None:
                row_obj = row_lookup.get((table_obj.id, issue.row_index))
            cell_obj = None
            if row_obj and issue.column_index is not None:
                cell_obj = cell_lookup.get((row_obj.id, issue.column_index))
            severity_value = issue.severity
            if severity_value not in valid_severities:
                severity_value = KnowledgeIssueSeverity.INFO.value
            issue_objects.append(
                KnowledgeUploadIssue(
                    upload=upload,
                    page=page_obj,
                    table=table_obj,
                    table_row=row_obj,
                    table_cell=cell_obj,
                    issue_code=self._clamp_text(issue.code, issue_code_max),
                    severity=severity_value,
                    description=issue.description,
                    details=issue.details,
                )
            )
            issue_summaries.append(self._issue_to_dict(issue))

        if issue_objects:
            KnowledgeUploadIssue.objects.bulk_create(issue_objects, batch_size=100)

        if not (page_summaries or table_summaries or issue_summaries):
            return {}
        return {
            "pages": page_summaries,
            "tables": table_summaries,
            "issues": issue_summaries,
        }

    @staticmethod
    def _issue_to_dict(issue: IssuePayload) -> dict[str, Any]:
        return {
            "code": issue.code,
            "severity": issue.severity,
            "description": issue.description,
            "page_number": issue.page_number,
            "table_order_index": issue.table_order_index,
            "row_index": issue.row_index,
            "column_index": issue.column_index,
            "details": issue.details,
        }



    # ------------------------------------------------------------------
    # Persistence

    def _persist_extraction(self, upload: KnowledgeUpload, extraction: ExtractionResult) -> None:
        extraction = self._enrich_extraction_with_column_roles(extraction)
        normalized = self._normalize_text(extraction.text)
        if not normalized:
            raise KnowledgeIngestionError("Extracted document is empty.")

        previous_chunk_count = int(getattr(upload, "chunk_count", 0) or 0)
        now = timezone.now()
        summary = self._build_summary(normalized)
        words = len(normalized.split())
        char_count = len(normalized)

        ingestion_metadata = dict(upload.ingestion_metadata or {})
        ingestion_metadata.update(
            {
                "format": extraction.format_hint,
                "word_count": words,
                "character_count": char_count,
                "ingested_at": now.isoformat(),
            }
        )
        ingestion_metadata.update(extraction.metadata or {})
        feature_state = FeatureFlagService.snapshot(upload.business_profile)
        ingestion_metadata["feature_flags"] = feature_state.as_dict()

        defaults = {
            "content": normalized,
            "metadata": {
                "ingested_at": now.isoformat(),
                "format": extraction.format_hint,
            },
        }

        entity_payloads = list(extraction.entities or [])
        format_hint = (extraction.format_hint or "").lower()
        if format_hint in {"pdf", "application/pdf"} and entity_payloads:
            logger.info(
                "pdf.entities.disabled upload=%s business=%s entities=%s",
                upload.id,
                upload.business_profile_id,
                len(entity_payloads),
            )
            entity_payloads = []
        if entity_payloads and not feature_state.entity_chunking:
            table_entities = [e for e in entity_payloads if e.get("alias_source_type") == "table"]
            if table_entities:
                logger.info(
                    "table.entities.persist upload=%s business=%s entities=%s",
                    upload.id,
                    upload.business_profile_id,
                    len(table_entities),
                )
            else:
                logger.info(
                    "json.entities.disabled upload=%s business=%s entities=%s",
                    upload.id,
                    upload.business_profile_id,
                    len(entity_payloads),
                )
            entity_payloads = table_entities
        entity_stats: dict[str, Any] = {}
        with transaction.atomic():
            structured_summary = self._persist_structured_artifacts(upload, extraction)
            table_profile = self._build_table_profile(extraction.tables or ())
            if table_profile:
                ingestion_metadata["table_profile"] = table_profile
            else:
                ingestion_metadata.pop("table_profile", None)
            KnowledgeUploadText.objects.update_or_create(upload=upload, defaults=defaults)
            chunk_count, missing_chunk_ids, chunk_objects = self._build_chunks(
                upload,
                normalized,
                entities=entity_payloads,
                ingestion_metadata=ingestion_metadata,
                pages=extraction.pages,
                format_hint=extraction.format_hint,
                shadow_ingestion=feature_state.rag_shadow_ingestion,
            )
            quality_report = self._build_quality_report(
                upload=upload,
                extraction=extraction,
                chunk_count=chunk_count,
                chunk_objects=chunk_objects,
                structured_summary=structured_summary,
            )
            if quality_report:
                ingestion_metadata["quality_report"] = quality_report
            else:
                ingestion_metadata.pop("quality_report", None)
            if entity_payloads:
                entity_stats = self._persist_entities(upload, entity_payloads, chunk_objects)
                alias_count = entity_stats.get("alias_count", 0)
                alias_sources = entity_stats.get("alias_sources") or extraction.metadata.get("json_alias_sources") or []
                ingestion_metadata["alias_count"] = alias_count
                ingestion_metadata["alias_patterns_used"] = sorted(set(alias_sources))
                if alias_count > self.alias_warning_threshold:
                    logger.warning(
                        "json.aliases.threshold upload=%s business=%s aliases=%s threshold=%s",
                        upload.id,
                        upload.business_profile_id,
                        alias_count,
                        self.alias_warning_threshold,
                    )
            else:
                # Ensure old entity/alias records are cleared when we intentionally skip entities
                # (e.g., PDFs) or when extraction no longer yields any entities.
                existing_entities = KnowledgeEntity.objects.filter(upload=upload)
                removed = existing_entities.count()
                if removed:
                    existing_entities.delete()
                    self._invalidate_alias_cache(upload.business_profile_id)
                    logger.info(
                        "json.entities.cleared upload=%s business=%s entities=%s",
                        upload.id,
                        upload.business_profile_id,
                        removed,
                    )
                ingestion_metadata.pop("alias_count", None)
                ingestion_metadata.pop("alias_patterns_used", None)
            lexicon_auto_learning = self._auto_learn_tenant_lexicon(
                upload=upload,
                extraction=extraction,
                structured_summary=structured_summary,
                ingestion_metadata=ingestion_metadata,
                entity_payloads=entity_payloads,
            )
            if lexicon_auto_learning:
                ingestion_metadata["lexicon_auto_learning"] = lexicon_auto_learning
            else:
                ingestion_metadata.pop("lexicon_auto_learning", None)
            upload.summary = summary
            upload.token_count = words
            upload.chunk_count = chunk_count
            upload.status = KnowledgeStatus.ACTIVE
            upload.last_ingested_at = now
            upload.ingestion_error = ""
            if structured_summary:
                ingestion_metadata["structured_exports"] = structured_summary
            if missing_chunk_ids:
                ingestion_metadata["pending_embedding_chunks"] = missing_chunk_ids[:50]
                ingestion_metadata["pending_embedding_chunk_count"] = len(missing_chunk_ids)
            else:
                ingestion_metadata.pop("pending_embedding_chunks", None)
                ingestion_metadata.pop("pending_embedding_chunk_count", None)
            if "json_entities_truncated" in extraction.metadata:
                ingestion_metadata["truncated_entities"] = extraction.metadata.get("json_entities_truncated", 0)
            upload.ingestion_metadata = ingestion_metadata
            upload.save(
                update_fields=[
                    "summary",
                    "token_count",
                    "chunk_count",
                    "status",
                    "last_ingested_at",
                    "ingestion_error",
                    "ingestion_metadata",
                    "updated_at",
                ]
            )
            self._schedule_azure_search_index_update(
                upload=upload,
                format_hint=extraction.format_hint,
                now=now,
                previous_chunk_count=previous_chunk_count,
                chunk_objects=chunk_objects,
            )
        if entity_payloads:
            truncated = extraction.metadata.get("json_entities_truncated", 0)
            logger.info(
                "json.entities.summary upload=%s business=%s entities=%s aliases=%s truncated=%s",
                upload.id,
                upload.business_profile_id,
                entity_stats.get("entity_count", len(entity_payloads)),
                entity_stats.get("alias_count", 0),
                truncated,
            )
            try:
                QualityMonitor.record_ingestion_sample(
                    business_profile=upload.business_profile,
                    alias_values=entity_stats.get("alias_values") or [],
                    truncated_entities=int(truncated),
                    indexed_entities=entity_stats.get("entity_count", len(entity_payloads)),
                )
            except Exception as exc:  # pragma: no cover - monitoring failures must not block ingestion
                logger.warning("quality.ingestion.monitor_failed business=%s error=%s", upload.business_profile_id, exc)
