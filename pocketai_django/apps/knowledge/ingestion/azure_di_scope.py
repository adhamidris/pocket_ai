from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from apps.knowledge.ingestion.contracts import TableCellPayload, TableRowPayload
from apps.knowledge.ingestion.signals import TABLE_SCOPE_CONTRACT_VERSION, _column_numeric_signal
from apps.knowledge.tables.semantic.column_roles import (
    COLUMN_ROLE_DESCRIPTOR,
    COLUMN_ROLE_NOTE,
    COLUMN_ROLE_QUALIFIER,
    COLUMN_ROLE_SCOPE_DIMENSION,
    column_role_groups,
    infer_column_roles,
)
from apps.knowledge.tables.semantic.scope_engine import (
    SCOPE_ENGINE_VERSION,
    build_scope_table_profile,
    canonical_scope_reason,
    infer_scope_for_row,
    legacy_scope_reason,
)


class _ScopeMetadata(dict):
    def __init__(self, *args, legacy_aliases: Mapping[str, Any] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._legacy_aliases = dict(legacy_aliases or {})

    def get(self, key: Any, default: Any = None) -> Any:
        if key in self._legacy_aliases and key not in self:
            return self._legacy_aliases[key]
        return super().get(key, default)


class AzureDocumentIntelligenceScopeMixin:

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
            applicability_mode = legacy_scope_reason(scope_reason)
            if applicability_mode == "explicit_span":
                applicability_mode = "explicit_cells"
            legacy_applies_to_labels = applies_to_labels
            if scope_reason == "scope_explicit_span" and len(applies_to_labels) < len(scope_dimension_labels):
                legacy_applies_to_labels = scope_dimension_labels
            legacy_aliases = {
                "applicability_mode": applicability_mode,
                "applies_to_columns": legacy_applies_to_labels,
            }
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
            row_meta = _ScopeMetadata(row_meta, legacy_aliases=legacy_aliases)

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
