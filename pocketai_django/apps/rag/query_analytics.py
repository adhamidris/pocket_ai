from __future__ import annotations

import statistics
from collections import Counter
from typing import Any, Mapping, Sequence


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: int(value) for key, value in sorted(counter.items(), key=lambda item: (-item[1], item[0]))}


def build_query_analytics_report(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    status_counter: Counter[str] = Counter()
    query_type_counter: Counter[str] = Counter()
    intent_counter: Counter[str] = Counter()
    path_counter: Counter[str] = Counter()
    error_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()

    latencies: list[float] = []
    empty_results = 0

    for sample in samples:
        metrics = sample.get("metrics") if isinstance(sample, Mapping) else None
        if not isinstance(metrics, Mapping):
            continue

        status_value = str(metrics.get("status") or "unknown").strip().lower() or "unknown"
        status_counter[status_value] += 1

        query_type = str(metrics.get("query_type") or "unknown").strip().lower() or "unknown"
        query_type_counter[query_type] += 1

        source = str(metrics.get("source") or "unknown").strip().lower() or "unknown"
        source_counter[source] += 1

        intent = str(metrics.get("query_intent") or "").strip().lower()
        if intent:
            intent_counter[intent] += 1

        path_value = str(metrics.get("stage") or "").strip().lower()
        if path_value:
            path_counter[path_value] += 1

        error_code = str(metrics.get("error_code") or "").strip().lower()
        if error_code:
            error_counter[error_code] += 1

        result_count = _safe_int(metrics.get("result_count"))
        if result_count is None:
            result_count = _safe_int(metrics.get("snippet_count"))
        if result_count is not None and result_count == 0 and status_value in {"ok", "not_found", "unknown"}:
            empty_results += 1

        latency_ms = _safe_float(metrics.get("latency_ms"))
        if latency_ms is not None and latency_ms >= 0:
            latencies.append(latency_ms)

    total = sum(status_counter.values())
    total_float = float(total or 1)

    p50 = statistics.median(latencies) if latencies else None
    p95 = None
    if latencies:
        ordered = sorted(latencies)
        idx = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
        p95 = float(ordered[idx])

    status_counts = _sorted_counter(status_counter)
    status_rates = {
        key: round((float(value) / total_float), 4)
        for key, value in status_counts.items()
    }

    ok_count = status_counter.get("ok", 0)
    not_found_count = status_counter.get("not_found", 0)
    throttled_count = status_counter.get("throttled", 0)
    constraint_count = status_counter.get("constraint_error", 0)
    error_count = status_counter.get("error", 0)

    return {
        "total_queries": total,
        "status": {
            "counts": status_counts,
            "rates": status_rates,
            "ok_rate": round(ok_count / total_float, 4) if total else 0.0,
            "not_found_rate": round(not_found_count / total_float, 4) if total else 0.0,
            "throttled_rate": round(throttled_count / total_float, 4) if total else 0.0,
            "constraint_error_rate": round(constraint_count / total_float, 4) if total else 0.0,
            "error_rate": round(error_count / total_float, 4) if total else 0.0,
        },
        "empty_results": {
            "count": int(empty_results),
            "rate": round(float(empty_results) / total_float, 4) if total else 0.0,
        },
        "latency_ms": {
            "count": len(latencies),
            "avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "p50": round(float(p50), 2) if p50 is not None else None,
            "p95": round(float(p95), 2) if p95 is not None else None,
        },
        "by_query_type": _sorted_counter(query_type_counter),
        "by_intent": _sorted_counter(intent_counter),
        "by_path": _sorted_counter(path_counter),
        "by_error_code": _sorted_counter(error_counter),
        "by_source": _sorted_counter(source_counter),
    }


def format_query_analytics_report(report: Mapping[str, object]) -> str:
    lines: list[str] = []
    lines.append("RAG Query Analytics")
    lines.append(f"- total_queries: {report.get('total_queries')}")

    status = report.get("status") if isinstance(report.get("status"), Mapping) else {}
    lines.append("- status_counts:")
    for key, value in (status.get("counts") or {}).items():
        lines.append(f"  - {key}: {value}")

    lines.append("- headline_rates:")
    for key in ("ok_rate", "not_found_rate", "throttled_rate", "constraint_error_rate", "error_rate"):
        value = status.get(key)
        if value is not None:
            lines.append(f"  - {key}: {value}")

    empty = report.get("empty_results") if isinstance(report.get("empty_results"), Mapping) else {}
    lines.append(f"- empty_results: count={empty.get('count')} rate={empty.get('rate')}")

    latency = report.get("latency_ms") if isinstance(report.get("latency_ms"), Mapping) else {}
    lines.append(
        "- latency_ms: "
        f"count={latency.get('count')} avg={latency.get('avg')} p50={latency.get('p50')} p95={latency.get('p95')}"
    )

    for section_key in ("by_query_type", "by_intent", "by_path", "by_error_code", "by_source"):
        section = report.get(section_key)
        if isinstance(section, Mapping) and section:
            lines.append(f"- {section_key}:")
            for key, value in section.items():
                lines.append(f"  - {key}: {value}")

    return "\n".join(lines)
