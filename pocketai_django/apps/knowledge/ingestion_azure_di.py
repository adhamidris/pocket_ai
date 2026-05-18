from __future__ import annotations

from pathlib import Path
import random
import re
import time
from typing import Any, Iterable, Mapping, Sequence

import requests

from apps.accounts.models import KnowledgeIssueSeverity
from apps.knowledge.column_role_inference import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_NOTE,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    column_role_groups,
    infer_column_roles,
)
from apps.knowledge.ingestion_contracts import (
    IssuePayload,
    TableCellPayload,
    TablePayload,
    TableRowPayload,
    _union_bbox,
)
from apps.knowledge.ingestion_table_detection import TableDetector


# Azure Document Intelligence table extraction (optional, REST-based)
class AzureDocumentIntelligenceExtractor:
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

    @staticmethod
    def _infer_segment_indices_from_structure(
        *,
        table_rows: Sequence[TableRowPayload],
        column_schema: Sequence[str],
        header_rows: set[int],
    ) -> list[int]:
        width = len(column_schema)
        if width <= 0:
            return []
        if not table_rows:
            return list(range(width))

        row_values: list[list[str]] = []
        header_row_positions: set[int] = set()
        for pos, row in enumerate(table_rows):
            row_meta = row.metadata if isinstance(row.metadata, Mapping) else {}
            row_type = str(row_meta.get("row_type") or "").strip().lower()
            if row.row_index in header_rows or row_type == "header":
                header_row_positions.add(pos)
            values: list[str] = []
            for idx in range(width):
                if idx < len(row.cells):
                    values.append(str(row.cells[idx].raw_text or ""))
                else:
                    values.append("")
            row_values.append(values)

        role_profiles = infer_column_roles(
            row_values=row_values,
            column_schema=[str(col or "") for col in column_schema],
            header_row_indices=header_row_positions,
            min_scope_columns=3,
        )
        role_groups = column_role_groups(role_profiles)
        scope_indices = set(role_groups.get(COLUMN_ROLE_SCOPE_DIMENSION, []))

        # Keep near-scope qualifiers when their scope score is close enough.
        # This prevents sparse-table edge cases from dropping a true segment
        # column that received a qualifier label due low support.
        for profile in role_profiles:
            role_scores = profile.role_scores if isinstance(profile.role_scores, Mapping) else {}
            scope_score = float(role_scores.get(COLUMN_ROLE_SCOPE_DIMENSION) or 0.0)
            qualifier_score = float(role_scores.get(COLUMN_ROLE_QUALIFIER) or 0.0)
            if (
                profile.role == COLUMN_ROLE_QUALIFIER
                and scope_score >= 0.46
                and (qualifier_score - scope_score) <= 0.2
            ):
                scope_indices.add(int(profile.column_index))

        profile_by_index = {
            int(profile.column_index): profile
            for profile in role_profiles
        }

        def _extend_sparse_scope_tail(base_scope: set[int]) -> set[int]:
            if not base_scope:
                return base_scope
            max_scope = max(base_scope)
            extended = set(base_scope)
            # Preserve sparse right-edge scope dimensions (e.g., "private") that
            # have real value participation but can be misclassified as note due to
            # low density and broad note rows elsewhere in the table.
            for idx in range(max_scope + 1, width):
                profile = profile_by_index.get(idx)
                if profile is None:
                    break
                if profile.role == COLUMN_ROLE_DESCRIPTOR:
                    break
                role_scores = profile.role_scores if isinstance(profile.role_scores, Mapping) else {}
                signals = profile.signals if isinstance(profile.signals, Mapping) else {}
                label = str(column_schema[idx] or f"column_{idx + 1}").strip().lower()
                tokens = [t for t in re.split(r"[^a-z0-9]+", label) if t]
                if not tokens or len(tokens) > 4:
                    break
                if any(tok in {"note", "notes", "remark", "remarks", "comment", "comments", "details"} for tok in tokens):
                    break

                non_empty_count = int(signals.get("non_empty_count") or 0)
                non_empty_ratio = float(signals.get("non_empty_ratio") or 0.0)
                avg_chars = float(signals.get("avg_chars") or 0.0)
                unique_count = int(signals.get("unique_count") or 0)
                scope_score = float(role_scores.get(COLUMN_ROLE_SCOPE_DIMENSION) or 0.0)

                if non_empty_count < 2 or non_empty_ratio < 0.05:
                    break
                if avg_chars > 40.0 and unique_count <= 2 and scope_score < 0.25:
                    break

                extended.add(idx)
            return extended

        resolved_scope_indices = sorted(scope_indices)
        if len(resolved_scope_indices) >= 3:
            min_scope = min(resolved_scope_indices)
            max_scope = max(resolved_scope_indices)
            bridged_scope = set(resolved_scope_indices)
            for profile in role_profiles:
                idx = int(profile.column_index)
                if idx <= min_scope or idx >= max_scope:
                    continue
                if profile.role in {COLUMN_ROLE_DESCRIPTOR, COLUMN_ROLE_NOTE}:
                    continue
                # If a non-descriptor column is between two scope-dimension
                # columns, treat it as a bridge to avoid dropping middle
                # segments due classifier noise.
                bridged_scope.add(idx)
            bridged_scope = _extend_sparse_scope_tail(bridged_scope)
            return sorted(bridged_scope)

        contextual_indices = sorted(
            set(role_groups.get(COLUMN_ROLE_DESCRIPTOR, []))
            | {
                int(profile.column_index)
                for profile in role_profiles
                if profile.role == COLUMN_ROLE_QUALIFIER
                and (
                    float((profile.role_scores or {}).get(COLUMN_ROLE_QUALIFIER) or 0.0)
                    - float((profile.role_scores or {}).get(COLUMN_ROLE_SCOPE_DIMENSION) or 0.0)
                ) >= 0.25
            }
        )
        fallback = [idx for idx in range(width) if idx not in contextual_indices]
        fallback = sorted(_extend_sparse_scope_tail(set(fallback)))
        if len(fallback) >= 3:
            return fallback
        return resolved_scope_indices or fallback

    def _reconcile_spans_from_geometry(
        self,
        *,
        grid: list[list[str]],
        cell_lookup: dict[tuple[int, int], dict[str, Any]],
        header_rows: set[int],
        row_count: int,
        col_count: int,
        page_unit_scale: Mapping[int, float] | None = None,
    ) -> None:
        """
        Compare data-cell bounding boxes against header-cell bounding boxes
        to detect true column spans that Azure DI did not report via columnSpan.

        Mutates *grid* and *cell_lookup* in place: when a data cell's
        horizontal extent overlaps N header columns but column_span == 1,
        the value is duplicated across those columns and column_span is
        updated.  This runs before frozen TableCellPayload objects are built.
        """
        if col_count < 3 or not header_rows:
            return

        # Build column x-boundaries from header cell bounding boxes.
        header_row_idx = min(header_rows)
        col_boundaries: list[tuple[float, float]] = []  # (x0, x1) per column
        for c in range(col_count):
            meta = cell_lookup.get((header_row_idx, c))
            if not meta:
                col_boundaries.append((0.0, 0.0))
                continue
            _page, bbox = self._bbox_from_regions(
                meta.get("regions"),
                page_unit_scale=page_unit_scale,
            )
            if bbox and bbox.get("x0", 0.0) < bbox.get("x1", 0.0):
                col_boundaries.append((float(bbox["x0"]), float(bbox["x1"])))
            else:
                col_boundaries.append((0.0, 0.0))

        # Need at least 3 valid column boundaries to make geometric decisions.
        valid_boundaries = [(x0, x1) for x0, x1 in col_boundaries if x1 > x0]
        if len(valid_boundaries) < 3:
            return

        # Use a small tolerance to avoid floating-point near-misses.
        # 15% of median column width is a safe margin.
        widths = [x1 - x0 for x0, x1 in valid_boundaries if (x1 - x0) > 0]
        if not widths:
            return
        sorted_widths = sorted(widths)
        median_width = sorted_widths[len(sorted_widths) // 2]
        tolerance = median_width * 0.15

        for r in range(row_count):
            if r in header_rows:
                continue
            for c in range(col_count):
                value = grid[r][c]
                if not value:
                    continue
                meta = cell_lookup.get((r, c))
                if not meta:
                    continue
                existing_span = int(meta.get("column_span") or 1)
                if existing_span > 1:
                    # Azure DI already reported a span — trust it.
                    continue
                _page, bbox = self._bbox_from_regions(
                    meta.get("regions"),
                    page_unit_scale=page_unit_scale,
                )
                if not bbox or bbox.get("x1", 0.0) <= bbox.get("x0", 0.0):
                    continue
                cell_x0 = float(bbox["x0"])
                cell_x1 = float(bbox["x1"])
                # Find all header columns whose x-range overlaps with this cell.
                overlapping: list[int] = []
                for hc, (hx0, hx1) in enumerate(col_boundaries):
                    if hx1 <= hx0:
                        continue
                    # Two ranges overlap if one starts before the other ends.
                    if cell_x0 < (hx1 - tolerance) and cell_x1 > (hx0 + tolerance):
                        overlapping.append(hc)
                if len(overlapping) <= 1:
                    continue
                # The cell physically spans multiple header columns.
                # Duplicate the value across all overlapped columns and
                # update column_span in cell_lookup.
                span = len(overlapping)
                for oc in overlapping:
                    # Only fill empty targets (or identical values). Never overwrite
                    # an already-populated cell because it likely contains a
                    # per-column value (e.g. "EGP 200") that should trump a
                    # broad-span label.
                    existing_value = grid[r][oc]
                    if existing_value and str(existing_value).strip() and str(existing_value).strip() != str(value).strip():
                        continue
                    grid[r][oc] = value
                    existing_meta = cell_lookup.get((r, oc)) or {}
                    cell_lookup[(r, oc)] = {
                        **existing_meta,
                        "column_span": span,
                        "geometric_span_reconciled": True,
                    }
                    # Preserve the original cell's regions on newly filled cells
                    # so downstream bbox extraction works correctly.
                    if oc != c and "regions" not in existing_meta:
                        cell_lookup[(r, oc)]["regions"] = meta.get("regions") or []

    def _annotate_row_applicability(
        self,
        *,
        table_rows: Sequence[TableRowPayload],
        column_schema: Sequence[str],
        header_rows: set[int],
    ) -> list[TableRowPayload]:
        """
        Infer row-level applicability across peer columns for centered/merged values.

        Azure sometimes anchors a centered value to one interior segment column
        even when visually it applies to a wider segment group.  We use multiple
        signals — explicit column spans, table-level sparse-row patterns, and
        per-row emptiness — to recover the intended multi-column scope.

        Key improvement over v1: instead of requiring a single *dominant* column
        to accumulate most single-value placements (which fails when Azure DI
        scatters values across different columns row-by-row), we count the
        *fraction of data rows that are sparse* (exactly one non-empty segment
        cell).  A high sparse fraction indicates the table uses centered/merged
        values regardless of which column each value landed in.
        """

        rows = list(table_rows or [])
        if not rows or not column_schema:
            return rows

        column_count = len(column_schema)

        def _is_contextual_broad_span_row(row: TableRowPayload) -> bool:
            if row.row_index in header_rows:
                return False
            non_empty_cells: list[TableCellPayload] = []
            normalized_values: set[str] = set()
            has_broad_context_span = False
            for cell in row.cells:
                value = str(cell.raw_text or "").strip()
                if not value:
                    continue
                non_empty_cells.append(cell)
                normalized_values.add(self._normalized_cell_text(value))
                try:
                    col_idx = int(cell.column_index)
                    span_width = int((cell.metadata or {}).get("column_span") or 1)
                except (TypeError, ValueError):
                    continue
                if (
                    span_width >= max(4, column_count - 1)
                    and col_idx <= 1
                ):
                    has_broad_context_span = True
            if not has_broad_context_span:
                # Secondary heuristic: all cells same text (no column_span needed).
                # Covers pdfplumber/heuristic extractors that duplicate the section
                # label into every column instead of reporting a column_span.
                if len(non_empty_cells) >= 4 and len(normalized_values) == 1:
                    return True
                return False
            # A near full-width span carrying one repeated phrase is usually a
            # note/footer row and should not shape scope-axis inference.
            return len(normalized_values) <= 1 or len(non_empty_cells) <= 2

        rows_for_structure = [row for row in rows if not _is_contextual_broad_span_row(row)]
        if not rows_for_structure:
            rows_for_structure = rows

        section_header_indices: set[int] = {
            row.row_index
            for row in rows
            if row.row_index not in header_rows and _is_contextual_broad_span_row(row)
        }

        segment_indices = self._infer_segment_indices_from_structure(
            table_rows=rows_for_structure,
            column_schema=column_schema,
            header_rows=header_rows,
        )
        base_segment_indices = sorted(set(segment_indices))
        base_segment_set = set(base_segment_indices)
        span_evidence_indices: set[int] = set()
        for row in rows:
            if row.row_index in header_rows:
                continue
            for cell in row.cells:
                try:
                    col_idx = int(cell.column_index)
                    span_width = int((cell.metadata or {}).get("column_span") or 1)
                except (TypeError, ValueError):
                    continue
                if span_width <= 1:
                    continue
                span_targets = set(range(col_idx, min(len(column_schema), col_idx + span_width)))
                if len(span_targets) <= 1:
                    continue

                if len(base_segment_set) >= 3:
                    # Keep span evidence constrained to the structurally inferred
                    # scope axis so wide note/footer rows do not pollute scope
                    # dimensions with descriptor or qualifier columns.
                    overlap = sorted(span_targets & base_segment_set)
                    if len(overlap) <= 1:
                        continue
                    span_evidence_indices.update(overlap)
                    continue

                # Bootstrap mode for weak structural inference: reject near
                # full-width spans beginning in contextual columns because these
                # are usually note rows, not scope axes.
                if (
                    len(span_targets) >= max(4, len(column_schema) - 1)
                    and min(span_targets) <= 1
                ):
                    continue
                span_evidence_indices.update(span_targets)
        if span_evidence_indices:
            segment_indices = sorted(set(segment_indices) | span_evidence_indices)
        if len(segment_indices) < 3:
            return rows

        scope_indices = sorted(set(segment_indices))
        scope_set = set(scope_indices)
        data_rows = [row for row in rows if row.row_index not in header_rows]
        if not data_rows:
            return rows

        table_scope_profile = build_scope_table_profile(
            rows=rows,
            scope_indices=scope_indices,
            header_rows=header_rows,
        )
        scope_dimension_labels = [
            str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
            for idx in scope_indices
        ]

        # ── Per-row annotation via deterministic precedence engine ──
        updated_rows: list[TableRowPayload] = []
        for row in rows:
            if row.row_index in header_rows:
                updated_rows.append(row)
                continue

            if row.row_index in section_header_indices:
                row_meta = dict(row.metadata or {})
                row_meta["row_type"] = "section_header"
                updated_rows.append(
                    TableRowPayload(
                        row_index=row.row_index,
                        page_number=row.page_number,
                        bbox=row.bbox,
                        raw_text=row.raw_text,
                        metadata=row_meta,
                        cells=row.cells,
                    )
                )
                continue

            scope_decision = infer_scope_for_row(
                row=row,
                table_profile=table_scope_profile,
            )
            if scope_decision is None:
                updated_rows.append(row)
                continue

            applies_to_indices = [
                idx for idx in scope_decision.applies_to_indices if idx in scope_set
            ]
            detected_indices = [
                idx for idx in scope_decision.detected_indices if idx in scope_set
            ]
            scope_reason = canonical_scope_reason(scope_decision.reason)
            scope_confidence = round(float(scope_decision.confidence), 3)

            applies_to_labels = [
                str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
                for idx in applies_to_indices
            ]
            detected_labels = [
                str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
                for idx in detected_indices
            ]
            observed_value_columns: list[str] = []
            qualifier_columns: list[str] = []
            representative_value = ""
            for cell in row.cells:
                value = str(cell.raw_text or "").strip()
                if not value:
                    continue
                try:
                    idx = int(cell.column_index)
                except (TypeError, ValueError):
                    continue
                if idx < len(column_schema):
                    label = str(column_schema[idx] or f"column_{idx + 1}").strip() or f"column_{idx + 1}"
                else:
                    label = f"column_{idx + 1}"
                observed_value_columns.append(label)
                if idx in scope_set and not representative_value:
                    representative_value = re.sub(r"\s+", " ", value).strip()
                if idx not in scope_set:
                    qualifier_columns.append(label)
            observed_value_columns = list(dict.fromkeys(observed_value_columns))
            qualifier_columns = list(dict.fromkeys(qualifier_columns))

            row_meta = dict(row.metadata or {})
            row_meta.update(
                {
                    "table_scope_contract_version": TABLE_SCOPE_CONTRACT_VERSION,
                    "scope_engine_version": SCOPE_ENGINE_VERSION,
                    "observed_value_columns": observed_value_columns,
                    "qualifier_columns": qualifier_columns,
                    "scope_dimension_columns": scope_dimension_labels,
                    "inferred_scope_columns": applies_to_labels,
                    "scope_confidence": scope_confidence,
                    "scope_reason": scope_reason,
                    "applicability_source": "scope_engine_v3",
                    "applicability_detected_columns": detected_labels,
                    "applicability_segment_columns": scope_dimension_labels,
                    "scope_value": representative_value or "",
                }
            )

            updated_rows.append(
                TableRowPayload(
                    row_index=row.row_index,
                    page_number=row.page_number,
                    bbox=row.bbox,
                    raw_text=row.raw_text,
                    metadata=row_meta,
                    cells=row.cells,
                )
            )
        return updated_rows

    def _build_analyze_url(self, *, locale: str | None = None) -> str:
        base_path = self.base_path or "formrecognizer"
        params = {"api-version": self.api_version}
        if locale:
            params["locale"] = locale
        query = urlencode(params)
        return f"{self.endpoint}/{base_path}/documentModels/{self.model}:analyze?{query}"

    @staticmethod
    def _parse_retry_after_seconds(raw_value: Any) -> float | None:
        if raw_value is None:
            return None
        raw = str(raw_value).strip()
        if not raw:
            return None
        try:
            seconds = float(raw)
            if seconds >= 0.0:
                return seconds
        except (TypeError, ValueError):
            pass
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = None
        if not parsed:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime_timezone.utc)
        delta = (parsed - datetime.now(datetime_timezone.utc)).total_seconds()
        return max(0.0, delta)

    def _retry_delay_seconds(
        self,
        attempt: int,
        *,
        response: Any = None,
    ) -> float:
        backoff = min(
            self.retry_backoff_max_seconds,
            self.retry_backoff_base_seconds * (2 ** max(0, int(attempt) - 1)),
        )
        jitter = random.uniform(0.0, min(0.25, backoff * 0.25))
        delay = backoff + jitter
        if response is not None:
            headers = getattr(response, "headers", None)
            retry_after_value = headers.get("retry-after") if isinstance(headers, Mapping) else None
            retry_after = self._parse_retry_after_seconds(retry_after_value)
            if retry_after is not None:
                delay = max(delay, min(retry_after, self.max_retry_after_seconds))
        return round(max(0.0, delay), 3)

    @classmethod
    def _classify_failure_class(
        cls,
        *,
        status_code: int | None = None,
        exc: Exception | None = None,
    ) -> str:
        if isinstance(exc, requests.Timeout):
            return "timeout"
        if status_code in cls._THROTTLE_HTTP_STATUS:
            return "throttle_retryable"
        if status_code in cls._RETRYABLE_HTTP_STATUS:
            return "throttle_retryable"
        if isinstance(exc, requests.ConnectionError):
            return "throttle_retryable"
        return "hard_failure"

    @staticmethod
    def _append_retry_event(
        meta: dict[str, Any],
        *,
        phase: str,
        attempt: int,
        delay_s: float,
        reason: str,
        status_code: int | None = None,
    ) -> None:
        events = meta.setdefault("retry_events", [])
        if not isinstance(events, list):
            events = []
            meta["retry_events"] = events
        events.append(
            {
                "phase": phase,
                "attempt": int(attempt),
                "delay_s": round(float(delay_s), 3),
                "reason": reason,
                "status_code": status_code,
            }
        )
        if len(events) > 24:
            del events[:-24]

    @staticmethod
    def _finalize_failure_meta(
        meta: dict[str, Any],
        *,
        status: str,
        failure_class: str,
        failure_stage: str,
        failure_reason: str,
        start_time: float,
        failure_status_code: int | None = None,
        last_error: str | None = None,
    ) -> None:
        meta["status"] = status
        meta["failure_class"] = failure_class
        meta["failure_stage"] = failure_stage
        meta["failure_reason"] = failure_reason
        if failure_status_code is not None:
            meta["failure_status_code"] = int(failure_status_code)
        if last_error:
            meta["last_error"] = str(last_error)[:300]
        meta["duration_ms"] = int((time.time() - start_time) * 1000)

    def _analyze_document(self, path: Path) -> tuple[dict[str, Any] | None, list[IssuePayload], dict[str, Any]]:
        issues: list[IssuePayload] = []
        meta: dict[str, Any] = {
            "request_attempts": 0,
            "poll_attempts": 0,
            "poll_http_attempts": 0,
            "retry_events": [],
        }
        if not self.endpoint or not self.key:
            issues.append(
                IssuePayload(
                    code="azure_di_missing",
                    severity=KnowledgeIssueSeverity.INFO.value,
                    description="Azure Document Intelligence credentials are missing; skipping.",
                )
            )
            meta["status"] = "skipped"
            return None, issues, meta

        url = self._build_analyze_url(locale=self.locale)
        headers = {
            "Ocp-Apim-Subscription-Key": self.key,
            "Content-Type": "application/pdf",
        }
        start = time.time()
        response: Any = None
        for attempt in range(1, self.request_max_attempts + 1):
            meta["request_attempts"] = attempt
            try:
                with path.open("rb") as handle:
                    response = requests.post(
                        url,
                        headers=headers,
                        data=handle,
                        timeout=self.timeout_seconds,
                    )
            except requests.RequestException as exc:
                failure_class = self._classify_failure_class(exc=exc)
                retryable = failure_class in {"timeout", "throttle_retryable"}
                if retryable and attempt < self.request_max_attempts:
                    delay = self._retry_delay_seconds(attempt)
                    self._append_retry_event(
                        meta,
                        phase="submit",
                        attempt=attempt,
                        delay_s=delay,
                        reason=f"submit_exception:{exc.__class__.__name__}",
                    )
                    time.sleep(delay)
                    continue
                issues.append(
                    IssuePayload(
                        code="azure_di_request_failed",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI request failed: {exc}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status=("timeout" if failure_class == "timeout" else "failed"),
                    failure_class=failure_class,
                    failure_stage="submit",
                    failure_reason="request_exception",
                    start_time=start,
                    last_error=str(exc),
                )
                return None, issues, meta

            if response.status_code in {200, 201, 202}:
                break

            failure_class = self._classify_failure_class(status_code=int(response.status_code))
            retryable_status = int(response.status_code) in self._RETRYABLE_HTTP_STATUS
            if retryable_status and attempt < self.request_max_attempts:
                delay = self._retry_delay_seconds(attempt, response=response)
                self._append_retry_event(
                    meta,
                    phase="submit",
                    attempt=attempt,
                    delay_s=delay,
                    reason="submit_http_retry",
                    status_code=int(response.status_code),
                )
                time.sleep(delay)
                continue

            issues.append(
                IssuePayload(
                    code="azure_di_request_error",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description=f"Azure DI request error {response.status_code}: {response.text[:200]}",
                )
            )
            self._finalize_failure_meta(
                meta,
                status=("timeout" if failure_class == "timeout" else "failed"),
                failure_class=failure_class,
                failure_stage="submit",
                failure_reason="request_http_error",
                start_time=start,
                failure_status_code=int(response.status_code),
            )
            return None, issues, meta

        operation_url = response.headers.get("operation-location") or response.headers.get("Operation-Location")
        if not operation_url:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if payload.get("status") == "succeeded" and payload.get("analyzeResult"):
                meta["duration_ms"] = int((time.time() - start) * 1000)
                meta["status"] = "succeeded"
                return payload.get("analyzeResult"), issues, meta
            issues.append(
                IssuePayload(
                    code="azure_di_missing_operation",
                    severity=KnowledgeIssueSeverity.WARNING.value,
                    description="Azure DI response missing operation-location header.",
                )
            )
            self._finalize_failure_meta(
                meta,
                status="failed",
                failure_class="hard_failure",
                failure_stage="submit",
                failure_reason="missing_operation_location",
                start_time=start,
            )
            return None, issues, meta

        poll_headers = {"Ocp-Apim-Subscription-Key": self.key}
        status_payload: dict[str, Any] | None = None
        for poll_attempt in range(1, self.max_polls + 1):
            meta["poll_attempts"] = poll_attempt
            poll_response: Any = None
            for http_attempt in range(1, self.poll_request_max_attempts + 1):
                meta["poll_http_attempts"] = int(meta.get("poll_http_attempts") or 0) + 1
                try:
                    poll_response = requests.get(
                        operation_url,
                        headers=poll_headers,
                        timeout=self.timeout_seconds,
                    )
                except requests.RequestException as exc:
                    failure_class = self._classify_failure_class(exc=exc)
                    retryable = failure_class in {"timeout", "throttle_retryable"}
                    if retryable and http_attempt < self.poll_request_max_attempts:
                        delay = self._retry_delay_seconds(http_attempt)
                        self._append_retry_event(
                            meta,
                            phase="poll",
                            attempt=http_attempt,
                            delay_s=delay,
                            reason=f"poll_exception:{exc.__class__.__name__}",
                        )
                        time.sleep(delay)
                        continue
                    issues.append(
                        IssuePayload(
                            code="azure_di_poll_failed",
                            severity=KnowledgeIssueSeverity.WARNING.value,
                            description=f"Azure DI poll failed: {exc}",
                        )
                    )
                    self._finalize_failure_meta(
                        meta,
                        status=("timeout" if failure_class == "timeout" else "failed"),
                        failure_class=failure_class,
                        failure_stage="poll",
                        failure_reason="poll_exception",
                        start_time=start,
                        last_error=str(exc),
                    )
                    return None, issues, meta

                if poll_response.status_code in {200, 201}:
                    break

                failure_class = self._classify_failure_class(status_code=int(poll_response.status_code))
                retryable_status = int(poll_response.status_code) in self._RETRYABLE_HTTP_STATUS
                if retryable_status and http_attempt < self.poll_request_max_attempts:
                    delay = self._retry_delay_seconds(http_attempt, response=poll_response)
                    self._append_retry_event(
                        meta,
                        phase="poll",
                        attempt=http_attempt,
                        delay_s=delay,
                        reason="poll_http_retry",
                        status_code=int(poll_response.status_code),
                    )
                    time.sleep(delay)
                    continue
                issues.append(
                    IssuePayload(
                        code="azure_di_poll_error",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI poll error {poll_response.status_code}: {poll_response.text[:200]}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status=("timeout" if failure_class == "timeout" else "failed"),
                    failure_class=failure_class,
                    failure_stage="poll",
                    failure_reason="poll_http_error",
                    start_time=start,
                    failure_status_code=int(poll_response.status_code),
                )
                return None, issues, meta

            if poll_response is None:
                continue
            try:
                status_payload = poll_response.json()
            except ValueError:
                status_payload = None
            if not status_payload:
                time.sleep(self.poll_interval_seconds)
                continue
            status = (status_payload.get("status") or "").lower()
            if status == "succeeded":
                meta["duration_ms"] = int((time.time() - start) * 1000)
                meta["status"] = "succeeded"
                return status_payload.get("analyzeResult"), issues, meta
            if status in {"failed", "error"}:
                issues.append(
                    IssuePayload(
                        code="azure_di_failed",
                        severity=KnowledgeIssueSeverity.WARNING.value,
                        description=f"Azure DI failed: {status_payload.get('error', {})}",
                    )
                )
                self._finalize_failure_meta(
                    meta,
                    status="failed",
                    failure_class="hard_failure",
                    failure_stage="poll",
                    failure_reason="poll_status_failed",
                    start_time=start,
                )
                return None, issues, meta
            time.sleep(self.poll_interval_seconds)

        issues.append(
            IssuePayload(
                code="azure_di_timeout",
                severity=KnowledgeIssueSeverity.WARNING.value,
                description="Azure DI polling timed out.",
            )
        )
        self._finalize_failure_meta(
            meta,
            status="timeout",
            failure_class="timeout",
            failure_stage="poll",
            failure_reason="poll_max_attempts_exceeded",
            start_time=start,
        )
        return None, issues, meta

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
