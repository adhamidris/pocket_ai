from __future__ import annotations

from typing import Any, Mapping, Sequence

from apps.knowledge.tables.canonical.reconstruction import (
    CanonicalReconstructionMeta,
    _anchor_for_block,
    _bbox_overlap_ratio,
    _bbox_tuple,
    _bbox_union,
    _clean_text,
    _looks_like_value_placeholder,
    _norm_for_contains,
    _numeric_signal,
)


class CanonicalPseudoTableMixin:

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
