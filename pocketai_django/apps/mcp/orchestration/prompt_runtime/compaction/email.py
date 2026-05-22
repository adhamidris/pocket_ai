from __future__ import annotations

from typing import Mapping


class McpEmailToolCompactionMixin:
    def _compact_email_search_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        max_snippets: int,
    ) -> dict[str, object]:
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

    def _compact_email_get_message_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        snippet_content_chars: int,
    ) -> dict[str, object]:
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

    def _compact_email_get_thread_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
        *,
        max_snippets: int,
        snippet_content_chars: int,
    ) -> dict[str, object]:
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

    def _compact_email_draft_payload(
        self,
        compact: dict[str, object],
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        for key in ("provider", "email_account_id", "draft_id", "message_id", "thread_id"):
            value = payload.get(key)
            if value is not None and value != "":
                compact[key] = value
        compact["prompt_compact"] = True
        return compact
