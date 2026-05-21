from __future__ import annotations

from typing import Mapping


def _clip_debug_text(value: object, *, limit: int = 480) -> str:
    text = str(value or "").strip()
    if limit and len(text) > limit:
        return f"{text[: max(0, limit - 1)].rstrip()}…"
    return text


def _json_safe_debug(value: object, *, depth: int = 3, string_limit: int = 240, list_limit: int = 12) -> object:
    def _is_numeric_metric(v: object) -> bool:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        if isinstance(v, str):
            candidate = v.strip()
            if not candidate:
                return False
            try:
                float(candidate)
            except ValueError:
                return False
            return True
        return False

    if value is None:
        return None
    if depth <= 0:
        return _clip_debug_text(value, limit=string_limit)
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return _clip_debug_text(value, limit=string_limit)
        return value
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= list_limit:
                out["…"] = f"+{max(0, len(value) - list_limit)} more keys"
                break
            key_str = str(key or "").strip() or f"key_{idx}"
            lowered = key_str.lower()
            if any(token in lowered for token in ("password", "secret", "api_key", "apikey")):
                out[key_str] = "[REDACTED]"
                continue
            if "token" in lowered:
                safe_token_metrics = {
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "token_limit",
                    "token_count",
                    "tokens",
                }
                if lowered not in safe_token_metrics and not _is_numeric_metric(item):
                    out[key_str] = "[REDACTED]"
                    continue
            out[key_str] = _json_safe_debug(item, depth=depth - 1, string_limit=string_limit, list_limit=list_limit)
        return out
    if isinstance(value, (list, tuple, set)):
        out_list: list[object] = []
        for idx, entry in enumerate(value):
            if idx >= list_limit:
                out_list.append(f"+{max(0, len(value) - list_limit)} more")
                break
            out_list.append(_json_safe_debug(entry, depth=depth - 1, string_limit=string_limit, list_limit=list_limit))
        return out_list
    return _clip_debug_text(value, limit=string_limit)
