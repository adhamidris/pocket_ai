from __future__ import annotations

from typing import Any, Iterable, Mapping

REDACTED_VALUE = "[REDACTED]"


def _is_numeric_metric(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return False
        try:
            float(candidate)
        except ValueError:
            return False
        return True
    return False


_SAFE_TOKEN_METRICS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "max_tokens",
    "max_context_tokens",
    "max_input_tokens",
    "response_token_reserve",
    "tokens_est",
    "tokens_est_before",
    "tokens_est_after",
    "token_budget",
}

_SECRET_KEY_TOKENS = (
    "password",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "bearer",
    "cookie",
    "session",
    "private_key",
    "client_secret",
)


def redact_tool_input_payload(
    payload: object,
    *,
    sensitive_keys: Iterable[str] | None = None,
    max_depth: int = 6,
) -> object:
    """
    Redact sensitive values from tool input payloads before emitting tool events or
    persisting tool history/artifacts.

    - `sensitive_keys` should include per-connection marketplace setupFields keys.
    - Additionally redacts common secret-ish key names (token/secret/password/etc).
    """

    sensitive_lower = {str(key or "").strip().lower() for key in (sensitive_keys or []) if str(key or "").strip()}

    def _redact(value: object, *, depth: int) -> object:
        if depth <= 0:
            return REDACTED_VALUE
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Mapping):
            out: dict[str, object] = {}
            for idx, (key, item) in enumerate(value.items()):
                key_str = str(key or "").strip() or f"key_{idx}"
                lowered = key_str.lower()

                if lowered in sensitive_lower:
                    out[key_str] = REDACTED_VALUE
                    continue
                if any(token in lowered for token in _SECRET_KEY_TOKENS):
                    out[key_str] = REDACTED_VALUE
                    continue
                if "token" in lowered:
                    if lowered not in _SAFE_TOKEN_METRICS or not _is_numeric_metric(item):
                        out[key_str] = REDACTED_VALUE
                        continue

                out[key_str] = _redact(item, depth=depth - 1)
            return out
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            return [_redact(item, depth=depth - 1) for item in items]
        return str(value)

    return _redact(payload, depth=max(1, int(max_depth)))

