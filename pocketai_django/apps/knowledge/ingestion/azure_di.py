from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.azure_di_client import AzureDocumentIntelligenceClientMixin
from apps.knowledge.ingestion import azure_di_client
from apps.knowledge.ingestion.azure_di_scope import AzureDocumentIntelligenceScopeMixin
from apps.knowledge.ingestion.contracts import (
    IssuePayload,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
    _union_bbox,
)
from apps.knowledge.ingestion.signals import _column_numeric_signal
from apps.knowledge.tables.detection import TableDetector

logger = logging.getLogger(__name__)
requests = azure_di_client.requests
time = azure_di_client.time


# Azure Document Intelligence table extraction (optional, REST-based)
class AzureDocumentIntelligenceExtractor(
    AzureDocumentIntelligenceClientMixin,
    AzureDocumentIntelligenceScopeMixin,
):
    _RETRYABLE_HTTP_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
    _THROTTLE_HTTP_STATUS = {429, 503}

    def __init__(
        self,
        *,
        endpoint: str | None,
        key: str | None,
        model: str = "prebuilt-layout",
        api_version: str = "2024-11-30",
        base_path: str = "formrecognizer",
        locale: str | None = None,
        timeout_seconds: float = 60.0,
        poll_interval_seconds: float = 1.5,
        max_polls: int = 40,
        request_max_attempts: int = 3,
        poll_request_max_attempts: int = 3,
        retry_backoff_base_seconds: float = 1.0,
        retry_backoff_max_seconds: float = 8.0,
        max_retry_after_seconds: float = 30.0,
    ) -> None:
        self.endpoint = (endpoint or "").rstrip("/")
        self.key = (key or "").strip()
        self.model = (model or "prebuilt-layout").strip()
        self.api_version = (api_version or "2024-11-30").strip()
        self.base_path = (base_path or "formrecognizer").strip().strip("/")
        self.locale = (locale or "").strip()
        self.timeout_seconds = max(5.0, float(timeout_seconds))
        # Azure DI guidance recommends spacing status polls (avoid rapid polling loops).
        self.poll_interval_seconds = max(2.0, float(poll_interval_seconds))
        self.max_polls = max(5, int(max_polls))
        self.request_max_attempts = max(1, int(request_max_attempts))
        self.poll_request_max_attempts = max(1, int(poll_request_max_attempts))
        self.retry_backoff_base_seconds = max(0.1, float(retry_backoff_base_seconds))
        self.retry_backoff_max_seconds = max(
            self.retry_backoff_base_seconds,
            float(retry_backoff_max_seconds),
        )
        self.max_retry_after_seconds = max(
            self.retry_backoff_base_seconds,
            float(max_retry_after_seconds),
        )

    @staticmethod
    def _polygon_to_bbox(polygon: Sequence[Any]) -> dict[str, float]:
        xs: list[float] = []
        ys: list[float] = []
        
        # Handle flat array format: [x1, y1, x2, y2, x3, y3, x4, y4]
        if polygon and isinstance(polygon, (list, tuple)) and all(isinstance(p, (int, float)) for p in polygon):
            # Flat array of coordinates - pair them up
            for i in range(0, len(polygon), 2):
                if i + 1 < len(polygon):
                    try:
                        xs.append(float(polygon[i]))
                        ys.append(float(polygon[i + 1]))
                    except (TypeError, ValueError):
                        continue
        else:
            # Dict format [{x, y}] or nested array [[x, y]]
            for point in polygon or []:
                if isinstance(point, dict):
                    x_val = point.get("x")
                    y_val = point.get("y")
                elif isinstance(point, (list, tuple)) and len(point) >= 2:
                    x_val, y_val = point[0], point[1]
                else:
                    continue
                try:
                    xs.append(float(x_val))
                    ys.append(float(y_val))
                except (TypeError, ValueError):
                    continue
        
        if not xs or not ys:
            return {}
        return {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}

    @staticmethod
    def _bbox_from_regions(
        regions: Sequence[Mapping[str, Any]] | None,
        *,
        page_unit_scale: Mapping[int, float] | None = None,
    ) -> tuple[int | None, dict[str, float]]:
        if not regions:
            return None, {}
        first = regions[0] if regions else {}
        page_number = first.get("pageNumber")
        polygon = first.get("polygon") or first.get("boundingPolygon") or []
        bbox = AzureDocumentIntelligenceExtractor._polygon_to_bbox(polygon)
        try:
            page_number = int(page_number) if page_number is not None else None
        except (TypeError, ValueError):
            page_number = None
        if bbox and page_unit_scale and page_number is not None:
            try:
                scale = float(page_unit_scale.get(page_number, 1.0) or 1.0)
            except (TypeError, ValueError):
                scale = 1.0
            if scale != 1.0:
                bbox = {
                    "x0": float(bbox.get("x0", 0.0)) * scale,
                    "y0": float(bbox.get("y0", 0.0)) * scale,
                    "x1": float(bbox.get("x1", 0.0)) * scale,
                    "y1": float(bbox.get("y1", 0.0)) * scale,
                }
        return page_number, bbox

    @staticmethod
    def _normalized_cell_text(value: str | None) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).lower()

    @staticmethod
    def _caption_text(caption: Any) -> str:
        if isinstance(caption, str):
            return caption.strip()
        if isinstance(caption, Mapping):
            content = caption.get("content")
            if isinstance(content, str):
                return content.strip()
            text = caption.get("text")
            if isinstance(text, str):
                return text.strip()
            return ""
        return str(caption or "").strip()



    def extract_tables(self, path: Path) -> tuple[list[TablePayload], list[IssuePayload], dict[str, Any]]:
        analyze_result, issues, meta = self._analyze_document(path)
        if not analyze_result:
            return [], issues, meta

        pages_data = analyze_result.get("pages") or []
        page_unit_scale: dict[int, float] = {}
        for page in pages_data:
            if not isinstance(page, Mapping):
                continue
            page_number = page.get("pageNumber")
            try:
                page_number_int = int(page_number) if page_number is not None else None
            except (TypeError, ValueError):
                page_number_int = None
            if not page_number_int:
                continue
            unit = str(page.get("unit") or "").strip().lower()
            # Azure DI uses page units (commonly "inch") for polygon coordinates. PyMuPDF uses PDF points (1/72 inch).
            if unit in {"inch", "in"}:
                page_unit_scale[page_number_int] = 72.0
            elif unit in {"point", "pt"}:
                page_unit_scale[page_number_int] = 1.0
            else:
                # Unknown units (e.g., "pixel" for images). Leave unscaled by default.
                page_unit_scale[page_number_int] = 1.0

        tables_data = analyze_result.get("tables") or []
        table_payloads: list[TablePayload] = []
        table_meta: dict[str, Any] = {
            "model": self.model,
            "api_version": self.api_version,
            "table_count": len(tables_data),
        }
        if meta:
            table_meta.update(meta)

        for order_index, table in enumerate(tables_data, start=1):
            row_count = int(table.get("rowCount") or 0)
            col_count = int(table.get("columnCount") or 0)
            cells = table.get("cells") or []
            
            # Debug: Check what boundingRegions Azure DI returns
            bounding_regions = table.get("boundingRegions")
            if order_index <= 2:  # Log first 2 tables only
                logger.info(
                    "azure_di.table_bbox_debug table=%s has_regions=%s region_count=%s first_region=%s",
                    order_index,
                    bool(bounding_regions),
                    len(bounding_regions) if bounding_regions else 0,
                    bounding_regions[0] if bounding_regions else None,
                )
            
            page_number, table_bbox = self._bbox_from_regions(
                bounding_regions,
                page_unit_scale=page_unit_scale,
            )
            header_rows: set[int] = set()
            cell_confidences: list[float] = []

            grid: list[list[str]] = [["" for _ in range(col_count)] for _ in range(row_count)]
            cell_lookup: dict[tuple[int, int], dict[str, Any]] = {}

            def _cell_value_signal(text: str) -> int:
                """
                Prefer value-like cells over label-like cells when spans overlap.

                Azure DI can emit broad-span "labels" (e.g. "Annual Fees") whose
                geometry overlaps value columns. If we write labels after values,
                the grid becomes unreadable (no numeric/value evidence). This
                signal is intentionally conservative and generic.
                """
                sample = str(text or "").strip()
                if not sample:
                    return 0
                if _column_numeric_signal(sample):
                    return 3
                lowered = sample.strip().lower()
                if lowered in {"free", "no fees", "no fee", "n/a", "na", "--", "-"}:
                    return 2
                return 0

            def _cell_priority(text: str, meta: Mapping[str, Any]) -> tuple[int, int, int]:
                value_score = _cell_value_signal(text)
                try:
                    span_area = int(meta.get("row_span") or 1) * int(meta.get("column_span") or 1)
                except (TypeError, ValueError):
                    span_area = 1
                span_score = -max(1, span_area)  # smaller span wins on ties
                kind = str(meta.get("kind") or "").strip().lower()
                kind_score = -1 if kind in {"columnheader", "rowheader"} else 0
                return (value_score, span_score, kind_score)

            def _should_write_cell(
                *,
                existing_text: str,
                existing_meta: Mapping[str, Any] | None,
                new_text: str,
                new_meta: Mapping[str, Any],
            ) -> bool:
                new_text = str(new_text or "").strip()
                if not new_text:
                    return False
                existing_text = str(existing_text or "").strip()
                if not existing_text:
                    return True
                if existing_text == new_text:
                    return False
                existing_meta = existing_meta or {}
                return _cell_priority(new_text, new_meta) > _cell_priority(existing_text, existing_meta)

            for cell in cells:
                try:
                    r_idx = int(cell.get("rowIndex") or 0)
                    c_idx = int(cell.get("columnIndex") or 0)
                except (TypeError, ValueError):
                    continue
                row_span = int(cell.get("rowSpan") or 1)
                col_span = int(cell.get("columnSpan") or 1)
                content = str(cell.get("content") or "").strip()
                kind = str(cell.get("kind") or "").lower()
                confidence = cell.get("confidence")
                if isinstance(confidence, (int, float)):
                    cell_confidences.append(float(confidence))
                if kind in {"columnheader", "rowheader"}:
                    header_rows.add(r_idx)
                regions = cell.get("boundingRegions") or []
                for rr in range(r_idx, min(r_idx + row_span, row_count)):
                    for cc in range(c_idx, min(c_idx + col_span, col_count)):
                        new_meta = {
                            "row_span": row_span,
                            "column_span": col_span,
                            "kind": kind,
                            "confidence": confidence,
                            "regions": regions,
                        }
                        existing_meta = cell_lookup.get((rr, cc)) or {}
                        if not _should_write_cell(
                            existing_text=grid[rr][cc],
                            existing_meta=existing_meta,
                            new_text=content,
                            new_meta=new_meta,
                        ):
                            continue
                        grid[rr][cc] = content
                        cell_lookup[(rr, cc)] = new_meta

            column_schema: list[str] = []
            header_row_indices = sorted(header_rows)
            for col_idx in range(col_count):
                header_parts: list[str] = []
                for row_idx in header_row_indices:
                    if 0 <= row_idx < row_count:
                        value = grid[row_idx][col_idx]
                        if value:
                            header_parts.append(value)
                header_text = " ".join(header_parts).strip()
                column_schema.append(TableDetector._normalize_header_cell(header_text, col_idx))

            # Geometric span reconciliation: compare data cell bounding
            # boxes against header cell bounding boxes to detect true column
            # spans that Azure DI failed to report via columnSpan.
            self._reconcile_spans_from_geometry(
                grid=grid,
                cell_lookup=cell_lookup,
                header_rows=header_rows,
                row_count=row_count,
                col_count=col_count,
                page_unit_scale=page_unit_scale,
            )

            table_rows: list[TableRowPayload] = []
            for row_idx in range(row_count):
                row_cells: list[TableCellPayload] = []
                row_native_bboxes: list[dict[str, float]] = []
                for col_idx in range(col_count):
                    raw_text = grid[row_idx][col_idx]
                    cell_meta = cell_lookup.get((row_idx, col_idx), {})
                    cell_page, cell_bbox = self._bbox_from_regions(
                        cell_meta.get("regions"),
                        page_unit_scale=page_unit_scale,
                    )
                    has_native_geometry = bool(cell_bbox)
                    if cell_bbox:
                        row_native_bboxes.append(cell_bbox)
                    normalized_value = TableDetector._normalize_cell_value(raw_text)
                    column_key = column_schema[col_idx] if col_idx < len(column_schema) else f"column_{col_idx+1}"
                    row_cells.append(
                        TableCellPayload(
                            row_index=row_idx,
                            column_index=col_idx,
                            column_key=column_key,
                            raw_text=raw_text,
                            normalized_value=normalized_value,
                            bbox=cell_bbox or {},
                            confidence=cell_meta.get("confidence"),
                            metadata={
                                "row_span": cell_meta.get("row_span", 1),
                                "column_span": cell_meta.get("column_span", 1),
                                "kind": cell_meta.get("kind"),
                                "page_number": cell_page or page_number,
                                "has_native_geometry": has_native_geometry,
                                "geometry_source": "native_region" if has_native_geometry else "synthetic_empty",
                            },
                        )
                    )
                row_type = "header" if row_idx in header_rows else "data"
                row_bbox = _union_bbox(row_native_bboxes) if row_native_bboxes else {}
                table_rows.append(
                    TableRowPayload(
                        row_index=row_idx,
                        page_number=page_number,
                        bbox=row_bbox,
                        raw_text=" | ".join(grid[row_idx]) if row_idx < len(grid) else "",
                        metadata={
                            "row_type": row_type,
                            "row_has_native_geometry": bool(row_native_bboxes),
                            "row_geometry_source": "native_union" if row_native_bboxes else "synthetic_empty",
                        },
                        cells=row_cells,
                    )
                )

            table_rows = self._annotate_row_applicability(
                table_rows=table_rows,
                column_schema=column_schema,
                header_rows=header_rows,
            )

            avg_conf = round(sum(cell_confidences) / max(1, len(cell_confidences)), 4) if cell_confidences else None
            caption_text = self._caption_text(table.get("caption"))
            table_payloads.append(
                TablePayload(
                    order_index=order_index,
                    title=caption_text or f"Table {order_index}",
                    section_heading="",
                    page_number=page_number,
                    bbox=table_bbox,
                    column_schema=column_schema,
                    data_dictionary={},
                    metadata={
                        "detected_via": "azure_di",
                        "model": self.model,
                        "structure_confidence": avg_conf,
                        "cell_confidence_avg": avg_conf,
                        "table_index": order_index,
                    },
                    rows=table_rows,
                )
            )

        return table_payloads, issues, table_meta
