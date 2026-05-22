from __future__ import annotations

import json
from typing import Mapping


class McpRemoteToolCompactionMixin:
    def _compact_mcp_content_items(
        self,
        content_in: object,
        *,
        snippet_content_chars: int,
    ) -> list[dict[str, object]]:
        content_out: list[dict[str, object]] = []
        if not isinstance(content_in, list):
            return content_out
        for item in content_in[:6]:
            if not isinstance(item, Mapping):
                continue
            item_type = item.get("type")
            if not isinstance(item_type, str) or not item_type.strip():
                continue
            entry: dict[str, object] = {"type": item_type.strip()}
            if item_type == "text" and isinstance(item.get("text"), str) and item.get("text").strip():
                entry["text"] = self._clip_text(item.get("text").strip(), int(snippet_content_chars))
            elif item_type == "resource_link":
                uri = item.get("uri")
                if isinstance(uri, str) and uri.strip():
                    entry["uri"] = uri.strip()
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    entry["name"] = self._clip_text(name.strip(), 200)
            elif item_type == "structured" and item.get("data") is not None:
                try:
                    blob = json.dumps(item.get("data"), ensure_ascii=False, default=str)
                except Exception:
                    blob = str(item.get("data"))
                entry["data"] = self._clip_text(blob, 2000)
            content_out.append(entry)
        return content_out

    def _add_remote_mcp_common_fields(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        snippet_content_chars: int,
    ) -> None:
        artifact_id = payload.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id.strip():
            compact["artifact_id"] = artifact_id.strip()
        prompt_view = payload.get("prompt_view")
        if isinstance(prompt_view, Mapping) and prompt_view:
            compact["prompt_view"] = self._compact_action_payload_for_prompt(
                prompt_view,
                max_string_chars=1200,
                max_keys=24,
                max_list_items=10,
                max_nested_keys=12,
            )
        is_error = payload.get("is_error")
        if isinstance(is_error, bool):
            compact["is_error"] = is_error
        remote = payload.get("remote")
        if isinstance(remote, Mapping):
            remote_out: dict[str, object] = {}
            for key in ("connection_id", "connection_name", "tool"):
                value = remote.get(key)
                if isinstance(value, str) and value.strip():
                    remote_out[key] = value.strip()
            if remote_out:
                compact["remote"] = remote_out

        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            compact["text"] = self._clip_text(text.strip(), int(snippet_content_chars))

        content_out = self._compact_mcp_content_items(
            payload.get("content"),
            snippet_content_chars=snippet_content_chars,
        )
        if content_out:
            compact["content"] = content_out

    def _compact_mcp_search_tools_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        max_snippets: int,
    ) -> dict[str, object]:
        raw_results = payload.get("results")
        results_out: list[dict[str, object]] = []
        if isinstance(raw_results, list):
            for result in raw_results[: max(1, max_snippets)]:
                if not isinstance(result, Mapping):
                    continue
                tool_id = result.get("tool_id")
                if not isinstance(tool_id, str) or not tool_id.strip():
                    continue
                entry: dict[str, object] = {"tool_id": tool_id.strip()}
                for key, limit in (
                    ("connection_name", 120),
                    ("remote_tool", 160),
                    ("description", 420),
                ):
                    value = result.get(key)
                    if not isinstance(value, str) or not value.strip():
                        continue
                    entry[key] = self._clip_text(value.strip(), limit)
                required_args = result.get("required_args")
                required_out: list[dict[str, str]] = []
                if isinstance(required_args, list):
                    for arg in required_args[:12]:
                        if not isinstance(arg, Mapping):
                            continue
                        name = arg.get("name")
                        if not isinstance(name, str) or not name.strip():
                            continue
                        arg_entry: dict[str, str] = {"name": name.strip()}
                        type_hint = arg.get("type")
                        if isinstance(type_hint, str) and type_hint.strip():
                            arg_entry["type"] = self._clip_text(type_hint.strip(), 48)
                        required_out.append(arg_entry)
                if required_out:
                    entry["required_args"] = required_out
                results_out.append(entry)

        compact["results"] = results_out
        compact["prompt_compact"] = True
        return compact

    def _compact_mcp_call_tool_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        snippet_content_chars: int,
    ) -> dict[str, object]:
        tool_id = payload.get("tool_id")
        if isinstance(tool_id, str) and tool_id.strip():
            compact["tool_id"] = tool_id.strip()
        self._add_remote_mcp_common_fields(
            compact,
            payload,
            snippet_content_chars=snippet_content_chars,
        )
        missing_fields = payload.get("missing_fields")
        if isinstance(missing_fields, list):
            compact["missing_fields"] = [str(field) for field in missing_fields if str(field).strip()][:24]
        type_errors = payload.get("type_errors")
        if isinstance(type_errors, list):
            errors_out: list[dict[str, str]] = []
            for err in type_errors[:24]:
                if not isinstance(err, Mapping):
                    continue
                field = err.get("field")
                expected = err.get("expected")
                received = err.get("received")
                if not isinstance(field, str) or not field.strip():
                    continue
                out: dict[str, str] = {"field": field.strip()}
                if isinstance(expected, str) and expected.strip():
                    out["expected"] = self._clip_text(expected.strip(), 80)
                if isinstance(received, str) and received.strip():
                    out["received"] = self._clip_text(received.strip(), 80)
                errors_out.append(out)
            if errors_out:
                compact["type_errors"] = errors_out

        compact["prompt_compact"] = True
        return compact

    def _compact_generic_mcp_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        snippet_content_chars: int,
    ) -> dict[str, object]:
        self._add_remote_mcp_common_fields(
            compact,
            payload,
            snippet_content_chars=snippet_content_chars,
        )
        compact["prompt_compact"] = True
        return compact
