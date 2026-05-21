from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

QUALITY_GATE_THRESHOLD_DEFAULTS: dict[str, float] = {
    "min_row_recall": 0.99,
    "min_row_order_stability": 0.95,
    "min_scope_f1": 0.95,
    "min_critical_value_coverage": 0.95,
    "min_scope_metadata_coverage": 1.0,
}

PDF_PORTFOLIO_EXPECTED_TABLE_MODES: frozenset[str] = frozenset({"none", "some", "range"})


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    for entry in value:
        token = str(entry or "").strip()
        if token:
            cleaned.append(token)
    return cleaned


def _chunk_bucket(metadata: Mapping[str, Any]) -> str:
    source = str(metadata.get("content_source") or "None")
    role = str(metadata.get("table_chunk_role") or "None")
    priority = str(metadata.get("chunk_priority") or "None")
    return f"{source}/{role}/{priority}"


def _table_schema_keys(column_schema: Any) -> list[str]:
    if not isinstance(column_schema, list):
        return []
    keys: list[str] = []
    for col in column_schema:
        if isinstance(col, Mapping):
            key = str(col.get("key") or col.get("name") or col.get("label") or "").strip()
        else:
            key = str(col or "").strip()
        if key:
            keys.append(key)
    return keys


def _row_focus_filter(
    rows: Sequence[dict[str, Any]],
    *,
    focus_row_start: int | None = None,
    focus_row_end: int | None = None,
) -> list[dict[str, Any]]:
    if focus_row_start is None and focus_row_end is None:
        return list(rows)
    result: list[dict[str, Any]] = []
    for row in rows:
        idx = _safe_int(row.get("table_row_index"))
        if idx is None:
            continue
        if focus_row_start is not None and idx < focus_row_start:
            continue
        if focus_row_end is not None and idx > focus_row_end:
            continue
        result.append(row)
    return result


def _scope_metrics(row_chunks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(row_chunks)
    multi_scope = 0
    ambiguous = 0
    inferred = 0
    explicit = 0
    histogram: Counter[int] = Counter()
    for row in row_chunks:
        applies = _row_scope_columns(row)
        card = len(applies)
        histogram[card] += 1
        if card > 1:
            multi_scope += 1
        if card <= 1:
            ambiguous += 1
        mode = _row_scope_reason(row).lower()
        if mode.startswith("inferred") or mode in {
            "scope_repeated_value_span",
            "scope_sparse_expansion",
            "scope_edge_completion",
        }:
            inferred += 1
        if mode in {"scope_explicit_span", "explicit_span"}:
            explicit += 1
    return {
        "row_chunk_count": total,
        "multi_scope_count": multi_scope,
        "multi_scope_rate": round(multi_scope / max(1, total), 4),
        "ambiguous_scope_count": ambiguous,
        "ambiguous_scope_rate": round(ambiguous / max(1, total), 4),
        "inferred_scope_count": inferred,
        "inferred_scope_rate": round(inferred / max(1, total), 4),
        "explicit_scope_count": explicit,
        "explicit_scope_rate": round(explicit / max(1, total), 4),
        "scope_cardinality_histogram": {str(k): int(v) for k, v in sorted(histogram.items(), key=lambda item: item[0])},
    }


def _normalized_token(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _row_scope_columns(row: Mapping[str, Any]) -> list[str]:
    return _clean_list(row.get("inferred_scope_columns"))


def _row_scope_reason(row: Mapping[str, Any]) -> str:
    return str(row.get("scope_reason") or "").strip()


def _row_scope_confidence(row: Mapping[str, Any]) -> float | None:
    confidence = _safe_float(row.get("table_row_scope_confidence"))
    if confidence is not None:
        return confidence
    return _safe_float(row.get("scope_confidence"))


def _normalized_scope_set(row: Mapping[str, Any]) -> set[str]:
    return {
        token
        for token in (_normalized_token(item) for item in _row_scope_columns(row))
        if token
    }


def _row_has_scope_metadata(row: Mapping[str, Any]) -> bool:
    mode = _normalized_token(_row_scope_reason(row))
    confidence = _row_scope_confidence(row)
    return bool(mode) and confidence is not None


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    current = [0] * (len(right) + 1)
    for left_token in left:
        for idx, right_token in enumerate(right, start=1):
            if left_token == right_token:
                current[idx] = previous[idx - 1] + 1
            else:
                current[idx] = max(previous[idx], current[idx - 1])
        previous, current = current, [0] * (len(right) + 1)
    return previous[-1]


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if float(denominator) <= 0:
        return 1.0
    return float(numerator) / float(denominator)


def _build_quality_gate_metrics(
    *,
    baseline_rows: Sequence[dict[str, Any]],
    candidate_rows: Sequence[dict[str, Any]],
    baseline_map: Mapping[str, dict[str, Any]],
    candidate_map: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    baseline_keys = set(baseline_map.keys())
    candidate_keys = set(candidate_map.keys())
    shared_keys = baseline_keys & candidate_keys

    row_recall = _safe_ratio(len(shared_keys), len(baseline_keys))
    row_precision = _safe_ratio(len(shared_keys), len(candidate_keys))

    baseline_order = [key for key in baseline_map.keys() if key in shared_keys]
    candidate_order = [key for key in candidate_map.keys() if key in shared_keys]
    lcs = _lcs_length(baseline_order, candidate_order)
    row_order_stability = _safe_ratio(lcs, len(baseline_order))

    baseline_scope_pairs = {
        (row_key, label)
        for row_key, row in baseline_map.items()
        for label in _normalized_scope_set(row)
    }
    candidate_scope_pairs = {
        (row_key, label)
        for row_key, row in candidate_map.items()
        for label in _normalized_scope_set(row)
    }
    scope_tp = len(baseline_scope_pairs & candidate_scope_pairs)
    scope_fp = len(candidate_scope_pairs - baseline_scope_pairs)
    scope_fn = len(baseline_scope_pairs - candidate_scope_pairs)
    scope_precision = _safe_ratio(scope_tp, scope_tp + scope_fp)
    scope_recall = _safe_ratio(scope_tp, scope_tp + scope_fn)
    if (scope_precision + scope_recall) <= 0:
        scope_f1 = 1.0
    else:
        scope_f1 = (2.0 * scope_precision * scope_recall) / (scope_precision + scope_recall)

    baseline_critical_values = {
        row_key: _normalized_token(row.get("table_row_fee_value"))
        for row_key, row in baseline_map.items()
        if _normalized_token(row.get("table_row_fee_value"))
    }
    critical_total = len(baseline_critical_values)
    critical_present = 0
    critical_matched = 0
    for row_key, baseline_value in baseline_critical_values.items():
        candidate_row = candidate_map.get(row_key)
        if not candidate_row:
            continue
        candidate_value = _normalized_token(candidate_row.get("table_row_fee_value"))
        if candidate_value:
            critical_present += 1
        if candidate_value == baseline_value:
            critical_matched += 1
    critical_value_presence_coverage = _safe_ratio(critical_present, critical_total)
    critical_value_coverage = _safe_ratio(critical_matched, critical_total)

    candidate_scope_rows = [
        row
        for row in candidate_rows
        if _row_scope_columns(row)
    ]
    scope_rows_count = len(candidate_scope_rows)
    scope_metadata_ready = sum(1 for row in candidate_scope_rows if _row_has_scope_metadata(row))
    scope_metadata_coverage = _safe_ratio(scope_metadata_ready, scope_rows_count)

    return {
        "row_recall": round(row_recall, 4),
        "row_precision": round(row_precision, 4),
        "row_order_stability": round(row_order_stability, 4),
        "scope_precision": round(scope_precision, 4),
        "scope_recall": round(scope_recall, 4),
        "scope_f1": round(scope_f1, 4),
        "critical_value_total": int(critical_total),
        "critical_value_present_count": int(critical_present),
        "critical_value_matched_count": int(critical_matched),
        "critical_value_presence_coverage": round(critical_value_presence_coverage, 4),
        "critical_value_coverage": round(critical_value_coverage, 4),
        "scope_rows_count": int(scope_rows_count),
        "scope_metadata_ready_count": int(scope_metadata_ready),
        "scope_metadata_coverage": round(scope_metadata_coverage, 4),
    }


def quality_gate_thresholds(
    *,
    min_row_recall: float | None = None,
    min_row_order_stability: float | None = None,
    min_scope_f1: float | None = None,
    min_critical_value_coverage: float | None = None,
    min_scope_metadata_coverage: float | None = None,
) -> dict[str, float]:
    resolved = dict(QUALITY_GATE_THRESHOLD_DEFAULTS)
    overrides = {
        "min_row_recall": min_row_recall,
        "min_row_order_stability": min_row_order_stability,
        "min_scope_f1": min_scope_f1,
        "min_critical_value_coverage": min_critical_value_coverage,
        "min_scope_metadata_coverage": min_scope_metadata_coverage,
    }
    for key, value in overrides.items():
        if value is None:
            continue
        try:
            resolved[key] = float(value)
        except Exception:
            continue
    return resolved


def evaluate_quality_gate(
    report: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = _metadata_dict(report.get("quality_gate_metrics"))
    resolved_thresholds = quality_gate_thresholds(
        min_row_recall=_safe_float((thresholds or {}).get("min_row_recall")),
        min_row_order_stability=_safe_float((thresholds or {}).get("min_row_order_stability")),
        min_scope_f1=_safe_float((thresholds or {}).get("min_scope_f1")),
        min_critical_value_coverage=_safe_float((thresholds or {}).get("min_critical_value_coverage")),
        min_scope_metadata_coverage=_safe_float((thresholds or {}).get("min_scope_metadata_coverage")),
    )
    failed_checks: list[str] = []

    row_recall = _safe_float(metrics.get("row_recall")) or 0.0
    if row_recall < float(resolved_thresholds["min_row_recall"]):
        failed_checks.append("row_recall")

    row_order = _safe_float(metrics.get("row_order_stability")) or 0.0
    if row_order < float(resolved_thresholds["min_row_order_stability"]):
        failed_checks.append("row_order_stability")

    scope_f1 = _safe_float(metrics.get("scope_f1")) or 0.0
    if scope_f1 < float(resolved_thresholds["min_scope_f1"]):
        failed_checks.append("scope_f1")

    critical_value_coverage = _safe_float(metrics.get("critical_value_coverage")) or 0.0
    if critical_value_coverage < float(resolved_thresholds["min_critical_value_coverage"]):
        failed_checks.append("critical_value_coverage")

    scope_metadata_coverage = _safe_float(metrics.get("scope_metadata_coverage")) or 0.0
    if scope_metadata_coverage < float(resolved_thresholds["min_scope_metadata_coverage"]):
        failed_checks.append("scope_metadata_coverage")

    regressions = [str(item) for item in (report.get("regressions") or []) if str(item).strip()]
    passed = not failed_checks and not regressions
    return {
        "passed": bool(passed),
        "thresholds": resolved_thresholds,
        "metrics": metrics,
        "failed_checks": failed_checks,
        "regressions": regressions,
    }
