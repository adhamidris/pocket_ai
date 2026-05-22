from __future__ import annotations

from collections.abc import Callable
from typing import Mapping, Sequence

from ....runtime.portal_block_stream import PORTAL_BLOCK_TOOL_NAME


def _split_portal_tool_calls(
    tool_calls: Sequence[Mapping[str, object]],
    *,
    tool_name_for_call: Callable[[Mapping[str, object]], str],
) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
    normal_calls: list[Mapping[str, object]] = []
    portal_calls: list[Mapping[str, object]] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, Mapping):
            continue
        try:
            tool_name = tool_name_for_call(tool_call)
        except Exception:
            continue
        if tool_name == PORTAL_BLOCK_TOOL_NAME:
            portal_calls.append(tool_call)
        else:
            normal_calls.append(tool_call)
    return normal_calls, portal_calls

def _tool_spinner_text(
    arguments: Mapping[str, object] | None,
    *,
    clip_text: Callable[[str, int], str],
) -> str:
    if not isinstance(arguments, Mapping):
        return ""
    text = ""
    raw_ui = arguments.get("__ui")
    if isinstance(raw_ui, Mapping):
        raw_text = raw_ui.get("spinner_text")
        text = str(raw_text or "").strip() if raw_text is not None else ""
    if not text:
        raw_text = arguments.get("spinner_text")
        text = str(raw_text or "").strip() if raw_text is not None else ""
    if not text:
        return ""
    text = " ".join(text.split())
    return clip_text(text, 160)
