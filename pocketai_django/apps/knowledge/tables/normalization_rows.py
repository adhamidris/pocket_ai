from __future__ import annotations

import math
import re
from typing import Any, Sequence

from apps.knowledge.tables.normalization import (
    SpreadsheetRowInput,
    TableNormalizationPolicy,
    _canonical,
)

ZERO_LIKE_TOKENS = {
    "0",
    "0.0",
    "0.00",
    "0%",
    "0.0%",
    "0.00%",
    "$0",
    "$0.0",
    "$0.00",
}
PLACEHOLDER_ROW_PATTERNS = (
    re.compile(r"^\s*select\b", re.IGNORECASE),
    re.compile(r"^\s*insert\b", re.IGNORECASE),
)
SCAFFOLD_PATTERNS = (
    re.compile(r"\bsandbox\b", re.IGNORECASE),
    re.compile(r"\brough work\b", re.IGNORECASE),
)
IDENTIFIER_LIKE_RE = re.compile(r"^[A-Za-z]{1,8}-\d+[A-Za-z0-9-]*$")


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


def _coerce_row_input(raw_row: Sequence[Any] | SpreadsheetRowInput) -> SpreadsheetRowInput:
    if isinstance(raw_row, SpreadsheetRowInput):
        return raw_row
    return SpreadsheetRowInput(values=raw_row)


def _classify_spreadsheet_row(
    normalized_row: Sequence[str],
    row_input: SpreadsheetRowInput,
    policy: TableNormalizationPolicy,
) -> str:
    if not isinstance(row_input, SpreadsheetRowInput):
        return "data"

    features = _spreadsheet_row_features(normalized_row)
    if not features["non_empty_values"]:
        return "empty"

    if (
        policy.drop_scaffold_rows
        and features["scaffold_count"] > 0
        and not features["has_identifier_like_value"]
        and features["numeric_count"] == 0
    ):
        return "scaffold_row"

    if (
        policy.drop_hidden_template_rows
        and row_input.hidden
        and not features["has_identifier_like_value"]
        and features["low_information"]
    ):
        return "hidden_template_row"

    if (
        policy.drop_zero_heavy_rows
        and features["zero_heavy"]
        and not features["has_identifier_like_value"]
        and features["long_text_count"] == 0
    ):
        return "default_zero_row"

    if (
        policy.drop_placeholder_rows
        and features["placeholder_only"]
        and not features["has_identifier_like_value"]
        and features["numeric_count"] == 0
    ):
        return "placeholder_row"

    return "data"


def _spreadsheet_row_features(normalized_row: Sequence[str]) -> dict[str, Any]:
    non_empty_values = [str(value).strip() for value in normalized_row if str(value or "").strip()]
    zero_like_count = sum(1 for value in non_empty_values if _is_zero_like(value))
    placeholder_count = sum(1 for value in non_empty_values if _looks_like_placeholder(value))
    scaffold_count = sum(1 for value in non_empty_values if _looks_like_scaffold(value))
    numeric_count = sum(1 for value in non_empty_values if _looks_numeric(value))
    long_text_count = sum(1 for value in non_empty_values if len(value) >= 24)
    has_identifier_like_value = any(_looks_like_identifier_value(value) for value in non_empty_values)
    non_empty_count = len(non_empty_values)
    zero_ratio = (zero_like_count / float(non_empty_count)) if non_empty_count else 0.0
    placeholder_only = bool(non_empty_values) and (placeholder_count + scaffold_count) == non_empty_count
    low_information = (
        non_empty_count > 0
        and (
            zero_ratio >= 0.75
            or placeholder_only
            or (scaffold_count > 0 and numeric_count == 0)
        )
    )
    return {
        "non_empty_values": non_empty_values,
        "zero_like_count": zero_like_count,
        "placeholder_count": placeholder_count,
        "scaffold_count": scaffold_count,
        "numeric_count": numeric_count,
        "long_text_count": long_text_count,
        "has_identifier_like_value": has_identifier_like_value,
        "zero_heavy": non_empty_count >= 4 and zero_ratio >= 0.75,
        "placeholder_only": placeholder_only,
        "low_information": low_information,
    }


def _looks_numeric(value: str) -> bool:
    candidate = value.strip().replace(",", "")
    if not candidate:
        return False
    if candidate.endswith("%"):
        candidate = candidate[:-1]
    if candidate.startswith("$"):
        candidate = candidate[1:]
    try:
        float(candidate)
    except ValueError:
        return False
    return True


def _is_zero_like(value: str) -> bool:
    candidate = _canonical(value)
    if candidate in ZERO_LIKE_TOKENS:
        return True
    if _looks_numeric(candidate):
        try:
            cleaned = candidate.rstrip("%").lstrip("$")
            return float(cleaned) == 0.0
        except ValueError:
            return False
    return False


def _looks_like_identifier_value(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return bool(IDENTIFIER_LIKE_RE.fullmatch(candidate))


def _looks_like_placeholder(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in PLACEHOLDER_ROW_PATTERNS)


def _looks_like_scaffold(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in SCAFFOLD_PATTERNS)


def _is_scaffold_column(header_value: str, column_values: Sequence[str]) -> bool:
    if not _looks_like_scaffold(header_value):
        return False
    meaningful_values = 0
    for value in column_values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        if _looks_like_scaffold(cleaned) or _is_zero_like(cleaned):
            continue
        meaningful_values += 1
        if meaningful_values >= 2:
            return False
    return True


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
