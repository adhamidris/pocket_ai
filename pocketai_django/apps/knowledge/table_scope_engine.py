from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence


SCOPE_ENGINE_VERSION = "v3"

SCOPE_REASON_EXPLICIT_SPAN = "scope_explicit_span"
SCOPE_REASON_REPEATED_VALUE_SPAN = "scope_repeated_value_span"
SCOPE_REASON_SPARSE_EXPANSION = "scope_sparse_expansion"
SCOPE_REASON_ABSTAIN = "scope_abstain"

SCOPE_REASON_TO_LEGACY: dict[str, str] = {
    SCOPE_REASON_EXPLICIT_SPAN: "explicit_span",
    SCOPE_REASON_REPEATED_VALUE_SPAN: "inferred_span_extension",
    SCOPE_REASON_SPARSE_EXPANSION: "inferred_sparse_expansion",
    SCOPE_REASON_ABSTAIN: "explicit_cells",
}

LEGACY_TO_SCOPE_REASON: dict[str, str] = {
    "explicit_span": SCOPE_REASON_EXPLICIT_SPAN,
    "inferred_span_extension": SCOPE_REASON_REPEATED_VALUE_SPAN,
    "inferred_sparse_expansion": SCOPE_REASON_SPARSE_EXPANSION,
    "explicit_cells": SCOPE_REASON_ABSTAIN,
    SCOPE_REASON_EXPLICIT_SPAN: SCOPE_REASON_EXPLICIT_SPAN,
    SCOPE_REASON_REPEATED_VALUE_SPAN: SCOPE_REASON_REPEATED_VALUE_SPAN,
    SCOPE_REASON_SPARSE_EXPANSION: SCOPE_REASON_SPARSE_EXPANSION,
    SCOPE_REASON_ABSTAIN: SCOPE_REASON_ABSTAIN,
}


def canonical_scope_reason(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return LEGACY_TO_SCOPE_REASON.get(normalized, SCOPE_REASON_ABSTAIN)


def legacy_scope_reason(value: Any) -> str:
    canonical = canonical_scope_reason(value)
    return SCOPE_REASON_TO_LEGACY.get(canonical, "explicit_cells")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalized_text(value: Any) -> str:
    return _clean_text(value).lower()


def _row_cells(row: Any) -> list[Any]:
    values = getattr(row, "cells", None)
    if values is None:
        return []
    if hasattr(values, "all"):
        return list(values.all())
    if isinstance(values, (list, tuple)):
        return list(values)
    return []


def _row_cell_lookup(row: Any) -> dict[int, Any]:
    lookup: dict[int, Any] = {}
    for cell in _row_cells(row):
        try:
            idx = int(getattr(cell, "column_index", -1))
        except (TypeError, ValueError):
            continue
        if idx >= 0:
            lookup[idx] = cell
    return lookup


@dataclass(frozen=True)
class ScopeTableProfile:
    scope_indices: list[int]
    rows_with_scope_values: int
    sparse_row_count: int
    sparse_fraction: float
    sparse_row_expansion: bool
    merged_span_evidence: bool


@dataclass(frozen=True)
class ScopeDecision:
    applies_to_indices: list[int]
    detected_indices: list[int]
    reason: str
    confidence: float


def build_scope_table_profile(
    *,
    rows: Sequence[Any],
    scope_indices: Sequence[int],
    header_rows: set[int],
) -> ScopeTableProfile:
    resolved_scope = sorted({int(idx) for idx in scope_indices})
    if not resolved_scope:
        return ScopeTableProfile(
            scope_indices=[],
            rows_with_scope_values=0,
            sparse_row_count=0,
            sparse_fraction=0.0,
            sparse_row_expansion=False,
            merged_span_evidence=False,
        )

    rows_with_scope_values = 0
    sparse_row_count = 0
    merged_span_evidence = False
    scope_set = set(resolved_scope)
    for row in rows or []:
        try:
            row_index = int(getattr(row, "row_index", -1))
        except (TypeError, ValueError):
            row_index = -1
        if row_index in header_rows:
            continue
        lookup = _row_cell_lookup(row)
        active: list[int] = []
        for idx in resolved_scope:
            cell = lookup.get(idx)
            value = _clean_text(getattr(cell, "raw_text", "")) if cell is not None else ""
            if not value:
                continue
            active.append(idx)
            try:
                span_width = int((getattr(cell, "metadata", {}) or {}).get("column_span") or 1)
            except (TypeError, ValueError):
                span_width = 1
            if span_width > 1:
                span_targets = [candidate for candidate in range(idx, idx + span_width) if candidate in scope_set]
                if len(span_targets) > 1:
                    merged_span_evidence = True
        if active:
            rows_with_scope_values += 1
        if len(active) == 1:
            sparse_row_count += 1

    sparse_fraction = (
        float(sparse_row_count) / float(rows_with_scope_values)
        if rows_with_scope_values > 0
        else 0.0
    )
    sparse_row_expansion = bool(
        sparse_row_count >= 2
        and (
            sparse_fraction >= 0.4
            or (sparse_fraction >= 0.25 and merged_span_evidence)
        )
    )
    return ScopeTableProfile(
        scope_indices=resolved_scope,
        rows_with_scope_values=rows_with_scope_values,
        sparse_row_count=sparse_row_count,
        sparse_fraction=round(sparse_fraction, 4),
        sparse_row_expansion=sparse_row_expansion,
        merged_span_evidence=merged_span_evidence,
    )


def infer_scope_for_row(
    *,
    row: Any,
    table_profile: ScopeTableProfile,
) -> ScopeDecision | None:
    scope_indices = list(table_profile.scope_indices or [])
    if not scope_indices:
        return None

    scope_set = set(scope_indices)
    first_scope = min(scope_indices)
    last_scope = max(scope_indices)
    lookup = _row_cell_lookup(row)
    non_empty: list[tuple[int, str, Any]] = []
    for idx in scope_indices:
        cell = lookup.get(idx)
        value = _clean_text(getattr(cell, "raw_text", "")) if cell is not None else ""
        if value:
            non_empty.append((idx, value, cell))
    if not non_empty:
        return None

    detected_indices = sorted(idx for idx, _value, _cell in non_empty)

    # 1) Explicit span (highest precedence).
    best_span: list[int] = []
    for idx, _value, cell in non_empty:
        try:
            span_width = int((getattr(cell, "metadata", {}) or {}).get("column_span") or 1)
        except (TypeError, ValueError):
            span_width = 1
        if span_width <= 1:
            continue
        span_targets = [candidate for candidate in range(idx, idx + span_width) if candidate in scope_set]
        if len(span_targets) > len(best_span):
            best_span = span_targets
    if len(best_span) > 1:
        return ScopeDecision(
            applies_to_indices=best_span,
            detected_indices=detected_indices,
            reason=SCOPE_REASON_EXPLICIT_SPAN,
            confidence=0.93,
        )

    normalized_values = [
        _normalized_text(value)
        for _idx, value, _cell in non_empty
        if _normalized_text(value)
    ]
    unique_values = set(normalized_values)

    # 2) Repeated-value span.
    explicit_indices = detected_indices
    repeated_partial = (
        len(explicit_indices) >= 2
        and len(unique_values) == 1
        and len(explicit_indices) < len(scope_indices)
    )
    partial_interior = bool(
        explicit_indices
        and min(explicit_indices) > first_scope
        and max(explicit_indices) < last_scope
    )
    if repeated_partial and (
        table_profile.merged_span_evidence
        or table_profile.sparse_row_expansion
        or partial_interior
    ):
        confidence = 0.86 if table_profile.merged_span_evidence else 0.82
        return ScopeDecision(
            applies_to_indices=list(scope_indices),
            detected_indices=explicit_indices,
            reason=SCOPE_REASON_REPEATED_VALUE_SPAN,
            confidence=confidence,
        )

    # 3) Sparse expansion.
    if len(explicit_indices) == 1 and table_profile.sparse_row_expansion:
        confidence = 0.8 if table_profile.merged_span_evidence else 0.76
        return ScopeDecision(
            applies_to_indices=list(scope_indices),
            detected_indices=explicit_indices,
            reason=SCOPE_REASON_SPARSE_EXPANSION,
            confidence=confidence,
        )

    # 4) Abstain (use explicit cells without synthetic expansion).
    abstain_confidence = 0.58 if len(explicit_indices) > 1 else 0.48
    return ScopeDecision(
        applies_to_indices=explicit_indices,
        detected_indices=explicit_indices,
        reason=SCOPE_REASON_ABSTAIN,
        confidence=abstain_confidence,
    )
