from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping

from apps.knowledge.models import KnowledgeUploadTable


@dataclass(slots=True)
class NewTableReadResult:
    payload: dict[str, object]
    payload_type: str
    evidence_kind: str
    title: str
    next_cursor: str | None
    complete: bool
    anchor_manifest_lookups: int = 0
    anchor_manifest_hits: int = 0
    anchor_fallback_reads: int = 0


def read_new_table_source(
    *,
    item_id: str,
    upload_id: str,
    row_record: object | None,
    table_record: object | None,
    chunk_meta: Mapping[str, object],
    mode: str,
    row_start: int | None,
    row_limit: int | None,
    per_item_budget: int,
    business: object,
    title: str,
    read_tabular_rows_segment_facts: Callable[..., tuple[dict[str, object], dict[str, object] | None, bool]],
    read_exact_table_row: Callable[..., tuple[dict[str, object], bool]],
    read_tabular_rows_with_anchor: Callable[..., tuple[dict[str, object], dict[str, object] | None, bool, bool, bool]],
) -> NewTableReadResult | None:
    is_table_chunk = bool(chunk_meta.get("is_table_chunk") or chunk_meta.get("table_chunk_role"))
    table_role = str(chunk_meta.get("table_chunk_role") or "").strip().lower()
    table_id = str(chunk_meta.get("table_id") or "").strip()

    if row_record is not None:
        table_obj = getattr(row_record, "table", None)
        table_id = str(getattr(table_obj, "id", "") or "").strip()
        row_index_raw = getattr(row_record, "row_index", 0)
        try:
            row_index = int(row_index_raw) if row_index_raw is not None else 0
        except (TypeError, ValueError):
            row_index = 0

        table_title = str(getattr(table_obj, "title", "") or "").strip() or str(
            getattr(table_obj, "section_heading", "") or ""
        ).strip()
        if not table_title:
            order_index = getattr(table_obj, "order_index", None)
            table_title = f"Table {order_index}" if order_index else "Table"
        title = table_title

        if row_start is not None or row_limit is not None:
            table_payload, _cursor_out, complete = read_tabular_rows_segment_facts(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                start_row_index=int(row_start or 0),
                budget_chars=per_item_budget,
                max_rows=row_limit,
                business_profile=business,
                selection_mode="row_range",
            )
            table_payload["upgraded_from_ref"] = "table_row"
            payload = table_payload
        else:
            table_payload, complete = read_exact_table_row(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                db_row_index=row_index,
                budget_chars=per_item_budget,
                business_profile=business,
            )
            payload = table_payload

        return NewTableReadResult(
            payload=payload,
            payload_type="table",
            evidence_kind="table_rows",
            title=title,
            next_cursor=None,
            complete=complete,
        )

    if table_record is not None:
        table_id = str(getattr(table_record, "id", "") or "").strip()
        table_title = str(getattr(table_record, "title", "") or "").strip() or str(
            getattr(table_record, "section_heading", "") or ""
        ).strip()
        if not table_title:
            order_index = getattr(table_record, "order_index", None)
            table_title = f"Table {order_index}" if order_index else "Table"
        title = table_title

        anchor_manifest_lookups = 0
        anchor_manifest_hits = 0
        anchor_fallback_reads = 0
        if row_start is not None or row_limit is not None:
            table_payload, _cursor_out, complete = read_tabular_rows_segment_facts(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                start_row_index=int(row_start or 0),
                budget_chars=per_item_budget,
                max_rows=row_limit,
                business_profile=business,
                selection_mode="row_range",
            )
        else:
            anchor_manifest_lookups += 1
            table_payload, _cursor_out, complete, anchor_used, fallback_used = read_tabular_rows_with_anchor(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                start_row_index=0,
                budget_chars=per_item_budget,
                max_rows=None,
                business_profile=business,
                use_anchor=True,
            )
            if anchor_used:
                anchor_manifest_hits += 1
            if fallback_used:
                anchor_fallback_reads += 1

        return NewTableReadResult(
            payload=table_payload,
            payload_type="table",
            evidence_kind="table_rows",
            title=title,
            next_cursor=None,
            complete=complete,
            anchor_manifest_lookups=anchor_manifest_lookups,
            anchor_manifest_hits=anchor_manifest_hits,
            anchor_fallback_reads=anchor_fallback_reads,
        )

    if mode != "excerpt" and is_table_chunk and table_id and (mode == "table_rows" or table_role not in {"row"}):
        # Resolve table title for friendlier output.
        try:
            table_obj = (
                KnowledgeUploadTable.objects.filter(id=table_id)
                .only("id", "title", "section_heading", "order_index")
                .first()
            )
            if table_obj:
                table_title = (table_obj.title or table_obj.section_heading or "").strip()
                if not table_title:
                    table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                title = table_title
        except Exception:
            pass

        anchor_manifest_lookups = 0
        anchor_manifest_hits = 0
        anchor_fallback_reads = 0
        if row_start is not None or row_limit is not None:
            table_payload, _cursor_out, complete = read_tabular_rows_segment_facts(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                start_row_index=int(row_start or 0),
                budget_chars=per_item_budget,
                max_rows=row_limit,
                business_profile=business,
                selection_mode="row_range",
            )
        else:
            anchor_manifest_lookups += 1
            table_payload, _cursor_out, complete, anchor_used, fallback_used = read_tabular_rows_with_anchor(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                start_row_index=0,
                budget_chars=per_item_budget,
                max_rows=None,
                business_profile=business,
                use_anchor=True,
            )
            if anchor_used:
                anchor_manifest_hits += 1
            if fallback_used:
                anchor_fallback_reads += 1

        return NewTableReadResult(
            payload=table_payload,
            payload_type="table",
            evidence_kind="table_rows",
            title=title,
            next_cursor=None,
            complete=complete,
            anchor_manifest_lookups=anchor_manifest_lookups,
            anchor_manifest_hits=anchor_manifest_hits,
            anchor_fallback_reads=anchor_fallback_reads,
        )

    if mode != "excerpt" and is_table_chunk and table_id and table_role == "row":
        # Resolve table title for friendlier output (row-chunk reads should still
        # look like "Table X", not just the upload title).
        try:
            table_obj = (
                KnowledgeUploadTable.objects.filter(id=table_id)
                .only("id", "title", "section_heading", "order_index")
                .first()
            )
            if table_obj:
                table_title = (table_obj.title or table_obj.section_heading or "").strip()
                if not table_title:
                    table_title = f"Table {table_obj.order_index}" if table_obj.order_index else "Table"
                title = table_title
        except Exception:
            pass

        if row_start is not None or row_limit is not None:
            table_payload, _cursor_out, complete = read_tabular_rows_segment_facts(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                start_row_index=int(row_start or 0),
                budget_chars=per_item_budget,
                max_rows=row_limit,
                business_profile=business,
                selection_mode="row_range",
            )
            table_payload["upgraded_from_ref"] = "table_row"
            payload = table_payload
        else:
            row_index_raw = chunk_meta.get("table_row_index")
            try:
                row_index_value = int(row_index_raw) if row_index_raw is not None else 0
            except (TypeError, ValueError):
                row_index_value = 0

            table_payload, complete = read_exact_table_row(
                item_id=item_id,
                upload_id=upload_id,
                table_id=table_id,
                db_row_index=row_index_value,
                budget_chars=per_item_budget,
                business_profile=business,
            )
            payload = table_payload

        return NewTableReadResult(
            payload=payload,
            payload_type="table",
            evidence_kind="table_rows",
            title=title,
            next_cursor=None,
            complete=complete,
        )

    return None
