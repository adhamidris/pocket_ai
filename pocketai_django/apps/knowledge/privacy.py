from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping, Sequence

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Only treat as phone when separators or leading "+" are present to avoid masking invoice/order ids.
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.IGNORECASE)
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def sha256_hex(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_column(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip().lower()


_PII_COLUMN_TOKENS = {
    "email",
    "e-mail",
    "mail",
    "phone",
    "mobile",
    "cell",
    "tel",
    "telephone",
    "address",
    "addr",
    "street",
    "ssn",
    "social security",
    "passport",
    "national id",
    "national_id",
    "id number",
    "id_number",
    "iban",
    "account number",
    "account_number",
    "card",
    "credit card",
    "credit_card",
    "cvv",
    "pin",
    "password",
}


def column_suggests_pii(column_name: str | None) -> bool:
    canonical = _canonical_column(column_name)
    if not canonical:
        return False
    if canonical in _PII_COLUMN_TOKENS:
        return True
    for token in _PII_COLUMN_TOKENS:
        if token and token in canonical:
            return True
    return False


def redact_email(value: str) -> str:
    cleaned = (value or "").strip()
    if "@" not in cleaned:
        return "[EMAIL]"
    local, _, domain = cleaned.partition("@")
    local = local.strip()
    domain = domain.strip()
    if not domain:
        return "[EMAIL]"
    if not local:
        return f"[EMAIL]@{domain}"
    return f"{local[:1]}***@{domain}"


def redact_digits(value: str, *, keep_last: int = 2) -> str:
    digits = [ch for ch in (value or "") if ch.isdigit()]
    if not digits:
        return "[REDACTED]"
    suffix = "".join(digits[-keep_last:]) if keep_last > 0 else ""
    return f"***{suffix}" if suffix else "***"


def redact_value_for_preview(value: Any, *, column_name: str | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    canonical = _canonical_column(column_name)
    if "email" in canonical or "mail" in canonical:
        return redact_email(text)
    if any(token in canonical for token in ("phone", "mobile", "tel", "cell")):
        return redact_digits(text, keep_last=2)
    if "address" in canonical or "addr" in canonical or "street" in canonical:
        # Keep only the first few characters to make previews useful without leaking full addresses.
        return text[:6].rstrip() + "…"
    if "ssn" in canonical or "passport" in canonical or ("id" in canonical and "order" not in canonical and "invoice" not in canonical):
        return redact_digits(text, keep_last=2)
    return text


def redact_tabular_preview(
    *,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    force: bool = False,
) -> list[list[str]]:
    """
    Redact potentially sensitive values in tabular previews (dashboard/preflight),
    leaving the underlying dataset/document untouched.
    """

    out: list[list[str]] = []
    redact_mask = [force or column_suggests_pii(col) for col in columns]
    for row in rows:
        rendered: list[str] = []
        for idx, col in enumerate(columns):
            value = row[idx] if idx < len(row) else ""
            text = str(value or "").strip()
            if redact_mask[idx]:
                rendered.append(redact_value_for_preview(text, column_name=col))
            else:
                rendered.append(text)
        out.append(rendered)
    return out


def redact_mapping_preview(
    row: Mapping[str, Any],
    *,
    force: bool = False,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in row.items():
        key_str = str(key or "")
        if not key_str:
            continue
        text = str(value or "").strip()
        if force or column_suggests_pii(key_str):
            out[key_str] = redact_value_for_preview(text, column_name=key_str)
        else:
            out[key_str] = text
    return out


def redact_free_text(text: str) -> str:
    """
    Best-effort PII masking for logs (does not attempt to detect names/addresses).
    """

    if not text:
        return ""
    # Protect UUIDs from false-positive phone matching.
    placeholders: dict[str, str] = {}
    def _protect(m: re.Match) -> str:
        key = f"\x00U{len(placeholders)}\x00"
        placeholders[key] = m.group(0)
        return key
    output = _UUID_RE.sub(_protect, text)
    output = EMAIL_RE.sub(lambda m: redact_email(m.group(0)), output)
    output = SSN_RE.sub("[SSN]", output)
    output = IBAN_RE.sub("[IBAN]", output)
    output = PHONE_RE.sub("[PHONE]", output)
    for key, original in placeholders.items():
        output = output.replace(key, original)
    return output
