from __future__ import annotations

import hashlib
import logging
import re
import uuid
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeBlockType, KnowledgeVisibility
from apps.core.logging_utils import LogEmoji, log_start, log_success
from apps.knowledge.dataset_cards import build_dataset_card_segment_payload
from apps.knowledge.ingestion.contracts import PageBlockPayload, PageLayout
from apps.knowledge.ingestion.signals import OCR_NORMALIZATION_VERSION
from apps.knowledge.models import (
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadShadowChunk,
    KnowledgeUploadTable,
)
from core.tenancy import tenant_context


logger = logging.getLogger(__name__)


class IngestionChunksMixin:

    def _build_text_segments_from_blocks(
        self,
        pages: Sequence[PageLayout],
        *,
        chunk_chars: int = 1200,
        overlap: int = 200,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        if not pages:
            return []
        chunk_chars = max(200, int(chunk_chars))
        overlap = max(0, min(int(overlap), chunk_chars // 2))
        skip_types = {
            KnowledgeBlockType.TABLE,
            KnowledgeBlockType.IMAGE,
            KnowledgeBlockType.FIGURE,
            KnowledgeBlockType.HEADER,
            KnowledgeBlockType.FOOTER,
            KnowledgeBlockType.OTHER,
        }
        anchor_limit = 12
        heading_limit = 6
        segments: list[dict[str, Any]] = []
        chrome_stats = self._build_pdf_page_chrome_stats(pages)

        def _dedupe(values: Sequence[str], limit: int) -> list[str]:
            seen: set[str] = set()
            output: list[str] = []
            for value in values:
                if not value or value in seen:
                    continue
                seen.add(value)
                output.append(value)
                if limit and len(output) >= limit:
                    break
            return output

        for page in pages:
            block_units: list[dict[str, Any]] = []
            for block in page.blocks:
                if block.block_type in skip_types:
                    continue
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block_meta.get("canonical_consumed_by_table"):
                    # Canonical table reconstruction already absorbed this block into a table cell.
                    continue
                if block_meta.get("is_decorative") or block_meta.get("region_role") == "decorative":
                    continue
                text = self._sanitize_text(block.text).strip()
                chrome_trimmed, chrome_meta = self._suppress_pdf_page_chrome(
                    text,
                    bbox=(block.bbox or {}),
                    page_height=(page.height or None),
                    page_region=block_meta.get("page_region"),
                    chrome_stats=chrome_stats,
                )
                if chrome_trimmed != text:
                    text = chrome_trimmed
                if chrome_meta:
                    block_meta = dict(block_meta)
                    block_meta["page_chrome_trimmed"] = True
                    block_meta["page_chrome_position"] = chrome_meta.get("position")
                    block_meta["page_chrome_trimmed_tokens"] = int(chrome_meta.get("trimmed_tokens") or 0)
                if not text:
                    continue
                anchor = block_meta.get("anchor") or f"p{page.page_number}-b{block.order_index}"
                heading = self._sanitize_text(block.section_heading).strip() if block.section_heading else ""
                is_table_residual = bool(
                    block_meta.get("table_residual_candidate")
                    or block_meta.get("table_residual")
                    or block_meta.get("content_source") == "table_residual"
                    or block_meta.get("region_role") == "table_residual"
                )
                overlap_ratio = 0.0
                try:
                    overlap_ratio = float(block_meta.get("table_overlap_ratio") or 0.0)
                except (TypeError, ValueError):
                    overlap_ratio = 0.0
                residual_reason = str(
                    block_meta.get("table_residual_reason")
                    or block_meta.get("table_overlap_candidate_reason")
                    or block_meta.get("suppression_reason")
                    or ""
                ).strip()
                residual_region_key = str(block_meta.get("table_region_key") or "").strip()
                block_units.append(
                    {
                        "text": text,
                        "page_number": page.page_number,
                        "anchor": anchor,
                        "section_heading": heading,
                        "segment_role": "table_residual" if is_table_residual else "text",
                        "table_overlap_ratio": max(0.0, min(1.0, overlap_ratio)),
                        "table_residual_reason": residual_reason,
                        "table_residual_region_key": residual_region_key,
                    }
                )
            if not block_units:
                continue

            current_blocks: list[dict[str, Any]] = []
            current_len = 0
            current_role = "text"

            def _metadata_for_blocks(blocks: Sequence[dict[str, Any]], *, role: str) -> dict[str, Any]:
                anchors = _dedupe([entry.get("anchor") for entry in blocks if entry.get("anchor")], anchor_limit)
                headings = _dedupe(
                    [entry.get("section_heading") for entry in blocks if entry.get("section_heading")],
                    heading_limit,
                )
                metadata: dict[str, Any] = {
                    "strategy": "page_blocks",
                    "index_type": "text",
                    "page_numbers": [page.page_number],
                    "page_anchor": f"p{page.page_number}",
                }
                if role == "table_residual":
                    overlap_values = [
                        float(entry.get("table_overlap_ratio") or 0.0)
                        for entry in blocks
                        if isinstance(entry.get("table_overlap_ratio"), (int, float))
                    ]
                    metadata.update(
                        {
                            "content_source": "table_residual",
                            "region_role": "table_residual",
                            "table_residual": True,
                            "search_tier": "fallback",
                        }
                    )
                    if overlap_values:
                        metadata["table_overlap_ratio_max"] = round(max(overlap_values), 4)
                    residual_reasons = _dedupe(
                        [entry.get("table_residual_reason") for entry in blocks if entry.get("table_residual_reason")],
                        4,
                    )
                    if residual_reasons:
                        metadata["table_residual_reasons"] = residual_reasons
                    region_keys = _dedupe(
                        [
                            entry.get("table_residual_region_key")
                            for entry in blocks
                            if entry.get("table_residual_region_key")
                        ],
                        8,
                    )
                    if region_keys:
                        metadata["table_residual_region_keys"] = region_keys
                        metadata["table_residual_region_key"] = region_keys[0]
                else:
                    metadata.update(
                        {
                            "content_source": "page_blocks",
                            "region_role": "text",
                        }
                    )
                    overlap_values = [
                        float(entry.get("table_overlap_ratio") or 0.0)
                        for entry in blocks
                        if isinstance(entry.get("table_overlap_ratio"), (int, float))
                    ]
                    overlap_candidates = [
                        entry
                        for entry in blocks
                        if entry.get("table_overlap_ratio") or entry.get("table_residual_region_key")
                    ]
                    if overlap_candidates:
                        metadata["table_adjacent"] = True
                        if overlap_values:
                            metadata["table_overlap_ratio_max"] = round(max(overlap_values), 4)
                        region_keys = _dedupe(
                            [
                                entry.get("table_residual_region_key")
                                for entry in overlap_candidates
                                if entry.get("table_residual_region_key")
                            ],
                            8,
                        )
                        if region_keys:
                            metadata["table_region_keys"] = region_keys
                            metadata["table_region_key"] = region_keys[0]
                if anchors:
                    metadata["block_anchors"] = anchors
                if headings:
                    metadata["section_headings"] = headings
                return metadata

            def emit(blocks: Sequence[dict[str, Any]], *, role: str) -> None:
                if not blocks:
                    return
                text = "\n\n".join(entry["text"] for entry in blocks).strip()
                if not text:
                    return
                text, aliases = self._inject_identifiers_into_text(text, alias_hygiene=alias_hygiene)
                metadata = _metadata_for_blocks(blocks, role=role)
                if aliases:
                    metadata.update(self._alias_metadata(aliases))
                segments.append({"text": text, "metadata": metadata})

            for unit in block_units:
                unit_role = str(unit.get("segment_role") or "text")
                if unit_role == "table_residual":
                    if current_blocks:
                        emit(current_blocks, role=current_role)
                        current_blocks = []
                        current_len = 0
                    current_role = unit_role
                    block_text = str(unit.get("text") or "")
                    residual_pieces = self._chunk_text(block_text, chunk_chars=chunk_chars, overlap=0)
                    if not residual_pieces:
                        residual_pieces = [block_text]
                    for piece in residual_pieces:
                        normalized_piece = str(piece or "").strip()
                        if not normalized_piece:
                            continue
                        piece_unit = dict(unit)
                        piece_unit["text"] = normalized_piece
                        rendered, aliases = self._inject_identifiers_into_text(
                            normalized_piece,
                            alias_hygiene=alias_hygiene,
                        )
                        metadata = _metadata_for_blocks([piece_unit], role=unit_role)
                        metadata["table_residual_granularity"] = "block"
                        if aliases:
                            metadata.update(self._alias_metadata(aliases))
                        segments.append({"text": rendered, "metadata": metadata})
                    continue
                if current_blocks and unit_role != current_role:
                    emit(current_blocks, role=current_role)
                    current_blocks = []
                    current_len = 0
                    current_role = unit_role

                block_text = unit["text"]
                if len(block_text) >= chunk_chars:
                    if current_blocks:
                        emit(current_blocks, role=current_role)
                        current_blocks = []
                        current_len = 0
                    current_role = unit_role
                    for piece in self._chunk_text(block_text, chunk_chars=chunk_chars, overlap=overlap):
                        if not piece:
                            continue
                        piece, aliases = self._inject_identifiers_into_text(piece, alias_hygiene=alias_hygiene)
                        metadata = _metadata_for_blocks([unit], role=unit_role)
                        if aliases:
                            metadata.update(self._alias_metadata(aliases))
                        segments.append({"text": piece, "metadata": metadata})
                    continue

                additional = len(block_text) + (2 if current_blocks else 0)
                if current_blocks and current_len + additional > chunk_chars:
                    emit(current_blocks, role=current_role)
                    if overlap > 0:
                        carried: list[dict[str, Any]] = []
                        carried_len = 0
                        for prev in reversed(current_blocks):
                            prev_len = len(prev["text"]) + (2 if carried else 0)
                            carried.insert(0, prev)
                            carried_len += prev_len
                            if carried_len >= overlap:
                                break
                        current_blocks = carried
                        current_len = carried_len
                    else:
                        current_blocks = []
                        current_len = 0

                if not current_blocks:
                    current_role = unit_role
                current_blocks.append(unit)
                current_len += additional

            if current_blocks:
                emit(current_blocks, role=current_role)

        return segments

    @staticmethod
    def _chunk_text(content: str, *, chunk_chars: int = 1200, overlap: int = 200) -> list[str]:
        """
        Boundary-aware chunker:
        - Prefers to end chunks on paragraph/line boundaries to avoid splitting table rows
        - If a chunk starts on a tab-delimited line, pull in up to 2 preceding lines to capture headers
        - Aligns the overlap start to token/line boundaries to avoid mid-word fragments (e.g., "ee", "pend")
        """
        text = (content or "").strip()
        if not text:
            return []

        segments: list[str] = []
        length = len(text)
        start = 0
        overlap = max(0, min(overlap, chunk_chars // 2))

        def _align_next_start(raw_start: int, *, min_progress: int) -> int:
            """
            Ensure the next chunk starts on a sane boundary so we don't create mid-word fragments
            when applying overlap (common in PDF-extracted tabular text).
            """
            if raw_start <= 0:
                return 0
            candidate = min(raw_start, length)
            if candidate <= min_progress:
                candidate = min_progress

            if candidate < length:
                # Prefer starting on a line boundary near the overlap start (helps tabular PDFs).
                lookback = min(200, candidate - min_progress)
                if lookback > 0:
                    nl = text.rfind("\n", candidate - lookback, candidate)
                    if nl != -1 and (nl + 1) >= min_progress:
                        candidate = nl + 1

                # If we're still inside a token, move back to the start of the token.
                if (
                    candidate > min_progress
                    and text[candidate].isalnum()
                    and text[candidate - 1].isalnum()
                ):
                    while candidate > min_progress and not text[candidate - 1].isspace():
                        candidate -= 1

            # Skip leading whitespace so chunk content starts cleanly.
            while candidate < length and text[candidate].isspace():
                candidate += 1

            # Guarantee forward progress even in edge cases.
            if candidate <= min_progress and raw_start > min_progress:
                candidate = raw_start
                while candidate < length and text[candidate].isspace():
                    candidate += 1

            return min(candidate, length)

        while start < length:
            current_start = start
            end_candidate = min(length, start + chunk_chars)
            window = text[start:end_candidate]

            # Try to cut on paragraph boundary; otherwise on line boundary.
            cut = window.rfind("\n\n")
            if cut == -1:
                cut = window.rfind("\n")
            if cut != -1 and cut >= int(chunk_chars * 0.6):
                end = start + cut
            else:
                end = end_candidate

            # Heuristic: if the chunk begins inside a table block (first non-empty line has tabs),
            # expand start backwards to include up to 2 previous lines (likely headers).
            # Identify the first non-empty line of the current chunk.
            first_line_start = start
            nl_pos = text.find("\n", start, end)
            if nl_pos == -1:
                first_line = text[start:end].lstrip()
            else:
                first_line = text[start:nl_pos].lstrip()

            if "\t" in first_line and start > 0:
                back_search_from = max(0, start - 300)
                back_slice = text[back_search_from:start]
                back_lines = back_slice.splitlines()
                take_lines = "\n".join(back_lines[-2:])  # pull up to 2 lines
                if take_lines:
                    start = max(0, start - (len(take_lines) + 1))  # +1 for newline
                    # Recompute cut with the expanded start
                    end_candidate = min(length, start + chunk_chars)
                    window = text[start:end_candidate]
                    cut2 = window.rfind("\n\n")
                    if cut2 == -1:
                        cut2 = window.rfind("\n")
                    end = start + (cut2 if cut2 != -1 else len(window))

            chunk = text[start:end].strip()
            if chunk:
                segments.append(chunk)
            if end >= length:
                break
            raw_next_start = max(0, end - overlap)
            start = _align_next_start(raw_next_start, min_progress=current_start + 1)

        return segments

    @staticmethod
    def _page_synopsis_from_blocks(blocks: Sequence[PageBlockPayload], *, max_chars: int = 480) -> str:
        if not blocks:
            return ""
        snippets: list[str] = []
        for block in blocks:
            text = (block.text or "").strip()
            if not text:
                continue
            snippets.append(text)
            combined = " ".join(snippets)
            if len(combined) >= max_chars:
                break
        synopsis = " ".join(snippets).strip()
        if len(synopsis) > max_chars:
            synopsis = synopsis[:max_chars].rsplit(" ", 1)[0].rstrip()
            synopsis = f"{synopsis}…"
        return synopsis


    def _build_flat_text_segment_payloads(
        self,
        content: str,
        *,
        alias_hygiene: bool = False,
    ) -> list[dict[str, Any]]:
        segment_payloads: list[dict[str, Any]] = []
        text_segments = self._chunk_text(content)
        for segment in text_segments:
            if not segment:
                continue
            augmented, aliases = self._inject_identifiers_into_text(segment, alias_hygiene=alias_hygiene)
            metadata = {
                "strategy": "sliding_window",
                "index_type": "text",
                "content_source": "flat_text",
                "region_role": "text",
            }
            if aliases:
                metadata.update(self._alias_metadata(aliases))
            segment_payloads.append({"text": augmented, "metadata": metadata})
        return segment_payloads

    def _page_block_fragmentation_score(self, pages: Sequence[PageLayout]) -> float:
        if not pages:
            return 0.0
        skip_types = {
            KnowledgeBlockType.TABLE,
            KnowledgeBlockType.IMAGE,
            KnowledgeBlockType.FIGURE,
            KnowledgeBlockType.HEADER,
            KnowledgeBlockType.FOOTER,
            KnowledgeBlockType.OTHER,
        }
        total_blocks = 0
        short_blocks = 0
        numeric_short_blocks = 0
        for page in pages:
            for block in page.blocks or []:
                if block.block_type in skip_types:
                    continue
                block_meta = block.metadata if isinstance(block.metadata, Mapping) else {}
                if block_meta.get("canonical_consumed_by_table"):
                    continue
                text = self._sanitize_text(str(block.text or "")).strip()
                if not text:
                    continue
                total_blocks += 1
                token_count = len(self._tokenize_for_quality(text))
                if token_count <= self.page_block_fragmentation_short_token_limit:
                    short_blocks += 1
                    if self._has_numeric_table_signal(text):
                        numeric_short_blocks += 1
        if total_blocks < self.page_block_fragmentation_min_blocks:
            return 0.0
        short_ratio = short_blocks / max(1, total_blocks)
        numeric_ratio = numeric_short_blocks / max(1, total_blocks)
        return round((short_ratio * 0.7) + (numeric_ratio * 0.3), 4)

    def _segment_quality_average(self, payloads: Sequence[Mapping[str, Any]]) -> float:
        if not payloads:
            return 0.0
        scores: list[float] = []
        for payload in payloads:
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            metrics = self._chunk_quality_metrics(text)
            scores.append(float(metrics.get("chunk_quality_score") or 0.0))
        if not scores:
            return 0.0
        return round(sum(scores) / len(scores), 4)

    def _should_prefer_flat_text_segments(
        self,
        *,
        pages: Sequence[PageLayout],
        page_segments: Sequence[Mapping[str, Any]],
        flat_segments: Sequence[Mapping[str, Any]],
    ) -> bool:
        if not pages or not page_segments or not flat_segments:
            return False
        fragmentation_score = self._page_block_fragmentation_score(pages)
        if fragmentation_score < self.page_block_fragmentation_short_ratio:
            return False
        page_score = self._segment_quality_average(page_segments)
        flat_score = self._segment_quality_average(flat_segments)
        if fragmentation_score >= 0.7 and flat_score >= page_score:
            return True
        return flat_score >= (page_score + self.page_block_flat_text_preference_margin)

    def _text_chunk_source_decision(
        self,
        *,
        pages: Sequence[PageLayout] | None,
        page_segments: Sequence[Mapping[str, Any]],
        flat_segments: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {
            "pages_available": bool(pages),
            "page_segment_count": len(page_segments),
            "flat_segment_count": len(flat_segments),
        }
        if not pages:
            diagnostics["selected_source"] = "flat_text"
            diagnostics["reason"] = "no_pages"
            return diagnostics
        fragmentation_score = self._page_block_fragmentation_score(pages)
        page_score = self._segment_quality_average(page_segments)
        flat_score = self._segment_quality_average(flat_segments)
        diagnostics.update(
            {
                "fragmentation_score": fragmentation_score,
                "page_blocks_quality_score": page_score,
                "flat_text_quality_score": flat_score,
                "fragmentation_threshold": self.page_block_fragmentation_short_ratio,
                "quality_margin_threshold": self.page_block_flat_text_preference_margin,
            }
        )
        if not page_segments:
            diagnostics["selected_source"] = "flat_text"
            diagnostics["reason"] = "no_page_segments"
            return diagnostics
        if not flat_segments:
            diagnostics["selected_source"] = "page_blocks"
            diagnostics["reason"] = "no_flat_segments"
            return diagnostics
        prefer_flat = self._should_prefer_flat_text_segments(
            pages=pages,
            page_segments=page_segments,
            flat_segments=flat_segments,
        )
        diagnostics["selected_source"] = "flat_text" if prefer_flat else "page_blocks"
        if fragmentation_score < self.page_block_fragmentation_short_ratio:
            diagnostics["reason"] = "fragmentation_below_threshold"
        elif flat_score >= (page_score + self.page_block_flat_text_preference_margin):
            diagnostics["reason"] = "flat_text_quality_margin"
        elif fragmentation_score >= 0.7 and flat_score >= page_score:
            diagnostics["reason"] = "severe_fragmentation"
        else:
            diagnostics["reason"] = "page_blocks_retained"
        return diagnostics

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

    @staticmethod
    def _tokenize_for_quality(text: str) -> list[str]:
        if not text:
            return []
        return re.findall(r"[a-z0-9]+", text.lower())

    @staticmethod
    def _is_heading_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        letters = [ch for ch in stripped if ch.isalpha()]
        if not letters:
            return False
        upper_ratio = sum(1 for ch in letters if ch.isupper()) / len(letters)
        if upper_ratio >= 0.7:
            return True
        words = [word for word in re.split(r"\s+", stripped) if word]
        if not words:
            return False
        starts = [word[0] for word in words if word[0].isalpha()]
        if not starts:
            return False
        title_ratio = sum(1 for ch in starts if ch.isupper()) / len(starts)
        return title_ratio >= 0.8

    def _is_heading_only_chunk(self, text: str, token_count: int) -> bool:
        if token_count <= 0:
            return False
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        if len(lines) > self.chunk_quality_heading_max_lines:
            return False
        if token_count > self.chunk_quality_heading_max_tokens:
            return False
        return all(self._is_heading_line(line) for line in lines)

    def _chunk_quality_metrics(self, text: str) -> dict[str, Any]:
        tokens = self._tokenize_for_quality(text)
        token_count = len(tokens)
        unique_ratio = round(len(set(tokens)) / token_count, 3) if token_count else 0.0
        heading_only = self._is_heading_only_chunk(text, token_count)
        flags: list[str] = []
        if token_count < self.chunk_quality_min_tokens:
            flags.append("short_tokens")
        if unique_ratio < self.chunk_quality_min_unique_ratio:
            flags.append("low_unique_ratio")
        if heading_only:
            flags.append("heading_only")
        token_score = min(1.0, token_count / self.chunk_quality_min_tokens) if self.chunk_quality_min_tokens else 1.0
        unique_score = (
            min(1.0, unique_ratio / self.chunk_quality_min_unique_ratio)
            if self.chunk_quality_min_unique_ratio
            else 1.0
        )
        heading_score = 0.0 if heading_only else 1.0
        score = (token_score * 0.45) + (unique_score * 0.45) + (heading_score * 0.10)
        score = round(max(0.0, min(1.0, score)), 3)
        return {
            "chunk_quality_score": score,
            "chunk_quality_tokens": token_count,
            "chunk_quality_unique_ratio": unique_ratio,
            "chunk_heading_only": heading_only,
            "chunk_quality_flags": flags,
        }

    def _is_low_quality_text_chunk(self, metadata: Mapping[str, Any]) -> bool:
        if not metadata:
            return False
        try:
            token_count = int(metadata.get("chunk_quality_tokens") or 0)
        except (TypeError, ValueError):
            token_count = 0
        try:
            unique_ratio = float(metadata.get("chunk_quality_unique_ratio") or 0.0)
        except (TypeError, ValueError):
            unique_ratio = 0.0
        try:
            score = float(metadata.get("chunk_quality_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        heading_only = bool(metadata.get("chunk_heading_only"))
        if heading_only:
            return True
        if token_count < self.chunk_quality_min_tokens:
            return True
        if unique_ratio < self.chunk_quality_min_unique_ratio:
            return True
        return score < self.chunk_quality_low_score

    @staticmethod
    def _segment_is_text(metadata: Mapping[str, Any]) -> bool:
        if not metadata:
            return False
        if metadata.get("is_table_chunk"):
            return False
        index_type = metadata.get("index_type")
        if index_type in {"table", "entity"}:
            return False
        return True

    @staticmethod
    def _chunk_fingerprint(text: str) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").strip().lower())
        if not normalized:
            return ""
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _payload_is_table_residual(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("table_residual"):
            return True
        return (
            metadata.get("content_source") == "table_residual"
            or metadata.get("region_role") == "table_residual"
        )

    @staticmethod
    def _payload_is_table_annotation(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if metadata.get("table_annotation"):
            return True
        return (
            metadata.get("content_source") == "table_annotation"
            or metadata.get("region_role") == "table_annotation"
        )

    @staticmethod
    def _payload_is_table_row(metadata: Mapping[str, Any]) -> bool:
        if not isinstance(metadata, Mapping):
            return False
        if not metadata.get("is_table_chunk"):
            return False
        return metadata.get("content_source") == "table_row"

    @staticmethod
    def _canonical_anchor_token(value: Any, *, max_length: int = 120) -> str:
        token = re.sub(r"[^a-z0-9:_\-]+", "-", str(value or "").strip().lower())
        token = token.strip("-")
        if not token:
            return ""
        return token[:max_length]

    @staticmethod
    def _canonical_chunk_kind(metadata: Mapping[str, Any]) -> str:
        if not isinstance(metadata, Mapping):
            return "narrative_paragraph"
        if metadata.get("is_dataset_card"):
            return "dataset_card"
        content_source = str(metadata.get("content_source") or "").strip().lower()
        index_type = str(metadata.get("index_type") or "").strip().lower()
        if index_type == "entity":
            return "entity_record"
        if content_source == "table_row" or metadata.get("table_chunk_role") == "row":
            return "table_row"
        if content_source == "table_summary" or metadata.get("table_chunk_role") == "summary":
            return "table_summary"
        if content_source == "table_annotation" or metadata.get("table_annotation"):
            return "table_annotation"
        if metadata.get("is_table_chunk") or index_type == "table":
            return "table_chunk"
        return "narrative_paragraph"

    @staticmethod
    def _coverage_reason_for_chunk(metadata: Mapping[str, Any], *, kind: str) -> str:
        content_source = str(metadata.get("content_source") or "").strip().lower()
        if kind == "table_row":
            return "canonical_table_row"
        if kind == "table_summary":
            return "canonical_table_summary"
        if kind == "table_annotation":
            return "anchored_table_annotation"
        if kind == "entity_record":
            return "entity_record_projection"
        if kind == "dataset_card":
            return "dataset_card_summary"
        if content_source == "page_blocks":
            return "layout_paragraph"
        if content_source == "flat_text":
            return "flat_text_fallback"
        if content_source:
            return content_source
        return "narrative_paragraph"

    def _canonical_anchor_id_for_payload(
        self,
        *,
        upload_id: uuid.UUID,
        text: str,
        metadata: Mapping[str, Any],
        kind: str,
    ) -> str:
        fingerprint = self._chunk_fingerprint(text)[:16] or "empty"
        upload_token = self._canonical_anchor_token(upload_id) or "upload"
        table_token = self._canonical_anchor_token(metadata.get("table_id")) or "table"
        if kind == "table_row":
            try:
                row_index = int(metadata.get("table_row_index"))
            except (TypeError, ValueError):
                row_index = None
            row_token = str(row_index) if isinstance(row_index, int) and row_index >= 0 else fingerprint
            return f"table:{table_token}:row:{row_token}"
        if kind == "table_summary":
            return f"table:{table_token}:summary"
        if kind == "table_annotation":
            return f"table:{table_token}:annotation:{fingerprint}"
        if kind == "entity_record":
            entity_name = self._canonical_anchor_token(metadata.get("entity_name"), max_length=48) or "record"
            return f"upload:{upload_token}:entity:{entity_name}:{fingerprint}"
        if kind == "dataset_card":
            return f"upload:{upload_token}:dataset-card"
        page_number = self._segment_page_number(metadata)
        page_token = str(page_number) if isinstance(page_number, int) and page_number > 0 else "na"
        block_anchor = ""
        raw_anchors = metadata.get("block_anchors")
        if isinstance(raw_anchors, Sequence) and not isinstance(raw_anchors, (str, bytes)):
            for entry in raw_anchors:
                normalized = self._canonical_anchor_token(entry, max_length=48)
                if normalized:
                    block_anchor = normalized
                    break
        if block_anchor:
            return f"page:{page_token}:paragraph:{block_anchor}"
        return f"upload:{upload_token}:paragraph:{fingerprint}"

    def _apply_canonical_chunk_metadata(
        self,
        *,
        upload_id: uuid.UUID,
        text: str,
        metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        canonical_meta = dict(metadata or {})
        kind = self._canonical_chunk_kind(canonical_meta)
        anchor_id = self._canonical_anchor_id_for_payload(
            upload_id=upload_id,
            text=text,
            metadata=canonical_meta,
            kind=kind,
        )
        canonical_meta["canonical_schema_version"] = self.canonical_chunk_schema_version
        canonical_meta["canonical_source_layer"] = "canonical"
        canonical_meta["canonical_chunk_kind"] = kind
        canonical_meta["canonical_anchor_id"] = anchor_id
        table_token = self._canonical_anchor_token(canonical_meta.get("table_id")) or ""
        if kind in {"table_row", "table_annotation"} and table_token:
            canonical_meta["canonical_parent_anchor_id"] = f"table:{table_token}:summary"
        elif kind == "table_summary":
            canonical_meta.pop("canonical_parent_anchor_id", None)
        canonical_meta["coverage_reason"] = self._coverage_reason_for_chunk(canonical_meta, kind=kind)
        return canonical_meta

    def _project_table_residual_annotations(
        self,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            metadata = payload.get("metadata")
            payloads.append(
                {
                    "text": text,
                    "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
                }
            )
        stats: dict[str, Any] = {
            "enabled": bool(self.table_annotation_enabled),
            "input_payloads": len(payloads),
            "input_residual_segments": 0,
            "residual_segments_projected": 0,
            "unanchored_residual_promoted": 0,
            "narrative_promoted_segments": 0,
            "table_annotation_chunks_created": 0,
            "tables_with_annotations": 0,
            "soft_limit_exceeded_tables": 0,
        }
        if not payloads or not self.table_annotation_enabled:
            stats["output_payloads"] = len(payloads)
            return payloads, stats

        table_context_by_id: dict[str, dict[str, Any]] = {}
        table_candidates_by_page: dict[int, list[tuple[int, int, str]]] = {}
        for payload in payloads:
            metadata = payload.get("metadata")
            if not isinstance(metadata, Mapping):
                continue
            if self._payload_is_table_residual(metadata):
                continue
            if self._payload_is_table_annotation(metadata):
                continue
            table_id = str(metadata.get("table_id") or "").strip()
            if not table_id:
                continue
            content_source = str(metadata.get("content_source") or "").strip().lower()
            table_context_by_id.setdefault(
                table_id,
                {
                    "table_id": table_id,
                    "table_title": str(metadata.get("table_title") or "").strip(),
                    "table_order_index": metadata.get("table_order_index"),
                    "table_page_number": self._segment_page_number(metadata),
                },
            )
            page_number = self._segment_page_number(metadata)
            if not page_number:
                continue
            try:
                order_index = int(metadata.get("table_order_index"))
            except (TypeError, ValueError):
                order_index = 10_000
            priority = 0 if content_source == "table_summary" else 1
            table_candidates_by_page.setdefault(page_number, []).append((priority, order_index, table_id))

        preferred_table_by_page: dict[int, str] = {}
        for page_number, candidates in table_candidates_by_page.items():
            if not candidates:
                continue
            best = sorted(candidates, key=lambda item: (item[0], item[1], item[2]))[0]
            preferred_table_by_page[page_number] = best[2]

        passthrough: list[dict[str, Any]] = []
        residual_groups: dict[str, list[dict[str, Any]]] = {}
        for payload in payloads:
            metadata = payload.get("metadata")
            if not isinstance(metadata, Mapping) or not self._payload_is_table_residual(metadata):
                passthrough.append(payload)
                continue
            stats["input_residual_segments"] = int(stats["input_residual_segments"]) + 1
            table_id = str(metadata.get("table_id") or "").strip()
            if not table_id:
                page_number = self._segment_page_number(metadata)
                if page_number:
                    table_id = preferred_table_by_page.get(page_number, "")
            if not table_id:
                promoted_meta = dict(metadata)
                promoted_meta.pop("table_residual", None)
                promoted_meta.pop("table_residual_candidate", None)
                promoted_meta["content_source"] = "page_blocks"
                promoted_meta["region_role"] = "text"
                promoted_meta["table_adjacent"] = True
                promoted_meta.pop("search_tier", None)
                promoted_meta["table_annotation_unanchored"] = True
                passthrough.append({"text": payload["text"], "metadata": promoted_meta})
                stats["unanchored_residual_promoted"] = int(stats["unanchored_residual_promoted"]) + 1
                continue
            projection_target = self._residual_projection_target(text=payload["text"], metadata=metadata)
            if projection_target == "narrative":
                promoted_meta = dict(metadata)
                promoted_meta.pop("table_residual", None)
                promoted_meta.pop("table_residual_candidate", None)
                promoted_meta["content_source"] = "page_blocks"
                promoted_meta["region_role"] = "text"
                promoted_meta["table_adjacent"] = True
                promoted_meta.pop("search_tier", None)
                promoted_meta["table_residual_projected"] = "narrative"
                promoted_meta["table_reference_id"] = table_id
                passthrough.append({"text": payload["text"], "metadata": promoted_meta})
                stats["narrative_promoted_segments"] = int(stats["narrative_promoted_segments"]) + 1
                continue
            residual_groups.setdefault(table_id, []).append(payload)
            stats["residual_segments_projected"] = int(stats["residual_segments_projected"]) + 1

        annotation_payloads: list[dict[str, Any]] = []
        for table_id, grouped in residual_groups.items():
            if not grouped:
                continue
            table_context = table_context_by_id.get(table_id, {"table_id": table_id})

            def _rank(payload: Mapping[str, Any]) -> tuple[float, int, int]:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
                overlap = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
                try:
                    overlap_score = float(overlap)
                except (TypeError, ValueError):
                    overlap_score = 0.0
                text = str(payload.get("text") or "")
                token_score = len(self._token_signature(text))
                return overlap_score, token_score, len(text)

            ranked = sorted(grouped, key=_rank, reverse=True)
            seen_fingerprints: set[str] = set()
            selected_texts: list[str] = []
            region_keys: list[str] = []
            reasons: list[str] = []
            source_anchors: list[str] = []
            page_numbers: list[int] = []
            for candidate in ranked:
                candidate_text = str(candidate.get("text") or "").strip()
                if not candidate_text:
                    continue
                fingerprint = self._chunk_fingerprint(candidate_text)
                if fingerprint and fingerprint in seen_fingerprints:
                    continue
                if fingerprint:
                    seen_fingerprints.add(fingerprint)
                selected_texts.append(candidate_text)
                metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
                region_key = str(metadata.get("table_residual_region_key") or "").strip()
                if region_key and region_key not in region_keys:
                    region_keys.append(region_key)
                reason = str(metadata.get("table_residual_reason") or "").strip()
                if reason and reason not in reasons:
                    reasons.append(reason)
                anchors = metadata.get("block_anchors")
                if isinstance(anchors, Sequence) and not isinstance(anchors, (str, bytes)):
                    for raw_anchor in anchors:
                        anchor = str(raw_anchor or "").strip()
                        if anchor and anchor not in source_anchors:
                            source_anchors.append(anchor)
                page_number = self._segment_page_number(metadata)
                if page_number and page_number not in page_numbers:
                    page_numbers.append(page_number)
            if not selected_texts:
                continue
            lines: list[str] = []
            table_title = str(table_context.get("table_title") or "").strip()
            if table_title:
                lines.append(f"[Table] {table_title}")
            lines.append("[Notes]")
            lines.extend(selected_texts)
            annotation_text = "\n".join(lines).strip()
            split_annotations = self._chunk_text(
                annotation_text,
                chunk_chars=self.table_annotation_max_chars,
                overlap=0,
            )
            if not split_annotations:
                split_annotations = [annotation_text]
            soft_limit = self.table_annotation_max_per_table
            soft_limit_exceeded = bool(soft_limit and len(split_annotations) > soft_limit)
            if soft_limit_exceeded:
                stats["soft_limit_exceeded_tables"] = int(stats["soft_limit_exceeded_tables"]) + 1
            for idx, rendered in enumerate(split_annotations, start=1):
                annotation_meta: dict[str, Any] = {
                    "strategy": "table_residual_projection",
                    "index_type": "text",
                    "content_source": "table_annotation",
                    "region_role": "table_annotation",
                    "table_annotation": True,
                    "table_annotation_source": "table_residual",
                    "table_annotation_rank": idx,
                    "table_annotation_total": len(split_annotations),
                    "table_annotation_fragment_count": len(selected_texts),
                    "table_annotation_max_chars": self.table_annotation_max_chars,
                    "table_annotation_soft_limit": soft_limit,
                    "table_annotation_soft_limit_exceeded": soft_limit_exceeded,
                    "table_id": table_id,
                    "search_tier": "supporting",
                }
                table_order_index = table_context.get("table_order_index")
                if table_order_index is not None:
                    annotation_meta["table_order_index"] = table_order_index
                if table_title:
                    annotation_meta["table_title"] = table_title
                table_page_number = table_context.get("table_page_number")
                if isinstance(table_page_number, int) and table_page_number > 0:
                    annotation_meta["table_page_number"] = table_page_number
                elif page_numbers:
                    annotation_meta["table_page_number"] = page_numbers[0]
                if page_numbers:
                    annotation_meta["page_numbers"] = page_numbers[:4]
                if region_keys:
                    annotation_meta["table_annotation_region_keys"] = region_keys[:8]
                if reasons:
                    annotation_meta["table_annotation_reasons"] = reasons[:6]
                if source_anchors:
                    annotation_meta["block_anchors"] = source_anchors[:12]
                annotation_payloads.append({"text": rendered, "metadata": annotation_meta})

        if residual_groups:
            stats["tables_with_annotations"] = len(residual_groups)
        stats["table_annotation_chunks_created"] = len(annotation_payloads)
        output_payloads = passthrough + annotation_payloads
        stats["output_payloads"] = len(output_payloads)
        return output_payloads, stats

    def _canonicalize_segment_payloads(
        self,
        *,
        upload: KnowledgeUpload,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        projected_payloads, projection_stats = self._project_table_residual_annotations(segment_payloads)
        canonicalized: list[dict[str, Any]] = []
        kind_counts: dict[str, int] = {}
        for payload in projected_payloads:
            text = str(payload.get("text") or "").strip()
            if not text:
                continue
            metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
            canonical_meta = self._apply_canonical_chunk_metadata(
                upload_id=upload.id,
                text=text,
                metadata=metadata,
            )
            kind = str(canonical_meta.get("canonical_chunk_kind") or "unknown")
            kind_counts[kind] = int(kind_counts.get(kind, 0)) + 1
            canonicalized.append({"text": text, "metadata": canonical_meta})
        stats: dict[str, Any] = {
            "schema_version": self.canonical_chunk_schema_version,
            "input_payloads": len(segment_payloads),
            "output_payloads": len(canonicalized),
            "kind_counts": kind_counts,
            "table_residual_projection": projection_stats,
        }
        return canonicalized, stats

    @staticmethod
    def _token_signature(text: str) -> set[str]:
        tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
        if not tokens:
            return set()
        stop = {
            "section",
            "table",
            "row",
            "rows",
            "columns",
            "column",
            "labels",
            "label",
            "identifiers",
            "identifier",
        }
        signature: set[str] = set()
        for token in tokens:
            if len(token) < 3:
                continue
            if token in stop:
                continue
            signature.add(token)
        return signature

    @staticmethod
    def _segment_page_number(metadata: Mapping[str, Any]) -> int | None:
        if not isinstance(metadata, Mapping):
            return None
        raw_page_numbers = metadata.get("page_numbers")
        if isinstance(raw_page_numbers, Sequence) and not isinstance(raw_page_numbers, (str, bytes)):
            for raw_value in raw_page_numbers:
                try:
                    page_number = int(raw_value)
                except (TypeError, ValueError):
                    continue
                if page_number > 0:
                    return page_number
        raw_page = metadata.get("table_page_number")
        try:
            page_number = int(raw_page)
        except (TypeError, ValueError):
            return None
        return page_number if page_number > 0 else None

    def _residual_region_key(self, metadata: Mapping[str, Any]) -> str:
        if not isinstance(metadata, Mapping):
            return "residual:unscoped"
        region_key = str(metadata.get("table_residual_region_key") or "").strip()
        if region_key:
            return region_key
        region_keys = metadata.get("table_residual_region_keys")
        if isinstance(region_keys, Sequence) and not isinstance(region_keys, (str, bytes)):
            for value in region_keys:
                normalized = str(value or "").strip()
                if normalized:
                    return normalized
        page_number = self._segment_page_number(metadata)
        if page_number:
            return f"p{page_number}-residual"
        page_anchor = str(metadata.get("page_anchor") or "").strip()
        if page_anchor:
            return f"{page_anchor}-residual"
        return "residual:unscoped"

    def _segment_semantically_equivalent_to_table_row(
        self,
        *,
        residual_text: str,
        row_signatures_for_page: Sequence[set[str]],
        all_row_signatures: Sequence[set[str]],
        min_residual_coverage: float | None = None,
        min_row_coverage: float | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        residual_tokens = self._token_signature(residual_text)
        if not residual_tokens:
            return False, None
        candidates = list(row_signatures_for_page) if row_signatures_for_page else list(all_row_signatures)
        if not candidates:
            return False, None
        if min_residual_coverage is None:
            min_residual_coverage = self.table_residual_equivalence_min_overlap
        if min_row_coverage is None:
            min_row_coverage = self.table_residual_equivalence_min_overlap
        min_residual = max(0.0, min(1.0, float(min_residual_coverage)))
        min_row = max(0.0, min(1.0, float(min_row_coverage)))
        best: dict[str, Any] | None = None
        for row_tokens in candidates:
            if not row_tokens:
                continue
            shared = residual_tokens & row_tokens
            if len(shared) < self.table_residual_equivalence_min_shared_tokens:
                continue
            residual_coverage = len(shared) / max(1, len(residual_tokens))
            row_coverage = len(shared) / max(1, len(row_tokens))
            union_count = max(1, len(residual_tokens | row_tokens))
            jaccard = len(shared) / union_count
            candidate = {
                "shared_tokens": int(len(shared)),
                "residual_tokens": int(len(residual_tokens)),
                "row_tokens": int(len(row_tokens)),
                "residual_coverage": round(float(residual_coverage), 4),
                "row_coverage": round(float(row_coverage), 4),
                "jaccard": round(float(jaccard), 4),
            }
            if best is None:
                best = candidate
            else:
                best_key = (
                    min(float(best["residual_coverage"]), float(best["row_coverage"])),
                    float(best["jaccard"]),
                    int(best["shared_tokens"]),
                )
                candidate_key = (
                    min(float(candidate["residual_coverage"]), float(candidate["row_coverage"])),
                    float(candidate["jaccard"]),
                    int(candidate["shared_tokens"]),
                )
                if candidate_key > best_key:
                    best = candidate
            if residual_coverage >= min_residual and row_coverage >= min_row:
                candidate["matched"] = True
                return True, candidate
        if best is not None:
            best["matched"] = False
        return False, best

    def _classify_table_residual_segment_kind(self, text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return "unknown"
        base_text = re.sub(r"\n?\s*Identifiers:\s.*$", "", normalized, flags=re.IGNORECASE).strip()
        if not base_text:
            base_text = normalized
        tokens = self._token_signature(base_text)
        token_count = len(tokens)
        line_count = len([line for line in base_text.splitlines() if line.strip()])
        key_value_pairs = len(re.findall(r"\b[^:\n]{1,40}:\s+\S+", base_text))
        has_row_marker = "[Row]" in base_text or "\t" in base_text
        has_sentence_punctuation = bool(re.search(r"[.!?;:]", base_text))
        numeric_signal = self._has_numeric_table_signal(base_text)
        if token_count <= 4 and line_count <= 2:
            return "cell_like"
        if has_row_marker or key_value_pairs >= 2:
            return "row_like"
        if token_count <= 12 and not has_sentence_punctuation:
            if numeric_signal:
                return "row_like"
            return "heading_like"
        if numeric_signal and token_count <= 24 and line_count <= 4 and not has_sentence_punctuation:
            return "row_like"
        return "note_like"

    def _compact_residual_text(self, text: str) -> tuple[str, bool]:
        normalized = str(text or "").strip()
        if not normalized:
            return "", False
        if len(normalized) <= self.table_residual_compact_max_chars:
            return normalized, False
        compact_segments = self._chunk_text(
            normalized,
            chunk_chars=self.table_residual_compact_max_chars,
            overlap=0,
        )
        if compact_segments:
            return compact_segments[0], True
        return normalized[: self.table_residual_compact_max_chars], True

    def _residual_projection_target(
        self,
        *,
        text: str,
        metadata: Mapping[str, Any],
    ) -> str:
        segment_kind = str(
            metadata.get("table_residual_segment_kind")
            or self._classify_table_residual_segment_kind(text)
            or "unknown"
        ).strip()
        base_text = re.sub(r"\n?\s*Identifiers:\s.*$", "", str(text or ""), flags=re.IGNORECASE).strip()
        if not base_text:
            base_text = str(text or "")
        numeric_signal = self._has_numeric_table_signal(base_text)
        starts_with_note_marker = base_text.lstrip().startswith("*")
        token_count = len(self._token_signature(base_text))
        has_sentence_punctuation = bool(re.search(r"[.!?;:]", base_text))
        overlap_raw = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
        try:
            overlap_ratio = float(overlap_raw)
        except (TypeError, ValueError):
            overlap_ratio = 0.0

        # Headline-like table-adjacent text should remain narrative, not anchored notes.
        if segment_kind == "heading_like" and not numeric_signal and not starts_with_note_marker:
            return "narrative"

        # Short, non-numeric near-table snippets are usually surrounding prose.
        if (
            segment_kind == "note_like"
            and not numeric_signal
            and not starts_with_note_marker
            and token_count <= 18
            and not has_sentence_punctuation
        ):
            return "narrative"

        # Weak-overlap text that survived residual reconciliation should stay narrative.
        if (
            segment_kind == "note_like"
            and not numeric_signal
            and not starts_with_note_marker
            and overlap_ratio < self.pdf_table_residual_overlap_min_ratio
        ):
            return "narrative"

        return "annotation"

    def _reconcile_table_residual_segments(
        self,
        segment_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for payload in segment_payloads:
            text = str(payload.get("text") or "")
            metadata = payload.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            payloads.append({"text": text, "metadata": dict(metadata)})
        if not payloads:
            return [], {
                "input_residual_segments": 0,
                "kept_residual_segments": 0,
                "dropped_equivalent_segments": 0,
                "dropped_equivalent_cell_like_segments": 0,
                "dropped_equivalent_heading_like_segments": 0,
                "dropped_equivalent_row_like_segments": 0,
                "dropped_equivalent_note_like_segments": 0,
                "dropped_cap_segments": 0,
                "regions_with_residuals": 0,
                "equivalence_uncertain_kept_segments": 0,
            }

        row_signatures_all: list[set[str]] = []
        row_signatures_by_page: dict[int, list[set[str]]] = {}
        row_fingerprints_by_page: dict[int, set[str]] = {}
        row_fingerprints_all: set[str] = set()
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_row(metadata):
                continue
            text = payload["text"]
            page_number = self._segment_page_number(metadata)
            row_signature = self._token_signature(text)
            if row_signature:
                row_signatures_all.append(row_signature)
                if page_number:
                    row_signatures_by_page.setdefault(page_number, []).append(row_signature)
            fingerprint = self._chunk_fingerprint(text)
            if not fingerprint:
                continue
            row_fingerprints_all.add(fingerprint)
            if page_number:
                row_fingerprints_by_page.setdefault(page_number, set()).add(fingerprint)

        residual_count = 0
        dropped_equivalent = 0
        dropped_equivalent_by_kind: dict[str, int] = {
            "cell_like": 0,
            "heading_like": 0,
            "row_like": 0,
            "note_like": 0,
            "unknown": 0,
        }
        equivalence_uncertain_kept = 0
        dropped_equivalence_audit: list[dict[str, Any]] = []

        def _record_equivalence_drop(
            *,
            reason: str,
            segment_kind: str,
            metadata: Mapping[str, Any],
            page_number: int | None,
            region_key: str,
            fingerprint: str,
            match: Mapping[str, Any] | None = None,
        ) -> None:
            dropped_equivalent_by_kind.setdefault(segment_kind, 0)
            dropped_equivalent_by_kind[segment_kind] += 1
            if len(dropped_equivalence_audit) >= 16:
                return
            anchor = ""
            anchors = metadata.get("block_anchors")
            if isinstance(anchors, Sequence) and not isinstance(anchors, (str, bytes)):
                for value in anchors:
                    normalized = str(value or "").strip()
                    if normalized:
                        anchor = normalized
                        break
            event: dict[str, Any] = {
                "reason": reason,
                "segment_kind": segment_kind,
                "page_number": page_number,
                "region_key": region_key,
                "anchor": anchor or None,
                "fingerprint": (fingerprint[:16] if fingerprint else None),
            }
            if isinstance(match, Mapping):
                event["shared_tokens"] = int(match.get("shared_tokens") or 0)
                event["residual_coverage"] = float(match.get("residual_coverage") or 0.0)
                event["row_coverage"] = float(match.get("row_coverage") or 0.0)
                event["jaccard"] = float(match.get("jaccard") or 0.0)
            dropped_equivalence_audit.append(event)

        residual_groups: dict[str, list[dict[str, Any]]] = {}
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_residual(metadata):
                continue
            residual_count += 1
            page_number = self._segment_page_number(metadata)
            residual_text = payload["text"]
            segment_kind = self._classify_table_residual_segment_kind(residual_text)
            metadata["table_residual_segment_kind"] = segment_kind
            region_key = self._residual_region_key(metadata)
            if segment_kind == "cell_like":
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="low_information_cell",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=self._chunk_fingerprint(residual_text),
                )
                continue
            residual_fingerprint = self._chunk_fingerprint(residual_text)
            if residual_fingerprint:
                page_fingerprints = row_fingerprints_by_page.get(page_number or -1, set())
                if residual_fingerprint in page_fingerprints or (
                    not page_fingerprints and residual_fingerprint in row_fingerprints_all
                ):
                    dropped_equivalent += 1
                    _record_equivalence_drop(
                        reason="fingerprint_match",
                        segment_kind=segment_kind,
                        metadata=metadata,
                        page_number=page_number,
                        region_key=region_key,
                        fingerprint=residual_fingerprint,
                    )
                    continue
            page_signatures = row_signatures_by_page.get(page_number or -1, [])
            semantic_min_residual: float | None = None
            semantic_min_row: float | None = None
            if segment_kind in {"row_like", "heading_like"}:
                semantic_min_residual = self.table_residual_equivalence_min_overlap
                semantic_min_row = min(0.4, self.table_residual_equivalence_min_overlap)
            elif segment_kind == "note_like":
                residual_token_count = len(self._token_signature(residual_text))
                if residual_token_count <= 16:
                    semantic_min_residual = self.table_residual_equivalence_min_overlap
                    semantic_min_row = self.table_residual_equivalence_min_overlap
                else:
                    strict = min(0.98, max(0.9, self.table_residual_equivalence_min_overlap + 0.25))
                    semantic_min_residual = strict
                    semantic_min_row = strict
            equivalent, match = self._segment_semantically_equivalent_to_table_row(
                residual_text=residual_text,
                row_signatures_for_page=page_signatures,
                all_row_signatures=row_signatures_all,
                min_residual_coverage=semantic_min_residual,
                min_row_coverage=semantic_min_row,
            )
            metadata["table_residual_equivalence_checked"] = True
            if isinstance(match, Mapping):
                metadata["table_residual_equivalence_best"] = dict(match)
            one_sided_row_equivalent = False
            if not equivalent and isinstance(match, Mapping) and segment_kind in {"row_like", "heading_like"}:
                residual_cov = float(match.get("residual_coverage") or 0.0)
                shared_tokens = int(match.get("shared_tokens") or 0)
                if (
                    residual_cov >= self.table_residual_equivalence_min_overlap
                    and shared_tokens >= self.table_residual_equivalence_min_shared_tokens
                ):
                    one_sided_row_equivalent = True
            if equivalent:
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="semantic_equivalent",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=residual_fingerprint,
                    match=match,
                )
                continue
            if one_sided_row_equivalent:
                dropped_equivalent += 1
                _record_equivalence_drop(
                    reason="semantic_row_subset",
                    segment_kind=segment_kind,
                    metadata=metadata,
                    page_number=page_number,
                    region_key=region_key,
                    fingerprint=residual_fingerprint,
                    match=match,
                )
                continue
            if isinstance(match, Mapping):
                residual_cov = float(match.get("residual_coverage") or 0.0)
                row_cov = float(match.get("row_coverage") or 0.0)
                if (
                    residual_cov >= self.table_residual_equivalence_min_overlap
                    or row_cov >= self.table_residual_equivalence_min_overlap
                ):
                    metadata["table_residual_equivalence_uncertain"] = True
                    equivalence_uncertain_kept += 1

            compact_text, compacted = self._compact_residual_text(residual_text)
            payload["text"] = compact_text
            if compacted:
                metadata["table_residual_compacted"] = True
                metadata["table_residual_compact_max_chars"] = self.table_residual_compact_max_chars

            metadata["table_residual_region_key"] = region_key
            residual_groups.setdefault(region_key, []).append(payload)

        kept_residual: list[dict[str, Any]] = []
        dropped_cap = 0
        for region_key, region_payloads in residual_groups.items():
            def _rank(payload: Mapping[str, Any]) -> tuple[float, int, int]:
                metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
                overlap = metadata.get("table_overlap_ratio_max") or metadata.get("table_overlap_ratio") or 0.0
                try:
                    overlap_score = float(overlap)
                except (TypeError, ValueError):
                    overlap_score = 0.0
                text = str(payload.get("text") or "")
                token_score = len(self._token_signature(text))
                length_score = len(text)
                return overlap_score, token_score, length_score

            ranked = sorted(region_payloads, key=_rank, reverse=True)
            # Coverage-first policy: once a residual segment is proven non-equivalent
            # to indexed table rows, keep it. Overlap is a dedupe hint, not a hard
            # suppression decision.
            kept_residual.extend(ranked)

        kept_residual_ids = {id(payload) for payload in kept_residual}
        reconciled: list[dict[str, Any]] = []
        for payload in payloads:
            metadata = payload["metadata"]
            if not self._payload_is_table_residual(metadata):
                reconciled.append(payload)
                continue
            if id(payload) in kept_residual_ids:
                reconciled.append(payload)
        stats = {
            "input_residual_segments": residual_count,
            "kept_residual_segments": len(kept_residual),
            "dropped_equivalent_segments": dropped_equivalent,
            "dropped_equivalent_cell_like_segments": int(dropped_equivalent_by_kind.get("cell_like", 0)),
            "dropped_equivalent_heading_like_segments": int(dropped_equivalent_by_kind.get("heading_like", 0)),
            "dropped_equivalent_row_like_segments": int(dropped_equivalent_by_kind.get("row_like", 0)),
            "dropped_equivalent_note_like_segments": int(dropped_equivalent_by_kind.get("note_like", 0)),
            "dropped_cap_segments": dropped_cap,
            "regions_with_residuals": len(residual_groups),
            "max_per_region": self.table_residual_max_per_region,
            "equiv_min_overlap": round(self.table_residual_equivalence_min_overlap, 4),
            "equiv_min_shared_tokens": self.table_residual_equivalence_min_shared_tokens,
            "equivalence_uncertain_kept_segments": equivalence_uncertain_kept,
        }
        if dropped_equivalence_audit:
            stats["dropped_equivalence_audit_sample"] = dropped_equivalence_audit
        return reconciled, stats
