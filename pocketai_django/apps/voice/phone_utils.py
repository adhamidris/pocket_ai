from __future__ import annotations

import re

import phonenumbers


E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def is_valid_e164(value: str) -> bool:
    return bool(E164_RE.match((value or "").strip()))


def detect_country_iso2(e164: str) -> str:
    """
    Return ISO 3166-1 alpha-2 country code inferred from an E.164 number.

    Returns empty string when unknown/unparseable.
    """

    raw = (e164 or "").strip()
    if not raw:
        return ""
    try:
        parsed = phonenumbers.parse(raw, None)
        region = phonenumbers.region_code_for_number(parsed) or ""
        return str(region).upper()[:2]
    except Exception:
        return ""
