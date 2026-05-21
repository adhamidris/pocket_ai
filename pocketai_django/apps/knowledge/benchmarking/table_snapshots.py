from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from core.tenancy import tenant_bypass

from apps.knowledge.benchmarking.quality_gate import (
    _build_quality_gate_metrics,
    _chunk_bucket,
    _clean_list,
    _metadata_dict,
    _normalized_token,
    _row_focus_filter,
    _row_scope_columns,
    _row_scope_reason,
    _safe_float,
    _safe_int,
    _scope_metrics,
    _table_schema_keys,
    evaluate_quality_gate,
    quality_gate_thresholds,
)
from apps.knowledge.models import (
    KnowledgeIngestionJob,
    KnowledgeUpload,
    KnowledgeUploadChunk,
    KnowledgeUploadIssue,
    KnowledgeUploadTable,
    KnowledgeUploadTableCell,
    KnowledgeUploadTableRow,
)


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
