from __future__ import annotations

import re
from typing import Any, Iterable, Optional, Sequence

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.ingestion.contracts import (
    IssuePayload,
    PageLayout,
    PdfSpan,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
    _union_bbox,
)
from apps.knowledge.tables.detection import TableDetector
from apps.knowledge.tables.geometry_tools.rows import GeometryTableRowsMixin


# === [ADD] GeometryTableReconstructor: x/y clustering → grid → TablePayloads ===
class GeometryTableReconstructor(GeometryTableRowsMixin):
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
