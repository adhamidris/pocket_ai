from __future__ import annotations

import re
from typing import Any, Sequence

from apps.knowledge.ingestion.contracts import TableCellPayload, TablePayload, TableRowPayload
from apps.knowledge.tables.detection import TableDetector


class IngestionPdfSparseMatrixMixin:

    @staticmethod
    def _merge_sparse_segment_text(parts: Sequence[str]) -> str:
        text = " ".join(part.strip() for part in parts if str(part or "").strip())
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return ""
        text = re.sub(r"\b([A-Z]{1,3})\s+([A-Z])\s+(\d)", r"\1\2 \3", text)
        text = re.sub(r"\b(\d{1,3})\s+(\d)\b", r"\1\2", text)
        text = re.sub(r"\b([A-Z]{1,2})\s+([A-Z]{2,4})\b", r"\1\2", text)
        text = re.sub(r"\b([A-Za-z]{1,3})\s+([a-z]{2,})\b", r"\1\2", text)
        text = re.sub(r"\b([A-Za-z]{2,4})\s+([a-z]{2,})\b", r"\1\2", text)
        text = re.sub(r"\b([A-Za-z]{2,})\s+([A-Za-z])\s+([A-Za-z])\s+([A-Za-z])\b", r"\1\2\3\4", text)
        text = re.sub(r"\b([A-Za-z]{2,})\s+([A-Za-z])\s+([A-Za-z])\b", r"\1\2\3", text)
        text = re.sub(r"\b([A-Za-z]-[A-Za-z]?)\s+([A-Za-z]{2,})\b", r"\1\2", text)
        text = re.sub(r"([(/-])\s+", r"\1", text)
        text = re.sub(r"\s+([)%/.,:;])", r"\1", text)
        return text.strip()

    @staticmethod
    def _sparse_cells_should_split(previous_text: str, current_text: str) -> bool:
        prev = re.sub(r"\s+", " ", str(previous_text or "").strip())
        curr = re.sub(r"\s+", " ", str(current_text or "").strip())
        if not prev or not curr:
            return False
        if curr[:1].islower():
            return False
        if prev.endswith(("-", "/", "(")):
            return False
        if len(prev) <= 2 and prev.isupper() and re.match(r"^[A-Z]{1,3}\b", curr):
            return False
        if re.fullmatch(r"\d{1,3}", curr) and re.search(r"(?:[A-Z]{1,4}\s*\d{1,3}|\d)$", prev):
            return False
        prev_numeric = bool(re.search(r"\d|%|\b(?:egp|usd|eur|gbp|sar|aed)\b", prev, flags=re.IGNORECASE))
        curr_numeric = bool(re.search(r"\d|%|\b(?:egp|usd|eur|gbp|sar|aed)\b", curr, flags=re.IGNORECASE))
        if prev_numeric and curr_numeric:
            return True
        prev_short = len(prev) <= 16 and len(prev.split()) <= 3
        curr_short = len(curr) <= 16 and len(curr.split()) <= 3
        prev_titleish = prev[:1].isupper()
        curr_titleish = curr[:1].isupper()
        return prev_short and curr_short and prev_titleish and curr_titleish

    def _sparse_row_segments(self, row: TableRowPayload) -> list[str]:
        cells = sorted(list(row.cells or []), key=lambda cell: cell.column_index)
        if not cells:
            return []
        segments: list[str] = []
        current_parts: list[str] = []
        prev_col: int | None = None
        for cell in cells:
            raw = self._sanitize_text(cell.raw_text).strip()
            if not raw:
                if current_parts and prev_col is not None and cell.column_index > (prev_col + 1):
                    merged = self._merge_sparse_segment_text(current_parts)
                    if merged:
                        segments.append(merged)
                    current_parts = []
                    prev_col = None
                continue
            if current_parts and prev_col is not None and cell.column_index > (prev_col + 1):
                merged = self._merge_sparse_segment_text(current_parts)
                if merged:
                    segments.append(merged)
                current_parts = []
            cleaned = raw.replace("\n", " ")
            if current_parts and prev_col is not None and cell.column_index == (prev_col + 1):
                previous_text = self._merge_sparse_segment_text(current_parts)
                if self._sparse_cells_should_split(previous_text, cleaned):
                    merged = self._merge_sparse_segment_text(current_parts)
                    if merged:
                        segments.append(merged)
                    current_parts = []
            current_parts.append(cleaned)
            prev_col = cell.column_index
        if current_parts:
            merged = self._merge_sparse_segment_text(current_parts)
            if merged:
                segments.append(merged)
        return [segment for segment in segments if segment]

    @staticmethod
    def _row_looks_like_sparse_matrix_header(segments: Sequence[str]) -> bool:
        if len(segments) < 3:
            return False
        first = str(segments[0] or "").strip().lower()
        if "card type" in first or "epp program" in first:
            return True
        if "type" not in first and "program" not in first:
            return False
        non_empty = [seg for seg in segments if str(seg or "").strip()]
        tail = non_empty[1:]
        tail_numeric_ratio = (
            sum(1 for seg in tail if re.search(r"\d", seg)) / float(len(tail) or 1)
        )
        return tail_numeric_ratio <= 0.35 and len(first.split()) <= 4

    def _build_sparse_matrix_subtables(self, table: TablePayload) -> list[dict[str, Any]]:
        sparse_rows: list[dict[str, Any]] = []
        for row in table.rows or []:
            segments = self._sparse_row_segments(row)
            if len(segments) >= 2:
                sparse_rows.append(
                    {
                        "row": row,
                        "segments": segments,
                        "is_header": self._row_looks_like_sparse_matrix_header(segments),
                    }
                )
        subtables: list[dict[str, Any]] = []
        current: list[dict[str, Any]] = []
        for entry in sparse_rows:
            if entry["is_header"]:
                if current:
                    subtables.append({"rows": current})
                current = [entry]
                continue
            if current:
                current.append(entry)
        if current:
            subtables.append({"rows": current})
        return [
            subtable
            for subtable in subtables
            if len(subtable.get("rows") or []) >= 3
            and len((subtable.get("rows") or [])[0].get("segments") or []) >= 4
        ]

    def _align_sparse_segments_to_schema(
        self,
        segments: Sequence[str],
        *,
        column_count: int,
    ) -> list[str]:
        values = [self._sanitize_text(value).strip() for value in segments if self._sanitize_text(value).strip()]
        if not values:
            return [""] * column_count
        if len(values) == column_count:
            return list(values)
        if column_count >= 3 and len(values) == 2:
            return [values[0]] + [values[1]] * (column_count - 1)
        if len(values) < column_count:
            return list(values) + [""] * (column_count - len(values))
        leading = len(values) - (column_count - 1)
        descriptor = self._merge_sparse_segment_text(values[:leading])
        return [descriptor] + list(values[leading:])

    def _table_payload_from_sparse_matrix(
        self,
        *,
        source_table: TablePayload,
        subtable_rows: Sequence[dict[str, Any]],
        target_table: TablePayload | None = None,
        source_name: str,
    ) -> TablePayload | None:
        if not subtable_rows:
            return None
        header_segments = list((subtable_rows[0] or {}).get("segments") or [])
        if len(header_segments) < 4:
            return None
        column_schema = [
            TableDetector._normalize_header_cell(segment, idx)
            for idx, segment in enumerate(header_segments)
        ]
        page_number = target_table.page_number if target_table is not None else source_table.page_number
        order_index = target_table.order_index if target_table is not None else source_table.order_index
        title = target_table.title if target_table is not None else source_table.title
        section_heading = target_table.section_heading if target_table is not None else source_table.section_heading
        metadata = dict(source_table.metadata or {})
        metadata["detected_via"] = f"reconstructed:{source_name}"
        metadata["structure_reconstructed"] = True
        metadata["structure_reconstruction_source"] = source_name
        metadata["structure_reconstruction_strategy"] = "sparse_matrix_segments"
        if target_table is not None:
            metadata["structure_reconstructed_from_order_index"] = target_table.order_index

        rows: list[TableRowPayload] = []
        header_cells = [
            TableCellPayload(
                row_index=0,
                column_index=idx,
                column_key=column_schema[idx],
                raw_text=segment,
                normalized_value=TableDetector._normalize_cell_value(segment),
                bbox={},
                confidence=None,
            )
            for idx, segment in enumerate(header_segments)
        ]
        rows.append(
            TableRowPayload(
                row_index=0,
                page_number=page_number,
                bbox={},
                raw_text=" | ".join(header_segments),
                metadata={"row_type": "header"},
                cells=header_cells,
            )
        )

        for row_index, entry in enumerate(subtable_rows[1:], start=1):
            aligned = self._align_sparse_segments_to_schema(
                entry.get("segments") or [],
                column_count=len(column_schema),
            )
            if len(aligned) != len(column_schema):
                continue
            cells = [
                TableCellPayload(
                    row_index=row_index,
                    column_index=idx,
                    column_key=column_schema[idx],
                    raw_text=value,
                    normalized_value=TableDetector._normalize_cell_value(value),
                    bbox={},
                    confidence=None,
                )
                for idx, value in enumerate(aligned)
            ]
            rows.append(
                TableRowPayload(
                    row_index=row_index,
                    page_number=page_number,
                    bbox={},
                    raw_text=" | ".join(aligned),
                    metadata={"row_type": "data"},
                    cells=cells,
                )
            )
        if len(rows) < 3:
            return None
        return TablePayload(
            order_index=order_index,
            title=title,
            section_heading=section_heading,
            page_number=page_number,
            bbox=target_table.bbox if target_table is not None else source_table.bbox,
            column_schema=column_schema,
            data_dictionary={},
            metadata=metadata,
            rows=rows,
        )
