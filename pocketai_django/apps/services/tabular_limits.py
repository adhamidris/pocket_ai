from __future__ import annotations

import dataclasses
import logging
from typing import Any, Mapping

from django.conf import settings
from django.core.cache import cache

from apps.accounts.models import BusinessProfile, KnowledgeUpload
from apps.services.mcp.types import ToolRateLimitExceeded


logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class ToolRateLimit:
    calls_per_minute: int | None
    window_seconds: int = 60
    scope: str = "business"  # business | upload


@dataclasses.dataclass(frozen=True)
class DatasetQueryLimits:
    max_seconds: float
    max_sort_window: int
    max_groups: int
    max_rows_returned: int
    max_columns_returned: int
    max_columns_returned_exact: int
    default_columns: int
    cell_value_chars: int
    rate_limit: ToolRateLimit


@dataclasses.dataclass(frozen=True)
class TableAggregateLimits:
    max_rows_returned: int
    max_columns_returned: int
    rate_limit: ToolRateLimit


@dataclasses.dataclass(frozen=True)
class TabularToolLimits:
    dataset_query: DatasetQueryLimits
    table_aggregate: TableAggregateLimits


def _coerce_int(value: object) -> int | None:
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


def _coerce_float(value: object) -> float | None:
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


def _override_value(
    upload: KnowledgeUpload | None,
    business_profile: BusinessProfile,
    key: str,
    default: object,
) -> object:
    upload_meta = getattr(upload, "metadata", None) if upload else None
    upload_overrides = upload_meta.get("tabular_limits") if isinstance(upload_meta, dict) else None
    if isinstance(upload_overrides, Mapping) and key in upload_overrides:
        return upload_overrides.get(key)

    biz_meta = getattr(business_profile, "metadata", None)
    overrides_key = getattr(settings, "RAG_BUSINESS_OVERRIDE_KEY", "rag_overrides")
    biz_overrides = biz_meta.get(overrides_key) if isinstance(biz_meta, dict) else None
    if isinstance(biz_overrides, Mapping) and key in biz_overrides:
        return biz_overrides.get(key)
    return default


def resolve_tabular_tool_limits(
    *,
    business_profile: BusinessProfile,
    upload: KnowledgeUpload | None = None,
) -> TabularToolLimits:
    """
    Shared policy layer for tabular tools (dataset_query + table_aggregate).

    Resolution order:
      1) Per-upload overrides: `KnowledgeUpload.metadata["tabular_limits"]`
      2) Per-business overrides: `BusinessProfile.metadata[RAG_BUSINESS_OVERRIDE_KEY]`
      3) Django settings defaults / env vars
    """

    window_seconds = _coerce_int(_override_value(upload, business_profile, "tool_rate_limit_window_seconds", None))
    if not window_seconds:
        window_seconds = int(getattr(settings, "MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
    window_seconds = max(10, min(600, window_seconds))

    dataset_calls = _coerce_int(_override_value(upload, business_profile, "dataset_query_calls_per_minute", None))
    if dataset_calls is None:
        dataset_calls = _coerce_int(getattr(settings, "MCP_DATASET_QUERY_CALLS_PER_MINUTE", 30))
    dataset_calls = None if not dataset_calls or dataset_calls <= 0 else max(1, dataset_calls)

    table_calls = _coerce_int(_override_value(upload, business_profile, "table_aggregate_calls_per_minute", None))
    if table_calls is None:
        table_calls = _coerce_int(getattr(settings, "MCP_TABLE_AGGREGATE_CALLS_PER_MINUTE", 60))
    table_calls = None if not table_calls or table_calls <= 0 else max(1, table_calls)

    max_seconds = _coerce_float(_override_value(upload, business_profile, "dataset_query_max_seconds", None))
    if max_seconds is None:
        max_seconds = float(getattr(settings, "DATASET_QUERY_MAX_SECONDS", 2.5) or 2.5)
    if max_seconds <= 0:
        max_seconds = 2.5
    max_seconds = max(0.2, min(30.0, max_seconds))

    max_sort_window = _coerce_int(_override_value(upload, business_profile, "dataset_query_max_sort_window", None))
    if max_sort_window is None:
        max_sort_window = int(getattr(settings, "DATASET_QUERY_MAX_SORT_WINDOW", 500) or 500)
    max_sort_window = max(50, min(5000, max_sort_window))

    max_groups = _coerce_int(_override_value(upload, business_profile, "dataset_query_max_groups", None))
    if max_groups is None:
        max_groups = int(getattr(settings, "DATASET_QUERY_MAX_GROUPS", 5000) or 5000)
    max_groups = max(100, min(20000, max_groups))

    max_rows = _coerce_int(_override_value(upload, business_profile, "dataset_query_max_rows_returned", None))
    if max_rows is None:
        max_rows = _coerce_int(getattr(settings, "DATASET_QUERY_MAX_ROWS_RETURNED", 50))
    max_rows = 50 if max_rows is None else max(1, min(50, max_rows))

    max_cols = _coerce_int(_override_value(upload, business_profile, "dataset_query_max_columns_returned", None))
    if max_cols is None:
        max_cols = _coerce_int(getattr(settings, "DATASET_QUERY_MAX_COLUMNS_RETURNED", 12))
    max_cols = 12 if max_cols is None else max(3, min(50, max_cols))

    max_cols_exact = _coerce_int(
        _override_value(upload, business_profile, "dataset_query_max_columns_returned_exact", None)
    )
    if max_cols_exact is None:
        max_cols_exact = _coerce_int(getattr(settings, "DATASET_QUERY_MAX_COLUMNS_RETURNED_EXACT", 50))
    max_cols_exact = max_cols if max_cols_exact is None else max(max_cols, min(50, max_cols_exact))

    default_cols = _coerce_int(_override_value(upload, business_profile, "dataset_query_default_columns", None))
    if default_cols is None:
        default_cols = int(getattr(settings, "DATASET_QUERY_DEFAULT_COLUMNS", 8) or 8)
    default_cols = max(3, min(20, default_cols))
    default_cols = min(default_cols, max_cols)

    cell_value_chars = _coerce_int(_override_value(upload, business_profile, "dataset_query_cell_value_chars", None))
    if cell_value_chars is None:
        cell_value_chars = int(getattr(settings, "DATASET_QUERY_CELL_VALUE_CHARS", 160) or 160)
    cell_value_chars = max(40, min(400, cell_value_chars))

    table_max_rows = _coerce_int(_override_value(upload, business_profile, "table_aggregate_max_rows_returned", None))
    if table_max_rows is None:
        table_max_rows = _coerce_int(getattr(settings, "TABLE_AGGREGATE_MAX_ROWS_RETURNED", 200))
    table_max_rows = 200 if table_max_rows is None else max(1, min(200, table_max_rows))

    table_max_cols = _coerce_int(_override_value(upload, business_profile, "table_aggregate_max_columns_returned", None))
    if table_max_cols is None:
        table_max_cols = _coerce_int(getattr(settings, "TABLE_AGGREGATE_MAX_COLUMNS_RETURNED", 50))
    table_max_cols = 50 if table_max_cols is None else max(3, min(200, table_max_cols))

    return TabularToolLimits(
        dataset_query=DatasetQueryLimits(
            max_seconds=max_seconds,
            max_sort_window=max_sort_window,
            max_groups=max_groups,
            max_rows_returned=max_rows,
            max_columns_returned=max_cols,
            max_columns_returned_exact=max_cols_exact,
            default_columns=default_cols,
            cell_value_chars=cell_value_chars,
            rate_limit=ToolRateLimit(
                calls_per_minute=dataset_calls,
                window_seconds=window_seconds,
                scope="business",
            ),
        ),
        table_aggregate=TableAggregateLimits(
            max_rows_returned=table_max_rows,
            max_columns_returned=table_max_cols,
            rate_limit=ToolRateLimit(
                calls_per_minute=table_calls,
                window_seconds=window_seconds,
                scope="business",
            ),
        ),
    )


def enforce_tool_rate_limit(
    *,
    business_profile: BusinessProfile,
    tool: str,
    rate_limit: ToolRateLimit,
    upload: KnowledgeUpload | None = None,
) -> None:
    if bool(getattr(settings, "MCP_DISABLE_TOOL_RATE_LIMITS", False)):
        return

    calls_per_minute = rate_limit.calls_per_minute
    if calls_per_minute is None or calls_per_minute <= 0:
        return
    window_seconds = max(10, int(rate_limit.window_seconds or 60))

    key = f"mcp:rate:{tool}:{business_profile.id}"
    if rate_limit.scope == "upload" and upload is not None:
        key += f":{upload.id}"

    try:
        current = cache.get(key)
        if current is None:
            cache.set(key, 1, timeout=window_seconds)
            return
        try:
            new_total = cache.incr(key)
        except Exception:
            new_total = int(current) + 1
            cache.set(key, new_total, timeout=window_seconds)
        if int(new_total) > calls_per_minute:
            raise ToolRateLimitExceeded(
                f"Rate limit exceeded for {tool}. Try again in a moment or narrow the request."
            )
    except ToolRateLimitExceeded:
        raise
    except Exception:
        # Rate limiting must never break the request flow.
        logger.exception("tabular.rate_limit.failed tool=%s business=%s", tool, business_profile.id)
        return
