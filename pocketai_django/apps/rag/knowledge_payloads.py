from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from apps.rag.contracts import KNOWLEDGE_READ_STATE_SUMMARY


def coerce_int(value: object) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def format_count(value: int) -> str:
    if value <= 0:
        return "0"
    for threshold, suffix in ((1_000_000, "M"), (1_000, "k")):
        if value >= threshold:
            scaled = value / threshold
            scaled_str = f"{scaled:.1f}" if scaled % 1 else str(int(scaled))
            scaled_str = scaled_str.rstrip("0").rstrip(".")
            return f"{scaled_str}{suffix}"
    return f"{value:,}"


def build_truncation_note_from_diagnostics(
    diagnostics: Mapping[str, object] | None,
    *,
    label: str | None,
    partial_index: bool = False,
) -> str:
    diag = diagnostics if isinstance(diagnostics, Mapping) else {}
    label_text = (label or "This document").strip() or "This document"
    truncated_rows = coerce_int(diag.get("truncated_rows"))
    truncated_tables = coerce_int(diag.get("truncated_tables"))
    truncated_columns = coerce_int(diag.get("truncated_columns"))
    truncated_entities = coerce_int(diag.get("truncated_entities"))
    total_rows = coerce_int(diag.get("table_total_rows") or diag.get("table_source_rows"))
    indexed_rows = coerce_int(diag.get("table_indexed_rows"))
    row_cap = coerce_int(diag.get("table_row_cap"))
    if not indexed_rows and row_cap:
        indexed_rows = row_cap
    partial_tables = coerce_int(diag.get("table_partial_tables"))
    partial_flag = partial_index or bool(diag.get("partial_index"))

    note = ""
    if indexed_rows and total_rows and indexed_rows < total_rows:
        note = (
            f"{label_text} only indexes the first {format_count(indexed_rows)} of ~{format_count(total_rows)} rows; "
            "later rows are unknown"
        )
    elif truncated_rows and row_cap:
        note = (
            f"{label_text} stops after {format_count(row_cap)} rows and skips at least "
            f"{format_count(truncated_rows)} later rows"
        )
    elif truncated_rows:
        note = f"{label_text} dropped {format_count(truncated_rows)} rows beyond the ingest cap"
    elif partial_flag and indexed_rows:
        note = (
            f"{label_text} is partially indexed ({format_count(indexed_rows)} rows captured); "
            "request another source if you need the remaining rows"
        )
    elif partial_flag:
        note = f"{label_text} is only partially indexed; some rows or records may be missing"
    elif truncated_entities:
        note = (
            f"{label_text} only captured a subset of structured records; "
            f"{format_count(truncated_entities)} records were left out"
        )
    elif truncated_columns:
        note = f"{label_text} omitted {format_count(truncated_columns)} columns, so some fields are missing"
    elif truncated_tables or partial_tables:
        count = truncated_tables or partial_tables
        note = f"{label_text} skipped {format_count(count)} tables or sheets due to size limits"

    note = note.strip()
    if note and not note.endswith("."):
        note = f"{note}."
    return note


def serialize_knowledge_snippet(snippet: Any) -> dict[str, object]:
    """
    Convert a KnowledgeSnippet-like object into the stable prompt/cache payload.

    This is active shared retrieval plumbing used by MCP and compatibility paths.
    It intentionally accepts `Any` to avoid coupling this utility module back to
    the large retrieval service module that currently defines KnowledgeSnippet.
    """

    payload: dict[str, object] = {
        "id": str(snippet.id),
        "title": snippet.title,
        "summary": snippet.summary,
        "source": snippet.source,
        "content": snippet.content or "",
        "content_mode": snippet.content_mode if snippet.content_mode else ("full" if snippet.content else None),
        "public_label": snippet.public_label or "",
        "structuredTables": [] if snippet.is_table_chunk else list(snippet.structured_tables or ()),
        "needs_table_refresh": bool(snippet.is_table_chunk),
        "issues": list(snippet.issues or ()),
        "pageSummaries": list(snippet.page_summaries or ()),
        "read_state": snippet.read_state or KNOWLEDGE_READ_STATE_SUMMARY,
        "topic_hints": list(snippet.topic_hints or ()),
        "is_pinned": bool(snippet.is_pinned),
        "structured_table_count": int(snippet.structured_table_count or 0),
        "issue_count": int(snippet.issue_count or 0),
        "supplemental_sections": list(snippet.supplemental_sections or ()),
        "page_number": snippet.page_number,
        "page_mode": snippet.page_mode,
    }
    if snippet.structured_table_hint:
        payload["structured_table_hint"] = snippet.structured_table_hint
    if snippet.search_stage:
        payload["search_stage"] = snippet.search_stage
    if snippet.confidence_score is not None:
        payload["confidence_score"] = float(snippet.confidence_score)
    payload["truncated"] = bool(snippet.truncated)
    payload["source_diagnostics"] = dict(snippet.source_diagnostics or {})
    if payload["source_diagnostics"].get("table_read_only"):  # type: ignore[union-attr]
        payload["table_read_only"] = True
    partial_index = bool(snippet.partial_index)
    payload["partial_index"] = partial_index
    payload["aliases"] = list(snippet.aliases or ())
    truncation_note = build_truncation_note_from_diagnostics(
        payload["source_diagnostics"] if isinstance(payload["source_diagnostics"], Mapping) else {},
        label=payload.get("public_label") or payload.get("title"),
        partial_index=partial_index,
    )
    if truncation_note:
        payload["truncation_note"] = truncation_note
    if snippet.upload_id:
        payload["upload_id"] = str(snippet.upload_id)
    if snippet.chunk_id:
        payload["chunk_id"] = str(snippet.chunk_id)
    if snippet.chunk_index is not None:
        payload["chunk_index"] = int(snippet.chunk_index)
    if snippet.entity_type:
        payload["entity_type"] = snippet.entity_type
    if snippet.entity_name:
        payload["entity_name"] = snippet.entity_name
    if snippet.entity_business:
        payload["entity_business"] = snippet.entity_business
    payload["is_table_chunk"] = bool(snippet.is_table_chunk)
    if snippet.table_id:
        payload["table_id"] = snippet.table_id
    if snippet.evidence_group_id:
        payload["evidence_group_id"] = snippet.evidence_group_id
    if snippet.evidence_type:
        payload["evidence_type"] = snippet.evidence_type
    if snippet.representation:
        payload["representation"] = snippet.representation
    return payload


def normalize_issue_severity(issue: Mapping[str, object]) -> str | None:
    severity = str(issue.get("severity") or "").strip().lower()
    if severity and severity not in {"warning", "error"}:
        return None
    return severity or "warning"


def issue_warning_payloads(
    issues: Sequence[Mapping[str, object]] | None,
    *,
    label: str,
    upload_id: str,
) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []
    if not isinstance(issues, Sequence) or isinstance(issues, (str, bytes)):
        return warnings
    for issue in issues:
        if not isinstance(issue, Mapping):
            continue
        severity = normalize_issue_severity(issue)
        if not severity:
            continue
        raw_code = issue.get("issue_code") or issue.get("code") or "ingestion_issue"
        code = str(raw_code).strip().lower()
        if "truncate" not in code and "missing" not in code:
            continue
        details = (
            issue.get("summary")
            or issue.get("message")
            or issue.get("details")
            or issue.get("explanation")
            or issue.get("label")
        )
        if not details:
            details = f"{label} reported ingestion issue {raw_code}."
        warnings.append(
            {
                "upload_id": str(upload_id),
                "label": label,
                "type": str(raw_code) or "ingestion_issue",
                "severity": severity,
                "details": str(details),
            }
        )
    return warnings


def diagnostic_warning_payload(
    *,
    label: str,
    upload_id: str,
    diagnostics: Mapping[str, object] | None,
    partial_index: bool,
    truncation_note: str | None,
) -> dict[str, object] | None:
    diag = diagnostics if isinstance(diagnostics, Mapping) else {}
    note = (truncation_note or build_truncation_note_from_diagnostics(diag, label=label, partial_index=partial_index)).strip()
    if not note:
        return None
    truncated_rows = coerce_int(diag.get("truncated_rows"))
    truncated_entities = coerce_int(diag.get("truncated_entities"))
    truncated_columns = coerce_int(diag.get("truncated_columns"))
    truncated_tables = coerce_int(diag.get("truncated_tables"))
    partial_tables = coerce_int(diag.get("table_partial_tables"))
    indexed_rows = coerce_int(diag.get("table_indexed_rows")) or coerce_int(diag.get("table_row_cap"))
    total_rows = coerce_int(diag.get("table_total_rows") or diag.get("table_source_rows"))

    warning_type = "ingestion_truncation"
    if truncated_rows or (indexed_rows and total_rows and indexed_rows < total_rows) or partial_index:
        warning_type = "table_rows_truncated"
    elif truncated_entities:
        warning_type = "structured_records_truncated"
    elif truncated_columns:
        warning_type = "table_columns_truncated"
    elif truncated_tables or partial_tables:
        warning_type = "table_count_truncated"

    return {
        "upload_id": str(upload_id),
        "label": label,
        "type": warning_type,
        "severity": "warning",
        "details": note,
    }
