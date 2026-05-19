from __future__ import annotations

from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.ingestion.contracts import PageBlockPayload, PageLayout


class IngestionChunkTextSegmentsMixin:

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
