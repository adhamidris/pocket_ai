from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.contracts import PdfSpan, TableCellPayload, TablePayload, TableRowPayload, _union_bbox
from apps.knowledge.tables.detection import TableDetector
from apps.knowledge.tables.geometry_tools.reconstructor import GeometryTableReconstructor


class IngestionPdfSpanMatrixMixin:

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
