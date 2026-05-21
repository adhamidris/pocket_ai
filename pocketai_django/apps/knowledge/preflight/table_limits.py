from __future__ import annotations

from typing import Any, Mapping

from django.conf import settings

from apps.knowledge.models import KnowledgeUpload


def _table_limits_snapshot(upload: KnowledgeUpload | None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "max_rows": max(1, int(getattr(settings, "TABLE_MAX_ROWS_DEFAULT", 5000))),
        "max_columns": int(getattr(settings, "TABLE_MAX_COLUMNS_DEFAULT", 0) or 0) or None,
        "column_whitelist": [],
        "max_rows_source": "default",
        "small_row_limit": int(getattr(settings, "RAG_TABLE_SMALL_ROW_LIMIT", 2000)),
        "large_row_limit": int(getattr(settings, "RAG_TABLE_LARGE_ROW_LIMIT", 20000)),
        "hard_row_cap": int(getattr(settings, "RAG_TABLE_MAX_HARD_CAP", 100000)),
    }
    whitelist: set[str] = set()

    def merge(source: Mapping[str, Any] | None) -> None:
        if not isinstance(source, Mapping):
            return
        rows = source.get("max_rows")
        columns = source.get("max_columns")
        raw_whitelist = source.get("column_whitelist")
        try:
            if rows is not None:
                value = int(rows)
                if value > 0:
                    config["max_rows"] = value
                    config["max_rows_source"] = "override"
        except (TypeError, ValueError):
            pass
        try:
            if columns is not None:
                value = int(columns)
                config["max_columns"] = value if value > 0 else None
        except (TypeError, ValueError):
            pass
        if isinstance(raw_whitelist, (list, tuple, set)):
            for item in raw_whitelist:
                token = str(item or "").strip()
                if token:
                    whitelist.add(token)

    business_meta = getattr(getattr(upload, "business_profile", None), "metadata", {}) if upload else {}
    upload_meta = getattr(upload, "metadata", {}) if upload else {}
    merge(business_meta.get("table_limits"))
    merge(upload_meta.get("table_limits"))

    config["column_whitelist"] = sorted(whitelist)
    return config


def _table_size_warnings(
    metrics: Mapping[str, Any],
    limits: Mapping[str, Any],
    warnings: list[str],
    recommendations: list[str],
) -> dict[str, Any]:
    row_count = (
        metrics.get("estimated_total_rows")
        or metrics.get("estimated_row_count")
        or metrics.get("row_count")
        or 0
    )
    try:
        row_count_int = int(row_count or 0)
    except (TypeError, ValueError):
        row_count_int = 0

    cap, tier, strategy = _determine_row_cap(row_count_int, limits)
    dataset_enabled = bool(getattr(settings, "DATASET_MODE_ENABLED", True))
    dataset_threshold = int(getattr(settings, "DATASET_MODE_ROW_THRESHOLD", limits.get("large_row_limit", 20000)))
    dataset_preview_rows = int(getattr(settings, "DATASET_MODE_PREVIEW_ROWS", 200))
    table_note = {
        "row_cap": cap,
        "row_tier": tier,
        "strategy": strategy,
        "dataset_mode": bool(dataset_enabled and row_count_int and row_count_int >= dataset_threshold),
    }

    if row_count_int and row_count_int > int(limits.get("large_row_limit", 20000)):
        if dataset_enabled and row_count_int >= dataset_threshold:
            warnings.append(
                f"Large table detected (~{row_count_int} rows). It will be stored in dataset mode (file-backed); only a preview is indexed for search."
            )
            recommendations.append(
                f"Expect better results when you query the dataset using an identifier column (e.g., order_id, ticket_id). Preview rows are limited to ~{dataset_preview_rows}."
            )
        else:
            warnings.append(
                f"Large table detected (~{row_count_int} rows). By default we only index the first {cap} rows."
            )
            recommendations.append(
                "If you need exact lookups across all rows, consider splitting the dataset or using a database-backed integration."
            )
    if row_count_int and row_count_int > int(limits.get("hard_row_cap", 100000)):
        warnings.append("Table exceeds hard row cap; ingestion will drop data beyond configured limits.")
    return {"table_budget": table_note}


def _determine_row_cap(row_count: int, limits: Mapping[str, Any]) -> tuple[int, str, str]:
    base_cap = max(1, int(limits.get("max_rows", 1)))
    small_limit = max(1, int(limits.get("small_row_limit", 2000)))
    large_limit = max(small_limit, int(limits.get("large_row_limit", 20000)))
    hard_cap = max(1, int(limits.get("hard_row_cap", 100000)))
    override = limits.get("max_rows_source") == "override"
    if row_count <= 0:
        return base_cap, "unknown", "default"
    if not override and row_count <= large_limit:
        tier = "small" if row_count <= small_limit else "medium"
        return row_count, tier, "full"
    tier = "large" if row_count > large_limit else "override"
    effective_cap = min(base_cap, hard_cap)
    strategy = "override" if override and tier != "large" else "capped"
    return effective_cap, tier, strategy
