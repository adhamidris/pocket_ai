"""
Identifier and table-column helpers for MCP knowledge tools.
"""

from __future__ import annotations

import re
from typing import Sequence


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


TOTAL_COLUMN_KEYWORDS = (
    "total",
    "sum",
    "overall",
    "اجمالي",
    "إجمالي",
    "الاجمالي",
    "المجموع",
)
PRIMARY_TOTAL_TERMS = (
    "total",
    "overall",
    "sum",
    "اجمالي",
    "إجمالي",
    "الاجمالي",
    "المجموع",
)


def _normalize_column_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip().lower())


def _normalize_identifier_value(value: object) -> str:
    """
    Normalize identifier-like values (invoice/order/ticket IDs) for strict matching.

    We remove whitespace and lowercase to avoid common ingestion artefacts like
    padding while preserving punctuation/hyphens.
    """

    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = text.strip("`\"'")
    return re.sub(r"\s+", "", text).lower()


_IDENTIFIER_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _extract_identifier_candidate(text: object) -> str | None:
    raw = _coerce_str(text).strip()
    if not raw:
        return None
    cleaned = raw.strip("`\"'")
    if not cleaned:
        return None
    if _IDENTIFIER_EMAIL_RE.match(cleaned):
        return cleaned
    if cleaned.isdigit() and len(cleaned) >= 6:
        return cleaned
    digit_runs = re.findall(r"\d{6,}", cleaned)
    if digit_runs:
        return max(digit_runs, key=len)
    token_runs = re.findall(r"[A-Za-z0-9][A-Za-z0-9_/-]{7,}", cleaned)
    if token_runs:
        return max(token_runs, key=len)
    return None


def _pick_best_identifier_column(
    columns: Sequence[str],
    *,
    query_text: str,
    identifier_value: str,
) -> str | None:
    candidates: list[str] = []
    for col in columns:
        if not isinstance(col, str):
            continue
        col_clean = col.strip()
        if not col_clean:
            continue
        if _column_suggests_identifier(col_clean):
            candidates.append(col_clean)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    query_norm = _normalize_column_name(query_text)
    ident_norm = _normalize_identifier_value(identifier_value)
    is_email = "@" in ident_norm
    is_digits = ident_norm.isdigit()

    best: str | None = None
    best_score = -1
    for col in candidates:
        col_norm = _normalize_column_name(col)
        if not col_norm:
            continue
        score = 0

        if is_email:
            if any(term in col_norm for term in ("email", "e-mail", "mail")):
                score += 200
        if is_digits:
            if "invoice" in col_norm:
                score += 80
            if "serial" in col_norm:
                score += 50
            if "order" in col_norm:
                score += 60
            if "ticket" in col_norm:
                score += 60
        if "invoice" in query_norm and "invoice" in col_norm:
            score += 60
        if "order" in query_norm and "order" in col_norm:
            score += 60
        if "ticket" in query_norm and "ticket" in col_norm:
            score += 60
        if "serial" in query_norm and "serial" in col_norm:
            score += 25
        if any(term in query_norm for term in ("id", "ref", "#")) and any(term in col_norm for term in ("id", "ref", "#")):
            score += 15
        if any(term in col_norm for term in ("id", "ref", "reference", "number", "no", "#")):
            score += 10

        if score > best_score:
            best_score = score
            best = col

    if best is None:
        return None
    if best_score >= 20:
        return best
    return None


def _column_suggests_identifier(column: object) -> bool:
    normalized = _normalize_column_name(column)
    if not normalized:
        return False
    if any(term in normalized for term in ("email", "e-mail", "mail")):
        return True
    if any(term in normalized for term in ("phone", "mobile", "msisdn")):
        return True
    if any(term in normalized for term in ("invoice", "order", "ticket")):
        if any(term in normalized for term in ("id", "serial", "number", "no", "ref", "reference", "#")):
            return True
        return True
    if "serial" in normalized or "reference" in normalized or re.search(r"(?:^|[\s_-])ref(?:$|[\s_-])", normalized):
        return True
    if re.search(r"(?:^|[\s_-])id(?:$|[\s_-])", normalized):
        return True
    if " code" in normalized or normalized.endswith("code") or "sku" in normalized:
        return True
    if "number" in normalized or normalized.endswith(" no") or normalized.endswith(" #"):
        return True
    return False


def _should_force_exact_identifier_match(column: object, value: object) -> bool:
    """
    Decide whether we should force op=eq (and reject contains/prefix) for this filter.
    """

    normalized = _normalize_identifier_value(value)
    if not normalized:
        return False
    if "@" in normalized:
        return True
    if normalized.isdigit() and len(normalized) >= 6:
        return True
    if len(normalized) >= 8 and any(ch.isdigit() for ch in normalized):
        return True
    if _column_suggests_identifier(column) and len(normalized) >= 4:
        return True
    return False


def _total_column_priority(value: object) -> int:
    normalized = _normalize_column_name(value)
    if not normalized:
        return 0
    if normalized in PRIMARY_TOTAL_TERMS:
        return 3
    for term in PRIMARY_TOTAL_TERMS:
        if normalized.startswith(f"{term} ") or normalized.endswith(f" {term}"):
            return 2
    if any(keyword in normalized for keyword in TOTAL_COLUMN_KEYWORDS):
        return 1
    return 0


def _is_total_column_label(value: object) -> bool:
    return _total_column_priority(value) > 0
