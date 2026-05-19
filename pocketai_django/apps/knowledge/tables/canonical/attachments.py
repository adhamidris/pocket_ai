from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from apps.knowledge.tables.canonical.reconstruction import (
    CanonicalReconstructionMeta,
    _anchor_for_block,
    _bbox_edge_distance,
    _bbox_overlap_ratio,
    _bbox_tuple,
    _bbox_width,
    _clean_text,
    _norm_for_contains,
    _numeric_signal,
)


class CanonicalTableAttachmentMixin:

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
                        cell_text = _clean_text(str(getattr(cell, "raw_text", "") or ""))
                        cell_meta = getattr(cell, "metadata", None)
                        cell_meta = cell_meta if isinstance(cell_meta, Mapping) else {}
                        if not cell_text:
                            continue
                        if "has_native_geometry" in cell_meta and not bool(cell_meta.get("has_native_geometry")):
                            continue
                        cell_ov = _bbox_overlap_ratio(block_bbox, cb)
                        if cell_ov <= 0.0:
                            continue
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

                best_row = next(
                    (
                        row
                        for row in (getattr(best_table, "rows", []) or [])
                        if int(getattr(row, "row_index", 0) or 0) == int(best_row_index)
                    ),
                    None,
                )
                if best_row is None:
                    updated_blocks.append(block)
                    continue

                overlapping_cells: list[tuple[int, float, float]] = []
                for row_cell in getattr(best_row, "cells", []) or []:
                    cell_bbox = _bbox_tuple(getattr(row_cell, "bbox", None))
                    if not cell_bbox:
                        continue
                    row_cell_text = _clean_text(str(getattr(row_cell, "raw_text", "") or ""))
                    row_cell_meta = getattr(row_cell, "metadata", None)
                    row_cell_meta = row_cell_meta if isinstance(row_cell_meta, Mapping) else {}
                    if not row_cell_text:
                        continue
                    if "has_native_geometry" in row_cell_meta and not bool(row_cell_meta.get("has_native_geometry")):
                        continue
                    cell_overlap = _bbox_overlap_ratio(block_bbox, cell_bbox)
                    if cell_overlap <= 0.0:
                        continue
                    overlapping_cells.append(
                        (
                            int(getattr(row_cell, "column_index", 0) or 0),
                            float(cell_overlap),
                            _bbox_width(cell_bbox),
                        )
                    )

                if len(overlapping_cells) >= 2:
                    strongest_other_overlap = max(
                        (
                            overlap
                            for column_index, overlap, _width in overlapping_cells
                            if column_index != int(best_cell_idx)
                        ),
                        default=0.0,
                    )
                    overlap_threshold = max(0.45, strongest_other_overlap - 0.05)
                    strong_cells = [
                        entry
                        for entry in overlapping_cells
                        if entry[1] >= overlap_threshold
                    ]
                    if len(strong_cells) >= 2:
                        widths = sorted(width for _idx, _ov, width in strong_cells if width > 0.0)
                        median_width = widths[len(widths) // 2] if widths else 0.0
                        block_width = _bbox_width(block_bbox)
                        width_ratio = (
                            block_width / max(1.0, median_width)
                            if median_width > 0.0
                            else 0.0
                        )
                        winner_margin = float(best_cell_overlap) - float(strongest_other_overlap)
                        # A residual block that spans multiple peer cells without a
                        # clear geometric winner is row-wide context, not a single
                        # cell completion candidate.
                        if width_ratio >= 1.6 and winner_margin <= 0.08:
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
