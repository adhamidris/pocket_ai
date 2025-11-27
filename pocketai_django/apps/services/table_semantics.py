from __future__ import annotations

import re
from typing import Any

PRIMARY_TOTAL_TERMS: tuple[str, ...] = (
    "total",
    "overall",
    "sum",
    "اجمالي",
    "إجمالي",
    "الاجمالي",
    "المجموع",
)

TOTAL_COLUMN_KEYWORDS: tuple[str, ...] = PRIMARY_TOTAL_TERMS


def normalize_column_name(value: Any) -> str:
    """
    Normalize a column label into a lowercase snake_case token.
    Mirrors the heuristics used during table detection so downstream systems
    can reliably match headers.
    """

    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    # Collapse spaced-out headers: "W H I T E" -> "WHITE"
    if re.fullmatch(r"(?:[A-Za-z]\s+){2,}[A-Za-z]", raw):
        raw = raw.replace(" ", "")
    text = re.sub(r"\s+", " ", raw)
    text = text.lower()
    text = re.sub(r"[^a-z0-9%$€£]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def total_column_priority(value: Any) -> int:
    """
    Return a weighting (0-3) indicating whether the supplied header looks like
    a canonical total column.
    """

    if not isinstance(value, str):
        return 0
    normalized = normalize_column_name(value)
    if not normalized:
        return 0
    if normalized in PRIMARY_TOTAL_TERMS:
        return 3
    for term in PRIMARY_TOTAL_TERMS:
        if normalized.startswith(f"{term}_") or normalized.endswith(f"_{term}"):
            return 2
    if any(term in normalized for term in TOTAL_COLUMN_KEYWORDS):
        return 1
    return 0
