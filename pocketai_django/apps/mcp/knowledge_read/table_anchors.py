from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Mapping

from django.db import models

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.models import KnowledgeUploadTableRow

from .table_rows import _read_tabular_rows_segment_facts


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _table_payload_is_informative(table_payload: Mapping[str, object]) -> bool:
    rows_value = table_payload.get("rows")
    if not isinstance(rows_value, list) or not rows_value:
        return False
    for row in rows_value:
        if isinstance(row, (list, tuple)):
            if any(str(cell or "").strip() for cell in row):
                return True
        elif str(row or "").strip():
            return True
    return False

def _table_payload_size(table_payload: Mapping[str, object]) -> int:
    try:
        return len(json.dumps(dict(table_payload), ensure_ascii=False, default=str))
    except Exception:
        return 0

def _merge_table_anchor_payloads(
    *,
    base_payload: Mapping[str, object],
    incoming_payload: Mapping[str, object],
    budget_chars: int,
    selection_mode: str,
) -> tuple[dict[str, object], int, bool]:
    merged_columns = list(base_payload.get("columns") or incoming_payload.get("columns") or [])
    if not merged_columns:
        merged_columns = list(incoming_payload.get("columns") or [])

    base_rows_raw = base_payload.get("rows")
    incoming_rows_raw = incoming_payload.get("rows")
    base_rows = [list(row) for row in base_rows_raw] if isinstance(base_rows_raw, list) else []
    incoming_rows = [list(row) for row in incoming_rows_raw] if isinstance(incoming_rows_raw, list) else []

    merged: dict[str, object] = {
        "type": "table",
        "table_id": str(base_payload.get("table_id") or incoming_payload.get("table_id") or ""),
        "columns": merged_columns,
        "rows": [],
        "row_offset": min(
            max(0, _coerce_int(base_payload.get("row_offset")) or 0),
            max(0, _coerce_int(incoming_payload.get("row_offset")) or 0),
        ),
        "rows_shown": 0,
        "total_rows": max(
            max(0, _coerce_int(base_payload.get("total_rows")) or 0),
            max(0, _coerce_int(incoming_payload.get("total_rows")) or 0),
        ),
        "selection_mode": selection_mode,
    }

    merged_rows: list[list[str]] = []
    seen_rows: set[tuple[str, ...]] = set()
    for row in [*base_rows, *incoming_rows]:
        normalized_row = tuple(str(cell or "") for cell in row)
        if normalized_row in seen_rows:
            continue
        candidate_rows = [*merged_rows, list(row)]
        candidate_payload = dict(merged)
        candidate_payload["rows"] = candidate_rows
        candidate_payload["rows_shown"] = len(candidate_rows)
        if _table_payload_size(candidate_payload) > max(0, int(budget_chars)):
            merged["rows"] = merged_rows
            merged["rows_shown"] = len(merged_rows)
            if (
                int(merged["row_offset"]) + len(merged_rows)
            ) < int(merged.get("total_rows") or 0):
                merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
            return merged, max(0, len(merged_rows) - len(base_rows)), True
        merged_rows = candidate_rows
        seen_rows.add(normalized_row)

    merged["rows"] = merged_rows
    merged["rows_shown"] = len(merged_rows)
    if (
        int(merged["row_offset"]) + len(merged_rows)
    ) < int(merged.get("total_rows") or 0):
        merged["next_row_start"] = int(merged["row_offset"]) + len(merged_rows)
    return merged, max(0, len(merged_rows) - len(base_rows)), False

def _table_db_row_index_to_visible_row_start(
    *,
    table_id: str,
    upload_id: str,
    business_profile,
    db_row_index: int,
) -> int:
    """
    Convert a stored table row index into a visible `row_start` offset.

    The visible table body excludes header / section-header rows, so row refs
    and anchor reads must normalize DB row indexes before paging facts.
    """
    try:
        raw_idx = int(db_row_index)
    except (TypeError, ValueError):
        return 0
    if raw_idx < 0:
        return 0
    try:
        table_uuid = uuid.UUID(str(table_id))
    except (TypeError, ValueError):
        return max(0, raw_idx)

    non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
        metadata__row_type__in=["header", "section_header"]
    )

    base_qs = KnowledgeUploadTableRow.objects.filter(
        table_id=table_uuid,
        table__upload_id=upload_id,
        table__upload__business_profile=business_profile,
        table__upload__status=KnowledgeStatus.ACTIVE,
    )

    target = (
        base_qs.filter(non_header_q, row_index__gte=raw_idx)
        .order_by("row_index")
        .only("row_index")
        .first()
    )
    if target is None:
        target = (
            base_qs.filter(non_header_q, row_index__lte=raw_idx)
            .order_by("-row_index")
            .only("row_index")
            .first()
        )
    if target is None:
        return 0

    try:
        target_db_idx = int(getattr(target, "row_index", 0) or 0)
    except (TypeError, ValueError):
        target_db_idx = raw_idx

    try:
        visible_before = int(
            base_qs.filter(non_header_q, row_index__lt=int(target_db_idx)).count()
        )
    except Exception:
        visible_before = max(0, raw_idx)
    return max(0, int(visible_before))

def _read_exact_table_row(
    *,
    item_id: str,
    upload_id: str,
    table_id: str,
    db_row_index: int,
    budget_chars: int,
    business_profile,
) -> tuple[dict[str, object], bool]:
    visible_row_start = _table_db_row_index_to_visible_row_start(
        table_id=table_id,
        upload_id=upload_id,
        business_profile=business_profile,
        db_row_index=db_row_index,
    )
    table_payload, _cursor_out, complete = _read_tabular_rows_segment_facts(
        item_id=item_id,
        upload_id=upload_id,
        table_id=table_id,
        start_row_index=visible_row_start,
        budget_chars=budget_chars,
        max_rows=1,
        business_profile=business_profile,
        selection_mode="row_ref",
    )
    return table_payload, complete

def _read_tabular_rows_with_anchor(
    *,
    load_table_anchor_manifest: Callable[..., dict[str, object] | None],
    item_id: str,
    upload_id: str,
    table_id: str,
    start_row_index: int,
    budget_chars: int,
    max_rows: int | None,
    business_profile,
    use_anchor: bool,
) -> tuple[dict[str, object], dict[str, object] | None, bool, bool, bool]:
    anchor_used = False
    fallback_used = False
    anchor_start_row = int(start_row_index)

    if use_anchor and anchor_start_row <= 0:
        manifest = load_table_anchor_manifest(ref_id=item_id, table_id=table_id)
        if isinstance(manifest, Mapping):
            anchor_rows: list[int] = []
            primary_anchor = _coerce_int(manifest.get("matched_row_index"))
            if primary_anchor is not None and primary_anchor >= 0:
                anchor_rows.append(int(primary_anchor))
            raw_anchors = manifest.get("anchors")
            if isinstance(raw_anchors, list):
                for entry in raw_anchors:
                    parsed = _coerce_int(entry)
                    if parsed is None or parsed < 0:
                        continue
                    anchor_rows.append(int(parsed))
            if anchor_rows:
                # Deduplicate while preserving order, and cap attempts.
                seen_rows: set[int] = set()
                anchor_rows = [row for row in anchor_rows if (row not in seen_rows and not seen_rows.add(row))]
                anchor_used = True
                merged_payload: dict[str, object] | None = None
                merged_complete = True
                merged_anchor_hits = 0
                for row_idx in anchor_rows[:2]:
                    visible_row_start = _table_db_row_index_to_visible_row_start(
                        table_id=table_id,
                        upload_id=upload_id,
                        business_profile=business_profile,
                        db_row_index=int(row_idx),
                    )
                    anchor_start_row = max(0, int(visible_row_start) - 1)
                    table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
                        item_id=item_id,
                        upload_id=upload_id,
                        table_id=table_id,
                        start_row_index=anchor_start_row,
                        budget_chars=budget_chars,
                        max_rows=max_rows,
                        business_profile=business_profile,
                        selection_mode="anchor_match",
                    )
                    if not _table_payload_is_informative(table_payload):
                        merged_complete = False
                        continue
                    if merged_payload is None:
                        merged_payload = dict(table_payload)
                        merged_complete = bool(complete)
                        merged_anchor_hits = 1
                        continue
                    merged_payload, added_rows, merge_budget_exhausted = _merge_table_anchor_payloads(
                        base_payload=merged_payload,
                        incoming_payload=table_payload,
                        budget_chars=budget_chars,
                        selection_mode="anchor_merge",
                    )
                    if added_rows > 0:
                        merged_anchor_hits += 1
                    merged_complete = bool(merged_complete and complete and not merge_budget_exhausted)
                if merged_payload is not None and _table_payload_is_informative(merged_payload):
                    if merged_anchor_hits <= 1:
                        merged_payload["selection_mode"] = "anchor_match"
                    return merged_payload, None, merged_complete, anchor_used, fallback_used
                # Anchors were tried but didn't yield useful rows; fall back.
                fallback_used = True

    table_payload, cursor_out, complete = _read_tabular_rows_segment_facts(
        item_id=item_id,
        upload_id=upload_id,
        table_id=table_id,
        start_row_index=max(0, int(start_row_index)),
        budget_chars=budget_chars,
        max_rows=max_rows,
        business_profile=business_profile,
        selection_mode="row_range",
    )

    return table_payload, cursor_out, complete, anchor_used, fallback_used
