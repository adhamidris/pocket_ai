from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence

from django.conf import settings

DEFAULT_NULL_TOKENS = {
    "",
    "null",
    "n/a",
    "na",
    "nan",
    "#n/a",
    "#ref!",
    "#div/0!",
    "undefined",
}


def _canonical(value: str) -> str:
    return value.strip().lower()


def _canonical_sheet(value: str) -> str:
    return value.strip().lower()


@dataclass(frozen=True)
class TableNormalizationPolicy:
    enabled: bool
    null_tokens: set[str] = field(default_factory=set)
    drop_empty_columns: bool = True
    sheet_whitelist: set[str] = field(default_factory=set)
    sheet_blacklist: set[str] = field(default_factory=set)
    policy_version: str = "v1"


@dataclass
class SheetNormalizationDiagnostics:
    sheet_name: str
    rows_dropped: int = 0
    columns_trimmed: int = 0
    tokens_replaced: int = 0
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class NormalizedSheet:
    sheet_name: str
    column_schema: list[str]
    rows: list[list[str]]
    diagnostics: SheetNormalizationDiagnostics


def resolve_normalization_policy(upload: Any | None) -> TableNormalizationPolicy:
    """
    Resolve normalization behavior by combining settings, business metadata, and upload metadata.
    """

    base_enabled = bool(getattr(settings, "INGEST_NORMALIZE_TABLES", True))
    policy_version = getattr(settings, "INGEST_NORMALIZATION_POLICY_VERSION", "v1")

    business_meta: Mapping[str, Any] | None = None
    upload_meta: Mapping[str, Any] | None = None

    if upload is not None:
        upload_meta = getattr(upload, "metadata", None)
        business = getattr(upload, "business_profile", None)
        business_meta = getattr(business, "metadata", None)

    def _extract_policy(source: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if not isinstance(source, Mapping):
            return {}
        payload = source.get("table_policy")
        return payload if isinstance(payload, Mapping) else {}

    business_policy = dict(_extract_policy(business_meta))
    upload_policy = dict(_extract_policy(upload_meta))

    enabled = base_enabled
    if "enable_normalization" in business_policy:
        enabled = bool(business_policy["enable_normalization"])
    if "enable_normalization" in upload_policy:
        enabled = bool(upload_policy["enable_normalization"])

    def _token_set(raw: Iterable[str]) -> set[str]:
        normalized: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            canonical = _canonical(item)
            if canonical:
                normalized.add(canonical)
        return normalized

    tokens = set(DEFAULT_NULL_TOKENS)
    extra_tokens = getattr(settings, "INGEST_NORMALIZATION_NULL_TOKENS", None)
    if isinstance(extra_tokens, (list, tuple, set)):
        tokens.update(_token_set(extra_tokens))
    tokens.update(_token_set(business_policy.get("null_tokens") or []))
    tokens.update(_token_set(upload_policy.get("null_tokens") or []))

    def _sheet_set(raw: Iterable[str]) -> set[str]:
        normalized: set[str] = set()
        for item in raw:
            if not isinstance(item, str):
                continue
            canonical = _canonical_sheet(item)
            if canonical:
                normalized.add(canonical)
        return normalized

    whitelist = _sheet_set(business_policy.get("sheet_whitelist") or [])
    upload_whitelist = _sheet_set(upload_policy.get("sheet_whitelist") or [])
    if upload_whitelist:
        whitelist = upload_whitelist
    blacklist = _sheet_set(business_policy.get("sheet_blacklist") or [])
    upload_blacklist = _sheet_set(upload_policy.get("sheet_blacklist") or [])
    if upload_blacklist:
        blacklist = upload_blacklist

    drop_empty_columns = True
    if "drop_empty_columns" in business_policy:
        drop_empty_columns = bool(business_policy["drop_empty_columns"])
    if "drop_empty_columns" in upload_policy:
        drop_empty_columns = bool(upload_policy["drop_empty_columns"])
    if not enabled:
        drop_empty_columns = False

    return TableNormalizationPolicy(
        enabled=enabled,
        null_tokens=tokens,
        drop_empty_columns=drop_empty_columns,
        sheet_whitelist=whitelist,
        sheet_blacklist=blacklist,
        policy_version=str(policy_version or "v1"),
    )


def sheet_is_allowed(sheet_name: str, policy: TableNormalizationPolicy) -> bool:
    canonical = _canonical_sheet(sheet_name or "")
    if policy.sheet_blacklist and canonical in policy.sheet_blacklist:
        return False
    if policy.sheet_whitelist and canonical not in policy.sheet_whitelist:
        return False
    return True


def normalize_sheet_rows(
    raw_rows: Sequence[Sequence[Any]] | Iterable[Sequence[Any]],
    *,
    sheet_name: str,
    policy: TableNormalizationPolicy,
) -> NormalizedSheet:
    diagnostics = SheetNormalizationDiagnostics(sheet_name=sheet_name)
    normalized_rows: list[list[str]] = []

    for raw_row in raw_rows:
        normalized_row, replaced, has_values = _normalize_row(raw_row, policy)
        diagnostics.tokens_replaced += replaced
        if not normalized_row:
            continue
        if not normalized_rows and not has_values:
            diagnostics.rows_dropped += 1
            continue
        if normalized_rows and not has_values:
            diagnostics.rows_dropped += 1
            continue
        normalized_rows.append(normalized_row)

    if not normalized_rows:
        diagnostics.skipped = True
        diagnostics.skip_reason = diagnostics.skip_reason or "empty"
        return NormalizedSheet(sheet_name=sheet_name, column_schema=[], rows=[], diagnostics=diagnostics)

    header = normalized_rows[0]
    data_rows = normalized_rows[1:]

    column_count = max(len(row) for row in normalized_rows)
    columns_to_keep: list[int] = []
    column_schema: list[str] = []

    for idx in range(column_count):
        header_value = header[idx] if idx < len(header) else ""
        column_values = [row[idx] if idx < len(row) else "" for row in data_rows]
        should_drop = False
        if policy.drop_empty_columns and not header_value and not any(column_values):
            should_drop = True
        if should_drop:
            diagnostics.columns_trimmed += 1
            continue
        columns_to_keep.append(idx)
        column_schema.append(header_value or f"column_{len(column_schema) + 1}")

    if not columns_to_keep:
        diagnostics.skipped = True
        diagnostics.skip_reason = diagnostics.skip_reason or "empty_columns"
        return NormalizedSheet(sheet_name=sheet_name, column_schema=[], rows=[], diagnostics=diagnostics)

    trimmed_rows: list[list[str]] = []
    for row in data_rows:
        trimmed_rows.append([row[idx] if idx < len(row) else "" for idx in columns_to_keep])

    diagnostics.skipped = False
    diagnostics.skip_reason = None
    return NormalizedSheet(
        sheet_name=sheet_name,
        column_schema=column_schema,
        rows=trimmed_rows,
        diagnostics=diagnostics,
    )


def summarize_normalization(policy: TableNormalizationPolicy, diagnostics: Sequence[SheetNormalizationDiagnostics]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "enabled": bool(policy.enabled),
        "policy_version": policy.policy_version,
    }
    if not policy.enabled:
        return summary

    total_rows = sum(item.rows_dropped for item in diagnostics)
    total_columns = sum(item.columns_trimmed for item in diagnostics)
    total_tokens = sum(item.tokens_replaced for item in diagnostics)
    if total_rows:
        summary["rows_dropped"] = {
            "total": total_rows,
            "by_sheet": {
                item.sheet_name: item.rows_dropped for item in diagnostics if item.rows_dropped
            },
        }
    if total_columns:
        summary["columns_trimmed"] = {
            "total": total_columns,
            "by_sheet": {
                item.sheet_name: item.columns_trimmed for item in diagnostics if item.columns_trimmed
            },
        }
    if total_tokens:
        summary["tokens_replaced"] = total_tokens

    skipped = [
        item.sheet_name
        for item in diagnostics
        if item.skipped and item.skip_reason in {"empty", "empty_columns"}
    ]
    if skipped:
        summary["empty_sheets_skipped"] = skipped
    policy_skipped = [
        item.sheet_name for item in diagnostics if item.skipped and item.skip_reason == "policy"
    ]
    if policy_skipped:
        summary["policy_skipped"] = policy_skipped

    summary["null_tokens"] = sorted(policy.null_tokens)
    return summary


def _normalize_row(row: Sequence[Any], policy: TableNormalizationPolicy) -> tuple[list[str], int, bool]:
    normalized: list[str] = []
    replaced_tokens = 0
    has_values = False
    for value in row:
        normalized_value, replaced, keep_flag = _normalize_cell_value(value, policy)
        if replaced:
            replaced_tokens += 1
        if keep_flag and normalized_value:
            has_values = True
        normalized.append(normalized_value)
    return normalized, replaced_tokens, has_values


def _normalize_cell_value(value: Any, policy: TableNormalizationPolicy) -> tuple[str, bool, bool]:
    """
    Return (normalized_value, replaced_token, has_value_flag).
    """
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), False, True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return "", True, False
        return (str(value), False, True)
    if value is None:
        return "", True, False
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="ignore")
        except Exception:
            value = ""
    text = str(value)
    trimmed = text.strip()
    if not trimmed:
        return "", bool(text), False
    canonical = _canonical(trimmed)
    if policy.enabled and canonical in policy.null_tokens:
        return "", True, False
    return trimmed, False, True
