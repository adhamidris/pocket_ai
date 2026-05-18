from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

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
from apps.knowledge.ingestion_geometry_tables import GeometryTableReconstructor
from apps.knowledge.ingestion_table_detection import TableDetector


class IngestionPdfTableReconstructionMixin:


    @staticmethod
    def _collapsed_matrix_text_signal_count(value: str) -> int:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text:
            return 0
        count = len(re.findall(r"\b\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?\b", text))
        count += len(re.findall(r"\b(?:egp|usd|eur|gbp|sar|aed)\b", text, flags=re.IGNORECASE))
        count += len(re.findall(r"%", text))
        return count

    def _diagnose_pdf_table_structure(
        self,
        table: TablePayload,
        *,
        selection_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        readable_rows = self._pdf_table_readable_rows(table)
        row_source = readable_rows or list(table.rows or [])
        column_count = self._table_column_count(table)
        header_row = next(
            (row for row in (table.rows or []) if (row.metadata or {}).get("row_type") == "header"),
            row_source[0] if row_source else None,
        )
        header_cells = list(header_row.cells or []) if header_row else []
        header_texts = [self._sanitize_text(cell.raw_text).strip() for cell in header_cells if str(cell.raw_text or "").strip()]
        header_joined = " ".join(header_texts).strip()
        merged_header = False
        if column_count <= 1:
            if len(header_texts) == 1 and len(header_joined.split()) >= 4:
                merged_header = True
            elif not header_texts and header_row and len(self._sanitize_text(header_row.raw_text).split()) >= 4:
                merged_header = True

        packed_value_rows = 0
        data_row_count = 0
        for row in row_source:
            cells = [cell for cell in (row.cells or []) if str(cell.raw_text or "").strip()]
            if not cells:
                continue
            if (row.metadata or {}).get("row_type") == "header":
                continue
            data_row_count += 1
            if len(cells) == 1 and self._collapsed_matrix_text_signal_count(cells[0].raw_text) >= 2:
                packed_value_rows += 1
        packed_value_ratio = float(packed_value_rows) / float(data_row_count or 1)

        lane = str((selection_context or {}).get("pdf_lane") or "")
        kind = "other"
        recoverable = False
        if lane == "native_text" and column_count <= 1 and data_row_count >= 3 and merged_header and packed_value_ratio >= 0.35:
            kind = "collapsed_matrix"
            recoverable = True

        return {
            "kind": kind,
            "recoverable": recoverable,
            "column_count": column_count,
            "data_row_count": data_row_count,
            "packed_value_ratio": round(packed_value_ratio, 4),
            "merged_header": merged_header,
            "header_text": header_joined or self._sanitize_text(getattr(header_row, "raw_text", "")).strip(),
        }

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

    def _reconstruct_collapsed_native_text_tables(
        self,
        *,
        tables: Sequence[TablePayload],
        candidates: Mapping[str, list[TablePayload]],
        page_spans: Sequence[Sequence[PdfSpan]] | None = None,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not self.pdf_collapsed_matrix_reconstruction_enabled:
            return list(tables), {"enabled": False}, []
        if str((selection_context or {}).get("pdf_lane") or "") != "native_text":
            return list(tables), {"enabled": True, "skipped_reason": "non_native_text_lane"}, []

        collapsed_targets = [
            table
            for table in tables
            if (self._diagnose_pdf_table_structure(table, selection_context=selection_context).get("kind") == "collapsed_matrix")
        ]
        if not collapsed_targets:
            return list(tables), {"enabled": True, "collapsed_targets": 0}, []

        span_reconstructed = self._reconstruct_collapsed_native_text_tables_from_spans(
            collapsed_targets=collapsed_targets,
            page_spans=page_spans or [],
        )
        if not span_reconstructed:
            return list(tables), {
                "enabled": True,
                "collapsed_targets": len(collapsed_targets),
                "reconstructed_tables": 0,
                "skipped_reason": "no_span_reconstruction_candidates",
            }, []

        issues: list[IssuePayload] = []
        replacements: dict[tuple[int, int | None], TablePayload] = {}
        for target_key, rebuilt in span_reconstructed.items():
            replacements[target_key] = rebuilt
            issues.append(
                IssuePayload(
                    code="pdf_table_structure_reconstructed",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Reconstructed collapsed native-text table from pymupdf:spans before canonical persistence.",
                    page_number=target_key[1],
                    table_order_index=target_key[0],
                    details={
                        "source_name": "pymupdf:spans",
                        "column_count": len(rebuilt.column_schema),
                        "row_count": len(rebuilt.rows),
                    },
                )
            )

        updated_tables: list[TablePayload] = []
        reconstructed_count = 0
        for table in tables:
            key = (table.order_index, table.page_number)
            replacement = replacements.get(key)
            if replacement is not None:
                updated_tables.append(replacement)
                reconstructed_count += 1
            else:
                updated_tables.append(table)
        return updated_tables, {
            "enabled": True,
            "collapsed_targets": len(collapsed_targets),
            "span_reconstruction_candidates": len(span_reconstructed),
            "reconstructed_tables": reconstructed_count,
        }, issues

    @staticmethod
    def _cluster_pdf_spans_by_row(spans: Sequence[PdfSpan], *, y_tol: float = 6.5) -> list[list[PdfSpan]]:
        if not spans:
            return []
        sorted_spans = sorted(spans, key=lambda span: (span.y_center, span.x0))
        rows: list[list[PdfSpan]] = [[sorted_spans[0]]]
        anchor = sorted_spans[0].y_center
        for span in sorted_spans[1:]:
            if abs(span.y_center - anchor) <= y_tol:
                rows[-1].append(span)
                row_members = rows[-1]
                anchor = sum(member.y_center for member in row_members) / float(len(row_members))
            else:
                rows.append([span])
                anchor = span.y_center
        return [sorted(row, key=lambda span: span.x0) for row in rows]

    @staticmethod
    def _span_text_signal_count(text: str) -> int:
        sample = str(text or "").strip()
        if not sample:
            return 0
        score = 0
        if re.search(r"\d", sample):
            score += 1
        if re.search(r"%|\b(?:egp|usd|eur|gbp|aed|sar)\b", sample, flags=re.IGNORECASE):
            score += 1
        lowered = sample.lower()
        if lowered in {"free", "n/a", "na"} or "free" in lowered:
            score += 1
        return score

    @staticmethod
    def _row_gap(row_a: Sequence[PdfSpan], row_b: Sequence[PdfSpan]) -> float:
        if not row_a or not row_b:
            return 0.0
        return float(min(span.y0 for span in row_b) - max(span.y1 for span in row_a))

    def _span_row_looks_like_data(
        self,
        row: Sequence[PdfSpan],
        *,
        descriptor_limit: float,
    ) -> bool:
        if not row:
            return False
        value_like = [
            span for span in row
            if span.x0 > descriptor_limit and self._span_text_signal_count(span.text) >= 1
        ]
        if len(value_like) >= 2:
            return True
        descriptor_present = any(span.x0 <= descriptor_limit for span in row)
        if descriptor_present and len(value_like) == 1:
            shared = value_like[0]
            shared_text = str(shared.text or "").strip().lower()
            if "across all" in shared_text or "all card types" in shared_text:
                return True
            if (shared.x1 - shared.x0) >= 72.0:
                return True
        return False

    def _span_row_is_header_support(
        self,
        row: Sequence[PdfSpan],
        *,
        descriptor_limit: float,
    ) -> bool:
        if not row:
            return False
        if any(self._span_text_signal_count(span.text) >= 1 for span in row if span.x0 > descriptor_limit):
            return False
        if row[0].x0 <= descriptor_limit and "card type" not in row[0].text.lower():
            return False
        return any(span.x0 > descriptor_limit for span in row)

    @staticmethod
    def _cluster_x_centers(values: Sequence[tuple[float, float, float]], *, x_tol: float = 18.0) -> list[list[tuple[float, float, float]]]:
        if not values:
            return []
        sorted_values = sorted(values, key=lambda item: item[0])
        clusters: list[list[tuple[float, float, float]]] = [[sorted_values[0]]]
        anchor = sorted_values[0][0]
        for value in sorted_values[1:]:
            if abs(value[0] - anchor) <= x_tol:
                clusters[-1].append(value)
                anchor = sum(item[0] for item in clusters[-1]) / float(len(clusters[-1]))
            else:
                clusters.append([value])
                anchor = value[0]
        return clusters

    def _build_span_matrix_subtables(
        self,
        spans: Sequence[PdfSpan],
        *,
        page_number: int,
    ) -> list[dict[str, Any]]:
        row_clusters = self._cluster_pdf_spans_by_row([span for span in spans if span.page_number == page_number])
        if not row_clusters:
            return []

        row_infos: list[dict[str, Any]] = []
        for row in row_clusters:
            texts = [str(span.text or "").strip() for span in row if str(span.text or "").strip()]
            if not texts:
                continue
            row_infos.append(
                {
                    "spans": row,
                    "texts": texts,
                    "bbox": _union_bbox(
                        [{"x0": span.x0, "y0": span.y0, "x1": span.x1, "y1": span.y1} for span in row]
                    ),
                }
            )

        subtables: list[dict[str, Any]] = []
        idx = 0
        while idx < len(row_infos):
            current = row_infos[idx]
            first_text = str((current.get("texts") or [""])[0]).strip().lower()
            if "card type" not in first_text:
                idx += 1
                continue

            anchor_row = current.get("spans") or []
            if not anchor_row:
                idx += 1
                continue
            descriptor_limit = float(anchor_row[0].x1 + 24.0)

            data_start = None
            for probe in range(idx + 1, len(row_infos)):
                probe_row = row_infos[probe].get("spans") or []
                if not probe_row:
                    continue
                if self._row_gap(anchor_row, probe_row) > 22.0:
                    break
                if self._span_row_looks_like_data(probe_row, descriptor_limit=descriptor_limit):
                    data_start = probe
                    break
            if data_start is None:
                idx += 1
                continue

            header_start = idx
            while header_start > 0:
                previous = row_infos[header_start - 1].get("spans") or []
                if not previous:
                    break
                if self._row_gap(previous, row_infos[header_start].get("spans") or []) > 18.0:
                    break
                if not self._span_row_is_header_support(previous, descriptor_limit=descriptor_limit):
                    break
                header_start -= 1

            sample_rows = [info.get("spans") or [] for info in row_infos[header_start : min(len(row_infos), data_start + 2)]]
            x_samples: list[tuple[float, float, float]] = []
            for sample_row in sample_rows:
                for span in sample_row:
                    if span.x0 <= descriptor_limit:
                        continue
                    x_samples.append((span.x_center, span.x0, span.x1))
            column_clusters = self._cluster_x_centers(x_samples)
            if len(column_clusters) < 3:
                idx = data_start
                continue

            column_bands: list[dict[str, float]] = []
            for cluster in column_clusters:
                centers = [item[0] for item in cluster]
                x0s = [item[1] for item in cluster]
                x1s = [item[2] for item in cluster]
                column_bands.append(
                    {
                        "center": float(sum(centers) / float(len(centers))),
                        "x0": float(min(x0s)),
                        "x1": float(max(x1s)),
                    }
                )
            column_bands.sort(key=lambda band: band["center"])

            data_end = data_start
            while data_end < len(row_infos):
                row_spans = row_infos[data_end].get("spans") or []
                if not row_spans:
                    break
                if data_end > data_start and "card type" in str((row_infos[data_end].get("texts") or [""])[0]).lower():
                    break
                overlaps_band = any(
                    span.x1 >= (column_bands[0]["x0"] - 8.0) and span.x0 <= (column_bands[-1]["x1"] + 8.0)
                    for span in row_spans
                )
                descriptor_present = any(span.x0 <= descriptor_limit for span in row_spans)
                if data_end > data_start and not overlaps_band and not descriptor_present:
                    break
                if data_end > data_start and self._row_gap(row_infos[data_end - 1].get("spans") or [], row_spans) > 20.0:
                    break
                data_end += 1

            header_rows = [info.get("spans") or [] for info in row_infos[header_start:data_start]]
            data_rows = [info.get("spans") or [] for info in row_infos[data_start:data_end]]
            if header_rows and data_rows:
                subtables.append(
                    {
                        "page_number": page_number,
                        "header_rows": header_rows,
                        "data_rows": data_rows,
                        "column_bands": column_bands,
                        "descriptor_limit": descriptor_limit,
                        "bbox": _union_bbox([info.get("bbox") or {} for info in row_infos[header_start:data_end]]),
                        "top": float((row_infos[header_start].get("bbox") or {}).get("y0") or 0.0),
                    }
                )
            idx = max(data_end, idx + 1)
        return subtables

    def _assign_span_row_to_bands(
        self,
        row_spans: Sequence[PdfSpan],
        *,
        descriptor_limit: float,
        column_bands: Sequence[Mapping[str, float]],
    ) -> tuple[list[str], list[dict[str, float]]]:
        descriptor_members: list[PdfSpan] = []
        value_members: list[list[PdfSpan]] = [[] for _ in column_bands]
        if not row_spans:
            return [""] * (len(column_bands) + 1), [{"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}] * (len(column_bands) + 1)

        for span in sorted(row_spans, key=lambda item: (item.y0, item.x0)):
            first_band_left = float(column_bands[0].get("x0") or 0.0) if column_bands else 0.0
            if span.x1 <= descriptor_limit or (first_band_left and span.x0 < (first_band_left - 12.0)):
                descriptor_members.append(span)
                continue
            overlaps: list[int] = []
            for idx, band in enumerate(column_bands):
                if span.x1 >= (float(band.get("x0") or 0.0) - 6.0) and span.x0 <= (float(band.get("x1") or 0.0) + 6.0):
                    overlaps.append(idx)
            if not overlaps:
                nearest = min(
                    range(len(column_bands)),
                    key=lambda idx: abs(span.x_center - float(column_bands[idx].get("center") or 0.0)),
                )
                overlaps = [nearest]
            for idx in overlaps:
                value_members[idx].append(span)

        cells: list[str] = []
        bboxes: list[dict[str, float]] = []
        all_members = [descriptor_members] + value_members
        for members in all_members:
            if members:
                members_sorted = sorted(members, key=lambda item: (item.y0, item.x0))
                text = self._merge_span_texts([member.text for member in members_sorted])
                bbox = _union_bbox(
                    [{"x0": member.x0, "y0": member.y0, "x1": member.x1, "y1": member.y1} for member in members_sorted]
                )
            else:
                text = ""
                bbox = {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}
            cells.append(text)
            bboxes.append(bbox)

        if len(column_bands) >= 3:
            descriptor_bbox = bboxes[0] if bboxes else {"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0}
            descriptor_overlaps_values = bool(
                column_bands
                and float((descriptor_bbox or {}).get("x1") or 0.0) >= (float(column_bands[0].get("x0") or 0.0) - 6.0)
            )
            if cells[0] and not any(re.sub(r"\s+", " ", value).strip() for value in cells[1:]) and descriptor_overlaps_values:
                split = self._split_descriptor_from_shared_value(cells[0])
                if split is not None:
                    descriptor_text, shared_value = split
                    cells[0] = descriptor_text
                    for idx in range(1, len(cells)):
                        cells[idx] = shared_value
                        bboxes[idx] = descriptor_bbox
            populated_values = [idx for idx, text in enumerate(cells[1:], start=1) if text]
            if len(populated_values) == 1:
                shared_text = cells[populated_values[0]]
                shared_bbox = bboxes[populated_values[0]]
                for idx in range(1, len(cells)):
                    cells[idx] = shared_text
                    bboxes[idx] = shared_bbox
            elif len(populated_values) >= 2:
                normalized_values = {
                    re.sub(r"\s+", " ", cells[idx]).strip().lower()
                    for idx in populated_values
                    if re.sub(r"\s+", " ", cells[idx]).strip()
                }
                if len(normalized_values) == 1:
                    shared_text = cells[populated_values[0]]
                    shared_bbox = bboxes[populated_values[0]]
                    for idx in range(1, len(cells)):
                        if not re.sub(r"\s+", " ", cells[idx]).strip():
                            cells[idx] = shared_text
                            bboxes[idx] = shared_bbox

        return cells, bboxes

    def _build_table_payload_from_span_matrix(
        self,
        *,
        target_table: TablePayload,
        subtable: Mapping[str, Any],
    ) -> TablePayload | None:
        header_rows = list(subtable.get("header_rows") or [])
        data_rows = list(subtable.get("data_rows") or [])
        column_bands = list(subtable.get("column_bands") or [])
        descriptor_limit = float(subtable.get("descriptor_limit") or 0.0)
        if not header_rows or not data_rows or len(column_bands) < 3:
            return None

        header_cells = ["Card Type"] + [""] * len(column_bands)
        header_bboxes = [{"x0": 0.0, "y0": 0.0, "x1": 0.0, "y1": 0.0} for _ in range(len(column_bands) + 1)]
        for row in header_rows:
            assigned, bboxes = self._assign_span_row_to_bands(
                row,
                descriptor_limit=descriptor_limit,
                column_bands=column_bands,
            )
            for idx, value in enumerate(assigned):
                cleaned = self._sanitize_text(value).strip()
                if not cleaned:
                    continue
                if idx == 0 and header_cells[idx].strip().lower() == cleaned.lower():
                    continue
                if header_cells[idx]:
                    header_cells[idx] = self._merge_span_texts([header_cells[idx], cleaned])
                    header_bboxes[idx] = _union_bbox([header_bboxes[idx], bboxes[idx]])
                else:
                    header_cells[idx] = cleaned
                    header_bboxes[idx] = bboxes[idx]

        if not header_cells[0]:
            header_cells[0] = "Card Type"
        column_schema = [
            TableDetector._normalize_header_cell(value, idx)
            for idx, value in enumerate(header_cells)
        ]

        rows: list[TableRowPayload] = []
        header_payload_cells = [
            TableCellPayload(
                row_index=0,
                column_index=idx,
                column_key=column_schema[idx],
                raw_text=header_cells[idx],
                normalized_value=TableDetector._normalize_cell_value(header_cells[idx]),
                bbox=header_bboxes[idx],
                confidence=None,
            )
            for idx in range(len(column_schema))
        ]
        rows.append(
            TableRowPayload(
                row_index=0,
                page_number=target_table.page_number,
                bbox=_union_bbox([bbox for bbox in header_bboxes if any(bbox.values())]),
                raw_text=" | ".join(header_cells),
                metadata={"row_type": "header"},
                cells=header_payload_cells,
            )
        )

        data_payload_rows: list[TableRowPayload] = []
        next_row_idx = 1
        for span_row in data_rows:
            assigned, bboxes = self._assign_span_row_to_bands(
                span_row,
                descriptor_limit=descriptor_limit,
                column_bands=column_bands,
            )
            if len(assigned) != len(column_schema):
                continue
            descriptor = self._sanitize_text(assigned[0]).strip()
            value_count = sum(1 for value in assigned[1:] if self._sanitize_text(value).strip())
            if not descriptor and value_count < 2:
                continue
            payload_cells = [
                TableCellPayload(
                    row_index=next_row_idx,
                    column_index=idx,
                    column_key=column_schema[idx],
                    raw_text=assigned[idx],
                    normalized_value=TableDetector._normalize_cell_value(assigned[idx]),
                    bbox=bboxes[idx],
                    confidence=None,
                )
                for idx in range(len(column_schema))
            ]
            data_payload_rows.append(
                TableRowPayload(
                    row_index=next_row_idx,
                    page_number=target_table.page_number,
                    bbox=_union_bbox([bbox for bbox in bboxes if any(bbox.values())]),
                    raw_text=" | ".join(assigned),
                    metadata={"row_type": "data"},
                    cells=payload_cells,
                )
            )
            next_row_idx += 1

        reconstructor = GeometryTableReconstructor()
        normalized_data_rows, _merged_pairs = reconstructor._normalize_geometry_logical_rows(
            data_payload_rows,
            len(column_schema),
        )
        rows.extend(normalized_data_rows)

        if len(rows) < 3:
            return None

        metadata = dict(target_table.metadata or {})
        metadata["detected_via"] = "reconstructed:pymupdf:spans"
        metadata["structure_reconstructed"] = True
        metadata["structure_reconstruction_source"] = "pymupdf:spans"
        metadata["structure_reconstruction_strategy"] = "span_matrix_bands"
        return TablePayload(
            order_index=target_table.order_index,
            title=target_table.title,
            section_heading=target_table.section_heading,
            page_number=target_table.page_number,
            bbox=dict(subtable.get("bbox") or target_table.bbox or {}),
            column_schema=column_schema,
            data_dictionary={},
            metadata=metadata,
            rows=rows,
        )

    @staticmethod
    def _merge_span_texts(parts: Sequence[str]) -> str:
        merged: list[str] = []
        for part in parts:
            cleaned = re.sub(r"\s+", " ", str(part or "").strip())
            if not cleaned:
                continue
            if merged and merged[-1] == cleaned:
                continue
            merged.append(cleaned)
        return " ".join(merged).strip()

    @staticmethod
    def _split_descriptor_from_shared_value(text: str) -> tuple[str, str] | None:
        candidate = re.sub(r"\s+", " ", str(text or "").strip())
        if not candidate:
            return None
        tokens = candidate.split()
        if len(tokens) < 3:
            return None
        split_idx: int | None = None
        for idx, token in enumerate(tokens[1:], start=1):
            normalized = token.strip().lower()
            if re.search(r"\d", normalized) or "%" in normalized or normalized in {"egp", "usd", "eur", "aed", "sar", "gbp"}:
                split_idx = idx
                break
        if split_idx is None or split_idx >= len(tokens):
            return None
        descriptor = " ".join(tokens[:split_idx]).strip()
        shared_value = " ".join(tokens[split_idx:]).strip()
        if not descriptor or not shared_value:
            return None
        return descriptor, shared_value

    @staticmethod
    def _header_similarity_score(target: str, candidate: str) -> float:
        target_tokens = {token for token in re.findall(r"[a-z0-9]+", str(target or "").lower()) if len(token) >= 2}
        candidate_tokens = {token for token in re.findall(r"[a-z0-9]+", str(candidate or "").lower()) if len(token) >= 2}
        if not target_tokens or not candidate_tokens:
            return 0.0
        return float(len(target_tokens & candidate_tokens)) / float(len(target_tokens | candidate_tokens))

    def _reconstruct_collapsed_native_text_tables_from_spans(
        self,
        *,
        collapsed_targets: Sequence[TablePayload],
        page_spans: Sequence[Sequence[PdfSpan]],
    ) -> dict[tuple[int, int | None], TablePayload]:
        if not page_spans:
            return {}

        candidates_by_page: dict[int, list[dict[str, Any]]] = {}
        for target in collapsed_targets:
            page_number = int(target.page_number or 0)
            if page_number <= 0 or page_number > len(page_spans):
                continue
            if page_number not in candidates_by_page:
                candidates_by_page[page_number] = self._build_span_matrix_subtables(
                    page_spans[page_number - 1],
                    page_number=page_number,
                )

        replacements: dict[tuple[int, int | None], TablePayload] = {}
        used_indices: dict[int, set[int]] = {}
        for target in collapsed_targets:
            page_number = int(target.page_number or 0)
            page_candidates = candidates_by_page.get(page_number) or []
            if not page_candidates:
                continue
            target_header = str(
                (self._diagnose_pdf_table_structure(target, selection_context={"pdf_lane": "native_text"}) or {}).get("header_text") or ""
            )
            best_idx = None
            best_score = 0.0
            used = used_indices.setdefault(page_number, set())
            for idx, candidate in enumerate(page_candidates):
                if idx in used:
                    continue
                header_rows = candidate.get("header_rows") or []
                header_text = " ".join(
                    self._sanitize_text(span.text)
                    for row in header_rows
                    for span in row
                )
                score = self._header_similarity_score(target_header, header_text)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx is None:
                continue
            rebuilt = self._build_table_payload_from_span_matrix(
                target_table=target,
                subtable=page_candidates[best_idx],
            )
            if rebuilt is None:
                continue
            used.add(best_idx)
            replacements[(target.order_index, target.page_number)] = rebuilt
        return replacements

    def _enforce_pdf_canonical_acceptance(
        self,
        tables: Sequence[TablePayload],
        *,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not self.pdf_structural_acceptance_enabled:
            return list(tables), {"enabled": False}, []
        kept: list[TablePayload] = []
        issues: list[IssuePayload] = []
        dropped_tables: list[dict[str, Any]] = []
        for table in tables:
            diagnosis = self._diagnose_pdf_table_structure(table, selection_context=selection_context)
            metadata = dict(table.metadata or {})
            metadata["structure_diagnosis"] = diagnosis
            unsafe = diagnosis.get("kind") == "collapsed_matrix" and not metadata.get("structure_reconstructed")
            metadata["canonical_acceptance"] = "fallback_text_only" if unsafe else "accepted"
            updated = TablePayload(
                order_index=table.order_index,
                title=table.title,
                section_heading=table.section_heading,
                page_number=table.page_number,
                bbox=table.bbox,
                column_schema=table.column_schema,
                data_dictionary=table.data_dictionary,
                metadata=metadata,
                rows=table.rows,
            )
            if unsafe:
                dropped_tables.append(
                    {
                        "order_index": table.order_index,
                        "page_number": table.page_number,
                        "diagnosis": diagnosis,
                    }
                )
                issues.append(
                    IssuePayload(
                        code="pdf_table_not_canonical",
                        severity=KnowledgeIssueSeverity.INFO.value,
                        description="Dropped structurally unsafe PDF table from canonical persistence; text fallback remains available.",
                        page_number=table.page_number,
                        table_order_index=table.order_index,
                        details={"diagnosis": diagnosis},
                    )
                )
                continue
            kept.append(updated)
        return kept, {
            "enabled": True,
            "input_tables": len(tables),
            "kept_tables": len(kept),
            "dropped_tables": len(dropped_tables),
            "dropped_examples": dropped_tables[:8],
        }, issues

    def _repair_pdf_table_structure(
        self,
        tables: Sequence[TablePayload],
        *,
        candidates: Mapping[str, list[TablePayload]],
        page_spans: Sequence[Sequence[PdfSpan]] | None = None,
        selected_extractor: str,
        selection_context: Mapping[str, Any] | None = None,
    ) -> tuple[list[TablePayload], dict[str, Any], list[IssuePayload]]:
        if not tables:
            return [], {"enabled": True, "selected_extractor": selected_extractor}, []
        reconstructed_tables, reconstruction_meta, reconstruction_issues = (
            self._reconstruct_collapsed_native_text_tables(
                tables=tables,
                candidates=candidates,
                page_spans=page_spans,
                selection_context=selection_context,
            )
        )
        accepted_tables, acceptance_meta, acceptance_issues = self._enforce_pdf_canonical_acceptance(
            reconstructed_tables,
            selection_context=selection_context,
        )
        meta = {
            "enabled": True,
            "selected_extractor": selected_extractor,
            "reconstruction": reconstruction_meta,
            "acceptance": acceptance_meta,
        }
        return accepted_tables, meta, reconstruction_issues + acceptance_issues

    def _pdf_pages_look_native_text(self, pages: Sequence[PageLayout]) -> bool:
        if not pages:
            return False
        non_ocr_pages = 0
        for page in pages:
            if bool(getattr(page, "has_ocr_content", False)):
                continue
            density = 0.0
            try:
                density = float(
                    (getattr(page, "metadata", {}) or {}).get("raw_text_density")
                    or getattr(page, "text_density", 0.0)
                    or 0.0
                )
            except (TypeError, ValueError):
                density = 0.0
            has_text_block = any(
                self._sanitize_text(str(getattr(block, "text", "") or "")).strip()
                for block in (getattr(page, "blocks", None) or [])
            )
            if has_text_block or density >= self.native_pdf_text_density_floor:
                non_ocr_pages += 1
        return non_ocr_pages >= max(1, math.ceil(len(pages) * 0.6))
