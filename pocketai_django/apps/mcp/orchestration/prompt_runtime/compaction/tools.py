from __future__ import annotations

from typing import Any, Mapping, Sequence

from .email import McpEmailToolCompactionMixin
from .files import McpFileToolCompactionMixin
from .remote import McpRemoteToolCompactionMixin


class McpPromptToolCompactionMixin(
    McpEmailToolCompactionMixin,
    McpFileToolCompactionMixin,
    McpRemoteToolCompactionMixin,
):

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
            return self._compact_mcp_search_tools_payload(
                compact,
                payload,
                max_snippets=max_snippets,
            )

        if normalized_name == "mcp_call_tool":
            return self._compact_mcp_call_tool_payload(
                compact,
                payload,
                snippet_content_chars=snippet_content_chars,
            )

        if normalized_name.startswith("mcp_"):
            return self._compact_generic_mcp_payload(
                compact,
                payload,
                snippet_content_chars=snippet_content_chars,
            )

        if normalized_name == "search_conversation_files":
            return self._compact_search_conversation_files_payload(
                compact,
                payload,
                max_snippets=max_snippets,
                snippet_content_chars=snippet_content_chars,
            )

        if normalized_name == "read_conversation_file":
            return self._compact_read_conversation_file_payload(
                compact,
                payload,
                max_snippets=max_snippets,
                snippet_content_chars=snippet_content_chars,
            )

        if normalized_name in {"pdf_generate", "pdf_merge", "pdf_extract_pages"}:
            return self._compact_pdf_artifact_payload(compact, payload)

        if normalized_name == "pdf_extract_text":
            return self._compact_pdf_extract_text_payload(
                compact,
                payload,
                snippet_content_chars=snippet_content_chars,
            )

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
            return self._compact_email_search_payload(
                compact,
                payload,
                max_snippets=max_snippets,
            )

        if normalized_name == "email_get_message":
            return self._compact_email_get_message_payload(
                compact,
                payload,
                snippet_content_chars=snippet_content_chars,
            )

        if normalized_name == "email_get_thread":
            return self._compact_email_get_thread_payload(
                compact,
                payload,
                max_snippets=max_snippets,
                snippet_content_chars=snippet_content_chars,
            )

        if normalized_name in ("email_create_draft", "email_send_draft"):
            return self._compact_email_draft_payload(compact, payload)

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
