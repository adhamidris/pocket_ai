from __future__ import annotations

from collections.abc import Callable

from ...runtime.agentic_read_cursor import _sign_agentic_read_cursor_v2


def _read_page_blocks_segment(
    *,
    item_id: str,
    cursor_payload_base: Callable[..., dict[str, object]],
    upload_id: str,
    page_number: int,
    start_order: int,
    start_offset: int,
    budget_chars: int,
    prepend_sep: bool = False,
) -> tuple[str, dict[str, object] | None, bool]:
    try:
        from apps.knowledge.models import (
            KnowledgeUploadPage,
            KnowledgeUploadPageBlock,
        )
    except Exception:
        return "", None, True

    page_obj = (
        KnowledgeUploadPage.objects.filter(upload_id=upload_id, page_number=page_number)
        .only("id")
        .first()
    )
    if not page_obj:
        return "", None, True

    blocks_qs = (
        KnowledgeUploadPageBlock.objects.filter(page_id=page_obj.id)
        .exclude(text="")
        .order_by("order_index")
        .values_list("order_index", "text")
    )

    remaining = max(0, int(budget_chars))
    out_parts: list[str] = []
    cursor_next: dict[str, object] | None = None
    complete = True
    # Only prepend a separator when resuming at an element boundary.
    prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

    started = False
    for order_index, text in blocks_qs.iterator():  # type: ignore[attr-defined]
        try:
            order_int = int(order_index)
        except (TypeError, ValueError):
            continue
        if order_int < int(start_order):
            continue
        raw_text = str(text or "")
        if not raw_text:
            continue

        chunk_text = raw_text
        offset = 0
        if not started:
            started = True
            offset = max(0, int(start_offset))
            if offset:
                chunk_text = chunk_text[offset:]
        separator = "\n\n" if (out_parts or prepend_sep) else ""
        # If we can't fit the separator + at least one character, stop and continue on next call.
        needed_min = len(separator) + 1
        if remaining < needed_min:
            next_prepend_sep = True if out_parts else bool(prepend_sep)
            cursor_next = {
                **cursor_payload_base(item_id=item_id, kind="page_blocks"),
                "upload_id": upload_id,
                "page_number": int(page_number),
                "block_order": int(order_int),
                "char_offset": int(offset),
            }
            if next_prepend_sep:
                cursor_next["prepend_sep"] = True
            complete = False
            break
        if separator:
            out_parts.append(separator)
            remaining -= len(separator)

        if len(chunk_text) <= remaining:
            out_parts.append(chunk_text)
            remaining -= len(chunk_text)
            # Continue to next block
            continue

        # Partial block
        out_parts.append(chunk_text[:remaining])
        cursor_next = {
            **cursor_payload_base(item_id=item_id, kind="page_blocks"),
            "upload_id": upload_id,
            "page_number": int(page_number),
            "block_order": int(order_int),
            "char_offset": int(offset + remaining),
        }
        complete = False
        break

    content_out = "".join(out_parts)
    cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
    return content_out, ({"cursor": cursor_str} if cursor_str else None), complete

def _read_section_span_segment(
    *,
    load_ordered_page_blocks: Callable[[str], list[dict[str, object]]],
    cursor_payload_base: Callable[..., dict[str, object]],
    item_id: str,
    upload_id: str,
    start_page_number: int,
    start_block_order: int,
    end_page_number: int,
    end_block_order: int,
    current_page_number: int,
    current_block_order: int,
    start_offset: int,
    budget_chars: int,
    prepend_sep: bool = False,
) -> tuple[str, dict[str, object] | None, bool]:
    blocks = load_ordered_page_blocks(upload_id)
    if not blocks:
        return "", None, True

    remaining = max(0, int(budget_chars))
    out_parts: list[str] = []
    cursor_next: dict[str, object] | None = None
    complete = True
    prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

    started = False
    current_page = int(current_page_number)
    current_order = int(current_block_order)
    for block in blocks:
        try:
            page_int = int(block.get("page_number") or 0)
            order_int = int(block.get("order_index") or 0)
        except (TypeError, ValueError):
            continue
        if (page_int, order_int) < (int(start_page_number), int(start_block_order)):
            continue
        if (page_int, order_int) > (int(end_page_number), int(end_block_order)):
            continue
        if (page_int, order_int) < (current_page, current_order):
            continue
        raw_text = str(block.get("text") or "")
        if not raw_text:
            continue

        chunk_text = raw_text
        offset = 0
        if not started:
            started = True
            offset = max(0, int(start_offset))
            if offset:
                chunk_text = chunk_text[offset:]

        separator = "\n\n" if (out_parts or prepend_sep) else ""
        needed_min = len(separator) + 1
        if remaining < needed_min:
            next_prepend_sep = True if out_parts else bool(prepend_sep)
            cursor_next = {
                **cursor_payload_base(item_id=item_id, kind="section_span"),
                "upload_id": upload_id,
                "start_page_number": int(start_page_number),
                "start_block_order": int(start_block_order),
                "end_page_number": int(end_page_number),
                "end_block_order": int(end_block_order),
                "current_page_number": int(page_int),
                "current_block_order": int(order_int),
                "char_offset": int(offset),
            }
            if next_prepend_sep:
                cursor_next["prepend_sep"] = True
            complete = False
            break
        if separator:
            out_parts.append(separator)
            remaining -= len(separator)

        if len(chunk_text) <= remaining:
            out_parts.append(chunk_text)
            remaining -= len(chunk_text)
            continue

        out_parts.append(chunk_text[:remaining])
        cursor_next = {
            **cursor_payload_base(item_id=item_id, kind="section_span"),
            "upload_id": upload_id,
            "start_page_number": int(start_page_number),
            "start_block_order": int(start_block_order),
            "end_page_number": int(end_page_number),
            "end_block_order": int(end_block_order),
            "current_page_number": int(page_int),
            "current_block_order": int(order_int),
            "char_offset": int(offset + remaining),
        }
        complete = False
        break

    content_out = "".join(out_parts)
    cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
    return content_out, ({"cursor": cursor_str} if cursor_str else None), complete
