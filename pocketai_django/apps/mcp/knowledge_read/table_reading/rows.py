from __future__ import annotations

import json
import re
import uuid
from typing import Mapping, Sequence

from django.db import models
from django.db.models import Prefetch

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.models import KnowledgeUploadTable, KnowledgeUploadTableCell

from ...knowledge_support.identifier_helpers import _normalize_column_name


def _read_tabular_rows_segment_facts(
    *,
    item_id: str,
    upload_id: str,
    table_id: str,
    start_row_index: int = 0,
    budget_chars: int,
    max_rows: int | None = None,
    business_profile,
    selection_mode: str | None = None,
) -> tuple[dict[str, object], dict[str, object] | None, bool]:
    """
    Facts-first table read for the LLM.

    Contract:
    - `start_row_index` is a 0-based row offset within the visible table body
      (header/separator rows excluded by ingestion metadata).
    - Returns only the table facts + paging signals needed to continue via
      `row_start` + `row_limit` (no table cursors).
    """

    budget_limit = max(0, int(budget_chars))
    complete = True

    payload: dict[str, object] = {
        "type": "table",
        "table_id": str(table_id),
        "columns": [],
        "rows": [],
        "row_offset": 0,
        "rows_shown": 0,
        "total_rows": 0,
    }
    if isinstance(selection_mode, str) and selection_mode.strip():
        payload["selection_mode"] = selection_mode.strip()

    try:
        table_uuid = uuid.UUID(str(table_id))
    except (TypeError, ValueError):
        return payload, None, True

    table = (
        KnowledgeUploadTable.objects.filter(
            id=table_uuid,
            upload_id=upload_id,
            upload__business_profile=business_profile,
            upload__status=KnowledgeStatus.ACTIVE,
        )
        .only("id", "order_index", "title", "section_heading", "column_schema")
        .first()
    )
    if not table:
        return payload, None, True

    def _dedupe_column_labels(raw_columns: Sequence[str]) -> list[str]:
        deduped: list[str] = []
        seen: dict[str, int] = {}
        for index, raw_column in enumerate(raw_columns):
            label = str(raw_column or "").strip() or f"column_{index + 1}"
            key = label.lower()
            count = seen.get(key, 0) + 1
            seen[key] = count
            deduped.append(label if count == 1 else f"{label}_{count}")
        return deduped

    # Column labels: prefer column_schema if it looks like a list of strings.
    raw_schema = table.column_schema if isinstance(getattr(table, "column_schema", None), list) else []
    columns: list[str] = []
    for entry in raw_schema:
        if isinstance(entry, str) and entry.strip():
            columns.append(entry.strip())
        elif isinstance(entry, Mapping):
            for key in ("column", "name", "label", "key", "column_key"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    columns.append(value.strip())
                    break
    columns = columns[:200]

    # Visible rows are non-header rows only (in ingestion metadata).
    non_header_q = models.Q(metadata__row_type__isnull=True) | ~models.Q(
        metadata__row_type__in=["header", "section_header"]
    )

    # If schema is missing/empty, infer columns from the first visible row's cells.
    if not columns:
        row_obj = (
            table.rows.filter(non_header_q)
            .order_by("row_index")
            .only("id", "row_index")
            .first()
        )
        if row_obj:
            cell_qs = (
                KnowledgeUploadTableCell.objects.filter(row_id=row_obj.id)
                .order_by("column_index")
                .values_list("column_index", "column_key")
            )
            inferred: list[tuple[int, str]] = []
            for col_idx, col_key in cell_qs:
                try:
                    idx = int(col_idx)
                except (TypeError, ValueError):
                    continue
                label = str(col_key or "").strip() or f"column_{idx + 1}"
                inferred.append((idx, label))
            inferred.sort(key=lambda item: item[0])
            columns = [label for _idx, label in inferred][:200]

    columns = _dedupe_column_labels(columns[:200])
    payload["columns"] = columns

    try:
        start_row = max(0, int(start_row_index))
    except (TypeError, ValueError):
        start_row = 0
    payload["row_offset"] = start_row

    try:
        total_rows = int(table.rows.filter(non_header_q).count())
    except Exception:
        total_rows = 0
    payload["total_rows"] = total_rows

    # Bound reads: even for "list everything", never scan unbounded rows in one call.
    try:
        scan_cap = int(max(50, min(500, int(budget_chars) // 30 or 50)))
    except Exception:
        scan_cap = 200
    limit_rows = scan_cap
    if isinstance(max_rows, int) and max_rows > 0:
        limit_rows = min(limit_rows, int(max_rows))
    limit_rows = max(1, int(limit_rows))

    row_qs = (
        table.rows.filter(non_header_q)
        .order_by("row_index")
        .only("id", "row_index", "metadata")
    )
    row_qs = row_qs[start_row : start_row + limit_rows]
    row_qs = row_qs.prefetch_related(
        Prefetch(
            "cells",
            queryset=KnowledgeUploadTableCell.objects.order_by("column_index").only(
                "row_id",
                "column_index",
                "column_key",
                "raw_text",
            ),
        )
    )

    def _normalize_cell_text(value: str) -> str:
        normalized = str(value or "").strip().lower()
        normalized = re.sub(r"\\s+", " ", normalized)
        return normalized

    def _is_uniform_section_separator(values: Sequence[str]) -> tuple[bool, str]:
        non_empty = [str(cell or "").strip() for cell in values if str(cell or "").strip()]
        if len(non_empty) < 4:
            return False, ""
        normalized = [_normalize_cell_text(cell) for cell in non_empty]
        first = normalized[0] if normalized else ""
        if not first:
            return False, ""
        if any(cell != first for cell in normalized[1:]):
            return False, ""
        return True, non_empty[0]

    scope_overlay_reasons = {
        "scope_explicit_span",
        "scope_repeated_value_span",
        "scope_sparse_expansion",
        "scope_edge_completion",
    }
    column_index_lookup: dict[str, int] = {}
    for idx, label in enumerate(columns):
        normalized = _normalize_column_name(label)
        if normalized and normalized not in column_index_lookup:
            column_index_lookup[normalized] = idx
        lowered = str(label or "").strip().lower()
        if lowered and lowered not in column_index_lookup:
            column_index_lookup[lowered] = idx

    index_offset: int | None = None
    rows_out: list[list[str]] = []

    def _payload_size(candidate_rows: Sequence[Sequence[str]]) -> int:
        candidate: dict[str, object] = {
            "type": "table",
            "table_id": str(table_id),
            "columns": list(columns),
            "rows": [list(row) for row in candidate_rows],
            "row_offset": int(start_row),
            "rows_shown": int(len(candidate_rows)),
            "total_rows": int(total_rows),
        }
        if isinstance(selection_mode, str) and selection_mode.strip():
            candidate["selection_mode"] = selection_mode.strip()
        try:
            return len(json.dumps(candidate, ensure_ascii=False, default=str))
        except Exception:
            return 0

    for row in row_qs:
        cell_lookup: dict[int, str] = {}
        for cell in row.cells.all():  # type: ignore[attr-defined]
            try:
                idx = int(getattr(cell, "column_index", None) or 0)
            except (TypeError, ValueError):
                continue
            text = str(getattr(cell, "raw_text", "") or "")
            cell_lookup[idx] = text

        if index_offset is None:
            keys = list(cell_lookup.keys())
            if 0 in cell_lookup:
                index_offset = 0
            elif 1 in cell_lookup:
                index_offset = 1
            elif keys:
                index_offset = min(keys)
            else:
                index_offset = 0

        values: list[str] = []
        for col_idx in range(len(columns)):
            values.append(str(cell_lookup.get(int(col_idx) + int(index_offset or 0), "")))

        # Collapse broad-span note rows (same text duplicated across many columns) to:
        #   [note_text, "", "", ...]
        is_uniform, uniform_label = _is_uniform_section_separator(values)
        if is_uniform and uniform_label:
            values = [uniform_label] + [""] * max(0, len(columns) - 1)

        row_meta = row.metadata if isinstance(getattr(row, "metadata", None), Mapping) else {}
        inferred_scope_columns: list[str] = []
        raw_applies_to = row_meta.get("inferred_scope_columns")
        if isinstance(raw_applies_to, (list, tuple)):
            for scope_entry in raw_applies_to:
                label = str(scope_entry or "").strip()
                if label:
                    inferred_scope_columns.append(label)
        scope_reason = str(row_meta.get("scope_reason") or "").strip()
        scope_reason_key = scope_reason.lower()
        fee_value = str(row_meta.get("scope_value") or "").strip()

        effective_values = list(values)
        if (
            fee_value
            and inferred_scope_columns
            and (
                scope_reason_key.startswith("inferred_")
                or scope_reason_key in scope_overlay_reasons
            )
        ):
            for scope_label in inferred_scope_columns:
                normalized_scope = _normalize_column_name(scope_label)
                if not normalized_scope:
                    continue
                col_pos = column_index_lookup.get(normalized_scope)
                if col_pos is None:
                    col_pos = column_index_lookup.get(str(scope_label).strip().lower())
                if col_pos is None or col_pos < 0 or col_pos >= len(effective_values):
                    continue
                if str(effective_values[col_pos] or "").strip():
                    continue
                effective_values[col_pos] = fee_value

        candidate_rows = [*rows_out, effective_values]
        if _payload_size(candidate_rows) > budget_limit:
            complete = False
            break
        rows_out = candidate_rows

    payload["rows"] = rows_out
    payload["rows_shown"] = len(rows_out)
    if (start_row + len(rows_out)) < int(total_rows or 0):
        payload["next_row_start"] = int(start_row + len(rows_out))

    return payload, None, complete
