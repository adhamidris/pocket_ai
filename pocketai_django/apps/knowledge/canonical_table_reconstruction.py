from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Iterable, Mapping, Sequence


_WS_RE = re.compile(r"\s+")
_HAS_DIGIT_RE = re.compile(r"\d")
_NUMERIC_SIGNAL_RE = re.compile(
    r"(?ix)"
    r"("
    r"\b(?:egp|usd|eur|gbp|sar|aed)\b"  # common currency codes
    r"|[%$€£]"
    r"|\b(?:min|max|minimum|maximum)\b"
    r"|\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\b"  # 1,560 / 1.560 / 1560 / 0.2
    r")"
)
_VALUE_PLACEHOLDER_RE = re.compile(r"(?ix)^(?:no\s+fees?|free|waived?)$")


def _clean_text(value: str) -> str:
    return _WS_RE.sub(" ", (value or "").strip())


def _norm_for_contains(value: str) -> str:
    return _clean_text(value).lower()


def _numeric_signal(value: str) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    if not _HAS_DIGIT_RE.search(text):
        return False
    return bool(_NUMERIC_SIGNAL_RE.search(text))


def _looks_like_value_placeholder(value: str) -> bool:
    """
    Generic filter: some tables include a low-information middle column (e.g. "No fees", "Free")
    between the true row label and the numeric value. When reconstructing 2-column pseudo tables
    we should avoid picking that placeholder as the row label.
    """
    text = _clean_text(value)
    if not text:
        return False
    return bool(_VALUE_PLACEHOLDER_RE.match(text))


def _bbox_tuple(bbox: Mapping[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox, Mapping):
        return None
    try:
        x0 = float(bbox.get("x0") or 0.0)
        y0 = float(bbox.get("y0") or 0.0)
        x1 = float(bbox.get("x1") or 0.0)
        y1 = float(bbox.get("y1") or 0.0)
    except Exception:
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _bbox_union(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _bbox_overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    inter_w = min(a[2], b[2]) - max(a[0], b[0])
    inter_h = min(a[3], b[3]) - max(a[1], b[1])
    if inter_w <= 0.0 or inter_h <= 0.0:
        return 0.0
    inter = inter_w * inter_h
    area_a = max(0.0, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(0.0, (b[2] - b[0]) * (b[3] - b[1]))
    denom = max(1.0, min(area_a, area_b))
    return max(0.0, min(1.0, inter / denom))


def _bbox_edge_distance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    # 0 if intersect; otherwise Euclidean distance between closest edges.
    dx = 0.0
    if a[2] < b[0]:
        dx = b[0] - a[2]
    elif b[2] < a[0]:
        dx = a[0] - b[2]
    dy = 0.0
    if a[3] < b[1]:
        dy = b[1] - a[3]
    elif b[3] < a[1]:
        dy = a[1] - b[3]
    return math.sqrt((dx * dx) + (dy * dy))


def _anchor_for_block(*, page_number: int, order_index: int, meta: Mapping[str, Any]) -> str:
    anchor = str(meta.get("anchor") or "").strip()
    if anchor:
        return anchor
    return f"p{page_number}-b{order_index}"


@dataclass(frozen=True)
class CanonicalReconstructionMeta:
    reconstructed_tables: int = 0
    reconstructed_rows: int = 0
    attached_blocks: int = 0
    attached_cells: int = 0
    consumed_blocks: int = 0
    modified_tables: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class CanonicalTableReconstructor:
    """
    Generic ingestion-time reconstruction for "table-ish" PDF layouts:
    - Pass A: Build pseudo-tables from aligned blocks (gridless tables).
    - Pass B: Attach orphan/residual blocks into the most likely cell (cell completion).
    """

    def __init__(
        self,
        *,
        PageLayout: type,
        PageBlockPayload: type,
        TablePayload: type,
        TableRowPayload: type,
        TableCellPayload: type,
    ) -> None:
        self.PageLayout = PageLayout
        self.PageBlockPayload = PageBlockPayload
        self.TablePayload = TablePayload
        self.TableRowPayload = TableRowPayload
        self.TableCellPayload = TableCellPayload

    def run(
        self,
        *,
        pages: Sequence[Any],
        tables: Sequence[Any],
    ) -> tuple[list[Any], list[Any], CanonicalReconstructionMeta, list[dict[str, Any]]]:
        # issues are returned as IssuePayload-like dicts so knowledge_ingestion can wrap them if desired.
        issues: list[dict[str, Any]] = []
        meta = CanonicalReconstructionMeta()

        if not pages:
            return list(pages), list(tables), meta, issues

        tables_in: list[Any] = list(tables or [])
        pages_in: list[Any] = list(pages)

        max_order_index = max((int(getattr(t, "order_index", 0) or 0) for t in tables_in), default=0)

        # Pass A: reconstruct missing pseudo-tables from page blocks.
        pages_in, new_tables, a_meta = self._reconstruct_pseudo_tables(pages_in, tables_in, start_order_index=max_order_index + 1)
        if new_tables:
            tables_in.extend(new_tables)
            max_order_index = max((int(getattr(t, "order_index", 0) or 0) for t in tables_in), default=max_order_index)
        meta = self._merge_meta(meta, a_meta)

        # Pass B: attach orphan/residual blocks into extracted table cells.
        pages_in, tables_in, b_meta = self._attach_orphan_blocks(pages_in, tables_in)
        meta = self._merge_meta(meta, b_meta)

        return pages_in, tables_in, meta, issues

    @staticmethod
    def _merge_meta(base: CanonicalReconstructionMeta, delta: CanonicalReconstructionMeta) -> CanonicalReconstructionMeta:
        details = dict(base.details)
        details.update(delta.details)
        return CanonicalReconstructionMeta(
            reconstructed_tables=base.reconstructed_tables + delta.reconstructed_tables,
            reconstructed_rows=base.reconstructed_rows + delta.reconstructed_rows,
            attached_blocks=base.attached_blocks + delta.attached_blocks,
            attached_cells=base.attached_cells + delta.attached_cells,
            consumed_blocks=base.consumed_blocks + delta.consumed_blocks,
            modified_tables=base.modified_tables + delta.modified_tables,
            details=details,
        )

    def _reconstruct_pseudo_tables(
        self,
        pages: Sequence[Any],
        tables: Sequence[Any],
        *,
        start_order_index: int,
    ) -> tuple[list[Any], list[Any], CanonicalReconstructionMeta]:
        # Conservative reconstruction: only build 2-column pseudo tables from stable aligned row pairs.
        out_pages: list[Any] = []
        new_tables: list[Any] = []
        consumed = 0
        reconstructed_rows = 0
        order_index = int(start_order_index)

        # Precompute table bboxes by page so we avoid reconstructing inside known tables.
        table_bboxes_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
        for table in tables or []:
            page_number = getattr(table, "page_number", None)
            if not page_number:
                continue
            tb = _bbox_tuple(getattr(table, "bbox", None))
            if not tb:
                continue
            table_bboxes_by_page.setdefault(int(page_number), []).append(tb)

        for page in pages:
            page_number = int(getattr(page, "page_number", 0) or 0)
            page_width = float(getattr(page, "width", 0.0) or 0.0)

            candidates: list[dict[str, Any]] = []
            for block in getattr(page, "blocks", []) or []:
                block_type = str(getattr(block, "block_type", "") or "")
                if block_type not in {"paragraph", "heading", "list"}:
                    continue
                meta = getattr(block, "metadata", None)
                meta = meta if isinstance(meta, Mapping) else {}
                if meta.get("is_decorative") or meta.get("region_role") == "decorative":
                    continue
                if meta.get("table_overlap_candidate"):
                    # Inside known extracted table region.
                    continue
                if meta.get("canonical_consumed_by_table"):
                    continue
                text = _clean_text(str(getattr(block, "text", "") or ""))
                if not text:
                    continue
                bb = _bbox_tuple(getattr(block, "bbox", None))
                if not bb:
                    continue

                # Skip blocks fully inside existing table bboxes (even if overlap annotator missed it).
                inside_existing = False
                for tb in table_bboxes_by_page.get(page_number, []):
                    if _bbox_overlap_ratio(bb, tb) >= 0.8:
                        inside_existing = True
                        break
                if inside_existing:
                    continue

                candidates.append(
                    {
                        "block": block,
                        "text": text,
                        "bbox": bb,
                        "x0": bb[0],
                        "y0": bb[1],
                        "x1": bb[2],
                        "y1": bb[3],
                        "numeric": _numeric_signal(text),
                        "anchor": _anchor_for_block(
                            page_number=page_number,
                            order_index=int(getattr(block, "order_index", 0) or 0),
                            meta=meta,
                        ),
                    }
                )

            if not candidates:
                out_pages.append(page)
                continue

            # Build line-like row groups by y overlap (with a small gap fallback).
            #
            # Important: do NOT use a large gap threshold here; it will merge adjacent
            # rows (common in compact tables) and destroy row pair formation.
            heights = sorted([max(1.0, c["y1"] - c["y0"]) for c in candidates])
            median_h = heights[len(heights) // 2] if heights else 10.0
            small_gap = max(2.0, median_h * 0.15)

            candidates.sort(key=lambda c: (c["y0"], c["x0"]))
            row_groups: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            cur_y0 = 0.0
            cur_y1 = 0.0
            for c in candidates:
                if not current:
                    current = [c]
                    cur_y0, cur_y1 = c["y0"], c["y1"]
                    continue
                overlap = min(cur_y1, c["y1"]) - max(cur_y0, c["y0"])
                gap = c["y0"] - cur_y1
                if overlap > 0.0 or (0.0 <= gap <= small_gap):
                    current.append(c)
                    cur_y0 = min(cur_y0, c["y0"])
                    cur_y1 = max(cur_y1, c["y1"])
                    continue
                row_groups.append(current)
                current = [c]
                cur_y0, cur_y1 = c["y0"], c["y1"]
            if current:
                row_groups.append(current)

            # Extract strict 2-column rows: left label + right value (value must be numeric-ish).
            row_pairs: list[dict[str, Any]] = []
            for group in row_groups:
                group_sorted = sorted(group, key=lambda c: c["x0"])
                if len(group_sorted) < 2:
                    continue
                # Choose the rightmost numeric-ish block as the value (fees tend to live there).
                numeric_blocks = [c for c in group_sorted if c.get("numeric")]
                if not numeric_blocks:
                    continue
                value = max(numeric_blocks, key=lambda c: c["x0"])
                # Choose the closest block immediately to the left of the value as the label.
                # This avoids picking far-left section headings when a "row" has extra blocks.
                left_candidates = [c for c in group_sorted if c["x0"] < (value["x0"] - 1e-6)]
                if not left_candidates:
                    continue
                # Drop common low-information placeholders (e.g. "No fees") when selecting a label.
                filtered_left = [c for c in left_candidates if not _looks_like_value_placeholder(c.get("text", ""))]
                if filtered_left:
                    left_candidates = filtered_left
                non_numeric_left = [c for c in left_candidates if not c.get("numeric")]
                label = max(non_numeric_left, key=lambda c: c["x0"]) if non_numeric_left else max(left_candidates, key=lambda c: c["x0"])
                # Avoid accidentally treating long sentences as a row label.
                if len(label["text"]) > 140:
                    continue
                # Many business tables have a low-information middle column (e.g. "No fees", "Free")
                # between the label and the numeric value. Treat those placeholder blocks as ignorable
                # noise so gating can still promote the underlying 2-column structure.
                placeholder_extra_count = 0
                if len(group_sorted) > 2:
                    label_anchor = str(label.get("anchor") or "")
                    value_anchor = str(value.get("anchor") or "")
                    placeholder_extra_count = sum(
                        1
                        for c in group_sorted
                        if str(c.get("anchor") or "") not in {label_anchor, value_anchor}
                        and _looks_like_value_placeholder(c.get("text", ""))
                    )
                extra_count = max(0, len(group_sorted) - 2)
                non_placeholder_extras = max(0, extra_count - int(placeholder_extra_count))
                effective_group_size = 2 + non_placeholder_extras
                row_pairs.append(
                    {
                        "y0": min(label["y0"], value["y0"]),
                        "y1": max(label["y1"], value["y1"]),
                        "label": label,
                        "value": value,
                        "group_size": len(group_sorted),
                        "placeholder_extra_count": int(placeholder_extra_count),
                        "effective_group_size": int(effective_group_size),
                    }
                )

            if len(row_pairs) < 3:
                out_pages.append(page)
                continue

            # Find the largest contiguous aligned region of row_pairs.
            # Alignment = stable x0 for value column and stable x0 for label column.
            def _aligned(a: dict[str, Any], b: dict[str, Any]) -> bool:
                if page_width <= 0:
                    tol = 12.0
                else:
                    tol = max(8.0, page_width * 0.03)
                return (
                    abs(a["label"]["x0"] - b["label"]["x0"]) <= tol
                    and abs(a["value"]["x0"] - b["value"]["x0"]) <= tol
                )

            best_start = 0
            best_end = 0
            i = 0
            while i < len(row_pairs):
                j = i + 1
                while j < len(row_pairs) and _aligned(row_pairs[j - 1], row_pairs[j]):
                    j += 1
                if (j - i) > (best_end - best_start):
                    best_start, best_end = i, j
                i = j

            region = row_pairs[best_start:best_end]
            if len(region) < 3:
                out_pages.append(page)
                continue

            # Derive a title/section heading from the closest short heading above the region.
            region_y0 = min(r["y0"] for r in region)
            heading_text = ""
            heading_anchor = None
            # Scan up to 12 blocks above by y position.
            above_all = [c for c in candidates if c["y1"] <= region_y0 and len(c["text"]) <= 80]
            # Prefer section-like headings over column header rows when possible.
            # "Pricing" is a common business label for table sections and helps avoid picking
            # concatenated column headers like "Disbursement Fees ... Monthly Transaction Amount".
            above = [c for c in above_all if "pricing" in _norm_for_contains(c["text"])] or above_all
            above.sort(key=lambda c: (region_y0 - c["y1"], len(c["text"])))
            for cand in above[:12]:
                txt = cand["text"]
                if "@" in txt or "http://" in txt.lower() or "https://" in txt.lower():
                    continue
                if txt.endswith((".", "?", "!")):
                    continue
                # Avoid headings that are mostly numeric.
                alnum = [ch for ch in txt if ch.isalnum()]
                if alnum:
                    digits = sum(1 for ch in alnum if ch.isdigit())
                    if (digits / len(alnum)) > 0.35:
                        continue
                heading_text = txt
                heading_anchor = cand["anchor"]
                break

            if not self._region_passes_gate(
                region=region,
                heading_text=heading_text,
                region_y0=region_y0,
                median_height=median_h,
                page_width=page_width,
            ):
                out_pages.append(page)
                continue

            title = heading_text or "Reconstructed Table"
            section_heading = heading_text

            # Build table rows/cells from the region.
            column_schema = ["Description", "Value"]
            table_bbox = None
            rows: list[Any] = []
            used_anchors: set[str] = set()
            for row_idx, entry in enumerate(region):
                label = entry["label"]
                value = entry["value"]
                used_anchors.add(label["anchor"])
                used_anchors.add(value["anchor"])
                row_bbox = (min(label["bbox"][0], value["bbox"][0]), entry["y0"], max(label["bbox"][2], value["bbox"][2]), entry["y1"])
                if table_bbox is None:
                    table_bbox = row_bbox
                else:
                    table_bbox = _bbox_union(table_bbox, row_bbox)
                cells = [
                    self.TableCellPayload(
                        row_index=row_idx,
                        column_index=0,
                        column_key=column_schema[0],
                        raw_text=label["text"],
                        bbox={"x0": label["bbox"][0], "y0": label["bbox"][1], "x1": label["bbox"][2], "y1": label["bbox"][3]},
                        metadata={"canonical_reconstructed": True, "source_block_anchor": label["anchor"]},
                    ),
                    self.TableCellPayload(
                        row_index=row_idx,
                        column_index=1,
                        column_key=column_schema[1],
                        raw_text=value["text"],
                        bbox={"x0": value["bbox"][0], "y0": value["bbox"][1], "x1": value["bbox"][2], "y1": value["bbox"][3]},
                        metadata={"canonical_reconstructed": True, "source_block_anchor": value["anchor"]},
                    ),
                ]
                rows.append(
                    self.TableRowPayload(
                        row_index=row_idx,
                        page_number=page_number or None,
                        bbox={"x0": row_bbox[0], "y0": row_bbox[1], "x1": row_bbox[2], "y1": row_bbox[3]},
                        raw_text="",
                        metadata={"row_type": "data", "canonical_reconstructed": True},
                        cells=cells,
                    )
                )
                reconstructed_rows += 1

            if table_bbox is None:
                out_pages.append(page)
                continue

            table_meta = {
                "detected_via": "canonical_reconstruction",
                "canonical_reconstruction": {
                    "type": "pseudo_table",
                    "block_anchors": sorted(used_anchors)[:40],
                    "derived_heading_anchor": heading_anchor,
                },
            }
            new_tables.append(
                self.TablePayload(
                    order_index=order_index,
                    title=title,
                    section_heading=section_heading,
                    page_number=page_number or None,
                    bbox={"x0": table_bbox[0], "y0": table_bbox[1], "x1": table_bbox[2], "y1": table_bbox[3]},
                    column_schema=column_schema,
                    metadata=table_meta,
                    rows=rows,
                )
            )
            order_index += 1

            # Mark blocks we used as consumed so they do not also become free-text chunks.
            updated_blocks: list[Any] = []
            for block in getattr(page, "blocks", []) or []:
                meta0 = getattr(block, "metadata", None)
                meta0 = dict(meta0) if isinstance(meta0, Mapping) else {}
                anchor = _anchor_for_block(
                    page_number=page_number,
                    order_index=int(getattr(block, "order_index", 0) or 0),
                    meta=meta0,
                )
                if anchor in used_anchors:
                    meta0["canonical_consumed_by_table"] = True
                    meta0["canonical_consumed_reason"] = "pseudo_table_reconstruction"
                    meta0["canonical_consumed_table_order_index"] = order_index - 1
                    consumed += 1
                updated_blocks.append(
                    self.PageBlockPayload(
                        block_type=getattr(block, "block_type", ""),
                        order_index=int(getattr(block, "order_index", 0) or 0),
                        text=getattr(block, "text", ""),
                        bbox=getattr(block, "bbox", {}) or {},
                        section_heading=getattr(block, "section_heading", "") or "",
                        heading_path=list(getattr(block, "heading_path", []) or []),
                        detected_language=getattr(block, "detected_language", "") or "",
                        confidence=getattr(block, "confidence", None),
                        metadata=meta0,
                    )
                )
            out_pages.append(
                self.PageLayout(
                    page_number=page_number,
                    width=float(getattr(page, "width", 0.0) or 0.0),
                    height=float(getattr(page, "height", 0.0) or 0.0),
                    rotation=int(getattr(page, "rotation", 0) or 0),
                    text_density=float(getattr(page, "text_density", 0.0) or 0.0),
                    has_ocr_content=bool(getattr(page, "has_ocr_content", False)),
                    content_type=str(getattr(page, "content_type", "") or ""),
                    blocks=updated_blocks,
                    metadata=dict(getattr(page, "metadata", {}) or {}),
                )
            )

        meta = CanonicalReconstructionMeta(
            reconstructed_tables=len(new_tables),
            reconstructed_rows=reconstructed_rows,
            consumed_blocks=consumed,
            details={"pass_a": {"start_order_index": int(start_order_index), "end_order_index": int(order_index - 1)}},
        )
        return out_pages, new_tables, meta

    def _region_passes_gate(
        self,
        *,
        region: Sequence[dict[str, Any]],
        heading_text: str,
        region_y0: float,
        median_height: float,
        page_width: float,
    ) -> bool:
        if len(region) < 4 and not heading_text:
            return False

        if len(region) < 3:
            return False

        label_xs = [float(entry["label"]["x0"]) for entry in region]
        value_xs = [float(entry["value"]["x0"]) for entry in region]
        if not label_xs or not value_xs:
            return False

        column_gap = min(value_xs) - max(label_xs)
        min_gap = max(80.0, page_width * 0.14) if page_width > 0 else 80.0
        if column_gap < min_gap:
            return False

        if len(region) == 3:
            if not heading_text:
                return False
            # For very small regions, require "pure" 2-column shape: any extra blocks must be
            # ignorable placeholders (e.g. "No fees"), otherwise the risk of false positives is high.
            if any(int(entry.get("effective_group_size") or entry.get("group_size") or 0) > 2 for entry in region):
                return False

        effective_sizes = [int(entry.get("effective_group_size") or entry.get("group_size") or 0) for entry in region]
        if effective_sizes and (sum(effective_sizes) / max(1, len(effective_sizes))) > 2.35:
            return False

        gaps: list[float] = []
        previous_y1: float | None = None
        for entry in region:
            if previous_y1 is not None:
                gaps.append(max(0.0, float(entry["y0"]) - previous_y1))
            previous_y1 = float(entry["y1"])
        if gaps:
            max_gap = max(gaps)
            min_gap_observed = min(gaps)
            allowed_gap_variance = max(10.0, median_height * 0.85)
            if (max_gap - min_gap_observed) > allowed_gap_variance:
                return False

        if heading_text:
            heading_len = len(_clean_text(heading_text))
            if heading_len > 90:
                return False

        return True

    def _attach_orphan_blocks(
        self,
        pages: Sequence[Any],
        tables: Sequence[Any],
    ) -> tuple[list[Any], list[Any], CanonicalReconstructionMeta]:
        if not pages or not tables:
            return list(pages), list(tables), CanonicalReconstructionMeta()

        attached_blocks = 0
        attached_cells = 0
        consumed_blocks = 0
        modified_tables: set[int] = set()

        # Track per-block updates.
        updated_pages: list[Any] = []
        # Track per-table updates (by index in `tables`). Always read/modify tables through this
        # list to avoid losing earlier attachments when multiple blocks target the same table.
        updated_tables: list[Any] = list(tables)
        table_indices_by_page: dict[int, list[int]] = {}
        for idx, t in enumerate(updated_tables):
            page_number = getattr(t, "page_number", None)
            if not page_number:
                continue
            table_indices_by_page.setdefault(int(page_number), []).append(idx)
        for idxs in table_indices_by_page.values():
            idxs.sort(key=lambda i: int(getattr(updated_tables[i], "order_index", 0) or 0))

        for page in pages:
            page_number = int(getattr(page, "page_number", 0) or 0)
            page_table_indices = table_indices_by_page.get(page_number) or []
            if not page_table_indices:
                updated_pages.append(page)
                continue

            updated_blocks: list[Any] = []
            for block in getattr(page, "blocks", []) or []:
                meta0 = getattr(block, "metadata", None)
                meta0 = dict(meta0) if isinstance(meta0, Mapping) else {}
                if meta0.get("canonical_consumed_by_table"):
                    updated_blocks.append(block)
                    continue

                text = _clean_text(str(getattr(block, "text", "") or ""))
                if not text:
                    updated_blocks.append(block)
                    continue
                block_bbox = _bbox_tuple(getattr(block, "bbox", None))
                if not block_bbox:
                    updated_blocks.append(block)
                    continue

                is_residual = bool(
                    meta0.get("table_residual")
                    or meta0.get("table_residual_candidate")
                    or meta0.get("content_source") == "table_residual"
                    or meta0.get("region_role") == "table_residual"
                )
                is_numeric = _numeric_signal(text)

                # Choose best table on this page by overlap, else nearest distance.
                best_table_idx: int | None = None
                best_overlap = 0.0
                best_distance = float("inf")
                for idx in page_table_indices:
                    t = updated_tables[idx]
                    tb = _bbox_tuple(getattr(t, "bbox", None))
                    if not tb:
                        continue
                    ov = _bbox_overlap_ratio(block_bbox, tb)
                    dist = _bbox_edge_distance(block_bbox, tb)
                    if ov > best_overlap + 1e-6:
                        best_overlap = ov
                        best_distance = dist
                        best_table_idx = idx
                        continue
                    if math.isclose(ov, best_overlap, rel_tol=1e-6, abs_tol=1e-6) and dist < best_distance:
                        best_distance = dist
                        best_table_idx = idx
                if best_table_idx is None:
                    updated_blocks.append(block)
                    continue

                # For non-residual candidates, require both:
                # - numeric signal (e.g., fee/limit text), and
                # - the block is inside/very close to a table region.
                #
                # This avoids accidental attachment of normal prose into tables.
                if not is_residual:
                    if not is_numeric:
                        updated_blocks.append(block)
                        continue
                    if best_overlap < 0.2 and best_distance > 10.0:
                        updated_blocks.append(block)
                        continue

                best_table = updated_tables[best_table_idx]

                # Choose best (row, cell) pair using overlap + y alignment + lexical containment.
                best_row_index: int | None = None
                best_cell_idx: int | None = None
                best_cell_overlap = 0.0
                best_score = 0.0
                best_len = -1
                norm_block = _norm_for_contains(text)
                block_yc = 0.5 * (block_bbox[1] + block_bbox[3])
                for row in getattr(best_table, "rows", []) or []:
                    rb = _bbox_tuple(getattr(row, "bbox", None))
                    if not rb:
                        continue
                    y_inter = min(block_bbox[3], rb[3]) - max(block_bbox[1], rb[1])
                    y_den = max(1.0, min(block_bbox[3] - block_bbox[1], rb[3] - rb[1]))
                    row_y_score = max(0.0, min(1.0, y_inter / y_den))
                    if row_y_score <= 0.0:
                        # Still allow if very close in y (useful when bboxes are coarse).
                        row_yc = 0.5 * (rb[1] + rb[3])
                        if abs(block_yc - row_yc) > 30.0:
                            continue

                    for cell in getattr(row, "cells", []) or []:
                        cb = _bbox_tuple(getattr(cell, "bbox", None))
                        if not cb:
                            continue
                        cell_ov = _bbox_overlap_ratio(block_bbox, cb)
                        if cell_ov <= 0.0:
                            continue
                        cell_text = _clean_text(str(getattr(cell, "raw_text", "") or ""))
                        norm_cell = _norm_for_contains(cell_text)
                        superset = bool(norm_cell and norm_cell in norm_block and len(text) >= len(cell_text) + 8)
                        subset = bool(norm_block and norm_block in norm_cell)
                        lexical = 0.0
                        if superset:
                            lexical = min(1.0, len(norm_cell) / max(1, len(norm_block))) + 0.25
                        elif subset:
                            lexical = 0.05

                        # Weighted score:
                        # - overlap dominates
                        # - lexical superset helps pick the right row when a block spans multiple rows
                        score = (cell_ov * 2.0) + (row_y_score * 0.5) + (lexical * 2.0)
                        tie_len = len(cell_text)
                        if (score > best_score + 1e-6) or (math.isclose(score, best_score, rel_tol=1e-6, abs_tol=1e-6) and tie_len > best_len):
                            best_score = score
                            best_len = tie_len
                            best_row_index = int(getattr(row, "row_index", 0) or 0)
                            best_cell_idx = int(getattr(cell, "column_index", 0) or 0)
                            best_cell_overlap = float(cell_ov)

                if best_row_index is None or best_cell_idx is None:
                    updated_blocks.append(block)
                    continue

                # Update the target cell text (replace if residual is a strict superset).
                new_rows: list[Any] = []
                table_changed = False
                for row in getattr(best_table, "rows", []) or []:
                    row_index = getattr(row, "row_index", None)
                    if row_index is None or int(row_index) != int(best_row_index):
                        new_rows.append(row)
                        continue
                    new_cells: list[Any] = []
                    for cell in getattr(row, "cells", []) or []:
                        column_index = getattr(cell, "column_index", None)
                        if column_index is None or int(column_index) != int(best_cell_idx):
                            new_cells.append(cell)
                            continue
                        cell_text = _clean_text(str(getattr(cell, "raw_text", "") or ""))
                        residual_text = text
                        norm_cell = _norm_for_contains(cell_text)
                        norm_res = _norm_for_contains(residual_text)
                        if norm_cell and norm_cell in norm_res and len(residual_text) >= len(cell_text) + 8:
                            merged = residual_text
                        elif norm_res and norm_res in norm_cell:
                            merged = cell_text
                        else:
                            merged = (cell_text + "\n" + residual_text).strip() if cell_text else residual_text

                        # Only attach if we actually changed the cell.
                        if _clean_text(merged) == _clean_text(cell_text):
                            new_cells.append(cell)
                            continue

                        cell_meta = getattr(cell, "metadata", None)
                        cell_meta = dict(cell_meta) if isinstance(cell_meta, Mapping) else {}
                        attachments = cell_meta.get("canonical_attached_blocks")
                        if not isinstance(attachments, list):
                            attachments = []
                        anchor = _anchor_for_block(
                            page_number=page_number,
                            order_index=int(getattr(block, "order_index", 0) or 0),
                            meta=meta0,
                        )
                        attachments.append(
                            {
                                "anchor": anchor,
                                "reason": "table_residual_attachment",
                                "overlap": round(float(best_cell_overlap), 4),
                            }
                        )
                        cell_meta["canonical_attached_blocks"] = attachments[:12]
                        cell_meta["canonical_cell_completed"] = True
                        new_cells.append(
                            self.TableCellPayload(
                                row_index=int(getattr(cell, "row_index", 0) or 0),
                                column_index=int(getattr(cell, "column_index", 0) or 0),
                                column_key=str(getattr(cell, "column_key", "") or ""),
                                raw_text=merged,
                                normalized_value=dict(getattr(cell, "normalized_value", {}) or {}),
                                bbox=dict(getattr(cell, "bbox", {}) or {}),
                                confidence=getattr(cell, "confidence", None),
                                metadata=cell_meta,
                            )
                        )
                        table_changed = True
                        attached_cells += 1
                    if table_changed:
                        new_rows.append(
                            self.TableRowPayload(
                                row_index=int(getattr(row, "row_index", 0) or 0),
                                page_number=getattr(row, "page_number", None),
                                bbox=dict(getattr(row, "bbox", {}) or {}),
                                raw_text=str(getattr(row, "raw_text", "") or ""),
                                metadata=dict(getattr(row, "metadata", {}) or {}),
                                cells=new_cells,
                            )
                        )
                    else:
                        new_rows.append(row)
                if not table_changed:
                    updated_blocks.append(block)
                    continue

                # Replace the table in the tables list.
                new_table_meta = dict(getattr(best_table, "metadata", {}) or {})
                recon = new_table_meta.get("canonical_reconstruction")
                recon = dict(recon) if isinstance(recon, Mapping) else {}
                recon["cell_attachments"] = int(recon.get("cell_attachments") or 0) + 1
                new_table_meta["canonical_reconstruction"] = recon

                new_table = self.TablePayload(
                    order_index=int(getattr(best_table, "order_index", 0) or 0),
                    title=str(getattr(best_table, "title", "") or ""),
                    section_heading=str(getattr(best_table, "section_heading", "") or ""),
                    page_number=getattr(best_table, "page_number", None),
                    bbox=dict(getattr(best_table, "bbox", {}) or {}),
                    column_schema=list(getattr(best_table, "column_schema", []) or []),
                    data_dictionary=dict(getattr(best_table, "data_dictionary", {}) or {}),
                    metadata=new_table_meta,
                    rows=new_rows,
                )

                updated_tables[best_table_idx] = new_table
                modified_tables.add(best_table_idx)

                # Mark this residual block as consumed so it doesn't become a separate chunk.
                meta0["canonical_consumed_by_table"] = True
                meta0["canonical_consumed_reason"] = "cell_completion_attachment"
                meta0["canonical_consumed_table_order_index"] = int(getattr(best_table, "order_index", 0) or 0)
                meta0["canonical_consumed_table_page_number"] = page_number
                meta0["canonical_consumed_row_index"] = int(best_row_index)
                meta0["canonical_consumed_column_index"] = int(best_cell_idx)
                consumed_blocks += 1
                attached_blocks += 1

                updated_blocks.append(
                    self.PageBlockPayload(
                        block_type=getattr(block, "block_type", ""),
                        order_index=int(getattr(block, "order_index", 0) or 0),
                        text=getattr(block, "text", ""),
                        bbox=getattr(block, "bbox", {}) or {},
                        section_heading=getattr(block, "section_heading", "") or "",
                        heading_path=list(getattr(block, "heading_path", []) or []),
                        detected_language=getattr(block, "detected_language", "") or "",
                        confidence=getattr(block, "confidence", None),
                        metadata=meta0,
                    )
                )

            updated_pages.append(
                self.PageLayout(
                    page_number=page_number,
                    width=float(getattr(page, "width", 0.0) or 0.0),
                    height=float(getattr(page, "height", 0.0) or 0.0),
                    rotation=int(getattr(page, "rotation", 0) or 0),
                    text_density=float(getattr(page, "text_density", 0.0) or 0.0),
                    has_ocr_content=bool(getattr(page, "has_ocr_content", False)),
                    content_type=str(getattr(page, "content_type", "") or ""),
                    blocks=updated_blocks,
                    metadata=dict(getattr(page, "metadata", {}) or {}),
                )
            )

        meta = CanonicalReconstructionMeta(
            attached_blocks=attached_blocks,
            attached_cells=attached_cells,
            consumed_blocks=consumed_blocks,
            modified_tables=len(modified_tables),
            details={"pass_b": {"modified_table_count": len(modified_tables)}},
        )
        return updated_pages, updated_tables, meta
