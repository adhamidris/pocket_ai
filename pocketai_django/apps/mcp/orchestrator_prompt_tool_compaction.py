from __future__ import annotations

import json
from typing import Any, Mapping, Sequence


class McpPromptToolCompactionMixin:

    def _truncate_large_fields(
        self,
        obj: Any,
        max_total: int,
        max_field: int = 10000,
        *,
        max_list_items: int | None = None,
    ) -> Any:
        """Recursively truncate large string fields (and optionally list lengths)."""
        if isinstance(obj, str):
            return obj[:max_field] + "..." if len(obj) > max_field else obj
        if isinstance(obj, dict):
            return {k: self._truncate_large_fields(v, max_total, max_field, max_list_items=max_list_items) for k, v in obj.items()}
        if isinstance(obj, list):
            items = obj[:max_list_items] if isinstance(max_list_items, int) and max_list_items >= 0 else obj
            return [self._truncate_large_fields(item, max_total, max_field, max_list_items=max_list_items) for item in items]
        return obj

    def _compact_knowledge_tool_payload_for_prompt(
        self,
        tool_name: str,
        payload: Mapping[str, object],
        *,
        max_snippets: int,
        snippet_content_chars: int,
        max_rows: int,
        max_contributions: int,
        max_cells: int = 12,
        max_cells_exact: int = 60,
    ) -> dict[str, object]:
        """Prompt compaction for high-volume knowledge and table evidence tools."""
        normalized_name = (tool_name or payload.get("tool") or "").strip()
        compact: dict[str, object] = {"tool": normalized_name or payload.get("tool") or tool_name}
        status = payload.get("status")
        if status is not None:
            compact["status"] = status
        for key in ("error", "error_code", "hint"):
            if key not in payload:
                continue
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, (list, tuple, set, dict)) and not value:
                continue
            compact[key] = value

        budget = payload.get("budget")
        if isinstance(budget, Mapping) and budget and normalized_name != "search_knowledge":
            # Budget telemetry is useful for read continuations and blocked-tool payloads.
            # Search success payloads keep model-facing control data in pagination/read hints.
            compact["budget"] = dict(budget)
        for guidance_key in ("budget_guidance", "search_repeat_guidance"):
            guidance = payload.get(guidance_key)
            if isinstance(guidance, Mapping) and guidance:
                compact[guidance_key] = dict(guidance)

        if normalized_name == "mcp_search_tools":
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

        if normalized_name == "mcp_call_tool":
            tool_id = payload.get("tool_id")
            if isinstance(tool_id, str) and tool_id.strip():
                compact["tool_id"] = tool_id.strip()
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

            content_in = payload.get("content")
            content_out: list[dict[str, object]] = []
            if isinstance(content_in, list):
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
            if content_out:
                compact["content"] = content_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name.startswith("mcp_"):
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

            content_in = payload.get("content")
            content_out: list[dict[str, object]] = []
            if isinstance(content_in, list):
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
            if content_out:
                compact["content"] = content_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "search_conversation_files":
            raw_snippets = payload.get("snippets")
            snippets_out: list[dict[str, object]] = []
            preview_chars = max(200, min(900, int(snippet_content_chars)))
            if isinstance(raw_snippets, list):
                for entry in raw_snippets[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    out: dict[str, object] = {}
                    snippet_id = entry.get("id")
                    if isinstance(snippet_id, str) and snippet_id.strip():
                        out["id"] = snippet_id.strip()
                    file_meta = entry.get("file")
                    if isinstance(file_meta, Mapping):
                        file_out: dict[str, object] = {}
                        for key in ("id", "filename", "page_count"):
                            value = file_meta.get(key)
                            if value is None:
                                continue
                            if isinstance(value, str) and not value.strip():
                                continue
                            file_out[key] = value
                        if file_out:
                            out["file"] = file_out
                    preview = entry.get("preview")
                    if isinstance(preview, str) and preview.strip():
                        out["preview"] = self._clip_text(preview.strip(), preview_chars)
                    read_hint = entry.get("read_hint") or entry.get("readHint")
                    if isinstance(read_hint, Mapping):
                        ids = read_hint.get("ids")
                        if isinstance(ids, list):
                            out["read_hint"] = {"ids": [str(v) for v in ids if str(v).strip()][:12]}
                    if out:
                        snippets_out.append(out)
            compact["snippets"] = snippets_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "read_conversation_file":
            raw_chunks = payload.get("chunks")
            chunks_out: list[dict[str, object]] = []
            content_chars = max(400, min(2400, int(snippet_content_chars)))
            if isinstance(raw_chunks, list):
                for entry in raw_chunks[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    out: dict[str, object] = {}
                    chunk_id = entry.get("id")
                    if isinstance(chunk_id, str) and chunk_id.strip():
                        out["id"] = chunk_id.strip()
                    file_meta = entry.get("file")
                    if isinstance(file_meta, Mapping):
                        file_out: dict[str, object] = {}
                        for key in ("id", "filename", "page_count"):
                            value = file_meta.get(key)
                            if value is None:
                                continue
                            if isinstance(value, str) and not value.strip():
                                continue
                            file_out[key] = value
                        if file_out:
                            out["file"] = file_out
                    content = entry.get("content")
                    if isinstance(content, str) and content.strip():
                        out["content"] = self._clip_text(content.strip(), content_chars)
                    if out:
                        chunks_out.append(out)
            compact["chunks"] = chunks_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name in {"pdf_generate", "pdf_merge", "pdf_extract_pages"}:
            artifact = payload.get("artifact")
            if isinstance(artifact, Mapping):
                artifact_out: dict[str, object] = {}
                file_id = artifact.get("file_id") or artifact.get("fileId") or artifact.get("id")
                filename = artifact.get("filename")
                if file_id is not None:
                    artifact_out["file_id"] = str(file_id)
                if isinstance(filename, str) and filename.strip():
                    artifact_out["filename"] = self._clip_text(filename.strip(), 180)
                if artifact_out:
                    compact["artifact"] = artifact_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "pdf_extract_text":
            file_meta = payload.get("file")
            if isinstance(file_meta, Mapping):
                file_out: dict[str, object] = {}
                for key in ("id", "filename", "page_count"):
                    value = file_meta.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    file_out[key] = value
                if file_out:
                    compact["file"] = file_out
            text_value = payload.get("text")
            if isinstance(text_value, str) and text_value.strip():
                max_text = max(2000, min(15000, int(snippet_content_chars) * 10))
                compact["text"] = self._clip_text(text_value.strip(), max_text)
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "search_knowledge":
            raw_diagnostics = payload.get("diagnostics")
            compact_diagnostics: dict[str, object] = {}
            if isinstance(raw_diagnostics, Mapping):
                for key in (
                    "reason",
                    "intent_clarification_question",
                    "assistant_guidance",
                    "scope_summary",
                    "conflict_detected",
                    "no_result_reason",
                ):
                    value = raw_diagnostics.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str):
                        if not value.strip():
                            continue
                        compact_diagnostics[key] = self._clip_text(value.strip(), 260)
                        continue
                    compact_diagnostics[key] = value

            if compact_diagnostics:
                compact["diagnostics"] = compact_diagnostics

            # Preserve control-plane fields so the LLM can plan coverage and paginate.
            # Keep pagination fields under `pagination`; top-level duplicates are
            # intentionally omitted from the compact public/search payload.
            total_found = payload.get("total_found")
            total_found_int: int | None = None
            if isinstance(total_found, (int, float)) and int(total_found) >= 0:
                total_found_int = int(total_found)
            elif isinstance(total_found, str) and total_found.strip().isdigit():
                total_found_int = int(total_found.strip())

            has_more = bool(payload.get("has_more")) if "has_more" in payload else None

            next_cursor = payload.get("next_cursor")

            read_budget = payload.get("read_budget")
            if not isinstance(read_budget, Mapping) or not read_budget:
                legacy_budget = payload.get("read_budget_hint")
                if isinstance(legacy_budget, Mapping):
                    read_budget = {
                        "suggested_chars": legacy_budget.get("total_suggested_max_chars"),
                        "max_chars": legacy_budget.get("max_chars_allowed"),
                    }
            if isinstance(read_budget, Mapping) and read_budget:
                hint_out: dict[str, object] = {}
                for key in ("suggested_chars", "max_chars"):
                    value = read_budget.get(key)
                    if value is None:
                        continue
                    try:
                        hint_out[key] = int(value)
                    except (TypeError, ValueError):
                        continue
                if hint_out:
                    compact["read_budget"] = hint_out

            completeness = payload.get("completeness")
            pagination_out: dict[str, object] = {}
            pagination_in = payload.get("pagination")
            if isinstance(pagination_in, Mapping) and pagination_in:
                for key in ("shown", "total", "has_more", "next_cursor", "message"):
                    value = pagination_in.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    pagination_out[key] = (
                        self._clip_text(value.strip(), 420)
                        if key == "message" and isinstance(value, str)
                        else self._clip_text(value.strip(), 240)
                        if key == "next_cursor" and isinstance(value, str)
                        else value
                    )
            if isinstance(completeness, Mapping) and completeness:
                for source_key, target_key in (
                    ("shown", "shown"),
                    ("refs_total_found", "total"),
                    ("total_found", "total"),
                    ("has_more", "has_more"),
                    ("message", "message"),
                ):
                    if target_key in pagination_out:
                        continue
                    value = completeness.get(source_key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    pagination_out[target_key] = (
                        self._clip_text(value.strip(), 420)
                        if source_key == "message" and isinstance(value, str)
                        else value
                    )
            if "total" not in pagination_out and total_found_int is not None:
                pagination_out["total"] = total_found_int
            if "has_more" not in pagination_out and has_more is not None:
                pagination_out["has_more"] = has_more
            if isinstance(next_cursor, str) and next_cursor.strip():
                pagination_out.setdefault("next_cursor", self._clip_text(next_cursor.strip(), 240))
            if pagination_out:
                compact["pagination"] = pagination_out

            for key in ("query", "intent", "match_policy"):
                if key not in payload:
                    continue
                value = payload.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, (list, tuple, set, dict)) and not value:
                    continue
                compact[key] = value
            raw_results = payload.get("refs")
            if not isinstance(raw_results, list):
                raw_results = payload.get("results")
            refs_out: list[dict[str, object]] = []
            if isinstance(raw_results, list):
                for result in raw_results[: max(1, max_snippets)]:
                    if not isinstance(result, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    for key in (
                        "id",
                        "document",
                        "kind",
                        "preview",
                        "read_chars",
                    ):
                        if key not in result:
                            continue
                        value = result.get(key)
                        if value is None:
                            continue
                        if isinstance(value, str) and not value.strip():
                            continue
                        if key == "preview" and isinstance(value, str) and value.strip():
                            entry[key] = self._clip_text(value.strip(), int(snippet_content_chars))
                            continue
                        entry[key] = value
                    if "document" not in entry:
                        label = result.get("label") or result.get("title")
                        if isinstance(label, str) and label.strip():
                            entry["document"] = self._clip_text(label.strip(), 180)
                    if "read_chars" not in entry:
                        read_hint = result.get("read_hint")
                        if isinstance(read_hint, Mapping):
                            value = read_hint.get("suggested_max_chars")
                            if value not in (None, ""):
                                entry["read_chars"] = value
                    if entry:
                        refs_out.append(entry)
            if refs_out:
                compact["refs"] = refs_out
                return compact
            raw_snippets = payload.get("snippets")
            snippets_out: list[dict[str, object]] = []
            search_content_chars = max(200, min(600, int(snippet_content_chars)))
            if isinstance(raw_snippets, list):
                for entry in raw_snippets[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    snippets_out.append(
                        self._compact_snippet_for_prompt(
                            entry,
                            include_content=True,
                            content_chars=search_content_chars,
                        )
                    )
            compact["snippets"] = snippets_out
            return compact



        if normalized_name == "read_knowledge":
            # Agentic contract (Phase 2): evidence is a list of canonical payloads.
            evidence_list = payload.get("evidence")
            if isinstance(evidence_list, list):
                budget_in = compact.pop("budget", None)
                if isinstance(budget_in, Mapping):
                    budget_out: dict[str, object] = {}
                    for key in ("warning", "next_action"):
                        value = budget_in.get(key)
                        if isinstance(value, str) and value.strip():
                            budget_out[key] = self._clip_text(value.strip(), 260)
                    if budget_out:
                        compact["budget"] = budget_out

                evidence_out: list[dict[str, object]] = []
                for entry in evidence_list[: max(1, max_snippets)]:
                    if not isinstance(entry, Mapping):
                        continue
                    out_entry: dict[str, object] = {}
                    entry_id = entry.get("id")
                    if isinstance(entry_id, str) and entry_id.strip():
                        out_entry["id"] = entry_id.strip()
                    title = entry.get("title")
                    if isinstance(title, str) and title.strip():
                        out_entry["document"] = self._clip_text(title.strip(), 180)
                    kind = entry.get("kind")
                    if isinstance(kind, str) and kind.strip():
                        out_entry["kind"] = kind.strip()
                    if bool(entry.get("truncated")):
                        out_entry["truncated"] = True
                    if entry.get("complete") is False:
                        out_entry["complete"] = False
                    for key in ("artifact_id", "cursor_used", "next_cursor"):
                        if key in entry and entry.get(key) not in {None, ""}:
                            out_entry[key] = entry.get(key)
                    payload_obj = entry.get("payload")
                    if isinstance(payload_obj, Mapping) and payload_obj:
                        payload_type = str(payload_obj.get("type") or entry.get("type") or "").strip()
                        if payload_type == "table":
                            columns = payload_obj.get("columns")
                            rows = payload_obj.get("rows")
                            if isinstance(columns, list):
                                out_entry["columns"] = list(columns)
                            if isinstance(rows, list):
                                out_entry["rows"] = list(rows)

                            selection_mode = str(payload_obj.get("selection_mode") or "").strip()
                            is_exact_row = selection_mode == "row_ref"
                            if not is_exact_row:
                                for key in ("row_offset", "rows_shown", "total_rows", "next_row_start"):
                                    value = payload_obj.get(key)
                                    if value not in (None, ""):
                                        out_entry[key] = value
                                if bool(entry.get("more_rows_available")):
                                    out_entry["more_rows_available"] = True
                        elif payload_type == "text":
                            text_value = payload_obj.get("text")
                            if isinstance(text_value, str):
                                # Do not truncate canonical text here; read_knowledge is already bounded by max_chars.
                                out_entry["text"] = text_value
                        else:
                            out_entry["payload"] = dict(payload_obj)
                    if out_entry:
                        evidence_out.append(out_entry)

                compact["evidence"] = evidence_out

                for key in ("read", "deferred", "errors"):
                    value = payload.get(key)
                    if isinstance(value, list) and value:
                        compact[key] = value[:24]

                compact["prompt_compact"] = True
                return compact

            engine = str(payload.get("engine") or "").strip()
            if engine:
                compact["engine"] = engine
            for key in ("total_matches", "truncated", "throttle_notice"):
                if key not in payload:
                    continue
                value = payload.get(key)
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, (list, tuple, set, dict)) and not value:
                    continue
                compact[key] = value

            diagnostics_in = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
            diagnostics_out: dict[str, object] = {}
            requested_identifier = (
                diagnostics_in.get("requested_identifier")
                if isinstance(diagnostics_in.get("requested_identifier"), Mapping)
                else None
            )
            if requested_identifier:
                requested_out: dict[str, object] = {}
                column = requested_identifier.get("column")
                if isinstance(column, str) and column.strip():
                    requested_out["column"] = column.strip()
                values = requested_identifier.get("values")
                if isinstance(values, list) and values:
                    requested_out["values"] = [str(item) for item in values[:6] if str(item).strip()]
                policy = requested_identifier.get("policy")
                if isinstance(policy, str) and policy.strip():
                    requested_out["policy"] = policy.strip()
                if requested_out:
                    diagnostics_out["requested_identifier"] = requested_out
            matched_identifiers = diagnostics_in.get("matched_identifiers")
            if isinstance(matched_identifiers, list) and matched_identifiers:
                diagnostics_out["matched_identifiers"] = [str(item) for item in matched_identifiers[:12] if str(item).strip()]
            match_policy = diagnostics_in.get("match_policy")
            if isinstance(match_policy, str) and match_policy.strip():
                diagnostics_out["match_policy"] = match_policy.strip()

            if engine == "text_page":
                for key in ("page", "mode", "mode_downgraded", "token_budget"):
                    value = diagnostics_in.get(key)
                    if value is None or value == "":
                        continue
                    diagnostics_out[key] = value
            elif engine == "table_preview":
                for key in (
                    "sheet_name",
                    "match_column",
                    "match_value",
                    "match_values",
                    "query",
                    "columns",
                    "mode",
                    "match_count",
                    "total",
                    "display_total",
                ):
                    value = diagnostics_in.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    if isinstance(value, (list, tuple, set, dict)) and not value:
                        continue
                    diagnostics_out[key] = value
            elif engine in {"file_dataset", "db_preview"}:
                for key in (
                    "sheet_name",
                    "sheet_index",
                    "query",
                    "filters",
                    "select_columns",
                    "sort_by",
                    "sort_direction",
                    "offset",
                    "limit",
                    "match_count",
                    "total_matches",
                    "scanned_rows",
                ):
                    value = diagnostics_in.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    if isinstance(value, (list, tuple, set, dict)) and not value:
                        continue
                    diagnostics_out[key] = value
                dataset = diagnostics_in.get("dataset") if isinstance(diagnostics_in.get("dataset"), Mapping) else None
                if dataset:
                    dataset_out: dict[str, object] = {}
                    for key in ("row_count", "sheet_row_count", "preview_rows_indexed", "sheet_count", "suggested_keys"):
                        value = dataset.get(key)
                        if value is None:
                            continue
                        if isinstance(value, str) and not value.strip():
                            continue
                        if isinstance(value, (list, tuple, set, dict)) and not value:
                            continue
                        dataset_out[key] = value
                    if dataset_out:
                        diagnostics_out["dataset"] = dataset_out

            if diagnostics_out:
                compact["diagnostics"] = diagnostics_out

            evidence_in = payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {}
            evidence_out: dict[str, object] = {"snippets": [], "rows": []}
            if engine == "text_page":
                raw_snippets = evidence_in.get("snippets")
                snippets_out: list[dict[str, object]] = []
                if isinstance(raw_snippets, list):
                    for entry in raw_snippets[: max(1, max_snippets)]:
                        if not isinstance(entry, Mapping):
                            continue
                        snippets_out.append(
                            self._compact_snippet_for_prompt(
                                entry,
                                include_content=True,
                                content_chars=snippet_content_chars,
                            )
                        )
                evidence_out["snippets"] = snippets_out
            else:
                cell_cap = max(1, int(max_cells))
                if engine in {"table_preview", "file_dataset", "db_preview"}:
                    total_matches = payload.get("total_matches")
                    if not isinstance(total_matches, int):
                        total_matches = None
                    requested_identifier = (
                        diagnostics_in.get("requested_identifier")
                        if isinstance(diagnostics_in.get("requested_identifier"), Mapping)
                        else None
                    )
                    policy = str(requested_identifier.get("policy") or "").strip().lower() if requested_identifier else ""
                    values = requested_identifier.get("values") if requested_identifier else None
                    has_values = isinstance(values, list) and any(str(item).strip() for item in values)
                    status_value = str(payload.get("status") or "ok").strip().lower()
                    if (
                        status_value == "ok"
                        and total_matches is not None
                        and total_matches <= max(1, int(max_rows))
                        and policy in {"eq", "in"}
                        and has_values
                    ):
                        cell_cap = max(cell_cap, int(max_cells_exact))
                raw_rows = evidence_in.get("rows")
                rows_out: list[dict[str, object]] = []
                if isinstance(raw_rows, list):
                    for row in raw_rows[: max(1, max_rows)]:
                        if not isinstance(row, Mapping):
                            continue
                        row_payload: dict[str, object] = {}
                        if "row_index" in row and row.get("row_index") not in {None, ""}:
                            row_payload["row_index"] = row.get("row_index")
                        for key in ("table_order_index", "sheet_name", "row_total", "row_total_display", "contribution_count"):
                            if key in row and row.get(key) not in {None, ""}:
                                row_payload[key] = row.get(key)
                        cells = row.get("cells")
                        if isinstance(cells, list) and cells:
                            row_payload["cells"] = [
                                {"column": cell.get("column"), "value": cell.get("value")}
                                for cell in cells[: max(1, cell_cap)]
                                if isinstance(cell, Mapping)
                            ]
                        contributions = row.get("contributions")
                        if isinstance(contributions, list) and contributions:
                            row_payload["contributions"] = [
                                {"column": entry.get("column"), "display": entry.get("display"), "value": entry.get("value")}
                                for entry in contributions[: max(1, max_contributions)]
                                if isinstance(entry, Mapping)
                            ]
                        if row_payload:
                            rows_out.append(row_payload)
                evidence_out["rows"] = rows_out

                aggregate_result = evidence_in.get("aggregate_result") if isinstance(evidence_in.get("aggregate_result"), Mapping) else None
                if aggregate_result:
                    evidence_out["aggregate_result"] = dict(aggregate_result)
                if evidence_in.get("total") is not None:
                    evidence_out["total"] = evidence_in.get("total")
                if evidence_in.get("display_total") not in (None, ""):
                    evidence_out["display_total"] = evidence_in.get("display_total")

            compact["evidence"] = evidence_out
            compact["prompt_compact"] = True
            return compact


        # Handle email tools to ensure results reach the LLM
        if normalized_name == "email_search":
            for key in ("provider", "email_account_id", "query", "result_size_estimate", "next_page_token"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            raw_results = payload.get("results")
            results_out: list[dict[str, object]] = []
            if isinstance(raw_results, list):
                for result in raw_results[: max(1, max_snippets)]:
                    if not isinstance(result, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    for key in ("message_id", "thread_id", "snippet", "subject", "from", "to", "date"):
                        value = result.get(key)
                        if value is not None and value != "":
                            if key == "snippet":
                                entry[key] = self._clip_text(str(value), 200)
                            else:
                                entry[key] = value
                    if entry:
                        results_out.append(entry)
            compact["results"] = results_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "email_get_message":
            for key in ("provider", "email_account_id", "message_id", "thread_id", "snippet", "labels"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            headers = payload.get("headers")
            if isinstance(headers, Mapping):
                compact["headers"] = dict(headers)
            body_text = payload.get("body_text")
            if isinstance(body_text, str) and body_text.strip():
                compact["body_text"] = self._clip_text(body_text.strip(), int(snippet_content_chars) * 2)
            if payload.get("body_truncated"):
                compact["body_truncated"] = True
            compact["prompt_compact"] = True
            return compact

        if normalized_name == "email_get_thread":
            for key in ("provider", "email_account_id", "thread_id", "message_count", "truncated"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            raw_messages = payload.get("messages")
            messages_out: list[dict[str, object]] = []
            if isinstance(raw_messages, list):
                for msg in raw_messages[: max(1, max_snippets)]:
                    if not isinstance(msg, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    for key in ("message_id", "thread_id", "snippet", "labels"):
                        value = msg.get(key)
                        if value is not None and value != "":
                            entry[key] = value
                    headers = msg.get("headers")
                    if isinstance(headers, Mapping):
                        entry["headers"] = dict(headers)
                    body_text = msg.get("body_text")
                    if isinstance(body_text, str) and body_text.strip():
                        entry["body_text"] = self._clip_text(body_text.strip(), int(snippet_content_chars))
                    if msg.get("body_truncated"):
                        entry["body_truncated"] = True
                    if entry:
                        messages_out.append(entry)
            compact["messages"] = messages_out
            compact["prompt_compact"] = True
            return compact

        if normalized_name in ("email_create_draft", "email_send_draft"):
            for key in ("provider", "email_account_id", "draft_id", "message_id", "thread_id"):
                value = payload.get(key)
                if value is not None and value != "":
                    compact[key] = value
            compact["prompt_compact"] = True
            return compact

        action_value = payload.get("action")
        action_payload = payload.get("payload")
        if action_value is not None or action_payload is not None:
            if action_value is not None:
                compact["action"] = action_value
            if isinstance(action_payload, Mapping):
                compact["payload"] = self._compact_action_payload_for_prompt(action_payload)
            elif isinstance(action_payload, str) and action_payload.strip():
                compact["payload"] = self._clip_text(action_payload.strip(), 800)
            compact["prompt_compact"] = True
            return compact

        raw_snippets = payload.get("snippets")
        snippets_out = []
        if isinstance(raw_snippets, list):
            for entry in raw_snippets[: max(1, max_snippets)]:
                if not isinstance(entry, Mapping):
                    continue
                snippets_out.append(
                    self._compact_snippet_for_prompt(
                        entry,
                        include_content=False,
                        content_chars=snippet_content_chars,
                    )
                )
        if snippets_out:
            compact["snippets"] = snippets_out

        scalar_limit = 12
        for key, value in payload.items():
            if key in {
                "tool",
                "status",
                "error",
                "error_code",
                "hint",
                "snippets",
                "rows",
                "results",
                "action",
                "payload",
            }:
                continue
            if len(compact) >= scalar_limit:
                break
            if value is None:
                continue
            if isinstance(value, (int, float, bool)):
                compact[key] = value
            elif isinstance(value, str):
                trimmed = value.strip()
                if trimmed:
                    compact[key] = self._clip_text(trimmed, 200)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                items: list[object] = []
                for item in value[:6]:
                    if item is None:
                        continue
                    if isinstance(item, (int, float, bool)):
                        items.append(item)
                    elif isinstance(item, str):
                        trimmed = item.strip()
                        if trimmed:
                            items.append(self._clip_text(trimmed, 120))
                if items:
                    compact[key] = items
        compact["prompt_compact"] = True
        return compact
