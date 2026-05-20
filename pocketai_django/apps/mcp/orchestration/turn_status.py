from __future__ import annotations

from collections.abc import Callable
from typing import Mapping


def _status_event(
    code: str,
    *,
    on_status_change: Callable[[Mapping[str, object]], None] | None,
    label: str | None = None,
    meta: Mapping[str, object] | None = None,
) -> None:
    if not on_status_change:
        return
    code_value = (code or "").strip()
    if not code_value:
        return
    payload: dict[str, object] = {"code": code_value}
    label_value = label.strip() if isinstance(label, str) else ""
    if label_value:
        payload["label"] = label_value
    if meta:
        try:
            payload["meta"] = dict(meta)
        except Exception:
            pass
    on_status_change(payload)
