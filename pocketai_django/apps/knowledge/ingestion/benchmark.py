from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.text import slugify

from core.tenancy import tenant_bypass

from .models import (
    KnowledgeIngestionJob,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadIssue,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
)

QUALITY_GATE_THRESHOLD_DEFAULTS: dict[str, float] = {
    "min_row_recall": 0.99,
    "min_row_order_stability": 0.95,
    "min_scope_f1": 0.95,
    "min_critical_value_coverage": 0.95,
    "min_scope_metadata_coverage": 1.0,
}

PDF_PORTFOLIO_EXPECTED_TABLE_MODES: frozenset[str] = frozenset({"none", "some", "range"})


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    for entry in value:
        token = str(entry or "").strip()
        if token:
            cleaned.append(token)
    return cleaned


def _chunk_bucket(metadata: Mapping[str, Any]) -> str:
    source = str(metadata.get("content_source") or "None")
    role = str(metadata.get("table_chunk_role") or "None")
    priority = str(metadata.get("chunk_priority") or "None")
    return f"{source}/{role}/{priority}"


def _table_schema_keys(column_schema: Any) -> list[str]:
    if not isinstance(column_schema, list):
        return []
    keys: list[str] = []
    for col in column_schema:
        if isinstance(col, Mapping):
            key = str(col.get("key") or col.get("name") or col.get("label") or "").strip()
        else:
            key = str(col or "").strip()
        if key:
            keys.append(key)
    return keys


def _row_focus_filter(
    rows: Sequence[dict[str, Any]],
    *,
    focus_row_start: int | None = None,
    focus_row_end: int | None = None,
) -> list[dict[str, Any]]:
    if focus_row_start is None and focus_row_end is None:
        return list(rows)
    result: list[dict[str, Any]] = []
    for row in rows:
        idx = _safe_int(row.get("table_row_index"))
        if idx is None:
            continue
        if focus_row_start is not None and idx < focus_row_start:
            continue
        if focus_row_end is not None and idx > focus_row_end:
            continue
        result.append(row)
    return result


def _scope_metrics(row_chunks: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(row_chunks)
    multi_scope = 0
    ambiguous = 0
    inferred = 0
    explicit = 0
    histogram: Counter[int] = Counter()
    for row in row_chunks:
        applies = _row_scope_columns(row)
        card = len(applies)
        histogram[card] += 1
        if card > 1:
            multi_scope += 1
        if card <= 1:
            ambiguous += 1
        mode = _row_scope_reason(row).lower()
        if mode.startswith("inferred") or mode in {
            "scope_repeated_value_span",
            "scope_sparse_expansion",
            "scope_edge_completion",
        }:
            inferred += 1
        if mode in {"scope_explicit_span", "explicit_span"}:
            explicit += 1
    return {
        "row_chunk_count": total,
        "multi_scope_count": multi_scope,
        "multi_scope_rate": round(multi_scope / max(1, total), 4),
        "ambiguous_scope_count": ambiguous,
        "ambiguous_scope_rate": round(ambiguous / max(1, total), 4),
        "inferred_scope_count": inferred,
        "inferred_scope_rate": round(inferred / max(1, total), 4),
        "explicit_scope_count": explicit,
        "explicit_scope_rate": round(explicit / max(1, total), 4),
        "scope_cardinality_histogram": {str(k): int(v) for k, v in sorted(histogram.items(), key=lambda item: item[0])},
    }


def _normalized_token(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _row_scope_columns(row: Mapping[str, Any]) -> list[str]:
    return _clean_list(row.get("inferred_scope_columns"))


def _row_scope_reason(row: Mapping[str, Any]) -> str:
    return str(row.get("scope_reason") or "").strip()


def _row_scope_confidence(row: Mapping[str, Any]) -> float | None:
    confidence = _safe_float(row.get("table_row_scope_confidence"))
    if confidence is not None:
        return confidence
    return _safe_float(row.get("scope_confidence"))


def _normalized_scope_set(row: Mapping[str, Any]) -> set[str]:
    return {
        token
        for token in (_normalized_token(item) for item in _row_scope_columns(row))
        if token
    }


def _row_has_scope_metadata(row: Mapping[str, Any]) -> bool:
    mode = _normalized_token(_row_scope_reason(row))
    confidence = _row_scope_confidence(row)
    return bool(mode) and confidence is not None


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    current = [0] * (len(right) + 1)
    for left_token in left:
        for idx, right_token in enumerate(right, start=1):
            if left_token == right_token:
                current[idx] = previous[idx - 1] + 1
            else:
                current[idx] = max(previous[idx], current[idx - 1])
        previous, current = current, [0] * (len(right) + 1)
    return previous[-1]


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if float(denominator) <= 0:
        return 1.0
    return float(numerator) / float(denominator)


def _build_quality_gate_metrics(
    *,
    baseline_rows: Sequence[dict[str, Any]],
    candidate_rows: Sequence[dict[str, Any]],
    baseline_map: Mapping[str, dict[str, Any]],
    candidate_map: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    baseline_keys = set(baseline_map.keys())
    candidate_keys = set(candidate_map.keys())
    shared_keys = baseline_keys & candidate_keys

    row_recall = _safe_ratio(len(shared_keys), len(baseline_keys))
    row_precision = _safe_ratio(len(shared_keys), len(candidate_keys))

    baseline_order = [key for key in baseline_map.keys() if key in shared_keys]
    candidate_order = [key for key in candidate_map.keys() if key in shared_keys]
    lcs = _lcs_length(baseline_order, candidate_order)
    row_order_stability = _safe_ratio(lcs, len(baseline_order))

    baseline_scope_pairs = {
        (row_key, label)
        for row_key, row in baseline_map.items()
        for label in _normalized_scope_set(row)
    }
    candidate_scope_pairs = {
        (row_key, label)
        for row_key, row in candidate_map.items()
        for label in _normalized_scope_set(row)
    }
    scope_tp = len(baseline_scope_pairs & candidate_scope_pairs)
    scope_fp = len(candidate_scope_pairs - baseline_scope_pairs)
    scope_fn = len(baseline_scope_pairs - candidate_scope_pairs)
    scope_precision = _safe_ratio(scope_tp, scope_tp + scope_fp)
    scope_recall = _safe_ratio(scope_tp, scope_tp + scope_fn)
    if (scope_precision + scope_recall) <= 0:
        scope_f1 = 1.0
    else:
        scope_f1 = (2.0 * scope_precision * scope_recall) / (scope_precision + scope_recall)

    baseline_critical_values = {
        row_key: _normalized_token(row.get("table_row_fee_value"))
        for row_key, row in baseline_map.items()
        if _normalized_token(row.get("table_row_fee_value"))
    }
    critical_total = len(baseline_critical_values)
    critical_present = 0
    critical_matched = 0
    for row_key, baseline_value in baseline_critical_values.items():
        candidate_row = candidate_map.get(row_key)
        if not candidate_row:
            continue
        candidate_value = _normalized_token(candidate_row.get("table_row_fee_value"))
        if candidate_value:
            critical_present += 1
        if candidate_value == baseline_value:
            critical_matched += 1
    critical_value_presence_coverage = _safe_ratio(critical_present, critical_total)
    critical_value_coverage = _safe_ratio(critical_matched, critical_total)

    candidate_scope_rows = [
        row
        for row in candidate_rows
        if _row_scope_columns(row)
    ]
    scope_rows_count = len(candidate_scope_rows)
    scope_metadata_ready = sum(1 for row in candidate_scope_rows if _row_has_scope_metadata(row))
    scope_metadata_coverage = _safe_ratio(scope_metadata_ready, scope_rows_count)

    return {
        "row_recall": round(row_recall, 4),
        "row_precision": round(row_precision, 4),
        "row_order_stability": round(row_order_stability, 4),
        "scope_precision": round(scope_precision, 4),
        "scope_recall": round(scope_recall, 4),
        "scope_f1": round(scope_f1, 4),
        "critical_value_total": int(critical_total),
        "critical_value_present_count": int(critical_present),
        "critical_value_matched_count": int(critical_matched),
        "critical_value_presence_coverage": round(critical_value_presence_coverage, 4),
        "critical_value_coverage": round(critical_value_coverage, 4),
        "scope_rows_count": int(scope_rows_count),
        "scope_metadata_ready_count": int(scope_metadata_ready),
        "scope_metadata_coverage": round(scope_metadata_coverage, 4),
    }


def quality_gate_thresholds(
    *,
    min_row_recall: float | None = None,
    min_row_order_stability: float | None = None,
    min_scope_f1: float | None = None,
    min_critical_value_coverage: float | None = None,
    min_scope_metadata_coverage: float | None = None,
) -> dict[str, float]:
    resolved = dict(QUALITY_GATE_THRESHOLD_DEFAULTS)
    overrides = {
        "min_row_recall": min_row_recall,
        "min_row_order_stability": min_row_order_stability,
        "min_scope_f1": min_scope_f1,
        "min_critical_value_coverage": min_critical_value_coverage,
        "min_scope_metadata_coverage": min_scope_metadata_coverage,
    }
    for key, value in overrides.items():
        if value is None:
            continue
        try:
            resolved[key] = float(value)
        except Exception:
            continue
    return resolved


def evaluate_quality_gate(
    report: Mapping[str, Any],
    *,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metrics = _metadata_dict(report.get("quality_gate_metrics"))
    resolved_thresholds = quality_gate_thresholds(
        min_row_recall=_safe_float((thresholds or {}).get("min_row_recall")),
        min_row_order_stability=_safe_float((thresholds or {}).get("min_row_order_stability")),
        min_scope_f1=_safe_float((thresholds or {}).get("min_scope_f1")),
        min_critical_value_coverage=_safe_float((thresholds or {}).get("min_critical_value_coverage")),
        min_scope_metadata_coverage=_safe_float((thresholds or {}).get("min_scope_metadata_coverage")),
    )
    failed_checks: list[str] = []

    row_recall = _safe_float(metrics.get("row_recall")) or 0.0
    if row_recall < float(resolved_thresholds["min_row_recall"]):
        failed_checks.append("row_recall")

    row_order = _safe_float(metrics.get("row_order_stability")) or 0.0
    if row_order < float(resolved_thresholds["min_row_order_stability"]):
        failed_checks.append("row_order_stability")

    scope_f1 = _safe_float(metrics.get("scope_f1")) or 0.0
    if scope_f1 < float(resolved_thresholds["min_scope_f1"]):
        failed_checks.append("scope_f1")

    critical_value_coverage = _safe_float(metrics.get("critical_value_coverage")) or 0.0
    if critical_value_coverage < float(resolved_thresholds["min_critical_value_coverage"]):
        failed_checks.append("critical_value_coverage")

    scope_metadata_coverage = _safe_float(metrics.get("scope_metadata_coverage")) or 0.0
    if scope_metadata_coverage < float(resolved_thresholds["min_scope_metadata_coverage"]):
        failed_checks.append("scope_metadata_coverage")

    regressions = [str(item) for item in (report.get("regressions") or []) if str(item).strip()]
    passed = not failed_checks and not regressions
    return {
        "passed": bool(passed),
        "thresholds": resolved_thresholds,
        "metrics": metrics,
        "failed_checks": failed_checks,
        "regressions": regressions,
    }


def _as_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def capture_upload_snapshot(
    *,
    upload_id: str,
    snapshot_label: str,
    terms: Sequence[str] | None = None,
    focus_row_start: int | None = None,
    focus_row_end: int | None = None,
) -> dict[str, Any]:
    with tenant_bypass():
        upload = (
            KnowledgeUpload.objects.select_related("business_profile")
            .filter(id=upload_id)
            .first()
        )
        if upload is None:
            raise ValueError(f"Upload not found: {upload_id}")

        latest_job = (
            KnowledgeIngestionJob.objects.filter(upload=upload, job_type="ingest")
            .order_by("-created_at")
            .first()
        )
        if latest_job is None:
            latest_job = (
                KnowledgeIngestionJob.objects.filter(upload=upload)
                .order_by("-created_at")
                .first()
            )

        issue_rows = (
            KnowledgeUploadIssue.objects.filter(upload=upload)
            .order_by("-created_at")
            .only(
                "issue_code",
                "severity",
                "description",
                "detected_by",
                "resolved",
                "page_id",
                "table_id",
                "table_row_id",
                "table_cell_id",
                "details",
            )
        )
        issues: list[dict[str, Any]] = []
        for issue in issue_rows:
            issues.append(
                {
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "description": issue.description,
                    "detected_by": issue.detected_by,
                    "resolved": bool(issue.resolved),
                    "page_id": str(issue.page_id) if issue.page_id else None,
                    "table_id": str(issue.table_id) if issue.table_id else None,
                    "table_row_id": str(issue.table_row_id) if issue.table_row_id else None,
                    "table_cell_id": str(issue.table_cell_id) if issue.table_cell_id else None,
                    "details": _metadata_dict(issue.details),
                }
            )

        chunk_rows = (
            KnowledgeUploadChunk.objects.filter(upload=upload)
            .order_by("chunk_index")
            .only("chunk_index", "content", "metadata")
        )
        chunk_counts: Counter[str] = Counter()
        row_chunks: list[dict[str, Any]] = []
        chunk_cache: list[dict[str, Any]] = []
        for chunk in chunk_rows:
            metadata = _metadata_dict(chunk.metadata)
            bucket = _chunk_bucket(metadata)
            chunk_counts[bucket] += 1
            source = str(metadata.get("content_source") or "")
            role = str(metadata.get("table_chunk_role") or "")
            row_index = _safe_int(metadata.get("table_row_index"))
            is_row_chunk = source == "table_row" or role == "row" or row_index is not None
            inferred_scope = _clean_list(metadata.get("table_row_inferred_scope_columns"))
            scope_reason = str(metadata.get("table_row_scope_reason") or "").strip()
            scope_confidence = _safe_float(metadata.get("table_row_scope_confidence"))
            record = {
                "chunk_index": int(chunk.chunk_index),
                "table_id": str(metadata.get("table_id") or ""),
                "table_anchor": str(metadata.get("table_anchor") or ""),
                "content_source": source or None,
                "table_chunk_role": role or None,
                "table_row_index": row_index,
                "inferred_scope_columns": inferred_scope,
                "observed_value_columns": _clean_list(metadata.get("table_row_observed_value_columns")),
                "qualifier_columns": _clean_list(metadata.get("table_row_qualifier_columns")),
                "scope_dimension_columns": _clean_list(metadata.get("table_row_scope_dimension_columns")),
                "scope_reason": scope_reason or None,
                "table_row_fee_value": str(metadata.get("table_row_fee_value") or "").strip() or None,
                "table_row_scope_confidence": scope_confidence,
                "text": str(chunk.content or ""),
            }
            chunk_cache.append(record)
            if is_row_chunk:
                row_chunks.append(record)

        row_chunks.sort(
            key=lambda item: (
                str(item.get("table_id") or ""),
                _safe_int(item.get("table_row_index")) if _safe_int(item.get("table_row_index")) is not None else 10**9,
                int(item.get("chunk_index") or 0),
            )
        )
        focused_row_chunks = _row_focus_filter(
            row_chunks,
            focus_row_start=focus_row_start,
            focus_row_end=focus_row_end,
        )

        table_qs = (
            KnowledgeUploadTable.objects.filter(upload=upload)
            .select_related("page")
            .prefetch_related(
                Prefetch(
                    "rows",
                    queryset=KnowledgeUploadTableRow.objects.order_by("row_index").prefetch_related(
                        Prefetch(
                            "cells",
                            queryset=KnowledgeUploadTableCell.objects.order_by("column_index"),
                        )
                    ),
                )
            )
            .order_by("order_index")
        )
        tables: list[dict[str, Any]] = []
        data_row_count = 0
        for table in table_qs:
            table_meta = _metadata_dict(table.metadata)
            table_rows: list[dict[str, Any]] = []
            for row in table.rows.all():
                row_meta = _metadata_dict(row.metadata)
                row_type = str(row_meta.get("row_type") or ("header" if int(row.row_index) == 0 else "data"))
                if row_type == "data":
                    data_row_count += 1
                non_empty_cells: list[dict[str, Any]] = []
                for cell in row.cells.all():
                    raw_text = str(cell.raw_text or "").strip()
                    if not raw_text:
                        continue
                    cell_meta = _metadata_dict(cell.metadata)
                    non_empty_cells.append(
                        {
                            "column_index": int(cell.column_index),
                            "column_key": str(cell.column_key or ""),
                            "raw_text": raw_text,
                            "column_span": _safe_int(cell_meta.get("column_span")),
                        }
                    )
                table_rows.append(
                    {
                        "row_index": int(row.row_index),
                        "row_type": row_type,
                        "contract_version": str(row_meta.get("table_scope_contract_version") or "").strip() or None,
                        "observed_value_columns": _clean_list(row_meta.get("observed_value_columns")) or None,
                        "qualifier_columns": _clean_list(row_meta.get("qualifier_columns")) or None,
                        "scope_dimension_columns": _clean_list(row_meta.get("scope_dimension_columns")) or None,
                        "inferred_scope_columns": _clean_list(row_meta.get("inferred_scope_columns")) or None,
                        "scope_reason": str(row_meta.get("scope_reason") or "").strip() or None,
                        "scope_confidence": _safe_float(row_meta.get("scope_confidence")),
                        "non_empty_cells": non_empty_cells,
                    }
                )
            tables.append(
                {
                    "table_id": str(table.id),
                    "order_index": int(table.order_index),
                    "title": str(table.title or ""),
                    "page_number": int(table.page.page_number) if table.page_id and table.page else None,
                    "schema": _table_schema_keys(table.column_schema),
                    "bbox": _metadata_dict(table.bbox),
                    "metadata": table_meta,
                    "row_count": len(table_rows),
                    "rows": table_rows,
                }
            )

        term_hits: dict[str, list[dict[str, Any]]] = {}
        for term in terms or []:
            normalized_term = str(term or "").strip()
            if not normalized_term:
                continue
            lower_term = normalized_term.lower()
            matches: list[dict[str, Any]] = []
            for chunk in chunk_cache:
                content = str(chunk.get("text") or "").lower()
                if lower_term not in content:
                    continue
                matches.append(
                    {
                        "chunk_index": int(chunk.get("chunk_index") or 0),
                        "content_source": chunk.get("content_source"),
                        "table_chunk_role": chunk.get("table_chunk_role"),
                        "table_row_index": chunk.get("table_row_index"),
                    }
                )
            term_hits[normalized_term] = matches

        ingestion_metadata = _metadata_dict(upload.ingestion_metadata)
        table_extraction = _metadata_dict(ingestion_metadata.get("table_extraction"))
        baseline_metrics = _metadata_dict(table_extraction.get("baseline_metrics"))
        quality_metrics = {
            "table_bbox_coverage_ratio": _safe_float(baseline_metrics.get("table_bbox_coverage_ratio")),
            "residual_text_ratio": _safe_float(baseline_metrics.get("residual_text_ratio")),
            "residual_text_blocks_count": _safe_int(baseline_metrics.get("residual_text_blocks_count")),
            "suppressed_text_blocks_count": _safe_int(baseline_metrics.get("suppressed_text_blocks_count")),
            "table_row_unique_evidence_count": _safe_int(baseline_metrics.get("table_row_unique_evidence_count")),
        }

        snapshot = {
            "snapshot_label": snapshot_label,
            "captured_at": timezone.now().isoformat(),
            "upload_id": str(upload.id),
            "business_profile_id": str(upload.business_profile_id),
            "upload_status": upload.status,
            "upload_created_at": upload.created_at.isoformat() if upload.created_at else None,
            "upload_updated_at": upload.updated_at.isoformat() if upload.updated_at else None,
            "display_name": str(upload.display_name or ""),
            "source_name": str(upload.source_name or ""),
            "settings": {
                "AZURE_DOCUMENT_INTELLIGENCE_API_VERSION": str(
                    getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_API_VERSION", "") or ""
                ),
                "AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH": str(
                    getattr(settings, "AZURE_DOCUMENT_INTELLIGENCE_BASE_PATH", "") or ""
                ),
                "RAG_TABLE_VLM_ENABLED": bool(getattr(settings, "RAG_TABLE_VLM_ENABLED", False)),
                "RAG_TABLE_VLM_CONFIDENCE_THRESHOLD": _safe_float(
                    getattr(settings, "RAG_TABLE_VLM_CONFIDENCE_THRESHOLD", None)
                ),
                "RAG_PDF_TABLE_EXTRACTOR": str(getattr(settings, "RAG_PDF_TABLE_EXTRACTOR", "") or ""),
            },
            "job": {
                "job_id": str(latest_job.id) if latest_job else None,
                "status": str(latest_job.status) if latest_job else None,
                "attempt_count": int(latest_job.attempt_count) if latest_job else None,
                "payload": _metadata_dict(latest_job.payload) if latest_job else {},
                "error_detail": str(latest_job.error_detail or "") if latest_job else "",
                "created_at": latest_job.created_at.isoformat() if latest_job and latest_job.created_at else None,
                "finished_at": latest_job.finished_at.isoformat() if latest_job and latest_job.finished_at else None,
            },
            "quality_metrics": quality_metrics,
            "issues": issues,
            "chunk_counts": dict(sorted(chunk_counts.items())),
            "tables": tables,
            "table_count": len(tables),
            "table_data_row_count": data_row_count,
            "row_chunks": row_chunks,
            "focused_row_chunks": focused_row_chunks,
            "scope_metrics": _scope_metrics(row_chunks),
            "focus_scope_metrics": _scope_metrics(focused_row_chunks),
            "focus": {
                "row_start": focus_row_start,
                "row_end": focus_row_end,
            },
            "term_hits": term_hits,
        }
        return snapshot


def compare_snapshots(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    focus_row_start: int | None = None,
    focus_row_end: int | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_rows = _row_focus_filter(
        list(baseline.get("row_chunks") or []),
        focus_row_start=focus_row_start,
        focus_row_end=focus_row_end,
    )
    candidate_rows = _row_focus_filter(
        list(candidate.get("row_chunks") or []),
        focus_row_start=focus_row_start,
        focus_row_end=focus_row_end,
    )

    def _distinct_table_tokens(rows: Sequence[Mapping[str, Any]]) -> set[str]:
        tokens: set[str] = set()
        for row in rows:
            anchor = str(row.get("table_anchor") or "").strip()
            table_id = str(row.get("table_id") or "").strip()
            token = anchor or table_id
            if token:
                tokens.add(token)
        return tokens

    baseline_table_tokens = _distinct_table_tokens(baseline_rows)
    candidate_table_tokens = _distinct_table_tokens(candidate_rows)
    compare_by_row_index_only = len(baseline_table_tokens) <= 1 and len(candidate_table_tokens) <= 1

    def row_key(row: Mapping[str, Any]) -> str:
        row_index = _safe_int(row.get("table_row_index"))
        if row_index is None:
            row_index = -1
        if compare_by_row_index_only:
            return f"row:{row_index}"
        table_token = str(row.get("table_anchor") or "").strip() or str(row.get("table_id") or "").strip() or "table"
        return f"{table_token}:{row_index}"

    baseline_map = {row_key(row): dict(row) for row in baseline_rows}
    candidate_map = {row_key(row): dict(row) for row in candidate_rows}
    keys = sorted(set(baseline_map.keys()) | set(candidate_map.keys()))

    changed_rows: list[dict[str, Any]] = []
    for key in keys:
        left = baseline_map.get(key)
        right = candidate_map.get(key)
        if left is None or right is None:
            changed_rows.append(
                {
                    "row_key": key,
                    "changed": True,
                    "baseline": left,
                    "candidate": right,
                    "reason": "missing_in_one_snapshot",
                }
            )
            continue
        left_scope = _row_scope_columns(left)
        right_scope = _row_scope_columns(right)
        left_mode = _row_scope_reason(left)
        right_mode = _row_scope_reason(right)
        left_fee = str(left.get("table_row_fee_value") or "")
        right_fee = str(right.get("table_row_fee_value") or "")
        changed = left_scope != right_scope or left_mode != right_mode or left_fee != right_fee
        if not changed:
            continue
        changed_rows.append(
            {
                "row_key": key,
                "changed": True,
                "baseline": {
                    "table_row_index": left.get("table_row_index"),
                    "inferred_scope_columns": left_scope,
                    "scope_reason": left_mode,
                    "table_row_fee_value": left_fee,
                },
                "candidate": {
                    "table_row_index": right.get("table_row_index"),
                    "inferred_scope_columns": right_scope,
                    "scope_reason": right_mode,
                    "table_row_fee_value": right_fee,
                },
                "reason": "scope_or_mode_or_fee_changed",
            }
        )

    baseline_scope = _scope_metrics(baseline_rows)
    candidate_scope = _scope_metrics(candidate_rows)

    def _snapshot_table_count(snapshot: Mapping[str, Any]) -> int:
        direct = _safe_int(snapshot.get("table_count"))
        if direct is not None:
            return direct
        tables = snapshot.get("tables")
        if isinstance(tables, list):
            return len(tables)
        return 0

    def _snapshot_data_row_count(snapshot: Mapping[str, Any], scoped_rows: Sequence[dict[str, Any]]) -> int:
        direct = _safe_int(snapshot.get("table_data_row_count"))
        if direct is not None:
            return direct
        tables = snapshot.get("tables")
        if isinstance(tables, list):
            count = 0
            for table in tables:
                rows = table.get("rows") if isinstance(table, Mapping) else None
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    if str(row.get("row_type") or "").strip().lower() == "data":
                        count += 1
            if count:
                return count
        return len(scoped_rows)

    baseline_table_count = _snapshot_table_count(baseline)
    candidate_table_count = _snapshot_table_count(candidate)
    baseline_data_row_count = _snapshot_data_row_count(baseline, baseline_rows)
    candidate_data_row_count = _snapshot_data_row_count(candidate, candidate_rows)

    def metric_delta(metric: str) -> float | None:
        left = _safe_float((baseline.get("quality_metrics") or {}).get(metric))
        right = _safe_float((candidate.get("quality_metrics") or {}).get(metric))
        if left is None or right is None:
            return None
        return round(right - left, 4)

    baseline_chunk_counts = baseline.get("chunk_counts") or {}
    candidate_chunk_counts = candidate.get("chunk_counts") or {}

    regressions: list[str] = []
    if int(candidate_scope.get("row_chunk_count") or 0) < int(baseline_scope.get("row_chunk_count") or 0):
        regressions.append("row_chunk_count_decreased")
    if float(candidate_scope.get("multi_scope_rate") or 0.0) < float(baseline_scope.get("multi_scope_rate") or 0.0):
        regressions.append("multi_scope_rate_decreased")
    if float(candidate_scope.get("ambiguous_scope_rate") or 0.0) > float(baseline_scope.get("ambiguous_scope_rate") or 0.0):
        regressions.append("ambiguous_scope_rate_increased")
    coverage_delta = metric_delta("table_bbox_coverage_ratio")
    if coverage_delta is not None and coverage_delta < 0:
        regressions.append("table_bbox_coverage_ratio_decreased")
    residual_delta = metric_delta("residual_text_ratio")
    if residual_delta is not None and residual_delta > 0:
        regressions.append("residual_text_ratio_increased")

    quality_gate_metrics = _build_quality_gate_metrics(
        baseline_rows=baseline_rows,
        candidate_rows=candidate_rows,
        baseline_map=baseline_map,
        candidate_map=candidate_map,
    )

    result = {
        "generated_at": timezone.now().isoformat(),
        "focus": {
            "row_start": focus_row_start,
            "row_end": focus_row_end,
        },
        "baseline": {
            "snapshot_label": baseline.get("snapshot_label"),
            "upload_id": baseline.get("upload_id"),
            "table_count": baseline_table_count,
            "table_data_row_count": baseline_data_row_count,
            "chunk_counts": baseline_chunk_counts,
            "scope_metrics": baseline_scope,
            "quality_metrics": baseline.get("quality_metrics") or {},
            "settings": baseline.get("settings") or {},
        },
        "candidate": {
            "snapshot_label": candidate.get("snapshot_label"),
            "upload_id": candidate.get("upload_id"),
            "table_count": candidate_table_count,
            "table_data_row_count": candidate_data_row_count,
            "chunk_counts": candidate_chunk_counts,
            "scope_metrics": candidate_scope,
            "quality_metrics": candidate.get("quality_metrics") or {},
            "settings": candidate.get("settings") or {},
        },
        "deltas": {
            "table_count_delta": (candidate_table_count - baseline_table_count),
            "table_data_row_count_delta": (candidate_data_row_count - baseline_data_row_count),
            "row_chunk_count_delta": (
                int(candidate_scope.get("row_chunk_count") or 0) - int(baseline_scope.get("row_chunk_count") or 0)
            ),
            "multi_scope_rate_delta": round(
                float(candidate_scope.get("multi_scope_rate") or 0.0) - float(baseline_scope.get("multi_scope_rate") or 0.0),
                4,
            ),
            "ambiguous_scope_rate_delta": round(
                float(candidate_scope.get("ambiguous_scope_rate") or 0.0)
                - float(baseline_scope.get("ambiguous_scope_rate") or 0.0),
                4,
            ),
            "inferred_scope_rate_delta": round(
                float(candidate_scope.get("inferred_scope_rate") or 0.0) - float(baseline_scope.get("inferred_scope_rate") or 0.0),
                4,
            ),
            "table_bbox_coverage_ratio_delta": coverage_delta,
            "residual_text_ratio_delta": residual_delta,
            "table_row_unique_evidence_count_delta": (
                (_safe_int((candidate.get("quality_metrics") or {}).get("table_row_unique_evidence_count")) or 0)
                - (_safe_int((baseline.get("quality_metrics") or {}).get("table_row_unique_evidence_count")) or 0)
            ),
        },
        "changed_row_count": len(changed_rows),
        "changed_rows": changed_rows,
        "regressions": regressions,
        "quality_gate_metrics": quality_gate_metrics,
        "term_hits_baseline": baseline.get("term_hits") or {},
        "term_hits_candidate": candidate.get("term_hits") or {},
    }
    result["quality_gate"] = evaluate_quality_gate(result, thresholds=thresholds)
    return result


def render_snapshot_markdown(snapshot: Mapping[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Ingestion Snapshot: {snapshot.get('snapshot_label')}")
    lines.append("")
    lines.append(f"- upload_id: `{snapshot.get('upload_id')}`")
    lines.append(f"- business_profile_id: `{snapshot.get('business_profile_id')}`")
    lines.append(f"- captured_at: `{snapshot.get('captured_at')}`")
    lines.append(f"- table_count: `{snapshot.get('table_count')}`")
    lines.append(f"- table_data_row_count: `{snapshot.get('table_data_row_count')}`")
    lines.append(f"- row_chunk_count: `{(snapshot.get('scope_metrics') or {}).get('row_chunk_count')}`")
    lines.append(f"- multi_scope_rate: `{(snapshot.get('scope_metrics') or {}).get('multi_scope_rate')}`")
    lines.append(f"- ambiguous_scope_rate: `{(snapshot.get('scope_metrics') or {}).get('ambiguous_scope_rate')}`")
    lines.append(f"- inferred_scope_rate: `{(snapshot.get('scope_metrics') or {}).get('inferred_scope_rate')}`")

    quality = snapshot.get("quality_metrics") or {}
    lines.append(f"- table_bbox_coverage_ratio: `{quality.get('table_bbox_coverage_ratio')}`")
    lines.append(f"- residual_text_ratio: `{quality.get('residual_text_ratio')}`")
    lines.append(f"- table_row_unique_evidence_count: `{quality.get('table_row_unique_evidence_count')}`")
    lines.append("")
    lines.append("## Settings")
    lines.append("")
    for key, value in (snapshot.get("settings") or {}).items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Chunk Counts")
    lines.append("")
    for key, value in sorted((snapshot.get("chunk_counts") or {}).items(), key=lambda item: item[0]):
        lines.append(f"- {key}: `{value}`")
    lines.append("")
    lines.append("## Focus Scope Metrics")
    lines.append("")
    for key, value in (snapshot.get("focus_scope_metrics") or {}).items():
        lines.append(f"- {key}: `{value}`")

    focus_rows = list(snapshot.get("focused_row_chunks") or [])
    if focus_rows:
        lines.append("")
        lines.append("## Focus Rows")
        lines.append("")
        for row in focus_rows:
            lines.append(
                "- row {row}: scope={scope} reason={mode} fee={fee}".format(
                    row=row.get("table_row_index"),
                    scope=_row_scope_columns(row),
                    mode=_row_scope_reason(row),
                    fee=row.get("table_row_fee_value"),
                )
            )

    term_hits = snapshot.get("term_hits") or {}
    if term_hits:
        lines.append("")
        lines.append("## Term Hits")
        lines.append("")
        for term, hits in term_hits.items():
            lines.append(f"- {term}: `{len(hits)}` hits")
    lines.append("")
    return "\n".join(lines)


def render_comparison_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    baseline = report.get("baseline") or {}
    candidate = report.get("candidate") or {}
    deltas = report.get("deltas") or {}

    lines.append("# Table Ingestion Comparison")
    lines.append("")
    lines.append(f"- baseline_snapshot: `{baseline.get('snapshot_label')}` (`{baseline.get('upload_id')}`)")
    lines.append(f"- candidate_snapshot: `{candidate.get('snapshot_label')}` (`{candidate.get('upload_id')}`)")
    lines.append(f"- generated_at: `{report.get('generated_at')}`")
    lines.append("")
    lines.append("## Delta Summary")
    lines.append("")
    for key, value in deltas.items():
        lines.append(f"- {key}: `{value}`")

    quality_gate = _metadata_dict(report.get("quality_gate"))
    quality_metrics = _metadata_dict(report.get("quality_gate_metrics"))
    lines.append("")
    lines.append("## Quality Gate")
    lines.append("")
    lines.append(f"- passed: `{quality_gate.get('passed')}`")
    failed_checks = _clean_list(quality_gate.get("failed_checks"))
    if failed_checks:
        lines.append(f"- failed_checks: `{', '.join(failed_checks)}`")
    else:
        lines.append("- failed_checks: `none`")
    for key, value in sorted(_metadata_dict(quality_gate.get("thresholds")).items(), key=lambda item: item[0]):
        lines.append(f"- threshold.{key}: `{value}`")

    lines.append("")
    lines.append("## Quality Metrics")
    lines.append("")
    for key, value in sorted(quality_metrics.items(), key=lambda item: item[0]):
        lines.append(f"- {key}: `{value}`")

    regressions = list(report.get("regressions") or [])
    lines.append("")
    lines.append("## Regressions")
    lines.append("")
    if regressions:
        for item in regressions:
            lines.append(f"- {item}")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("## Changed Rows")
    lines.append("")
    changed_rows = list(report.get("changed_rows") or [])
    if not changed_rows:
        lines.append("- none")
    else:
        for row in changed_rows:
            lines.append(
                "- {key}: baseline={left} | candidate={right} | reason={reason}".format(
                    key=row.get("row_key"),
                    left=(row.get("baseline") or {}).get("inferred_scope_columns"),
                    right=(row.get("candidate") or {}).get("inferred_scope_columns"),
                    reason=row.get("reason"),
                )
            )
    lines.append("")
    return "\n".join(lines)


def write_snapshot_files(snapshot: Mapping[str, Any], *, output_dir: Path, stem: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(_as_json(snapshot) + "\n", encoding="utf-8")
    md_path.write_text(render_snapshot_markdown(snapshot), encoding="utf-8")
    return json_path, md_path


def write_comparison_files(report: Mapping[str, Any], *, output_dir: Path, stem: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(_as_json(report) + "\n", encoding="utf-8")
    md_path.write_text(render_comparison_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_snapshot_stem(label: str, upload_id: str) -> str:
    date_token = timezone.now().date().isoformat()
    safe_label = slugify(label) or "snapshot"
    short_upload = str(upload_id).replace("-", "")[:8]
    return f"{safe_label}_{short_upload}_{date_token}"


def build_comparison_stem(label: str) -> str:
    date_token = timezone.now().date().isoformat()
    safe_label = slugify(label) or "comparison"
    return f"{safe_label}_{date_token}"


def resolve_output_dir(output_dir: str | Path | None) -> Path:
    raw = str(output_dir or "").strip()
    if not raw:
        return Path(settings.BASE_DIR) / "docs" / "ingestion_snapshots"
    path = Path(raw)
    if not path.is_absolute():
        path = Path(settings.BASE_DIR) / path
    return path


def load_snapshot_from_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"Snapshot must be an object: {path}")
    return dict(payload)


def normalize_pdf_portfolio_expectations(payload: Any) -> dict[str, Any]:
    if isinstance(payload, Mapping):
        label = str(payload.get("label") or "pdf_portfolio_benchmark").strip() or "pdf_portfolio_benchmark"
        raw_entries = payload.get("entries") or []
    elif isinstance(payload, list):
        label = "pdf_portfolio_benchmark"
        raw_entries = payload
    else:
        raise ValueError("PDF portfolio expectations must be a JSON object or array.")

    if not isinstance(raw_entries, list):
        raise ValueError("PDF portfolio expectations 'entries' must be a list.")

    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"PDF portfolio expectation entry #{index} must be an object.")
        filename = str(raw.get("filename") or "").strip()
        if not filename:
            raise ValueError(f"PDF portfolio expectation entry #{index} is missing 'filename'.")

        mode = str(raw.get("expected_table_mode") or "").strip().lower()
        exact = _safe_int(raw.get("expected_table_count"))
        min_table_count = _safe_int(raw.get("min_table_count"))
        max_table_count = _safe_int(raw.get("max_table_count"))

        if exact is not None:
            mode = "range"
            min_table_count = exact
            max_table_count = exact
        elif not mode:
            if min_table_count is None and max_table_count is None:
                mode = "none"
            elif min_table_count is not None or max_table_count is not None:
                mode = "range"

        if mode not in PDF_PORTFOLIO_EXPECTED_TABLE_MODES:
            raise ValueError(
                f"Unsupported expected_table_mode '{mode}' for '{filename}'. "
                f"Use one of: {', '.join(sorted(PDF_PORTFOLIO_EXPECTED_TABLE_MODES))}."
            )

        if mode == "none":
            min_table_count = 0
            max_table_count = 0
        elif mode == "some":
            min_table_count = max(1, int(min_table_count or 1))
            max_table_count = max_table_count if max_table_count is not None else None
        else:
            if min_table_count is None and max_table_count is None:
                raise ValueError(
                    f"Range expectations for '{filename}' require min_table_count/max_table_count "
                    "or expected_table_count."
                )
            if min_table_count is None:
                min_table_count = 0
            if max_table_count is not None and max_table_count < min_table_count:
                raise ValueError(
                    f"Range expectations for '{filename}' have max_table_count < min_table_count."
                )

        entries.append(
            {
                "filename": filename,
                "document_shape": str(raw.get("document_shape") or "").strip() or None,
                "expected_table_mode": mode,
                "min_table_count": int(min_table_count or 0),
                "max_table_count": int(max_table_count) if max_table_count is not None else None,
                "notes": str(raw.get("notes") or "").strip() or None,
                "confidence": str(raw.get("confidence") or "").strip() or None,
                "tags": _clean_list(raw.get("tags")),
            }
        )

    return {
        "label": label,
        "entries": entries,
    }


def load_pdf_portfolio_expectations_from_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_pdf_portfolio_expectations(payload)


def evaluate_pdf_portfolio_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    entries = list(snapshot.get("entries") or [])
    evaluated_entries: list[dict[str, Any]] = []
    passed_count = 0
    failed_count = 0
    missing_count = 0

    for raw in entries:
        if not isinstance(raw, Mapping):
            continue
        entry = dict(raw)
        mode = str(entry.get("expected_table_mode") or "none").strip().lower()
        min_table_count = max(0, int(_safe_int(entry.get("min_table_count")) or 0))
        max_table_count = _safe_int(entry.get("max_table_count"))
        actual_table_count = _safe_int(entry.get("actual_table_count"))
        upload_found = bool(entry.get("upload_found"))
        status = "passed"
        reasons: list[str] = []

        if not upload_found or actual_table_count is None:
            status = "missing_upload"
            reasons.append("missing_upload")
            missing_count += 1
        else:
            if mode == "none":
                if actual_table_count != 0:
                    status = "failed"
                    reasons.append("unexpected_tables")
            else:
                if actual_table_count < min_table_count:
                    status = "failed"
                    reasons.append("too_few_tables")
                if max_table_count is not None and actual_table_count > max_table_count:
                    status = "failed"
                    reasons.append("too_many_tables")

        if status == "passed":
            passed_count += 1
        else:
            failed_count += 1

        entry["status"] = status
        entry["failed_checks"] = reasons
        evaluated_entries.append(entry)

    total = len(evaluated_entries)
    report = dict(snapshot)
    report["entries"] = evaluated_entries
    report["summary"] = {
        "total": total,
        "passed": passed_count,
        "failed": failed_count,
        "missing_uploads": missing_count,
        "pass_rate": round(_safe_ratio(passed_count, total), 4),
    }
    report["failed_entries"] = [entry for entry in evaluated_entries if entry.get("status") != "passed"]
    report["passed"] = failed_count == 0
    return report


def capture_pdf_portfolio_snapshot(
    *,
    expectations: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    business_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_pdf_portfolio_expectations(expectations)
    resolved_label = str(label or normalized.get("label") or "pdf_portfolio_benchmark").strip() or "pdf_portfolio_benchmark"
    entries: list[dict[str, Any]] = []
    business_token = str(business_id or "").strip()

    with tenant_bypass():
        for expected in normalized.get("entries") or []:
            filename = str(expected.get("filename") or "").strip()
            query = KnowledgeUpload.objects.filter(file_detail__filename__iexact=filename).select_related("file_detail")
            if business_token:
                query = query.filter(business_profile_id=business_token)
            upload = query.order_by("-created_at").first()

            if upload is None:
                entries.append(
                    {
                        **dict(expected),
                        "upload_found": False,
                        "upload_id": None,
                        "business_profile_id": business_token or None,
                        "actual_table_count": None,
                        "chunk_count": None,
                        "issue_count": None,
                        "updated_at": None,
                        "table_titles": [],
                        "table_previews": [],
                    }
                )
                continue

            tables = (
                KnowledgeUploadTable.objects.filter(upload=upload)
                .select_related("page")
                .order_by("page__page_number", "order_index")
            )
            table_previews: list[dict[str, Any]] = []
            for table in tables[:5]:
                first_row = (
                    KnowledgeUploadTableRow.objects.filter(table=table)
                    .order_by("row_index")
                    .only("raw_text")
                    .first()
                )
                table_previews.append(
                    {
                        "title": str(table.title or ""),
                        "page_number": int(table.page.page_number) if table.page_id and table.page else None,
                        "row_count": int(KnowledgeUploadTableRow.objects.filter(table=table).count()),
                        "column_count": len(table.column_schema) if isinstance(table.column_schema, list) else 0,
                        "preview": str((first_row.raw_text if first_row else "") or "").strip()[:240],
                    }
                )

            entries.append(
                {
                    **dict(expected),
                    "upload_found": True,
                    "upload_id": str(upload.id),
                    "business_profile_id": str(upload.business_profile_id),
                    "actual_table_count": int(tables.count()),
                    "chunk_count": int(upload.chunk_count),
                    "issue_count": int(KnowledgeUploadIssue.objects.filter(upload=upload).count()),
                    "updated_at": upload.updated_at.isoformat() if upload.updated_at else None,
                    "table_titles": [item["title"] for item in table_previews if item.get("title")],
                    "table_previews": table_previews,
                }
            )

    snapshot = {
        "label": resolved_label,
        "captured_at": timezone.now().isoformat(),
        "business_profile_id": business_token or None,
        "entries": entries,
    }
    return evaluate_pdf_portfolio_snapshot(snapshot)


def render_pdf_portfolio_markdown(report: Mapping[str, Any]) -> str:
    summary = _metadata_dict(report.get("summary"))
    lines: list[str] = []
    lines.append(f"# PDF Portfolio Benchmark: {report.get('label')}")
    lines.append("")
    lines.append(f"- captured_at: `{report.get('captured_at')}`")
    lines.append(f"- business_profile_id: `{report.get('business_profile_id')}`")
    lines.append(f"- passed: `{report.get('passed')}`")
    lines.append(f"- total: `{summary.get('total')}`")
    lines.append(f"- passed_count: `{summary.get('passed')}`")
    lines.append(f"- failed_count: `{summary.get('failed')}`")
    lines.append(f"- missing_uploads: `{summary.get('missing_uploads')}`")
    lines.append(f"- pass_rate: `{summary.get('pass_rate')}`")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    for entry in list(report.get("entries") or []):
        filename = str(entry.get("filename") or "")
        status = str(entry.get("status") or "")
        mode = str(entry.get("expected_table_mode") or "")
        min_tables = entry.get("min_table_count")
        max_tables = entry.get("max_table_count")
        actual = entry.get("actual_table_count")
        lines.append(
            "- {filename}: status=`{status}` expected_mode=`{mode}` expected_range=`{min_tables}-{max_tables}` actual_tables=`{actual}` upload_id=`{upload_id}`".format(
                filename=filename,
                status=status,
                mode=mode,
                min_tables=min_tables,
                max_tables=max_tables if max_tables is not None else "∞",
                actual=actual,
                upload_id=entry.get("upload_id"),
            )
        )
        failed_checks = _clean_list(entry.get("failed_checks"))
        if failed_checks:
            lines.append(f"  failed_checks: `{', '.join(failed_checks)}`")
        notes = str(entry.get("notes") or "").strip()
        if notes:
            lines.append(f"  notes: {notes}")
        previews = list(entry.get("table_previews") or [])
        for preview in previews[:3]:
            lines.append(
                "  kept_table: page=`{page}` rows=`{rows}` cols=`{cols}` title=`{title}` preview=`{preview}`".format(
                    page=preview.get("page_number"),
                    rows=preview.get("row_count"),
                    cols=preview.get("column_count"),
                    title=preview.get("title"),
                    preview=preview.get("preview"),
                )
            )
    lines.append("")
    return "\n".join(lines)


def write_pdf_portfolio_files(report: Mapping[str, Any], *, output_dir: Path, stem: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{stem}.json"
    md_path = output_dir / f"{stem}.md"
    json_path.write_text(_as_json(report) + "\n", encoding="utf-8")
    md_path.write_text(render_pdf_portfolio_markdown(report), encoding="utf-8")
    return json_path, md_path


def build_pdf_portfolio_stem(label: str) -> str:
    date_token = timezone.now().date().isoformat()
    safe_label = slugify(label) or "pdf-portfolio-benchmark"
    return f"{safe_label}_{date_token}"


def parse_terms(values: Iterable[str]) -> list[str]:
    terms: list[str] = []
    for value in values:
        token = str(value or "").strip()
        if token:
            terms.append(token)
    return terms
