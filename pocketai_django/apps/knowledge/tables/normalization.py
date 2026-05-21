from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from django.conf import settings
from core.otel import otel_trace

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

TRACER = otel_trace.get_tracer(__name__)


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
    drop_hidden_template_rows: bool = True
    drop_zero_heavy_rows: bool = True
    drop_placeholder_rows: bool = True
    drop_scaffold_rows: bool = True
    drop_scaffold_columns: bool = True
    policy_version: str = "v1"


@dataclass
class SheetNormalizationDiagnostics:
    sheet_name: str
    rows_dropped: int = 0
    columns_trimmed: int = 0
    tokens_replaced: int = 0
    hidden_rows_dropped: int = 0
    zero_heavy_rows_dropped: int = 0
    placeholder_rows_dropped: int = 0
    scaffold_rows_dropped: int = 0
    scaffold_columns_trimmed: int = 0
    skipped: bool = False
    skip_reason: str | None = None


@dataclass(frozen=True)
class SpreadsheetRowInput:
    values: Sequence[Any]
    row_index: int | None = None
    hidden: bool = False


@dataclass(frozen=True)
class NormalizedRowMetadata:
    source_row_index: int | None = None
    hidden: bool = False
    row_kind: str = "data"


@dataclass
class NormalizedSheet:
    sheet_name: str
    column_schema: list[str]
    rows: list[list[str]]
    diagnostics: SheetNormalizationDiagnostics
    row_metadata: list[NormalizedRowMetadata] = field(default_factory=list)


from apps.knowledge.tables.normalization_rows import (  # noqa: E402
    _classify_spreadsheet_row,
    _coerce_row_input,
    _is_scaffold_column,
    _normalize_row,
)


def resolve_normalization_policy(upload: Any | None) -> TableNormalizationPolicy:
    """
    Resolve normalization behavior by combining settings, business metadata, and upload metadata.
    """

    with TRACER.start_as_current_span("ingest.table.resolve_policy") as span:
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

        drop_hidden_template_rows = bool(
            upload_policy.get(
                "drop_hidden_template_rows",
                business_policy.get(
                    "drop_hidden_template_rows",
                    getattr(settings, "INGEST_NORMALIZATION_DROP_HIDDEN_TEMPLATE_ROWS", True),
                ),
            )
        )
        drop_zero_heavy_rows = bool(
            upload_policy.get(
                "drop_zero_heavy_rows",
                business_policy.get(
                    "drop_zero_heavy_rows",
                    getattr(settings, "INGEST_NORMALIZATION_DROP_ZERO_HEAVY_ROWS", True),
                ),
            )
        )
        drop_placeholder_rows = bool(
            upload_policy.get(
                "drop_placeholder_rows",
                business_policy.get(
                    "drop_placeholder_rows",
                    getattr(settings, "INGEST_NORMALIZATION_DROP_PLACEHOLDER_ROWS", True),
                ),
            )
        )
        drop_scaffold_rows = bool(
            upload_policy.get(
                "drop_scaffold_rows",
                business_policy.get(
                    "drop_scaffold_rows",
                    getattr(settings, "INGEST_NORMALIZATION_DROP_SCAFFOLD_ROWS", True),
                ),
            )
        )
        drop_scaffold_columns = bool(
            upload_policy.get(
                "drop_scaffold_columns",
                business_policy.get(
                    "drop_scaffold_columns",
                    getattr(settings, "INGEST_NORMALIZATION_DROP_SCAFFOLD_COLUMNS", True),
                ),
            )
        )

        if span.is_recording():
            span.set_attribute("ingest.table.enabled", enabled)
            span.set_attribute("ingest.table.whitelist", len(whitelist))
            span.set_attribute("ingest.table.blacklist", len(blacklist))

        return TableNormalizationPolicy(
            enabled=enabled,
            null_tokens=tokens,
            drop_empty_columns=drop_empty_columns,
            sheet_whitelist=whitelist,
            sheet_blacklist=blacklist,
            drop_hidden_template_rows=drop_hidden_template_rows,
            drop_zero_heavy_rows=drop_zero_heavy_rows,
            drop_placeholder_rows=drop_placeholder_rows,
            drop_scaffold_rows=drop_scaffold_rows,
            drop_scaffold_columns=drop_scaffold_columns,
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
    raw_rows: Sequence[Sequence[Any] | SpreadsheetRowInput] | Iterable[Sequence[Any] | SpreadsheetRowInput],
    *,
    sheet_name: str,
    policy: TableNormalizationPolicy,
) -> NormalizedSheet:
    with TRACER.start_as_current_span("ingest.table.normalize_sheet") as span:
        diagnostics = SheetNormalizationDiagnostics(sheet_name=sheet_name)
        normalized_rows: list[list[str]] = []
        row_metadata: list[NormalizedRowMetadata] = []

        for raw_row in raw_rows:
            row_input = _coerce_row_input(raw_row)
            normalized_row, replaced, has_values = _normalize_row(row_input.values, policy)
            diagnostics.tokens_replaced += replaced
            if not normalized_row:
                continue
            if not normalized_rows and not has_values:
                diagnostics.rows_dropped += 1
                continue
            if normalized_rows and not has_values:
                diagnostics.rows_dropped += 1
                continue
            if normalized_rows:
                row_kind = _classify_spreadsheet_row(normalized_row, row_input, policy)
                if row_kind == "hidden_template_row":
                    diagnostics.rows_dropped += 1
                    diagnostics.hidden_rows_dropped += 1
                    continue
                if row_kind == "default_zero_row":
                    diagnostics.rows_dropped += 1
                    diagnostics.zero_heavy_rows_dropped += 1
                    continue
                if row_kind == "placeholder_row":
                    diagnostics.rows_dropped += 1
                    diagnostics.placeholder_rows_dropped += 1
                    continue
                if row_kind == "scaffold_row":
                    diagnostics.rows_dropped += 1
                    diagnostics.scaffold_rows_dropped += 1
                    continue
            else:
                row_kind = "header"
            normalized_rows.append(normalized_row)
            row_metadata.append(
                NormalizedRowMetadata(
                    source_row_index=row_input.row_index,
                    hidden=bool(row_input.hidden),
                    row_kind=row_kind,
                )
            )

        if not normalized_rows:
            diagnostics.skipped = True
            diagnostics.skip_reason = diagnostics.skip_reason or "empty"
            if span.is_recording():
                span.set_attribute("ingest.table.skipped", True)
            return NormalizedSheet(
                sheet_name=sheet_name,
                column_schema=[],
                rows=[],
                row_metadata=[],
                diagnostics=diagnostics,
            )

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
            if (
                not should_drop
                and policy.drop_scaffold_columns
                and _is_scaffold_column(header_value, column_values)
            ):
                should_drop = True
                diagnostics.scaffold_columns_trimmed += 1
            if should_drop:
                diagnostics.columns_trimmed += 1
                continue
            columns_to_keep.append(idx)
            column_schema.append(header_value or f"column_{len(column_schema) + 1}")

        if not columns_to_keep:
            diagnostics.skipped = True
            diagnostics.skip_reason = diagnostics.skip_reason or "empty_columns"
            if span.is_recording():
                span.set_attribute("ingest.table.skipped", True)
            return NormalizedSheet(sheet_name=sheet_name, column_schema=[], rows=[], diagnostics=diagnostics)

        trimmed_rows: list[list[str]] = []
        trimmed_row_metadata: list[NormalizedRowMetadata] = []
        for meta, row in zip(row_metadata[1:], data_rows):
            trimmed_rows.append([row[idx] if idx < len(row) else "" for idx in columns_to_keep])
            trimmed_row_metadata.append(meta)

        diagnostics.skipped = False
        diagnostics.skip_reason = None
        if span.is_recording():
            span.set_attribute("ingest.table.rows", len(trimmed_rows))
            span.set_attribute("ingest.table.columns", len(column_schema))
            span.set_attribute("ingest.table.columns_trimmed", diagnostics.columns_trimmed)
            span.set_attribute("ingest.table.rows_dropped", diagnostics.rows_dropped)
        return NormalizedSheet(
            sheet_name=sheet_name,
            column_schema=column_schema,
            rows=trimmed_rows,
            row_metadata=trimmed_row_metadata,
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
    for key, attr in (
        ("hidden_rows_dropped", "hidden_rows_dropped"),
        ("zero_heavy_rows_dropped", "zero_heavy_rows_dropped"),
        ("placeholder_rows_dropped", "placeholder_rows_dropped"),
        ("scaffold_rows_dropped", "scaffold_rows_dropped"),
    ):
        total = sum(int(getattr(item, attr, 0) or 0) for item in diagnostics)
        if total:
            summary[key] = {
                "total": total,
                "by_sheet": {
                    item.sheet_name: int(getattr(item, attr, 0) or 0)
                    for item in diagnostics
                    if int(getattr(item, attr, 0) or 0)
                },
            }
    if total_columns:
        summary["columns_trimmed"] = {
            "total": total_columns,
            "by_sheet": {
                item.sheet_name: item.columns_trimmed for item in diagnostics if item.columns_trimmed
            },
        }
    total_scaffold_columns = sum(item.scaffold_columns_trimmed for item in diagnostics)
    if total_scaffold_columns:
        summary["scaffold_columns_trimmed"] = {
            "total": total_scaffold_columns,
            "by_sheet": {
                item.sheet_name: item.scaffold_columns_trimmed
                for item in diagnostics
                if item.scaffold_columns_trimmed
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
