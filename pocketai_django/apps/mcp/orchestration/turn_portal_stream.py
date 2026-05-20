from __future__ import annotations

from typing import Any, Mapping


def _on_stream_tool_call_delta(tool_call: Mapping[str, object] | None, *, portal_block_stream: Any) -> None:
    if not tool_call:
        return
    portal_block_stream.ingest_stream_state(tool_call)
