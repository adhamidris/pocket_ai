from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from apps.accounts.models import KnowledgeBlockType
from apps.knowledge.ingestion_contracts import PageBlockPayload, PageLayout, TablePayload
from apps.knowledge.ingestion_signals import (
    _TABLE_DATE_TIME_LIKE_RE,
    _TABLE_NUMBER_LIKE_RE,
    _TABLE_NUMBER_WITH_UNIT_RE,
    _TABLE_NUMERIC_SIGNAL_TOKEN_RE,
)


class IngestionPdfTableGeometryMixin:

    @staticmethod
    def _normalize_bbox(raw_bbox: Mapping[str, Any] | None) -> dict[str, float] | None:
        if not isinstance(raw_bbox, Mapping):
            return None
        x0 = y0 = x1 = y1 = None
        if all(key in raw_bbox for key in ("x0", "y0", "x1", "y1")):
            x0, y0, x1, y1 = (
                raw_bbox.get("x0"),
                raw_bbox.get("y0"),
                raw_bbox.get("x1"),
                raw_bbox.get("y1"),
            )
        elif all(key in raw_bbox for key in ("left", "top", "right", "bottom")):
            x0, y0, x1, y1 = (
                raw_bbox.get("left"),
                raw_bbox.get("top"),
                raw_bbox.get("right"),
                raw_bbox.get("bottom"),
            )
        elif all(key in raw_bbox for key in ("x", "y", "width", "height")):
            x0 = raw_bbox.get("x")
            y0 = raw_bbox.get("y")
            width = raw_bbox.get("width")
            height = raw_bbox.get("height")
            try:
                x1 = float(x0) + float(width)
                y1 = float(y0) + float(height)
            except (TypeError, ValueError):
                return None
        try:
            parsed = {
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
            }
        except (TypeError, ValueError):
            return None
        if parsed["x1"] <= parsed["x0"] or parsed["y1"] <= parsed["y0"]:
            return None
        return parsed

    @staticmethod
    def _bbox_area(bbox: Mapping[str, float] | None) -> float:
        if not bbox:
            return 0.0
        width = float(bbox.get("x1", 0.0) - bbox.get("x0", 0.0))
        height = float(bbox.get("y1", 0.0) - bbox.get("y0", 0.0))
        if width <= 0.0 or height <= 0.0:
            return 0.0
        return width * height

    @staticmethod
    def _bbox_union(first: Mapping[str, float], second: Mapping[str, float]) -> dict[str, float]:
        return {
            "x0": min(float(first["x0"]), float(second["x0"])),
            "y0": min(float(first["y0"]), float(second["y0"])),
            "x1": max(float(first["x1"]), float(second["x1"])),
            "y1": max(float(first["y1"]), float(second["y1"])),
        }

    @staticmethod
    def _expand_bbox(
        bbox: Mapping[str, float],
        *,
        margin_x: float = 0.0,
        margin_y: float = 0.0,
    ) -> dict[str, float]:
        margin_x = max(0.0, float(margin_x))
        margin_y = max(0.0, float(margin_y))
        return {
            "x0": float(bbox["x0"]) - margin_x,
            "y0": float(bbox["y0"]) - margin_y,
            "x1": float(bbox["x1"]) + margin_x,
            "y1": float(bbox["y1"]) + margin_y,
        }

    @staticmethod
    def _bbox_intersects(first: Mapping[str, float], second: Mapping[str, float]) -> bool:
        return not (
            float(first["x1"]) <= float(second["x0"])
            or float(second["x1"]) <= float(first["x0"])
            or float(first["y1"]) <= float(second["y0"])
            or float(second["y1"]) <= float(first["y0"])
        )

    @classmethod
    def _bbox_edge_distance(cls, first: Mapping[str, float], second: Mapping[str, float]) -> float:
        if cls._bbox_intersects(first, second):
            return 0.0
        dx = max(
            float(second["x0"]) - float(first["x1"]),
            float(first["x0"]) - float(second["x1"]),
            0.0,
        )
        dy = max(
            float(second["y0"]) - float(first["y1"]),
            float(first["y0"]) - float(second["y1"]),
            0.0,
        )
        return math.sqrt((dx * dx) + (dy * dy))

    @classmethod
    def _bbox_overlap_ratio(cls, block_bbox: Mapping[str, float], region_bbox: Mapping[str, float]) -> float:
        block_area = cls._bbox_area(block_bbox)
        if block_area <= 0.0:
            return 0.0
        x0 = max(float(block_bbox["x0"]), float(region_bbox["x0"]))
        y0 = max(float(block_bbox["y0"]), float(region_bbox["y0"]))
        x1 = min(float(block_bbox["x1"]), float(region_bbox["x1"]))
        y1 = min(float(block_bbox["y1"]), float(region_bbox["y1"]))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        overlap_area = (x1 - x0) * (y1 - y0)
        return max(0.0, min(1.0, overlap_area / block_area))

    def _merge_table_regions(
        self,
        regions: Sequence[Mapping[str, float]],
        *,
        page_width: float | None = None,
        page_height: float | None = None,
    ) -> list[dict[str, float]]:
        if not regions:
            return []
        if len(regions) == 1:
            region = regions[0]
            return [
                {
                    "x0": float(region["x0"]),
                    "y0": float(region["y0"]),
                    "x1": float(region["x1"]),
                    "y1": float(region["y1"]),
                }
            ]
        width = max(0.0, float(page_width or 0.0))
        height = max(0.0, float(page_height or 0.0))
        margin_x = max(2.0, width * self.pdf_table_region_merge_x_margin_ratio)
        margin_y = max(2.0, height * self.pdf_table_region_merge_y_margin_ratio)
        pending: list[dict[str, float]] = [
            {
                "x0": float(region["x0"]),
                "y0": float(region["y0"]),
                "x1": float(region["x1"]),
                "y1": float(region["y1"]),
            }
            for region in regions
        ]
        while True:
            merged_any = False
            next_regions: list[dict[str, float]] = []
            while pending:
                current = pending.pop(0)
                current_expanded = self._expand_bbox(current, margin_x=margin_x, margin_y=margin_y)
                compare_index = 0
                while compare_index < len(pending):
                    candidate = pending[compare_index]
                    candidate_expanded = self._expand_bbox(candidate, margin_x=margin_x, margin_y=margin_y)
                    if not self._bbox_intersects(current_expanded, candidate_expanded):
                        compare_index += 1
                        continue
                    current = self._bbox_union(current, candidate)
                    current_expanded = self._expand_bbox(current, margin_x=margin_x, margin_y=margin_y)
                    pending.pop(compare_index)
                    merged_any = True
                next_regions.append(current)
            pending = next_regions
            if not merged_any:
                break
        return sorted(pending, key=lambda bbox: (float(bbox["y0"]), float(bbox["x0"])))

    def _table_regions_by_page(
        self,
        tables: Sequence[TablePayload],
        *,
        pages: Sequence[PageLayout] | None = None,
    ) -> dict[int, list[dict[str, float]]]:
        raw_regions_by_page: dict[int, list[dict[str, float]]] = {}
        for table in tables:
            if not table.page_number:
                continue
            normalized_bbox = self._normalize_bbox(table.bbox)
            if not normalized_bbox:
                continue
            raw_regions_by_page.setdefault(int(table.page_number), []).append(normalized_bbox)
        if not raw_regions_by_page:
            return {}
        page_dimensions: dict[int, tuple[float, float]] = {}
        for page in pages or []:
            page_dimensions[int(page.page_number)] = (float(page.width or 0.0), float(page.height or 0.0))
        merged_regions_by_page: dict[int, list[dict[str, float]]] = {}
        for page_number, page_regions in raw_regions_by_page.items():
            width, height = page_dimensions.get(page_number, (0.0, 0.0))
            merged_regions_by_page[page_number] = self._merge_table_regions(
                page_regions,
                page_width=width,
                page_height=height,
            )
        return merged_regions_by_page

    @staticmethod
    def _has_numeric_table_signal(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if not re.search(r"\d", normalized):
            return False

        # Generic, domain-agnostic "structured numeric" cues:
        # - repeated numeric values (typical for row-wise factual cells),
        # - percentages / currency symbols or common currency codes,
        # - date/time-like tokens,
        # - number + short unit patterns (e.g. 12 kg, 24 hrs).
        number_like_tokens = _TABLE_NUMBER_LIKE_RE.findall(normalized)
        if len(number_like_tokens) >= 2:
            return True
        if _TABLE_NUMERIC_SIGNAL_TOKEN_RE.search(normalized):
            return True
        if _TABLE_DATE_TIME_LIKE_RE.search(normalized):
            return True
        if _TABLE_NUMBER_WITH_UNIT_RE.search(normalized):
            return True
        return False

    @staticmethod
    def _pdf_cell_looks_placeholder(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if re.search(r"[_\.]{3,}|[□☐☑]", normalized):
            return True
        lowered = normalized.lower()
        if re.search(r"\bpage\s+\d+\s+of\s+\d+\b", lowered):
            return True
        if "rev." in lowered or lowered.startswith("rev "):
            return True
        return False

    @staticmethod
    def _pdf_cell_looks_label_like(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return False
        if normalized.endswith(":"):
            return True
        words = [word for word in re.findall(r"[A-Za-z]+", normalized) if word]
        if not words:
            return False
        if len(words) > 8:
            return False
        uppercase_ratio = sum(1 for word in words if word.isupper()) / len(words)
        return uppercase_ratio >= 0.75

    def _build_pdf_table_baseline_metrics(
        self,
        pages: Sequence[PageLayout],
        tables: Sequence[TablePayload],
        *,
        overlap_diagnostics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        table_regions_by_page = self._table_regions_by_page(tables, pages=pages)
        text_block_types = {KnowledgeBlockType.PARAGRAPH, KnowledgeBlockType.HEADING}

        total_page_area = 0.0
        total_table_area = 0.0
        checked_text_blocks = 0
        suppressed_text_blocks = 0
        residual_text_blocks = 0
        residual_numeric_blocks = 0
        pages_with_regions = 0

        for page in pages:
            page_regions = table_regions_by_page.get(int(page.page_number), [])
            if not page_regions:
                continue
            pages_with_regions += 1
            page_area = max(0.0, float(page.width or 0.0) * float(page.height or 0.0))
            if page_area > 0.0:
                total_page_area += page_area
                page_table_area = sum(self._bbox_area(region_bbox) for region_bbox in page_regions)
                total_table_area += min(page_area, page_table_area)

            for block in page.blocks:
                if block.block_type not in text_block_types:
                    continue
                block_text = self._sanitize_text(block.text).strip()
                if not block_text:
                    continue
                checked_text_blocks += 1
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block_meta.get("table_overlap_candidate") or block_meta.get("suppress_text_chunk"):
                    suppressed_text_blocks += 1
                residual_text_blocks += 1
                if self._has_numeric_table_signal(block_text):
                    residual_numeric_blocks += 1

        row_labels: set[str] = set()
        for table in tables:
            row_labels.update(self._table_row_label_set(table))

        coverage_ratio = (total_table_area / total_page_area) if total_page_area > 0.0 else 0.0
        residual_ratio = (residual_text_blocks / checked_text_blocks) if checked_text_blocks > 0 else 0.0

        metrics: dict[str, Any] = {
            "table_bbox_coverage_ratio": round(max(0.0, min(1.0, coverage_ratio)), 4),
            "table_pages_with_regions": pages_with_regions,
            "checked_text_blocks_count": checked_text_blocks,
            "suppressed_text_blocks_count": suppressed_text_blocks,
            "residual_text_blocks_count": residual_text_blocks,
            "residual_text_ratio": round(max(0.0, min(1.0, residual_ratio)), 4),
            "residual_text_with_numeric_signals_count": residual_numeric_blocks,
            "table_row_unique_evidence_count": len(row_labels),
        }
        if isinstance(overlap_diagnostics, Mapping):
            metrics["overlap_threshold"] = overlap_diagnostics.get("threshold")
            metrics["overlap_checked_text_blocks"] = int(overlap_diagnostics.get("checked_text_blocks") or 0)
            metrics["overlap_suppressed_text_blocks"] = int(overlap_diagnostics.get("suppressed_text_blocks") or 0)
        return metrics

    def _annotate_pdf_blocks_with_table_overlap(
        self,
        pages: Sequence[PageLayout],
        tables: Sequence[TablePayload],
    ) -> tuple[list[PageLayout], dict[str, Any]]:
        table_regions_by_page = self._table_regions_by_page(tables, pages=pages)

        diagnostics: dict[str, Any] = {
            "enabled": True,
            "threshold": round(self.pdf_table_text_overlap_min_ratio, 4),
            "residual_overlap_threshold": round(self.pdf_table_residual_overlap_min_ratio, 4),
            "residual_near_region_ratio": round(self.pdf_table_residual_near_region_ratio, 4),
            "table_regions": sum(len(v) for v in table_regions_by_page.values()),
            "checked_text_blocks": 0,
            "overlapping_text_blocks": 0,
            "suppressed_text_blocks": 0,
            "table_residual_blocks": 0,
            "table_residual_numeric_blocks": 0,
        }
        if not pages or not table_regions_by_page:
            diagnostics["reason"] = "no_pages_or_table_regions"
            return list(pages), diagnostics

        text_block_types = {KnowledgeBlockType.PARAGRAPH, KnowledgeBlockType.HEADING}
        annotated_pages: list[PageLayout] = []
        for page in pages:
            page_regions = table_regions_by_page.get(int(page.page_number), [])
            if not page_regions:
                annotated_pages.append(page)
                continue

            page_checked = 0
            page_suppressed = 0
            page_residual = 0
            page_diagonal = math.sqrt((float(page.width or 0.0) ** 2) + (float(page.height or 0.0) ** 2))
            near_distance_threshold = max(2.0, page_diagonal * self.pdf_table_residual_near_region_ratio)
            updated_blocks: list[PageBlockPayload] = []
            for block in page.blocks:
                block_meta = block.metadata if isinstance(block.metadata, dict) else {}
                if block.block_type not in text_block_types:
                    updated_blocks.append(block)
                    continue

                block_text = self._sanitize_text(block.text).strip()
                numeric_signal = self._has_numeric_table_signal(block_text) if block_text else False
                page_checked += 1
                diagnostics["checked_text_blocks"] = int(diagnostics["checked_text_blocks"]) + 1
                normalized_block_bbox = self._normalize_bbox(block.bbox)
                overlap_ratio = 0.0
                nearest_distance: float | None = None
                best_region_index: int | None = None
                if normalized_block_bbox:
                    best_overlap = -1.0
                    best_distance = float("inf")
                    for region_index, region_bbox in enumerate(page_regions):
                        overlap = self._bbox_overlap_ratio(normalized_block_bbox, region_bbox)
                        distance = self._bbox_edge_distance(normalized_block_bbox, region_bbox)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best_distance = distance
                            best_region_index = region_index
                            continue
                        if math.isclose(overlap, best_overlap, rel_tol=1e-6, abs_tol=1e-6) and distance < best_distance:
                            best_distance = distance
                            best_region_index = region_index
                    if best_overlap > 0.0:
                        overlap_ratio = best_overlap
                    nearest_distance = best_distance if best_region_index is not None else None
                if overlap_ratio > 0.0:
                    diagnostics["overlapping_text_blocks"] = int(diagnostics["overlapping_text_blocks"]) + 1
                near_table_region = bool(
                    nearest_distance is not None and nearest_distance <= near_distance_threshold
                )

                updated_meta = dict(block_meta)
                if overlap_ratio > 0.0:
                    updated_meta["table_overlap_ratio"] = round(overlap_ratio, 4)
                    updated_meta["overlaps_table_region"] = True
                if nearest_distance is not None:
                    updated_meta["table_region_distance"] = round(nearest_distance, 4)
                if best_region_index is not None:
                    updated_meta["table_region_index"] = int(best_region_index)
                    updated_meta["table_region_key"] = f"p{page.page_number}-r{best_region_index}"
                if overlap_ratio >= self.pdf_table_text_overlap_min_ratio:
                    updated_meta["table_overlap_candidate"] = True
                    updated_meta["table_overlap_candidate_reason"] = "table_overlap"
                    page_suppressed += 1
                    diagnostics["suppressed_text_blocks"] = int(diagnostics["suppressed_text_blocks"]) + 1
                elif numeric_signal and (
                    overlap_ratio >= self.pdf_table_residual_overlap_min_ratio or near_table_region
                ):
                    updated_meta["table_residual_candidate"] = True
                    updated_meta["table_residual"] = True
                    updated_meta["region_role"] = "table_residual"
                    updated_meta["content_source"] = "table_residual"
                    updated_meta["search_tier"] = "fallback"
                    if overlap_ratio >= self.pdf_table_residual_overlap_min_ratio:
                        updated_meta["table_residual_reason"] = "numeric_overlap"
                    else:
                        updated_meta["table_residual_reason"] = "numeric_near_table_region"
                    page_residual += 1
                    diagnostics["table_residual_blocks"] = int(diagnostics["table_residual_blocks"]) + 1
                    diagnostics["table_residual_numeric_blocks"] = int(diagnostics["table_residual_numeric_blocks"]) + 1

                updated_blocks.append(
                    PageBlockPayload(
                        block_type=block.block_type,
                        order_index=block.order_index,
                        text=block.text,
                        bbox=block.bbox,
                        section_heading=block.section_heading,
                        heading_path=list(block.heading_path or []),
                        detected_language=block.detected_language,
                        confidence=block.confidence,
                        metadata=updated_meta,
                    )
                )

            updated_page_meta = dict(page.metadata or {})
            updated_page_meta["table_overlap_checked_blocks"] = page_checked
            if page_suppressed:
                updated_page_meta["table_overlap_suppressed_blocks"] = page_suppressed
                updated_page_meta["table_overlap_threshold"] = round(
                    self.pdf_table_text_overlap_min_ratio,
                    4,
                )
            if page_residual:
                updated_page_meta["table_residual_blocks"] = page_residual
                updated_page_meta["table_residual_overlap_threshold"] = round(
                    self.pdf_table_residual_overlap_min_ratio,
                    4,
                )
            annotated_pages.append(
                PageLayout(
                    page_number=page.page_number,
                    width=page.width,
                    height=page.height,
                    rotation=page.rotation,
                    text_density=page.text_density,
                    has_ocr_content=page.has_ocr_content,
                    content_type=page.content_type,
                    blocks=updated_blocks,
                    metadata=updated_page_meta,
                )
            )
        return annotated_pages, diagnostics
