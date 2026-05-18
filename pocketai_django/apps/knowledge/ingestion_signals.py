from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Sequence

from apps.knowledge.models import KnowledgeUpload


logger = logging.getLogger(__name__)


OCR_NORMALIZATION_VERSION = "v2"
TABLE_SCOPE_CONTRACT_VERSION = "v2"
COLUMN_ROLE_INFERENCE_VERSION = "v1"
_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u06D6-\u06ED]")
_TABLE_NUMERIC_SIGNAL_TOKEN_RE = re.compile(
    r"(?:%|[$€£¥₹]|(?:^|[\s(])(?:USD|EUR|GBP|JPY|CHF|AUD|CAD|CNY|INR|SAR|AED|EGP|QAR|KWD|OMR|BHD|TRY|ZAR)(?:$|[\s):,.;]))",
    flags=re.IGNORECASE,
)
_TABLE_NUMBER_LIKE_RE = re.compile(r"[+-]?\d[\d,]*(?:[.:]\d+)?")
_TABLE_DATE_TIME_LIKE_RE = re.compile(r"\b\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
_TABLE_NUMBER_WITH_UNIT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:[a-zA-Z]{1,5}|%)\b")
_TABLE_ROW_VALUE_KEYWORD_RE = re.compile(
    r"\b(?:free|discount|waived?|commission|fee|fees|charge|charges|min(?:imum)?|max(?:imum)?|equivalent)\b",
    flags=re.IGNORECASE,
)
_SPREADSHEET_INSTRUCTION_SHEET_RE = re.compile(r"\b(?:instructions?|guidance|notes?|help)\b", flags=re.IGNORECASE)
_SPREADSHEET_PLACEHOLDER_CELL_RE = re.compile(
    r"^\s*(?:select\b|insert\b)\s*",
    flags=re.IGNORECASE,
)
_SPREADSHEET_CONTROL_CELL_RE = re.compile(r"^(?:yes|no|true|false|n/?a|none)$", flags=re.IGNORECASE)
_SPREADSHEET_MASKED_PLACEHOLDER_RE = re.compile(r"^#{4,}$")
_SPREADSHEET_SUMMARY_ROW_RE = re.compile(
    r"\b(?:total|totals|summary|grand total)\b",
    flags=re.IGNORECASE,
)
_SPREADSHEET_ZERO_LIKE_RE = re.compile(r"^(?:0|0\.0+|0%)$")
_SPREADSHEET_PURE_NUMBER_RE = re.compile(r"^[+-]?\d[\d,]*(?:\.\d+)?%?$")
_SPREADSHEET_RECORD_ID_RE = re.compile(r"^[A-Za-z]{1,8}-\d+[A-Za-z0-9-]*$")
_SPREADSHEET_REFERENCE_SHEET_RE = re.compile(
    r"\b(?:lists?|lookup|lookups|options?|choices|reference|references|validation)\b",
    flags=re.IGNORECASE,
)
_SPREADSHEET_INSTRUCTION_TOKEN_RE = re.compile(r"\b(?:instructions?|guidance|notes?|comment|comments?)\b", flags=re.IGNORECASE)


def _column_numeric_signal(text: str) -> bool:
    sample = str(text or "").strip()
    if not sample:
        return False
    return bool(
        _TABLE_NUMERIC_SIGNAL_TOKEN_RE.search(sample)
        or _TABLE_NUMBER_LIKE_RE.search(sample)
        or _TABLE_NUMBER_WITH_UNIT_RE.search(sample)
    )


def _normalize_cell_for_stats(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _infer_contextual_column_indices(
    *,
    row_values: Sequence[Sequence[str]],
    header_row_indices: set[int] | None = None,
    min_segment_columns: int = 3,
) -> set[int]:
    """
    Infer descriptor/context columns using structural signals only.

    This avoids header-name dependence so the behavior generalizes across tenants
    and domains with different column labels.
    """
    rows = list(row_values or [])
    if not rows:
        return set()

    max_cols = max((len(row) for row in rows), default=0)
    if max_cols <= 0:
        return set()

    header_set = set(header_row_indices or set())
    data_rows = [row for idx, row in enumerate(rows) if idx not in header_set]
    if not data_rows:
        data_rows = rows
    data_count = max(1, len(data_rows))

    candidates: set[int] = set()
    profiles: list[dict[str, float]] = []
    for col_idx in range(max_cols):
        values: list[str] = []
        for row in data_rows:
            value = _normalize_cell_for_stats(row[col_idx] if col_idx < len(row) else "")
            if value:
                values.append(value)
        non_empty = len(values)
        if non_empty <= 0:
            profiles.append(
                {
                    "non_empty": 0.0,
                    "non_empty_ratio": 0.0,
                    "numeric_ratio": 0.0,
                    "unique_ratio": 0.0,
                    "long_ratio": 0.0,
                    "avg_chars": 0.0,
                }
            )
            continue

        normalized = [value.lower() for value in values]
        unique_ratio = float(len(set(normalized))) / float(non_empty)
        numeric_ratio = float(sum(1 for value in values if _column_numeric_signal(value))) / float(non_empty)
        long_ratio = float(sum(1 for value in values if len(value) >= 18 or len(value.split()) >= 4)) / float(non_empty)
        avg_chars = float(sum(len(value) for value in values)) / float(non_empty)
        non_empty_ratio = float(non_empty) / float(data_count)

        descriptor_score = 0.0
        if unique_ratio >= 0.68:
            descriptor_score += 1.0
        if long_ratio >= 0.35 or avg_chars >= 16.0:
            descriptor_score += 1.0
        if numeric_ratio <= 0.35:
            descriptor_score += 1.0
        if non_empty_ratio >= 0.5:
            descriptor_score += 0.5

        value_score = 0.0
        if numeric_ratio >= 0.45:
            value_score += 1.0
        if avg_chars <= 14.0:
            value_score += 0.5
        if unique_ratio <= 0.6:
            value_score += 0.5

        if non_empty >= max(2, int(round(0.2 * data_count))) and descriptor_score >= 2.0 and descriptor_score > value_score:
            candidates.add(col_idx)

        profiles.append(
            {
                "non_empty": float(non_empty),
                "non_empty_ratio": non_empty_ratio,
                "numeric_ratio": numeric_ratio,
                "unique_ratio": unique_ratio,
                "long_ratio": long_ratio,
                "avg_chars": avg_chars,
            }
        )

    contextual: set[int] = set()
    for idx in range(max_cols):
        if idx in candidates:
            contextual.add(idx)
        else:
            break

    # Fallback: at least recognize a dominant descriptor first column.
    if not contextual and profiles:
        first = profiles[0]
        if (
            first.get("non_empty", 0.0) >= 2.0
            and first.get("avg_chars", 0.0) >= 18.0
            and first.get("unique_ratio", 0.0) >= 0.7
            and first.get("numeric_ratio", 0.0) <= 0.25
        ):
            contextual.add(0)
            if len(profiles) > 1:
                second = profiles[1]
                if (
                    second.get("non_empty", 0.0) >= 2.0
                    and second.get("avg_chars", 0.0) >= 14.0
                    and second.get("unique_ratio", 0.0) >= 0.6
                    and second.get("numeric_ratio", 0.0) <= 0.35
                ):
                    contextual.add(1)

    if len(contextual) >= max_cols:
        contextual = set()

    contextual_sorted = sorted(contextual)
    while max_cols - len(contextual_sorted) < max(1, int(min_segment_columns)) and contextual_sorted:
        contextual_sorted.pop()
    return set(contextual_sorted)


def _log_normalization_summary(upload: KnowledgeUpload | None, source: str, summary: Mapping[str, Any] | None) -> None:
    if not summary or not summary.get("enabled"):
        return
    rows = int((summary.get("rows_dropped") or {}).get("total", 0))
    columns = int((summary.get("columns_trimmed") or {}).get("total", 0))
    tokens = int(summary.get("tokens_replaced") or 0)
    skipped = len(summary.get("empty_sheets_skipped") or []) + len(summary.get("policy_skipped") or [])
    if not any([rows, columns, tokens, skipped]):
        return
    logger.info(
        "ingest.normalization upload=%s source=%s rows_dropped=%s columns_trimmed=%s tokens_replaced=%s sheets_skipped=%s",
        getattr(upload, "id", None),
        source,
        rows,
        columns,
        tokens,
        skipped,
    )
