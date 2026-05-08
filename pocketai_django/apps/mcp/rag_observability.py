"""Compact observability helpers for agentic RAG tool contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _clean(value: object) -> object:
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                continue
            cleaned = _clean(item)
            if cleaned in (None, "", [], {}):
                continue
            out[key] = cleaned
        return out
    if isinstance(value, list):
        out_list = [_clean(item) for item in value]
        return [item for item in out_list if item not in (None, "", [], {})]
    return value


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: object) -> int | None:
    for value in values:
        coerced = _coerce_int(value)
        if coerced is not None:
            return coerced
    return None


def _string(value: object) -> str:
    return str(value or "").strip()


def build_query_scope_observability(
    *,
    context: object | None,
    rewrite_result: object | None,
    rewrite_enabled: bool,
    rewrite_error: str | None = None,
) -> dict[str, object]:
    """Return a small, content-free record of the search scope decision."""

    strategy = _string(getattr(rewrite_result, "rewrite_strategy", ""))
    reason = _string(getattr(rewrite_result, "reason", "")) or strategy
    confidence = _coerce_float(getattr(rewrite_result, "confidence", None))
    context_injected = bool(getattr(rewrite_result, "context_injected", False))
    primary_upload_id = _string(getattr(context, "primary_upload_id", ""))
    referenced_ids = getattr(context, "referenced_upload_ids", []) if context is not None else []
    if not isinstance(referenced_ids, Sequence) or isinstance(referenced_ids, (str, bytes, bytearray)):
        referenced_ids = []

    if not rewrite_enabled:
        decision = "disabled"
        topic_scope = "not_evaluated"
        reason = reason or "rewrite_disabled"
    elif rewrite_error:
        decision = "error"
        topic_scope = "not_evaluated"
        reason = "rewrite_error"
    elif rewrite_result is None:
        decision = "not_evaluated"
        topic_scope = "not_evaluated"
        reason = "rewrite_not_evaluated" if primary_upload_id else "no_primary_document"
    elif context_injected:
        decision = "followup"
        topic_scope = "followup"
    elif strategy == "already_contextual":
        decision = "followup"
        topic_scope = "followup"
        reason = reason or "already_contextual"
    elif strategy == "topic_shift" or reason == "topic_shift":
        decision = "new_topic"
        topic_scope = "new_topic"
        reason = "topic_shift"
    else:
        decision = "not_followup"
        topic_scope = "new_topic"
        reason = reason or strategy or "not_followup"

    continuity_allowed = bool(
        rewrite_enabled
        and rewrite_result is not None
        and (context_injected or strategy == "already_contextual")
    )

    payload: dict[str, object] = {
        "rewrite_enabled": bool(rewrite_enabled),
        "query_rewritten": bool(context_injected),
        "followup_decision": decision,
        "topic_scope": topic_scope,
        "strategy": strategy,
        "reason": reason,
        "confidence": confidence,
        "primary_upload_id": primary_upload_id or None,
        "primary_document_present": bool(primary_upload_id),
        "referenced_upload_count": len(list(referenced_ids)),
        "document_continuity_allowed": continuity_allowed,
        "document_continuity_reason": reason or decision,
    }
    if rewrite_error:
        payload["rewrite_error"] = _string(rewrite_error)[:160]
    return _clean(payload)  # type: ignore[return-value]


def _item_table_id(item: object) -> str:
    if not isinstance(item, Mapping):
        return ""
    coverage = item.get("coverage_hint")
    if isinstance(coverage, Mapping):
        table_id = _string(coverage.get("table_id"))
        if table_id:
            return table_id
    for key in ("table_id", "canonical_table_id"):
        table_id = _string(item.get(key))
        if table_id:
            return table_id
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("table_id", "canonical_table_id", "table_uuid"):
            table_id = _string(metadata.get(key))
            if table_id:
                return table_id
    return ""


def _distinct_returned_tables(*collections: object) -> int:
    table_ids: set[str] = set()
    for collection in collections:
        if not isinstance(collection, Sequence) or isinstance(collection, (str, bytes, bytearray)):
            continue
        for item in collection:
            table_id = _item_table_id(item)
            if table_id:
                table_ids.add(table_id)
    return len(table_ids)


def _completeness_status(
    *,
    status: str,
    completeness: Mapping[str, object],
    enumeration_diag: Mapping[str, object],
) -> str:
    enum_status = _string(enumeration_diag.get("completeness_status"))
    if enum_status:
        return enum_status
    if status == "not_found":
        return "not_found"
    if bool(completeness.get("has_more")):
        return "partial"
    shown = _first_int(completeness.get("shown"))
    total = _first_int(
        completeness.get("refs_total_found"),
        completeness.get("snippets_total_found"),
        completeness.get("total_found"),
    )
    if status == "ok" and shown is not None and total is not None and shown >= total:
        return "complete"
    if status == "ok" and not bool(completeness.get("has_more")):
        return "complete"
    return "partial" if status == "ok" else "unknown"


def compact_retrieval_observability(value: object) -> dict[str, object]:
    """Keep only small, safe observability fields for traces/prompts."""

    if not isinstance(value, Mapping):
        return {}
    allowed: dict[str, tuple[str, ...]] = {
        "query_scope": (
            "rewrite_enabled",
            "query_rewritten",
            "followup_decision",
            "topic_scope",
            "strategy",
            "reason",
            "confidence",
            "primary_upload_id",
            "primary_document_present",
            "referenced_upload_count",
            "document_continuity_allowed",
            "document_continuity_reason",
            "rewrite_error",
        ),
        "continuity": (
            "allowed",
            "reason",
            "primary_upload_id",
            "referenced_upload_count",
            "rag_received_context",
            "boosted_candidates",
            "max_bonus",
        ),
        "enumeration": (
            "triggered",
            "reason",
            "attribute",
            "candidate_rows",
            "matched_rows",
            "expanded_rows",
            "returned_items",
            "source_table_count",
            "completeness_status",
            "error",
        ),
        "table_coverage": (
            "tables_considered",
            "tables_returned",
            "table_uploads",
            "coverage_diversification_applied",
            "coverage_diversification_input_count",
            "coverage_diversification_output_count",
            "coverage_diversification_bucket_count",
        ),
        "evidence_completeness": (
            "status",
            "search_status",
            "shown",
            "total_found",
            "snippets_total_found",
            "refs_total_found",
            "has_more",
            "paging_mode",
            "enumeration_status",
        ),
    }
    compact: dict[str, object] = {}
    for section, keys in allowed.items():
        raw_section = value.get(section)
        if not isinstance(raw_section, Mapping):
            continue
        section_out = {key: raw_section.get(key) for key in keys if key in raw_section}
        cleaned = _clean(section_out)
        if cleaned not in ({}, None):
            compact[section] = cleaned
    return compact


def build_retrieval_observability(
    *,
    query_scope: Mapping[str, object] | None,
    diagnostics: Mapping[str, object] | None,
    completeness: Mapping[str, object] | None,
    refs: object,
    snippets: object,
    enumeration_diag: Mapping[str, object] | None,
    status: str,
) -> dict[str, object]:
    diag = diagnostics or {}
    completion = completeness or {}
    enum_diag = enumeration_diag or {"triggered": False}
    query_scope_payload = dict(query_scope or {})
    search_status = _string(status) or "unknown"

    tables_considered = _first_int(
        diag.get("coverage_diversification_table_buckets"),
        diag.get("tabular_table_count"),
        enum_diag.get("source_table_count"),
    )
    table_coverage = {
        "tables_considered": tables_considered,
        "tables_returned": _distinct_returned_tables(refs, snippets),
        "table_uploads": _first_int(diag.get("tabular_table_uploads")),
        "coverage_diversification_applied": bool(diag.get("coverage_diversification_applied")),
        "coverage_diversification_input_count": _first_int(diag.get("coverage_diversification_input_count")),
        "coverage_diversification_output_count": _first_int(diag.get("coverage_diversification_output_count")),
        "coverage_diversification_bucket_count": _first_int(diag.get("coverage_diversification_bucket_count")),
    }
    continuity = {
        "allowed": bool(
            query_scope_payload.get("document_continuity_allowed")
            or diag.get("document_continuity_allowed")
        ),
        "reason": query_scope_payload.get("document_continuity_reason") or diag.get("document_continuity_reason"),
        "primary_upload_id": query_scope_payload.get("primary_upload_id"),
        "referenced_upload_count": query_scope_payload.get("referenced_upload_count"),
        "rag_received_context": "document_continuity_allowed" in diag,
        "boosted_candidates": _first_int(diag.get("document_continuity_boosted_candidates")),
        "max_bonus": _coerce_float(diag.get("document_continuity_max_bonus")),
    }
    evidence_completeness = {
        "status": _completeness_status(
            status=search_status,
            completeness=completion,
            enumeration_diag=enum_diag,
        ),
        "search_status": search_status,
        "shown": _first_int(completion.get("shown")),
        "total_found": _first_int(completion.get("total_found")),
        "snippets_total_found": _first_int(completion.get("snippets_total_found")),
        "refs_total_found": _first_int(completion.get("refs_total_found")),
        "has_more": bool(completion.get("has_more")),
        "paging_mode": completion.get("paging_mode"),
        "enumeration_status": enum_diag.get("completeness_status"),
    }
    enumeration = {
        key: enum_diag.get(key)
        for key in (
            "triggered",
            "reason",
            "attribute",
            "candidate_rows",
            "matched_rows",
            "expanded_rows",
            "returned_items",
            "source_table_count",
            "completeness_status",
            "error",
        )
        if key in enum_diag
    }
    return compact_retrieval_observability(
        {
            "query_scope": query_scope_payload,
            "continuity": continuity,
            "enumeration": enumeration,
            "table_coverage": table_coverage,
            "evidence_completeness": evidence_completeness,
        }
    )
