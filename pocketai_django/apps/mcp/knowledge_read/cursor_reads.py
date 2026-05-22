from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping


@dataclass(slots=True)
class CursorReadResult:
    handled: bool
    payload: dict[str, object]
    payload_type: str
    evidence_kind: str
    title: str
    next_cursor: str | None
    complete: bool


def read_from_cursor(
    *,
    item_id: str,
    upload: object,
    upload_id: str,
    cursor_payload: Mapping[str, object],
    row_start: int | None,
    row_limit: int | None,
    per_item_budget: int,
    business: object,
    title: str,
    errors: list[dict[str, object]],
    read: list[dict[str, object]],
    read_section_span_segment: Callable[..., tuple[str, dict[str, object] | None, bool]],
    read_page_blocks_segment: Callable[..., tuple[str, dict[str, object] | None, bool]],
    read_tabular_rows_segment_facts: Callable[..., tuple[dict[str, object], dict[str, object] | None, bool]],
    read_chunk_window_segment: Callable[..., tuple[str, dict[str, object] | None, bool]],
    read_artifact_segment: Callable[..., tuple[str, dict[str, object] | None, bool, dict[str, object] | None]],
) -> CursorReadResult | None:
    payload: dict[str, object] = {"type": "text", "text": ""}
    payload_type = "text"
    evidence_kind = "text_excerpt"
    next_cursor: str | None = None
    complete = True

    kind = str(cursor_payload.get("kind") or "")
    prepend_sep = bool(cursor_payload.get("prepend_sep"))
    if kind == "section_span":
        start_page_number = int(cursor_payload.get("start_page_number") or 1)
        start_block_order = int(cursor_payload.get("start_block_order") or 0)
        end_page_number = int(cursor_payload.get("end_page_number") or start_page_number)
        end_block_order = int(cursor_payload.get("end_block_order") or start_block_order)
        current_page_number = int(cursor_payload.get("current_page_number") or start_page_number)
        current_block_order = int(cursor_payload.get("current_block_order") or start_block_order)
        char_offset = int(cursor_payload.get("char_offset") or 0)
        content_text, cursor_out, complete = read_section_span_segment(
            item_id=item_id,
            upload_id=upload_id,
            start_page_number=start_page_number,
            start_block_order=start_block_order,
            end_page_number=end_page_number,
            end_block_order=end_block_order,
            current_page_number=current_page_number,
            current_block_order=current_block_order,
            start_offset=char_offset,
            budget_chars=per_item_budget,
            prepend_sep=prepend_sep,
        )
        payload["text"] = content_text
        next_cursor = cursor_out.get("cursor") if cursor_out else None
    elif kind == "page_blocks":
        page_number = int(cursor_payload.get("page_number") or 1)
        block_order = int(cursor_payload.get("block_order") or 0)
        char_offset = int(cursor_payload.get("char_offset") or 0)
        content_text, cursor_out, complete = read_page_blocks_segment(
            item_id=item_id,
            upload=upload,
            upload_id=upload_id,
            page_number=page_number,
            start_order=block_order,
            start_offset=char_offset,
            budget_chars=per_item_budget,
            prepend_sep=prepend_sep,
        )
        payload["text"] = content_text
        next_cursor = cursor_out.get("cursor") if cursor_out else None
    elif kind == "table_rows":
        payload_type = "table"
        evidence_kind = "table_rows"
        table_id = str(cursor_payload.get("table_id") or "").strip()
        raw_row_index = cursor_payload.get("row_index")
        try:
            start_row_index = int(raw_row_index) if raw_row_index is not None else 0
        except (TypeError, ValueError):
            start_row_index = 0
        effective_start_row = row_start if row_start is not None else start_row_index
        table_payload, _cursor_out, complete = read_tabular_rows_segment_facts(
            item_id=item_id,
            upload_id=upload_id,
            table_id=table_id,
            start_row_index=effective_start_row,
            budget_chars=per_item_budget,
            max_rows=row_limit,
            business_profile=business,
            selection_mode="row_range",
        )
        payload = table_payload
        # Tables page via row_start/row_limit (no cursors).
        next_cursor = None
    elif kind == "chunk_window":
        start_index = int(cursor_payload.get("chunk_start") or 0)
        end_index = int(cursor_payload.get("chunk_end") or start_index)
        chunk_index = int(cursor_payload.get("chunk_index") or start_index)
        char_offset = int(cursor_payload.get("char_offset") or 0)
        content_text, cursor_out, complete = read_chunk_window_segment(
            item_id=item_id,
            upload_id=upload_id,
            start_index=start_index,
            end_index=end_index,
            current_index=chunk_index,
            start_offset=char_offset,
            budget_chars=per_item_budget,
            prepend_sep=prepend_sep,
            business_profile=business,
        )
        payload["text"] = content_text
        next_cursor = cursor_out.get("cursor") if cursor_out else None
    elif kind == "artifact":
        artifact_id = str(cursor_payload.get("artifact_id") or "").strip()
        char_offset = int(cursor_payload.get("char_offset") or 0)
        content_text, cursor_out, complete, artifact_meta = read_artifact_segment(
            item_id=item_id,
            artifact_id=artifact_id,
            start_offset=char_offset,
            budget_chars=per_item_budget,
        )
        if artifact_meta and artifact_meta.get("error_code"):
            errors.append(dict(artifact_meta))
            read.append({"id": item_id, "status": "error"})
            return None
        if artifact_meta:
            title_override = artifact_meta.get("title")
            if isinstance(title_override, str) and title_override.strip():
                title = title_override.strip()
            type_override = artifact_meta.get("type")
            if isinstance(type_override, str) and type_override.strip():
                payload_type = type_override.strip()
                evidence_kind = "table_rows" if payload_type == "table" else "text_excerpt"
        if payload_type == "table":
            # Artifact segments currently stream text; tables should not route here.
            payload = {"type": "table", "columns": [], "rows": []}
        else:
            payload["text"] = content_text
        next_cursor = cursor_out.get("cursor") if cursor_out else None
    else:
        errors.append({"id": item_id, "error_code": "invalid_cursor", "hint": "Unknown cursor kind."})
        read.append({"id": item_id, "status": "error"})
        return None

    return CursorReadResult(
        handled=True,
        payload=payload,
        payload_type=payload_type,
        evidence_kind=evidence_kind,
        title=title,
        next_cursor=next_cursor,
        complete=complete,
    )
