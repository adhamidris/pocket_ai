"""
Observability helpers for MCP knowledge tool payloads.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Mapping, Sequence

from apps.conversations.models import Conversation
from apps.knowledge.privacy_tools.hashing import sha256_hex
from apps.rag.rag_logging import structured_log
from core.metrics import latency_monitor

from .result_helpers import _estimate_tokens
from ..runtime.tool_runtime_helpers import (
    _log_safe_text_fields,
    _mcp_log_full_snippet_content_enabled,
    _mcp_log_pii_enabled,
    _mcp_log_snippet_previews_enabled,
)


logger = logging.getLogger(__name__)


def _coerce_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _snippet_payload_metrics(snippets: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """
    Compute lightweight telemetry for snippet payloads so we can benchmark prompt costs.
    """

    total_chars = 0
    chunk_count = 0
    upload_count = 0
    table_rich = 0
    issue_rich = 0
    truncated_tables = 0
    read_state_counter: Counter[str] = Counter()

    for entry in snippets:
        content = entry.get("content")
        if not isinstance(content, str):
            content = entry.get("summary") if isinstance(entry.get("summary"), str) else ""
        total_chars += len(content or "")
        if entry.get("chunk_id"):
            chunk_count += 1
        else:
            upload_count += 1
        structured_table_count = 0
        issue_count = 0
        try:
            structured_table_count = int(entry.get("structured_table_count") or 0)
            issue_count = int(entry.get("issue_count") or 0)
        except (TypeError, ValueError):
            structured_table_count = 0
            issue_count = 0
        if structured_table_count:
            table_rich += 1
        if issue_count:
            issue_rich += 1
        diag = entry.get("source_diagnostics") if isinstance(entry.get("source_diagnostics"), Mapping) else None
        if diag and diag.get("table_truncated"):
            truncated_tables += 1
        read_state = str(entry.get("read_state") or "summary")
        read_state_counter[read_state] += 1

    metrics = {
        "snippet_count": len(snippets),
        "chunk_snippet_count": chunk_count,
        "upload_snippet_count": upload_count,
        "char_count": total_chars,
        "token_estimate": _estimate_tokens(total_chars),
        "table_snippet_count": table_rich,
        "issue_snippet_count": issue_rich,
        "table_truncated_count": truncated_tables,
        "read_state_breakdown": dict(read_state_counter),
    }
    return metrics


def _log_tool_metrics(
    *,
    tool: str,
    conversation: Conversation,
    snippets: Sequence[Mapping[str, object]],
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """
    Emit structured telemetry for MCP tools without changing runtime behavior.
    """
    metrics = _snippet_payload_metrics(snippets)
    if extra:
        merged_extra = dict(extra)
    else:
        merged_extra = {}
    merged_extra.update(metrics)
    structured_log(
        "mcp",
        f"tool.{tool}",
        merged_extra,
        context={"business": conversation.business_profile_id, "conversation": conversation.id},
    )
    tags = {
        "tool": tool,
        "business": str(conversation.business_profile_id),
    }
    latency_monitor.observe("mcp.tool.char_count", metrics.get("char_count"), tags=tags)
    latency_monitor.observe("mcp.tool.snippet_count", metrics.get("snippet_count"), tags=tags)
    return metrics


def _snippet_preview_text(payload: Mapping[str, object]) -> str:
    candidates = [
        payload.get("raw_text"),
        payload.get("text"),
        payload.get("preview"),
        payload.get("content"),
        payload.get("summary"),
        payload.get("snippet"),
    ]
    for value in candidates:
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed:
                return trimmed[:200]
    rows = payload.get("rows")
    if isinstance(rows, list) and rows:
        first_row = rows[0]
        if isinstance(first_row, Mapping):
            cells = first_row.get("cells")
            if isinstance(cells, list):
                values: list[str] = []
                for cell in cells[:6]:
                    if isinstance(cell, Mapping):
                        text = cell.get("text") or cell.get("value")
                        if isinstance(text, str) and text.strip():
                            values.append(text.strip())
                if values:
                    return " | ".join(values)[:200]
    return ""


def _log_snippet_payloads(
    *,
    tool: str,
    conversation: Conversation,
    snippet_payloads: Sequence[Mapping[str, object]],
    meta: Mapping[str, object] | None = None,
) -> None:
    preview_items: list[dict[str, object]] = []
    include_previews = _mcp_log_snippet_previews_enabled()
    include_pii = _mcp_log_pii_enabled()
    include_full_content = _mcp_log_full_snippet_content_enabled()
    for payload in snippet_payloads[:5]:
        upload_id = payload.get("upload_id")
        chunk_id = payload.get("chunk_id") or payload.get("id")
        preview_text = _snippet_preview_text(payload) if include_previews else ""
        preview: str | None = None
        preview_hash: str | None = None
        preview_len: int | None = None
        if preview_text:
            preview_len = len(preview_text)
            if include_pii:
                preview = preview_text
            else:
                preview_hash = sha256_hex(preview_text)

        item_dict = {
            "label": payload.get("public_label") or payload.get("title") or payload.get("label"),
            "upload_id": str(upload_id) if upload_id else None,
            "chunk_id": str(chunk_id) if chunk_id else None,
            "read_state": payload.get("read_state"),
            "read_required": bool(payload.get("read_required")),
            "read_required_reasons": payload.get("read_required_reasons"),
            "is_table_chunk": bool(payload.get("is_table_chunk")),
            "score": payload.get("score"),
            "preview": preview,
            "preview_sha256": preview_hash,
            "preview_len": preview_len,
        }

        # Add full content when enabled
        if include_full_content:
            content = payload.get("content")
            if content is not None and content != "":
                content_text = content if isinstance(content, str) else str(content)
                item_dict["full_content_len"] = len(content_text)
                if include_pii:
                    item_dict["full_content"] = content_text
                else:
                    item_dict["full_content_sha256"] = sha256_hex(content_text)

            summary = payload.get("summary")
            if summary is not None and summary != "":
                summary_text = summary if isinstance(summary, str) else str(summary)
                item_dict["summary_len"] = len(summary_text)
                if include_pii:
                    item_dict["summary"] = summary_text
                else:
                    item_dict["summary_sha256"] = sha256_hex(summary_text)

            rows = payload.get("rows")
            if isinstance(rows, list) and rows:
                item_dict["rows_count"] = len(rows)
                if include_pii:
                    item_dict["rows"] = rows

        preview_items.append(item_dict)
    detail = dict(meta or {})
    if "query" in detail:
        query_value = _coerce_str(detail.pop("query")).strip()
        detail.update(_log_safe_text_fields("query", query_value or None))
    detail["snippet_count"] = len(snippet_payloads)
    if preview_items:
        detail["snippets"] = [{k: v for k, v in entry.items() if v not in (None, "")} for entry in preview_items]
    structured_log(
        "mcp",
        f"{tool}.snippets",
        detail,
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger,
    )
