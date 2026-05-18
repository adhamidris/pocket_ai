from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion_contracts import (
    IssuePayload,
    PageLayout,
    PdfSpan,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
    _union_bbox,
)
from apps.knowledge.ingestion_table_detection import TableDetector


# === [ADD] GeometryTableReconstructor: x/y clustering → grid → TablePayloads ===
class GeometryTableReconstructor:
    def __init__(
        self,
        y_tol: float = 6.0,
        x_tol_min: float = 6.0,
        header_keywords: Optional[Iterable[str]] = None,  # optional, default generic
    ):
        self.y_tol = y_tol
        self.x_tol_min = x_tol_min
        self.header_keywords = {k.strip().lower() for k in header_keywords} if header_keywords else set()

    @staticmethod
    def _mostly_numeric_or_amount(s: str) -> bool:
        """
        True if cell looks numeric/currency/percent-heavy (typical for data rows).
        """
        if not s:
            return False
        t = s.replace("\u00A0", " ").replace("\u2009", " ").replace("\u202F", " ").strip()
        # obvious numeric/amount/percent signals
        if re.search(r"\d", t) and (re.search(r"[%\d]", t) or re.search(r"[$€£]|EGP|USD|EUR|AED|SAR|GBP|LE", t, re.I)):
            return True
        # general numeric density heuristic
        letters = sum(c.isalpha() for c in t)
        digits = sum(c.isdigit() for c in t)
        return digits > 0 and digits >= letters

    # ---- Clustering helpers ----
    @staticmethod
    def _greedy_cluster(values: list[tuple[float, int]], tol: float) -> list[list[int]]:
        """
        values: list of (key, index) sorted by key
        Returns list of clusters of indices based on tolerance.
        """
        clusters: list[list[int]] = []
        if not values:
            return clusters
        current = [values[0][1]]
        anchor = values[0][0]
        for val, idx in values[1:]:
            if abs(val - anchor) <= tol:
                current.append(idx)
            else:
                clusters.append(current)
                current = [idx]
                anchor = val
        clusters.append(current)
        return clusters

    def _cluster_rows(self, spans: list[PdfSpan]) -> list[list[int]]:
        sorted_by_y = sorted(((s.y_center, i) for i, s in enumerate(spans)), key=lambda t: t[0])
        return self._greedy_cluster(sorted_by_y, self.y_tol)

    def _cluster_columns(self, spans: list[PdfSpan], page_width: float) -> list[list[int]]:
        x_tol = max(self.x_tol_min, page_width * 0.01)  # ~1% of page width
        sorted_by_x = sorted(((s.x_center, i) for i, s in enumerate(spans)), key=lambda t: t[0])
        return self._greedy_cluster(sorted_by_x, x_tol)

    # ---- Header detection ----
    def _is_header_row(self, cell_texts: list[str], avg_font: float, page_font_median: float) -> bool:
        """
        Generic header row scoring with layout-first cues:
          - font size prominence vs page median
          - uppercase ratio
          - trailing colon
          - cells are mostly NOT numeric/amount values
          - optional domain keywords (only if provided)
        """
        text = " ".join(cell_texts).strip()
        if not text:
            return False

        stripped = text.replace("\t", " ").strip()
        letters = sum(1 for c in stripped if c.isalpha())
        uppers  = sum(1 for c in stripped if c.isupper())
        digits  = sum(1 for c in stripped if c.isdigit())

        caps_ratio   = (uppers / letters) if letters else 0.0
        digit_ratio  = (digits / max(1, len([c for c in stripped if c.isalnum()])))
        colon        = stripped.endswith(":")

        size_boost = (avg_font > 0 and page_font_median > 0 and (avg_font >= page_font_median * 1.12))

        non_numeric_cells = sum(1 for t in cell_texts if t and not self._mostly_numeric_or_amount(t))
        non_numeric_ratio = non_numeric_cells / max(1, len(cell_texts))

        keyword_hit = False
        if self.header_keywords:
            low = stripped.lower()
            keyword_hit = any(k in low for k in self.header_keywords)

        # Combine signals (tuned to be conservative):
        # - any strong layout signal, or majority non-numeric cells with low digit density
        if size_boost:
            return True
        if colon:
            return True
        if caps_ratio > 0.6:
            return True
        if non_numeric_ratio >= 0.6 and digit_ratio < 0.35:
            return True
        if keyword_hit:
            return True
        return False

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

    # ---- Build tables for a single page ----
        # ---- Build tables for a single page ----
        # ---- Build tables for a single page ----
    def _build_page_tables(
            self,
            page_number: int,
            page_width: float,
            page_spans: list[PdfSpan],
            page_layout: PageLayout,
            order_offset: int,
        ) -> tuple[list[TablePayload], list[IssuePayload], dict]:
            """
            Build geometry-first tables by:
            - clustering spans into row bins (y)
            - deriving column bins from the FIRST QUALIFYING ROW (row-local)
            - allowing 2 columns if header/numeric cues present
            - using union-of-spans bboxes for cells & rows
            - accumulating subsequent rows that map to those bins
            - stopping when rows become too sparse (prevents giant noisy tables)
            """
            issues: list[IssuePayload] = []
            tables: list[TablePayload] = []

            if not page_spans:
                return tables, issues, {"bins": None, "header": None, "schema": None}

            # Page-wide row bins (y)
            row_clusters = self._cluster_rows(page_spans)

            # For header cue only
            font_sizes = [s.size for s in page_spans if s.size]
            page_font_median = statistics.median(font_sizes) if font_sizes else 0.0

            # Helper: compute row-local column bins from spans in the row
            def make_local_bins(row_span_idxs: list[int]) -> list[tuple[float, float]]:
                spans_in_row = [page_spans[i] for i in row_span_idxs]
                if not spans_in_row:
                    return []
                # cluster x-centers within the row
                x_tol = max(self.x_tol_min, page_width * 0.01)
                sorted_by_x = sorted(((s.x_center, j) for j, s in enumerate(spans_in_row)), key=lambda t: t[0])
                clusters = self._greedy_cluster(sorted_by_x, x_tol)
                # convert to (min_x0, max_x1) per bin
                bins: list[tuple[float, float]] = []
                for cl in clusters:
                    members = [spans_in_row[j] for j in cl]
                    if not members:
                        bins.append((0.0, 0.0))
                    else:
                        x0 = min(m.x0 for m in members)
                        x1 = max(m.x1 for m in members)
                        bins.append((float(x0), float(x1)))
                # left→right
                bins.sort(key=lambda b: (b[0], b[1]))
                return bins

            def assign_to_bins(row_span_idxs: list[int], col_bins: list[tuple[float, float]]):
                spans_in_row = [page_spans[i] for i in row_span_idxs]
                row_fonts = [s.size for s in spans_in_row if s.size]
                avg_font = (sum(row_fonts) / len(row_fonts)) if row_fonts else 0.0
                cell_texts: list[str] = []
                cell_bboxes: list[dict[str, float]] = []
                cell_counts: list[int] = []
                for (x_min, x_max) in col_bins:
                    members = [s for s in spans_in_row if (x_min <= s.x_center <= x_max)]
                    members.sort(key=lambda s: (round(s.y_center / 2) * 2, s.x_center))
                    text = " ".join([m.text for m in members if m.text]).strip()
                    if members:
                        bbox = {
                            "x0": float(min(m.x0 for m in members)),
                            "y0": float(min(m.y0 for m in members)),
                            "x1": float(max(m.x1 for m in members)),
                            "y1": float(max(m.y1 for m in members)),
                        }
                    else:
                        bbox = {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}
                    cell_texts.append(text)
                    cell_bboxes.append(bbox)
                    cell_counts.append(len(members))
                return cell_texts, cell_bboxes, cell_counts, avg_font

            # Find anchor row: row that qualifies as table start
            start_row_idx = None
            anchor_bins: list[tuple[float, float]] = []
            header_is_present = False
            header_cells: list[str] = []
            for r_idx, row_span_idxs in enumerate(row_clusters):
                bins = make_local_bins(row_span_idxs)
                if len(bins) < 2:
                    continue
                cell_texts, cell_bboxes, cell_counts, avg_font = assign_to_bins(row_span_idxs, bins)
                populated_cols = sum(1 for t in cell_texts if t)
                has_numeric = any(self._mostly_numeric_or_amount(t) for t in cell_texts if t)
                is_header = self._is_header_row(cell_texts, avg_font, page_font_median)

                # reject obvious bullet-list anchors: col0 is just bullets and col1 is a long paragraph
                first = (cell_texts[0] or "").strip()
                second = (cell_texts[1] or "").strip() if len(cell_texts) > 1 else ""
                if re.fullmatch(r"(\*{1,5}|[-•—])+\.?", first) and len(second) >= 60:
                    continue

                # Guard against "repeating labels" grids (e.g., VALID THRU/dates repeated across columns)
                short_repeats = sum(1 for t in cell_texts if 0 < len(t.strip()) <= 12)
                distinct = len({t.strip().lower() for t in cell_texts if t.strip()})
                repetitive = (distinct <= max(2, len(cell_texts)//4)) and (short_repeats >= len(cell_texts)//2)
                
                qualifies = ((populated_cols >= 3) or (populated_cols >= 2 and (is_header or has_numeric))) and not repetitive
                if not qualifies:
                    continue

                start_row_idx = r_idx
                anchor_bins = bins
                header_is_present = is_header
                header_cells = cell_texts[:] if is_header else []
                break

            if start_row_idx is None:
                return tables, issues, {"bins": None, "header": None, "schema": None}

            # Build schema
            if header_is_present and any(c.strip() for c in header_cells):
                schema = [
                    TableDetector._normalize_header_cell(raw, idx) or f"column_{idx+1}"
                    for idx, raw in enumerate(header_cells)
                ]
            else:
                schema = [f"column_{i+1}" for i in range(len(anchor_bins))]

            # Collect rows
            order_index = order_offset + 1
            table_rows: list[TableRowPayload] = []

            # Header row payload (if present)
            if header_is_present:
                h_texts, h_bboxes, h_counts, h_font = assign_to_bins(row_clusters[start_row_idx], anchor_bins)
                header_cells_payload: list[TableCellPayload] = []
                for c_idx, (raw, bbox) in enumerate(zip(h_texts, h_bboxes)):
                    header_cells_payload.append(
                        TableCellPayload(
                            row_index=0,
                            column_index=c_idx,
                            column_key=schema[c_idx] if c_idx < len(schema) else f"column_{c_idx+1}",
                            raw_text=raw,
                            normalized_value=self._normalize_cell_value(raw),
                            bbox=bbox,
                            confidence=None,
                            metadata={"span_count": h_counts[c_idx]},
                        )
                    )
                table_rows.append(
                    TableRowPayload(
                        row_index=0,
                        page_number=page_number,
                        bbox=_union_bbox(h_bboxes),
                        raw_text=" | ".join(h_texts),
                        metadata={"row_type": "header"},
                        cells=header_cells_payload,
                    )
                )

            # Data rows (including anchor row if it wasn't header)
            # Sparsity control: stop growing table when rows become too empty
            sparse_streak = 0
            SPARSE_ROW_MAX_EMPTY_RATIO = 0.7  # tweakable: 70% or more cells empty = sparse
            SPARSE_STREAK_LIMIT = 5           # tweakable: stop after 5 consecutive sparse rows
            
            next_row_idx = 1 if header_is_present else 0
            data_start = start_row_idx + (1 if header_is_present else 0)
            
            for r_idx in range(data_start, len(row_clusters)):
                texts, bboxes, counts, _ = assign_to_bins(row_clusters[r_idx], anchor_bins)
                
                # skip totally empty
                if not any(t.strip() for t in texts):
                    continue
                
                # Calculate sparsity: what fraction of cells are empty?
                empty_ratio = 1.0 - (sum(1 for t in texts if t.strip()) / max(1, len(texts)))
                
                if empty_ratio >= SPARSE_ROW_MAX_EMPTY_RATIO:
                    sparse_streak += 1
                    if sparse_streak >= SPARSE_STREAK_LIMIT:
                        # Stop table: we've entered a different layout/section
                        break
                else:
                    # Reset streak when we hit a non-sparse row
                    sparse_streak = 0
                
                # Build cell payloads for this row
                cells_payload: list[TableCellPayload] = []
                for c_idx, (raw, bbox) in enumerate(zip(texts, bboxes)):
                    cells_payload.append(
                        TableCellPayload(
                            row_index=next_row_idx,
                            column_index=c_idx,
                            column_key=schema[c_idx] if c_idx < len(schema) else f"column_{c_idx+1}",
                            raw_text=raw,
                            normalized_value=self._normalize_cell_value(raw),
                            bbox=bbox,
                            confidence=None,
                            metadata={"span_count": counts[c_idx]},
                        )
                    )
                
                table_rows.append(
                    TableRowPayload(
                        row_index=next_row_idx,
                        page_number=page_number,
                        bbox=_union_bbox(bboxes),
                        raw_text=" | ".join(texts),
                        metadata={"row_type": "data"},
                        cells=cells_payload,
                    )
                )
                next_row_idx += 1

            logical_merge_pairs = 0
            if table_rows:
                header_rows = [row for row in table_rows if str((row.metadata or {}).get("row_type") or "").strip().lower() == "header"]
                data_rows = [row for row in table_rows if str((row.metadata or {}).get("row_type") or "").strip().lower() != "header"]
                normalized_data_rows, logical_merge_pairs = self._normalize_geometry_logical_rows(
                    data_rows,
                    len(schema),
                )
                if header_rows:
                    header_row = header_rows[0]
                    normalized_header_cells = [
                        TableCellPayload(
                            row_index=0,
                            column_index=cell.column_index,
                            column_key=cell.column_key,
                            raw_text=cell.raw_text,
                            normalized_value=cell.normalized_value,
                            bbox=cell.bbox,
                            confidence=cell.confidence,
                            metadata=cell.metadata,
                        )
                        for cell in (header_row.cells or [])
                    ]
                    normalized_header = TableRowPayload(
                        row_index=0,
                        page_number=header_row.page_number,
                        bbox=header_row.bbox,
                        raw_text=header_row.raw_text,
                        metadata=header_row.metadata,
                        cells=normalized_header_cells,
                    )
                    table_rows = [normalized_header, *normalized_data_rows]
                else:
                    table_rows = normalized_data_rows

            table_payload = TablePayload(
                order_index=order_index,
                title=page_layout.section_heading if getattr(page_layout, "section_heading", "") else f"Table {order_index}",
                section_heading=getattr(page_layout, "section_heading", "") or "",
                page_number=page_number,
                bbox=_union_bbox([row.bbox for row in table_rows]) if table_rows else {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0},
                column_schema=schema,
                data_dictionary={},
                metadata={
                    "detected_via": "geometry",
                    "col_bins": anchor_bins,
                    "start_row_idx": start_row_idx,
                    "geometry_logical_row_merged_pairs": logical_merge_pairs,
                },
                rows=table_rows,
            )
            tables.append(table_payload)

            meta = {"bins": anchor_bins, "header": schema if header_is_present else None, "schema": schema}
            return tables, issues, meta


    # ---- Header propagation across pages ----
    @staticmethod
    def _bins_compatible(prev_bins: list[tuple[float, float]] | None, next_bins: list[tuple[float, float]] | None, tol: float = 8.0) -> bool:
        if not prev_bins or not next_bins or len(prev_bins) != len(next_bins):
            return False
        for (a0, a1), (b0, b1) in zip(prev_bins, next_bins):
            if max(abs(a0 - b0), abs(a1 - b1)) > tol:
                return False
        return True

    def reconstruct(self, page_spans: list[list[PdfSpan]], pages: list[PageLayout]) -> tuple[list[TablePayload], list[IssuePayload]]:
        all_tables: list[TablePayload] = []
        all_issues: list[IssuePayload] = []
        prev_bins: list[tuple[float, float]] | None = None
        prev_order_index = 0
        prev_schema: list[str] | None = None

        for p_idx, (spans, layout) in enumerate(zip(page_spans, pages), start=1):
            width = float(getattr(layout, "width", 612.0) or 612.0)
            page_tables, page_issues, meta = self._build_page_tables(
                page_number=p_idx,
                page_width=width,
                page_spans=spans,
                page_layout=layout,
                order_offset=len(all_tables),
            )
            # Header propagation: if no explicit header and bins align with previous
            if not page_tables and prev_bins and spans:
                # None produced — try to create a propagated header notice if bins align
                # (No-op; we only signal if a table exists.)
                pass
            elif page_tables:
                # Compare first table bins to previous
                bins = meta.get("bins")
                schema = page_tables[0].column_schema
                if self._bins_compatible(prev_bins, bins) and schema == prev_schema:
                    # Mark propagated
                    page_tables[0].metadata["header_propagated"] = True
                    page_tables[0].metadata["continuation_of"] = prev_order_index
                    all_issues.append(
                        IssuePayload(
                            code="header_propagated",
                            severity=KnowledgeIssueSeverity.INFO.value,
                            description="Header propagated across page break.",
                            page_number=p_idx,
                            table_order_index=page_tables[0].order_index,
                            details={}
                        )
                    )
                prev_bins = bins
                prev_order_index = page_tables[0].order_index
                prev_schema = schema

            all_tables.extend(page_tables)
            all_issues.extend(page_issues)
        return all_tables, all_issues

    # Reuse existing normalization for consistency
    _normalize_cell_value = staticmethod(TableDetector._normalize_cell_value)
