from __future__ import annotations

import re


ALIAS_KEYWORDS = (
    "slug",
    "id",
    "identifier",
    "code",
    "sku",
    "policy",
    "policy_id",
    "record_id",
    "product_code",
    "trip_code",
    "trip_id",
    "reference",
    "reference_id",
)

SLUG_PATTERN = re.compile(r"\b[a-z0-9]+(?:-[a-z0-9]+){1,}\b")
IDENTIFIER_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}", re.IGNORECASE)
ID_LINE_PATTERN = re.compile(
    r"(?:^|\b)(?:id|identifier|sku|code|policy|ref|reference)\s*[:#]\s*([a-z0-9][a-z0-9_\-\/]+)",
    re.IGNORECASE,
)
ALIAS_MIN_LENGTH = 4
ALIAS_SYMBOL_MIN_LENGTH = 3
ALIAS_MAX_LENGTH = 255
CARD_NUMBER_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
DATE_TOKEN_PATTERN = re.compile(
    r"\b(?:\d{4}[/-]\d{1,2}[/-]\d{1,2}|(?:0?[1-9]|1[0-2])[/-](?:\d{2}|\d{4}))\b"
)
