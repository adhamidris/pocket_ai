from __future__ import annotations

import json
from typing import Callable, Mapping, Sequence

from apps.conversations.rich_blocks import coerce_block_event


PORTAL_BLOCK_TOOL_NAME = "portal_emit_blocks"


class _PortalBlockStream:
    def __init__(self, emit: Callable[[Mapping[str, object]], None] | None) -> None:
        self._emit = emit
        self._processed: set[str] = set()

    def ingest_stream_state(self, tool_call: Mapping[str, object]) -> None:
        if not self._emit or not isinstance(tool_call, Mapping):
            return
        if not self._is_portal_block_call(tool_call):
            return
        args = self._tool_arguments(tool_call)
        call_key = self._call_key(tool_call)
        self._ingest_args(call_key, args)

    def ingest_tool_calls(self, tool_calls: Sequence[Mapping[str, object]]) -> None:
        if not self._emit:
            return
        for tool_call in tool_calls:
            if not isinstance(tool_call, Mapping):
                continue
            if not self._is_portal_block_call(tool_call):
                continue
            args = self._tool_arguments(tool_call)
            call_key = self._call_key(tool_call)
            self._ingest_args(call_key, args)

    def _is_portal_block_call(self, tool_call: Mapping[str, object]) -> bool:
        func = tool_call.get("function")
        if isinstance(func, Mapping):
            name = func.get("name")
            if isinstance(name, str):
                return name == PORTAL_BLOCK_TOOL_NAME
        name = tool_call.get("name")
        return isinstance(name, str) and name == PORTAL_BLOCK_TOOL_NAME

    def _call_key(self, tool_call: Mapping[str, object]) -> str:
        raw = tool_call.get("id") or tool_call.get("index") or ""
        key = str(raw).strip()
        return key or str(id(tool_call))

    def _tool_arguments(self, tool_call: Mapping[str, object]) -> object:
        func = tool_call.get("function")
        raw_args = None
        if isinstance(func, Mapping):
            raw_args = func.get("arguments")
        if raw_args is None:
            raw_args = tool_call.get("arguments")
        return raw_args

    def _ingest_args(self, key: str, args: object) -> None:
        if not self._emit:
            return
        if key in self._processed:
            return
        parsed = None
        if isinstance(args, str):
            raw = args.strip()
            if not raw:
                return
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return
        elif isinstance(args, (list, tuple, dict)):
            parsed = args
        if parsed is None:
            return
        self._processed.add(key)
        events = self._extract_events(parsed)
        if not events:
            return
        for raw_event in events:
            event = coerce_block_event(raw_event)
            if event and self._emit:
                self._emit(event)

    @staticmethod
    def _extract_events(payload: object) -> list[object]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, Mapping):
            events = payload.get("events")
            if isinstance(events, list):
                return list(events)
            if isinstance(events, Mapping):
                return [events]
            event = payload.get("event")
            if isinstance(event, list):
                return list(event)
            if isinstance(event, Mapping):
                return [event]
            if "type" in payload:
                return [payload]
        return []
