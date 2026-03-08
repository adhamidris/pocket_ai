from __future__ import annotations

import hashlib


def sha256_hex(value: str) -> str:
    """
    Stable SHA-256 helper used for telemetry/debug hashing.

    NOTE: Legacy PII redaction/masking utilities were removed from this module as
    the product has shifted to a knowledge-RAG deployment model.
    """

    text = (value or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

