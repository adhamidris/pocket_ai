from __future__ import annotations

import hashlib
from typing import Mapping

from .rag_observability import compact_retrieval_observability


class McpToolTraceSummaryMixin:

    def _email_tool_event_input(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, object] | None:
        normalized = str(tool_name or "").strip().lower()
        if normalized == "email_search":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return None
            try:
                limit = int(arguments.get("limit") or 0)
            except (TypeError, ValueError):
                limit = 0
            payload: dict[str, object] = {"query": self._clip_text(query, 240)}
            if limit:
                payload["limit"] = max(1, min(25, limit))
            return payload
        if normalized == "email_get_message":
            message_id = str(arguments.get("message_id") or arguments.get("messageId") or "").strip()
            return {"message_id": message_id} if message_id else None
        if normalized == "email_get_thread":
            thread_id = str(arguments.get("thread_id") or arguments.get("threadId") or "").strip()
            return {"thread_id": thread_id} if thread_id else None
        if normalized == "email_create_draft":
            # Email drafts are user-facing (approval UX). Include the full preview payload so
            # the portal can render + stream the message as it is drafted.
            def _coerce_list(value: object) -> list[str]:
                if not isinstance(value, list):
                    return []
                out: list[str] = []
                for item in value[:64]:
                    text = str(item or "").strip()
                    if text:
                        out.append(text)
                return out

            subject = str(arguments.get("subject") or "").strip()
            body_text = str(arguments.get("body_text") or arguments.get("bodyText") or "").strip()
            if len(body_text) > 12_000:
                body_text = body_text[:12_000].rstrip()

            payload: dict[str, object] = {}
            to_list = _coerce_list(arguments.get("to"))
            cc_list = _coerce_list(arguments.get("cc"))
            bcc_list = _coerce_list(arguments.get("bcc"))
            if to_list:
                payload["to"] = to_list
            if cc_list:
                payload["cc"] = cc_list
            if bcc_list:
                payload["bcc"] = bcc_list
            if subject:
                payload["subject"] = self._clip_text(subject, 240)
            if body_text:
                payload["body_text"] = body_text
            return payload or None
        if normalized == "email_send_draft":
            draft_id = str(arguments.get("draft_id") or arguments.get("draftId") or "").strip()
            return {"draft_id": draft_id} if draft_id else None
        return None

    def _email_tool_trace_arguments(self, tool_name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        # Keep trace payloads privacy-safe; do not store full email bodies/recipients.
        payload = self._email_tool_event_input(tool_name, arguments) or {}
        account_id = str(arguments.get("email_account_id") or arguments.get("emailAccountId") or "").strip()
        if account_id:
            payload["email_account_id"] = account_id
        return payload

    def _email_tool_event_output(self, tool_name: str, tool_result: Mapping[str, object]) -> dict[str, object]:
        normalized = str(tool_name or "").strip().lower()
        status = str(tool_result.get("status") or "").strip() or "ok"
        output: dict[str, object] = {"status": status}
        if status != "ok":
            error_code = str(tool_result.get("error_code") or tool_result.get("error") or "").strip()
            hint = str(tool_result.get("hint") or "").strip()
            if error_code:
                output["error_code"] = error_code
            if hint:
                output["hint"] = self._clip_text(hint, 240)
            return output

        if normalized == "email_search":
            results = tool_result.get("results")
            if isinstance(results, list):
                output["result_count"] = len(results)
                message_ids: list[str] = []
                thread_ids: list[str] = []
                for item in results[:5]:
                    if not isinstance(item, Mapping):
                        continue
                    mid = str(item.get("message_id") or item.get("messageId") or "").strip()
                    tid = str(item.get("thread_id") or item.get("threadId") or "").strip()
                    if mid:
                        message_ids.append(mid)
                    if tid:
                        thread_ids.append(tid)
                if message_ids:
                    output["message_ids"] = message_ids
                if thread_ids:
                    output["thread_ids"] = thread_ids
            return output

        if normalized == "email_get_message":
            output["message_id"] = str(tool_result.get("message_id") or tool_result.get("messageId") or "").strip()
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            output["body_truncated"] = bool(tool_result.get("body_truncated") or tool_result.get("bodyTruncated"))
            return output

        if normalized == "email_get_thread":
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            output["message_count"] = int(tool_result.get("message_count") or tool_result.get("messageCount") or 0)
            messages = tool_result.get("messages")
            if isinstance(messages, list):
                output["returned_messages"] = len(messages)
            output["truncated"] = bool(tool_result.get("truncated"))
            return output

        if normalized == "email_create_draft":
            output["draft_id"] = str(tool_result.get("draft_id") or tool_result.get("draftId") or "").strip()
            output["message_id"] = str(tool_result.get("message_id") or tool_result.get("messageId") or "").strip()
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            output["body_truncated"] = bool(tool_result.get("body_truncated") or tool_result.get("bodyTruncated"))
            return output

        if normalized == "email_send_draft":
            output["draft_id"] = str(tool_result.get("draft_id") or tool_result.get("draftId") or "").strip()
            output["message_id"] = str(tool_result.get("message_id") or tool_result.get("messageId") or "").strip()
            output["thread_id"] = str(tool_result.get("thread_id") or tool_result.get("threadId") or "").strip()
            return output

        # Fallback: do not dump arbitrary tool outputs.
        return output

    def _tool_trace_output_summary(self, tool_name: str, tool_result: Mapping[str, object]) -> dict[str, object] | None:
        """
        Build a compact, privacy-safe summary of the tool output for the portal debug panel.

        This intentionally avoids returning any large free-text payloads (e.g., read_knowledge
        excerpts) while still exposing enough metadata to debug budgets/cursors/artifacts.
        """

        normalized = str(tool_name or "").strip().lower()
        status = str(tool_result.get("status") or "").strip() or "ok"

        if self._is_email_tool(tool_name):
            return self._email_tool_event_output(tool_name, tool_result)

        def _cursor_fingerprint(value: object) -> dict[str, object] | None:
            if not isinstance(value, str):
                return None
            raw = value.strip()
            if not raw:
                return None
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            return {"len": len(raw), "sha256_10": digest[:10]}

        if normalized == "search_knowledge":
            results = tool_result.get("refs")
            if not isinstance(results, list):
                results = tool_result.get("results")
            out: dict[str, object] = {"status": status}
            for key in ("error", "error_code", "hint"):
                value = tool_result.get(key)
                if isinstance(value, str) and value.strip():
                    out[key] = self._clip_text(value.strip(), 240)
            if isinstance(results, list):
                out["results_count"] = len(results)
                preview_list: list[dict[str, object]] = []
                for item in results[:8]:
                    if not isinstance(item, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        entry["id"] = item_id
                    label = item.get("label") or item.get("title")
                    if isinstance(label, str) and label.strip():
                        entry["title"] = self._clip_text(label.strip(), 140)
                    kind = item.get("kind")
                    if isinstance(kind, str) and kind.strip():
                        entry["kind"] = kind.strip()
                    item_type = item.get("type")
                    if isinstance(item_type, str) and item_type.strip():
                        entry["type"] = item_type.strip()
                    for key in ("char_estimate", "chars", "row_count", "column_count"):
                        if key in item:
                            try:
                                entry[key] = int(item.get(key) or 0)
                            except (TypeError, ValueError):
                                pass
                    try:
                        suggested = int(item.get("read_chars") or 0)
                    except (TypeError, ValueError):
                        suggested = 0
                    if not suggested:
                        hint = item.get("read_hint")
                        if isinstance(hint, Mapping):
                            try:
                                suggested = int(hint.get("suggested_max_chars") or 0)
                            except (TypeError, ValueError):
                                suggested = 0
                    if suggested:
                        entry["read_chars"] = suggested
                    why = item.get("why")
                    if isinstance(why, list) and why:
                        why_out = [self._clip_text(str(token), 80) for token in why[:2] if str(token).strip()]
                        if why_out:
                            entry["why"] = why_out
                    preview_text = item.get("preview")
                    if isinstance(preview_text, str) and preview_text.strip():
                        entry["preview_chars"] = len(preview_text.strip())
                        if item.get("preview_truncated") is True:
                            entry["preview_truncated"] = True
                    if entry:
                        preview_list.append(entry)
                if preview_list:
                    out["results_preview"] = preview_list
            pagination = tool_result.get("pagination")
            pagination_map = pagination if isinstance(pagination, Mapping) else {}
            total_found = tool_result.get("total_found")
            if total_found is None:
                total_found = pagination_map.get("total")
            if isinstance(total_found, (int, float)) or (isinstance(total_found, str) and total_found.strip().isdigit()):
                try:
                    out["total_found"] = int(total_found)
                except (TypeError, ValueError):
                    pass
            budget = tool_result.get("budget")
            if isinstance(budget, Mapping):
                out["budget"] = dict(budget)
            budget_guidance = tool_result.get("budget_guidance")
            if isinstance(budget_guidance, Mapping) and budget_guidance:
                guidance_out: dict[str, object] = {}
                for key in ("reason", "available_refs_count", "available_read_evidence_count"):
                    if key in budget_guidance:
                        guidance_out[key] = budget_guidance.get(key)
                actions = budget_guidance.get("next_actions")
                if isinstance(actions, list):
                    action_names: list[str] = []
                    for action in actions[:4]:
                        if not isinstance(action, Mapping):
                            continue
                        action_name = str(action.get("action") or "").strip()
                        if action_name:
                            action_names.append(action_name)
                    if action_names:
                        guidance_out["next_actions"] = action_names
                if guidance_out:
                    out["budget_guidance"] = guidance_out
            repeat_guidance = tool_result.get("search_repeat_guidance")
            if isinstance(repeat_guidance, Mapping) and repeat_guidance:
                repeat_out: dict[str, object] = {}
                for key in ("reason", "available_refs_count", "similarity"):
                    if key in repeat_guidance:
                        repeat_out[key] = repeat_guidance.get(key)
                if repeat_out:
                    out["search_repeat_guidance"] = repeat_out
            retrieval_observability = compact_retrieval_observability(
                tool_result.get("retrieval_observability")
            )
            if retrieval_observability:
                out["retrieval_observability"] = retrieval_observability
            completeness = tool_result.get("completeness")
            if isinstance(completeness, Mapping) and completeness:
                # Keep this compact: surfaced fields help debug pagination/dedupe without leaking content.
                completeness_out: dict[str, object] = {}
                for key in (
                    "shown",
                    "legacy_shown",
                    "planned_unique_snippets",
                    "raw_snippet_count",
                    "already_seen",
                    "legacy_already_seen",
                    "total_found",
                    "snippets_total_found",
                    "refs_total_found",
                    "paging_mode",
                    "ref_offset",
                    "has_more",
                    "excluded_seen",
                    "all_previously_shown",
                ):
                    if key in completeness and completeness.get(key) not in (None, ""):
                        completeness_out[key] = completeness.get(key)
                if completeness_out:
                    out["completeness"] = completeness_out
            has_more = tool_result.get("has_more")
            if has_more is None:
                has_more = pagination_map.get("has_more")
            if isinstance(has_more, bool):
                out["has_more"] = has_more
            next_cursor_value = tool_result.get("next_cursor") or pagination_map.get("next_cursor")
            next_cursor_fp = _cursor_fingerprint(next_cursor_value)
            if next_cursor_fp:
                out["next_cursor"] = next_cursor_fp

            requested_query = str(tool_result.get("query") or "").strip()
            if requested_query:
                out["requested_query"] = self._clip_text(requested_query, 220)
            effective_query = requested_query
            if effective_query:
                out["effective_query"] = self._clip_text(effective_query, 260)
            return out

        if normalized == "read_knowledge":
            out: dict[str, object] = {"status": status}
            for key in ("total_chars", "max_chars", "max_chars_allowed"):
                if key in tool_result:
                    try:
                        out[key] = int(tool_result.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
            mode = tool_result.get("mode")
            if isinstance(mode, str) and mode.strip():
                out["mode"] = mode.strip()
            for key in ("error_code", "error"):
                value = tool_result.get(key)
                if isinstance(value, str) and value.strip():
                    out[key] = self._clip_text(value.strip(), 120)

            evidence = tool_result.get("evidence")
            if not isinstance(evidence, list):
                evidence = tool_result.get("contents")
            if isinstance(evidence, list):
                out["evidence_count"] = len(evidence)
                preview: list[dict[str, object]] = []
                for item in evidence[:6]:
                    if not isinstance(item, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        entry["id"] = item_id
                    title = item.get("title")
                    if isinstance(title, str) and title.strip():
                        entry["title"] = self._clip_text(title.strip(), 140)
                    item_type = item.get("type")
                    if isinstance(item_type, str) and item_type.strip():
                        entry["type"] = item_type.strip()
                    kind = item.get("kind")
                    if isinstance(kind, str) and kind.strip():
                        entry["kind"] = kind.strip()
                    try:
                        entry["chars"] = int(item.get("chars") or 0)
                    except (TypeError, ValueError):
                        pass
                    entry["complete"] = bool(item.get("complete"))
                    entry["truncated"] = bool(item.get("truncated"))
                    artifact_id = item.get("artifact_id")
                    if isinstance(artifact_id, str) and artifact_id.strip():
                        entry["artifact_id"] = artifact_id.strip()
                    cursor_used_fp = _cursor_fingerprint(item.get("cursor_used"))
                    if cursor_used_fp:
                        entry["cursor_used"] = cursor_used_fp
                    next_cursor_fp = _cursor_fingerprint(item.get("next_cursor"))
                    if next_cursor_fp:
                        entry["next_cursor"] = next_cursor_fp
                    if entry:
                        preview.append(entry)
                if preview:
                    out["evidence_preview"] = preview

            read = tool_result.get("read")
            if isinstance(read, list):
                out["read_count"] = len(read)
                read_preview: list[dict[str, object]] = []
                for item in read[:8]:
                    if not isinstance(item, Mapping):
                        continue
                    entry: dict[str, object] = {}
                    item_id = str(item.get("id") or "").strip()
                    if item_id:
                        entry["id"] = item_id
                    status_value = item.get("status")
                    if isinstance(status_value, str) and status_value.strip():
                        entry["status"] = status_value.strip()
                    try:
                        entry["chars"] = int(item.get("chars") or 0)
                    except (TypeError, ValueError):
                        pass
                    artifact_id = item.get("artifact_id")
                    if isinstance(artifact_id, str) and artifact_id.strip():
                        entry["artifact_id"] = artifact_id.strip()
                    if entry:
                        read_preview.append(entry)
                if read_preview:
                    out["read_preview"] = read_preview
            deferred = tool_result.get("deferred")
            if isinstance(deferred, list):
                out["deferred_count"] = len(deferred)
            errors = tool_result.get("errors")
            if isinstance(errors, list):
                out["errors_count"] = len(errors)
            throttle_notice = tool_result.get("throttle_notice")
            if isinstance(throttle_notice, Mapping):
                out["throttle_notice"] = dict(throttle_notice)
            budget = tool_result.get("budget")
            if isinstance(budget, Mapping):
                out["budget"] = dict(budget)
            return out

        # Default: status + a small set of safe fields (avoid dumping arbitrary payloads).
        out: dict[str, object] = {"status": status}
        for key in (
            "id",
            "document_id",
            "draft_id",
            "message_id",
            "thread_id",
            "artifact_id",
            "count",
            "total",
        ):
            value = tool_result.get(key)
            if value is None or value == "":
                continue
            if isinstance(value, (int, float, bool)):
                out[key] = value
            else:
                out[key] = self._clip_text(value, 160)
        budget = tool_result.get("budget")
        if isinstance(budget, Mapping):
            out["budget"] = dict(budget)
        return out or None
