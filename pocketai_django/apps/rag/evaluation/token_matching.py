from __future__ import annotations

import re
import unicodedata
from typing import Sequence


_NBSP = "\u00A0"
_CURRENCY_CODES = (
    # Keep this list short and pragmatic: it's for evaluation normalization,
    # not for parsing money amounts in production.
    "egp",
    "usd",
    "eur",
    "gbp",
    "sar",
    "aed",
    "qar",
    "kwd",
    "bhd",
    "omr",
)

_CURRENCY_PREFIX_RE = re.compile(rf"\b({'|'.join(_CURRENCY_CODES)})(?=\d)")
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d)[,.](?=\d{3}(\D|$))")
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d),(?=\d{1,2}(\D|$))")
_TRAILING_PUNCT_RE = re.compile(r"([a-z0-9%])([.,;:])(\s|$)")


def normalize_strict(text: object) -> str:
    """
    Minimal normalization used by the original benchmark:
    - Unicode NFKC
    - whitespace collapse
    - lowercase
    """

    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace(_NBSP, " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip().lower()


def normalize_tolerant(text: object) -> str:
    """
    "Business correctness" normalization for eval matching.

    The goal is to reduce false misses when the answer/evidence is correct but
    formatting differs (e.g., `1,560` vs `1.560`, `EGP10` vs `EGP 10`,
    `120/Quarter` vs `120 per quarter`).

    This intentionally stays conservative; it is *not* a semantic matcher.
    """

    value = normalize_strict(text)

    # Normalize slash-based "per" forms (Quarter/Month/Leaf/Txn, etc.).
    value = re.sub(r"\s*/\s*", " per ", value)

    # Ensure a space between currency code and number (EGP10 -> egp 10).
    value = _CURRENCY_PREFIX_RE.sub(r"\1 ", value)

    # Normalize number grouping separators when they're clearly thousands groups:
    # 1,560 -> 1560, 1.560 -> 1560, 20.000 -> 20000, etc.
    value = _THOUSANDS_SEP_RE.sub("", value)

    # Normalize comma-as-decimal in common cases (0,5% -> 0.5%).
    value = _DECIMAL_COMMA_RE.sub(".", value)

    # Strip punctuation that commonly appears as trailing tokens in PDFs/answers.
    value = _TRAILING_PUNCT_RE.sub(r"\1\3", value)

    value = re.sub(r"\s+", " ", value)
    return value.strip()


def tokens_check(*, required_tokens: Sequence[str], text: str, mode: str = "strict") -> tuple[bool, list[str]]:
    """
    Returns (hit, missing_tokens) using substring matching after normalization.

    mode:
      - "strict": legacy normalization
      - "tolerant": additional formatting normalization for eval scoring
    """

    normalizer = normalize_tolerant if mode == "tolerant" else normalize_strict
    hay = normalizer(text)
    missing = [tok for tok in required_tokens if normalizer(tok) not in hay]
    return len(missing) == 0, missing


def tokens_check_both(*, required_tokens: Sequence[str], text: str) -> dict[str, object]:
    strict_hit, strict_missing = tokens_check(required_tokens=required_tokens, text=text, mode="strict")
    tolerant_hit, tolerant_missing = tokens_check(required_tokens=required_tokens, text=text, mode="tolerant")
    return {
        "strict_hit": strict_hit,
        "strict_missing": strict_missing,
        "tolerant_hit": tolerant_hit,
        "tolerant_missing": tolerant_missing,
    }

