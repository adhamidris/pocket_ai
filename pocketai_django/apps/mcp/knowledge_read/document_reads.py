from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping

from apps.knowledge.models import KnowledgeUploadPageBlock


@dataclass(slots=True)
class DocumentReadResult:
    payload: dict[str, object]
    next_cursor: str | None
    complete: bool


def read_new_document_source(
    *,
    item_id: str,
    upload: object,
    upload_id: str,
    upload_record: object | None,
    chunk_record: object | None,
    chunk_meta: Mapping[str, object],
    per_item_budget: int,
    business: object,
    errors: list[dict[str, object]],
    read: list[dict[str, object]],
    resolve_text_section_span: Callable[..., dict[str, object] | None],
    read_page_blocks_segment: Callable[..., tuple[str, dict[str, object] | None, bool]],
    read_section_span_segment: Callable[..., tuple[str, dict[str, object] | None, bool]],
    read_chunk_window_segment: Callable[..., tuple[str, dict[str, object] | None, bool]],
) -> DocumentReadResult | None:
    payload: dict[str, object] = {"type": "text", "text": ""}

    if upload_record is not None and chunk_record is None:
        page_number = 1
        has_page_blocks = False
        try:
            has_page_blocks = (
                KnowledgeUploadPageBlock.objects.filter(
                    upload_id=upload_id,
                    page__page_number=page_number,
                )
                .exclude(text="")
                .exists()
            )
        except Exception:
            has_page_blocks = False

        if has_page_blocks:
            content_text, cursor_out, complete = read_page_blocks_segment(
                item_id=item_id,
                upload=upload,
                upload_id=upload_id,
                page_number=page_number,
                start_order=0,
                start_offset=0,
                budget_chars=per_item_budget,
            )
            payload["text"] = content_text
            next_cursor = cursor_out.get("cursor") if cursor_out else None
            return DocumentReadResult(payload=payload, next_cursor=next_cursor, complete=complete)

        errors.append(
            {
                "id": item_id,
                "error_code": "not_found",
                "hint": "No readable content found for that id.",
            }
        )
        read.append({"id": item_id, "status": "error"})
        return None

    # Prefer page blocks when a page can be resolved; fall back to a chunk window.
    page_number = 1
    if chunk_record:
        meta_page = chunk_meta.get("table_page_number") or chunk_meta.get("chunk_page") or chunk_meta.get("page_number")
        if meta_page:
            try:
                parsed_page = int(meta_page)
                if parsed_page >= 1:
                    page_number = parsed_page
            except (TypeError, ValueError):
                pass

    section_span = resolve_text_section_span(
        upload_id=upload_id,
        chunk_record=chunk_record,
        chunk_meta=chunk_meta,
        fallback_page_number=page_number,
    )
    if section_span is not None:
        content_text, cursor_out, complete = read_section_span_segment(
            item_id=item_id,
            upload_id=upload_id,
            start_page_number=int(section_span["start_page_number"]),
            start_block_order=int(section_span["start_block_order"]),
            end_page_number=int(section_span["end_page_number"]),
            end_block_order=int(section_span["end_block_order"]),
            current_page_number=int(section_span["start_page_number"]),
            current_block_order=int(section_span["start_block_order"]),
            start_offset=0,
            budget_chars=per_item_budget,
        )
        payload["text"] = content_text
        next_cursor = cursor_out.get("cursor") if cursor_out else None
        return DocumentReadResult(payload=payload, next_cursor=next_cursor, complete=complete)

    has_page_blocks = False
    try:
        has_page_blocks = (
            KnowledgeUploadPageBlock.objects.filter(
                upload_id=upload_id,
                page__page_number=page_number,
            )
            .exclude(text="")
            .exists()
        )
    except Exception:
        has_page_blocks = False

    # If page blocks exist for the resolved page, use them.
    if has_page_blocks:
        content_text, cursor_out, complete = read_page_blocks_segment(
            item_id=item_id,
            upload=upload,
            upload_id=upload_id,
            page_number=page_number,
            start_order=0,
            start_offset=0,
            budget_chars=per_item_budget,
        )
        payload["text"] = content_text
        next_cursor = cursor_out.get("cursor") if cursor_out else None
        return DocumentReadResult(payload=payload, next_cursor=next_cursor, complete=complete)

    if chunk_record and chunk_record.chunk_index is not None:
        neighbor = 1
        start_index = max(0, int(chunk_record.chunk_index) - neighbor)
        end_index = int(chunk_record.chunk_index) + neighbor
        content_text, cursor_out, complete = read_chunk_window_segment(
            item_id=item_id,
            upload_id=upload_id,
            start_index=start_index,
            end_index=end_index,
            current_index=start_index,
            start_offset=0,
            budget_chars=per_item_budget,
            business_profile=business,
        )
        payload["text"] = content_text
        next_cursor = cursor_out.get("cursor") if cursor_out else None
        return DocumentReadResult(payload=payload, next_cursor=next_cursor, complete=complete)

    errors.append({"id": item_id, "error_code": "not_found", "hint": "No readable content found for that id."})
    read.append({"id": item_id, "status": "error"})
    return None
