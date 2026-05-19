from __future__ import annotations

import re
from typing import Any, Sequence

from apps.knowledge.ingestion.contracts import TableCellPayload, TableRowPayload, _union_bbox
from apps.knowledge.tables.detection import TableDetector


class GeometryTableRowsMixin:

    @staticmethod
    def _merge_fragment_text(left: str, right: str) -> str:
        left_text = str(left or "").strip()
        right_text = str(right or "").strip()
        if not left_text:
            return right_text
        if not right_text:
            return left_text
        if left_text.lower() in {"n/a", "na", "not applicable"}:
            return left_text
        if right_text.lower() in {"n/a", "na", "not applicable"}:
            return right_text
        if left_text == right_text:
            return left_text
        if right_text in left_text:
            return left_text
        if left_text in right_text:
            return right_text
        if left_text.endswith(("+", "/", "-", "(")):
            return f"{left_text} {right_text}".strip()
        return f"{left_text} {right_text}".strip()

    @staticmethod
    def _row_texts(row: TableRowPayload, column_count: int) -> list[str]:
        texts = [""] * max(0, column_count)
        for cell in (row.cells or []):
            if 0 <= cell.column_index < len(texts):
                texts[cell.column_index] = str(cell.raw_text or "").strip()
        return texts

    def _row_non_empty_columns(self, texts: Sequence[str]) -> list[int]:
        return [idx for idx, text in enumerate(texts) if str(text or "").strip()]

    def _leading_descriptor_text(self, texts: Sequence[str]) -> str:
        return " ".join(str(texts[idx] or "").strip() for idx in range(min(2, len(texts))) if str(texts[idx] or "").strip()).strip()

    @staticmethod
    def _looks_like_range_descriptor(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        return bool(
            re.search(r"\b(?:from|to|up to|above|below|over|under)\b", candidate)
            or re.search(r"\d+\s*\+\b", candidate)
        )

    @staticmethod
    def _looks_like_value_continuation(text: str) -> bool:
        candidate = str(text or "").strip()
        if not candidate:
            return False
        lowered = candidate.lower()
        if candidate.endswith(("+", "/", "-", "(")):
            return True
        if lowered.startswith(("+", "correspondent", "courier", "swift", "telex", "fees", "max.", "min.")):
            return True
        if re.search(r"\b(?:correspondent|courier|swift|telex)\b", lowered):
            return True
        if re.search(r"\bmin\.?\b", lowered) and "max" not in lowered:
            return True
        return False

    def _is_descriptor_only_fragment_row(self, texts: Sequence[str]) -> bool:
        non_empty = self._row_non_empty_columns(texts)
        if not non_empty:
            return False
        if any(idx >= 2 for idx in non_empty):
            return False
        joined = self._leading_descriptor_text(texts)
        if not joined:
            return False
        if self._mostly_numeric_or_amount(joined):
            return False
        return len(re.findall(r"\w+", joined)) <= 6

    def _has_complementary_value_fragments(self, current_texts: Sequence[str], next_texts: Sequence[str]) -> bool:
        value_columns = range(2, min(len(current_texts), len(next_texts)))
        for idx in value_columns:
            current = str(current_texts[idx] or "").strip()
            nxt = str(next_texts[idx] or "").strip()
            if current and nxt and (
                self._looks_like_value_continuation(current) or self._looks_like_value_continuation(nxt)
            ):
                return True
            if current and not nxt and self._looks_like_value_continuation(current):
                return True
            if nxt and not current and self._looks_like_value_continuation(nxt):
                return True
        return False

    @staticmethod
    def _matrix_value_columns(texts: Sequence[str]) -> list[str]:
        if len(texts) <= 1:
            return []
        return [str(text or "").strip() for text in texts[1:]]

    @staticmethod
    def _descriptor_looks_incomplete_for_matrix(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        if candidate.endswith(("from", "for", "with", "to", "within", "per")):
            return True
        return False

    @staticmethod
    def _descriptor_looks_like_continuation_fragment(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        if candidate.startswith(("or ", "and ", "per ", "with ", "within ", "from ", "to ")):
            return True
        return False

    @staticmethod
    def _row_vertical_gap(current: TableRowPayload, nxt: TableRowPayload) -> float:
        current_bbox = current.bbox or {}
        next_bbox = nxt.bbox or {}
        try:
            current_y1 = float(current_bbox.get("y1") or 0.0)
            next_y0 = float(next_bbox.get("y0") or 0.0)
        except (TypeError, ValueError):
            return 9999.0
        if not current_y1 or not next_y0:
            return 9999.0
        return next_y0 - current_y1

    @staticmethod
    def _rows_overlap_vertically(current: TableRowPayload, nxt: TableRowPayload) -> bool:
        current_bbox = current.bbox or {}
        next_bbox = nxt.bbox or {}
        try:
            current_y0 = float(current_bbox.get("y0") or 0.0)
            current_y1 = float(current_bbox.get("y1") or 0.0)
            next_y0 = float(next_bbox.get("y0") or 0.0)
            next_y1 = float(next_bbox.get("y1") or 0.0)
        except (TypeError, ValueError):
            return False
        if not current_y1 or not next_y1:
            return False
        return min(current_y1, next_y1) - max(current_y0, next_y0) >= -2.0

    def _matrix_rows_share_parallel_values(self, current_texts: Sequence[str], next_texts: Sequence[str]) -> bool:
        current_values = self._matrix_value_columns(current_texts)
        next_values = self._matrix_value_columns(next_texts)
        if not current_values or not next_values:
            return False
        overlap_pairs = 0
        for current_value, next_value in zip(current_values, next_values):
            if not current_value or not next_value:
                continue
            overlap_pairs += 1
        return overlap_pairs >= 2

    @staticmethod
    def _looks_like_value_prefix_fragment(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        if not re.search(r"\d", candidate):
            return False
        if candidate.endswith(("from", "with", "for", "of", "on", "per", "to")):
            return True
        if candidate.endswith(("total", "loaded", "outstanding", "balance")):
            return True
        return False

    def _row_values_look_like_prefix_fragment(self, texts: Sequence[str]) -> bool:
        values = [value for value in self._matrix_value_columns(texts) if value]
        if len(values) < 2:
            return False
        prefix_count = sum(1 for value in values if self._looks_like_value_prefix_fragment(value))
        return prefix_count >= max(2, len(values) // 2)

    @staticmethod
    def _looks_like_value_suffix_fragment(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        if candidate.startswith(("min", "max", "per ", "purchase", "purchases", "month", "months", "only", "within")):
            return True
        if candidate in {"free", "n/a"}:
            return False
        return False

    def _row_values_look_like_suffix_fragment(self, texts: Sequence[str]) -> bool:
        values = [value for value in self._matrix_value_columns(texts) if value]
        if len(values) < 2:
            return False
        suffix_count = sum(1 for value in values if self._looks_like_value_suffix_fragment(value))
        return suffix_count >= max(2, len(values) // 2)

    def _rows_should_merge_logically(self, current: TableRowPayload, nxt: TableRowPayload, column_count: int) -> bool:
        current_texts = self._row_texts(current, column_count)
        next_texts = self._row_texts(nxt, column_count)
        current_non_empty = self._row_non_empty_columns(current_texts)
        next_non_empty = self._row_non_empty_columns(next_texts)
        if not current_non_empty or not next_non_empty:
            return False

        current_descriptor = self._leading_descriptor_text(current_texts)
        next_descriptor = self._leading_descriptor_text(next_texts)

        if self._looks_like_range_descriptor(current_descriptor) and self._looks_like_range_descriptor(next_descriptor):
            return False

        # Descriptor-only continuation lines should attach to the nearest logical row.
        if self._is_descriptor_only_fragment_row(current_texts) and self._is_descriptor_only_fragment_row(next_texts):
            return True
        if current_descriptor and self._is_descriptor_only_fragment_row(next_texts):
            return True

        # Value continuation rows are common in dense tariff tables where the fee formula wraps
        # across the next physical line while the descriptor stays on the first line.
        next_descriptor_only = self._is_descriptor_only_fragment_row(next_texts)
        if current_descriptor and (not next_descriptor or next_descriptor_only):
            if self._has_complementary_value_fragments(current_texts, next_texts):
                return True

        gap = self._row_vertical_gap(current, nxt)
        tightly_stacked = gap <= max(18.0, self.y_tol * 3.0) or self._rows_overlap_vertically(current, nxt)
        if tightly_stacked and column_count >= 4:
            current_descriptor_cell = str(current_texts[0] or "").strip()
            next_descriptor_cell = str(next_texts[0] or "").strip()
            if (
                current_descriptor_cell
                and not next_descriptor_cell
                and self._matrix_rows_share_parallel_values(current_texts, next_texts)
                and self._row_values_look_like_suffix_fragment(next_texts)
            ):
                return True
            if (
                current_descriptor_cell
                and next_descriptor_cell
                and self._descriptor_looks_incomplete_for_matrix(current_descriptor_cell)
                and self._matrix_rows_share_parallel_values(current_texts, next_texts)
            ):
                return True
            if (
                current_descriptor_cell
                and next_descriptor_cell
                and self._descriptor_looks_like_continuation_fragment(next_descriptor_cell)
                and self._matrix_rows_share_parallel_values(current_texts, next_texts)
            ):
                return True

        return False

    def _rows_should_merge_forward_logically(self, current: TableRowPayload, nxt: TableRowPayload, column_count: int) -> bool:
        if column_count < 4:
            return False
        current_texts = self._row_texts(current, column_count)
        next_texts = self._row_texts(nxt, column_count)
        current_descriptor_cell = str(current_texts[0] or "").strip()
        next_descriptor_cell = str(next_texts[0] or "").strip()
        if current_descriptor_cell or not next_descriptor_cell:
            return False
        gap = self._row_vertical_gap(current, nxt)
        tightly_stacked = gap <= max(18.0, self.y_tol * 3.0) or self._rows_overlap_vertically(current, nxt)
        if not tightly_stacked:
            return False
        if not self._matrix_rows_share_parallel_values(current_texts, next_texts):
            return False
        return self._row_values_look_like_prefix_fragment(current_texts)

    def _merge_geometry_rows(
        self,
        current: TableRowPayload,
        nxt: TableRowPayload,
        column_count: int,
    ) -> TableRowPayload:
        current_cells = {cell.column_index: cell for cell in (current.cells or [])}
        next_cells = {cell.column_index: cell for cell in (nxt.cells or [])}
        merged_cells: list[TableCellPayload] = []
        merged_bboxes: list[dict[str, Any]] = []

        for col_idx in range(column_count):
            current_cell = current_cells.get(col_idx)
            next_cell = next_cells.get(col_idx)
            current_text = str(current_cell.raw_text if current_cell else "").strip()
            next_text = str(next_cell.raw_text if next_cell else "").strip()
            merged_text = self._merge_fragment_text(current_text, next_text)
            current_bbox = current_cell.bbox if current_cell else {}
            next_bbox = next_cell.bbox if next_cell else {}
            merged_bbox = _union_bbox([bbox for bbox in [current_bbox, next_bbox] if bbox and any(bbox.values())])
            if any(merged_bbox.values()):
                merged_bboxes.append(merged_bbox)
            column_key = (
                current_cell.column_key
                if current_cell is not None
                else next_cell.column_key
                if next_cell is not None
                else f"column_{col_idx+1}"
            )
            span_count = int((current_cell.metadata or {}).get("span_count") or 0) + int((next_cell.metadata or {}).get("span_count") or 0)
            metadata = {"span_count": span_count}
            if current_text and next_text and merged_text != current_text:
                metadata["logical_row_merged"] = True
            merged_cells.append(
                TableCellPayload(
                    row_index=current.row_index,
                    column_index=col_idx,
                    column_key=column_key,
                    raw_text=merged_text,
                    normalized_value=TableDetector._normalize_cell_value(merged_text),
                    bbox=merged_bbox,
                    confidence=None,
                    metadata=metadata,
                )
            )

        merged_meta = dict(current.metadata or {})
        merged_meta["geometry_logical_row_merged"] = True
        merged_meta["geometry_merged_row_count"] = int(merged_meta.get("geometry_merged_row_count") or 1) + int((nxt.metadata or {}).get("geometry_merged_row_count") or 1)
        merged_bbox = _union_bbox([bbox for bbox in [current.bbox, nxt.bbox] if bbox and any(bbox.values())] + merged_bboxes)
        merged_raw = " | ".join(str(cell.raw_text or "").strip() for cell in merged_cells)
        return TableRowPayload(
            row_index=current.row_index,
            page_number=current.page_number,
            bbox=merged_bbox,
            raw_text=merged_raw,
            metadata=merged_meta,
            cells=merged_cells,
        )

    def _merge_geometry_rows_forward(
        self,
        current: TableRowPayload,
        nxt: TableRowPayload,
        column_count: int,
    ) -> TableRowPayload:
        current_cells = {cell.column_index: cell for cell in (current.cells or [])}
        next_cells = {cell.column_index: cell for cell in (nxt.cells or [])}
        merged_cells: list[TableCellPayload] = []
        merged_bboxes: list[dict[str, Any]] = []

        for col_idx in range(column_count):
            current_cell = current_cells.get(col_idx)
            next_cell = next_cells.get(col_idx)
            current_text = str(current_cell.raw_text if current_cell else "").strip()
            next_text = str(next_cell.raw_text if next_cell else "").strip()
            if col_idx == 0:
                merged_text = next_text or current_text
            else:
                merged_text = self._merge_fragment_text(current_text, next_text)
            current_bbox = current_cell.bbox if current_cell else {}
            next_bbox = next_cell.bbox if next_cell else {}
            merged_bbox = _union_bbox([bbox for bbox in [current_bbox, next_bbox] if bbox and any(bbox.values())])
            if any(merged_bbox.values()):
                merged_bboxes.append(merged_bbox)
            column_key = (
                next_cell.column_key
                if next_cell is not None
                else current_cell.column_key
                if current_cell is not None
                else f"column_{col_idx+1}"
            )
            span_count = int((current_cell.metadata or {}).get("span_count") or 0) + int((next_cell.metadata or {}).get("span_count") or 0)
            metadata = {"span_count": span_count, "logical_row_merged": True, "logical_row_merge_direction": "forward"}
            merged_cells.append(
                TableCellPayload(
                    row_index=current.row_index,
                    column_index=col_idx,
                    column_key=column_key,
                    raw_text=merged_text,
                    normalized_value=TableDetector._normalize_cell_value(merged_text),
                    bbox=merged_bbox,
                    confidence=None,
                    metadata=metadata,
                )
            )

        merged_meta = dict(nxt.metadata or {})
        merged_meta["geometry_logical_row_merged"] = True
        merged_meta["geometry_merged_row_count"] = int(merged_meta.get("geometry_merged_row_count") or 1) + int((current.metadata or {}).get("geometry_merged_row_count") or 1)
        merged_meta["geometry_merge_direction"] = "forward"
        merged_bbox = _union_bbox([bbox for bbox in [current.bbox, nxt.bbox] if bbox and any(bbox.values())] + merged_bboxes)
        merged_raw = " | ".join(str(cell.raw_text or "").strip() for cell in merged_cells)
        return TableRowPayload(
            row_index=current.row_index,
            page_number=nxt.page_number,
            bbox=merged_bbox,
            raw_text=merged_raw,
            metadata=merged_meta,
            cells=merged_cells,
        )

    def _normalize_geometry_logical_rows(self, rows: list[TableRowPayload], column_count: int) -> tuple[list[TableRowPayload], int]:
        if not rows:
            return rows, 0
        merged_rows: list[TableRowPayload] = []
        merged_pairs = 0
        idx = 0
        while idx < len(rows):
            current = rows[idx]
            while idx + 1 < len(rows):
                nxt = rows[idx + 1]
                if self._rows_should_merge_logically(current, nxt, column_count):
                    current = self._merge_geometry_rows(current, nxt, column_count)
                    merged_pairs += 1
                    idx += 1
                    continue
                if self._rows_should_merge_forward_logically(current, nxt, column_count):
                    current = self._merge_geometry_rows_forward(current, nxt, column_count)
                    merged_pairs += 1
                    idx += 1
                    continue
                break
            merged_rows.append(current)
            idx += 1
        normalized_rows: list[TableRowPayload] = []
        for idx, row in enumerate(merged_rows, start=1):
            normalized_cells = [
                TableCellPayload(
                    row_index=idx,
                    column_index=cell.column_index,
                    column_key=cell.column_key,
                    raw_text=cell.raw_text,
                    normalized_value=cell.normalized_value,
                    bbox=cell.bbox,
                    confidence=cell.confidence,
                    metadata=cell.metadata,
                )
                for cell in (row.cells or [])
            ]
            normalized_rows.append(
                TableRowPayload(
                    row_index=idx,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row.metadata,
                    cells=normalized_cells,
                )
            )
        return normalized_rows, merged_pairs
