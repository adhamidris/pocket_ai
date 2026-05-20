from __future__ import annotations

from collections.abc import Callable

from apps.accounts.models import KnowledgeStatus
from apps.knowledge.access.visibility import apply_customer_visible_chunks
from apps.knowledge.models import KnowledgeUploadChunk

from ..runtime.agentic_read_cursor import _sign_agentic_read_cursor_v2


def _read_chunk_window_segment(
    *,
    cursor_payload_base: Callable[..., dict[str, object]],
    item_id: str,
    upload_id: str,
    start_index: int,
    end_index: int,
    current_index: int,
    start_offset: int,
    budget_chars: int,
    prepend_sep: bool = False,
    business_profile,
) -> tuple[str, dict[str, object] | None, bool]:
    remaining = max(0, int(budget_chars))
    out_parts: list[str] = []
    cursor_next: dict[str, object] | None = None
    complete = True
    # Only prepend a separator when resuming at an element boundary.
    prepend_sep = bool(prepend_sep) and int(start_offset or 0) <= 0

    window_qs = (
        apply_customer_visible_chunks(
            KnowledgeUploadChunk.objects.filter(
                upload_id=upload_id,
                business_profile=business_profile,
                upload__status=KnowledgeStatus.ACTIVE,
                chunk_index__gte=int(start_index),
                chunk_index__lte=int(end_index),
            )
        )
        .exclude(content="")
        .order_by("chunk_index")
        .values_list("chunk_index", "content")
    )

    cur_idx = int(current_index)
    offset = max(0, int(start_offset))
    started = False
    for chunk_index, content in window_qs.iterator():  # type: ignore[attr-defined]
        try:
            ci = int(chunk_index)
        except (TypeError, ValueError):
            continue
        if ci < cur_idx:
            continue
        raw = str(content or "")
        if not raw:
            continue
        text = raw
        local_offset = 0
        if not started:
            started = True
            local_offset = offset
            if local_offset:
                text = text[local_offset:]

        separator = "\n\n" if (out_parts or prepend_sep) else ""
        needed_min = len(separator) + 1
        if remaining < needed_min:
            next_prepend_sep = True if out_parts else bool(prepend_sep)
            cursor_next = {
                **cursor_payload_base(item_id=item_id, kind="chunk_window"),
                "upload_id": upload_id,
                "chunk_start": int(start_index),
                "chunk_end": int(end_index),
                "chunk_index": int(ci),
                "char_offset": int(local_offset),
            }
            if next_prepend_sep:
                cursor_next["prepend_sep"] = True
            complete = False
            break
        if separator:
            out_parts.append(separator)
            remaining -= len(separator)

        if len(text) <= remaining:
            out_parts.append(text)
            remaining -= len(text)
            continue

        out_parts.append(text[:remaining])
        cursor_next = {
            **cursor_payload_base(item_id=item_id, kind="chunk_window"),
            "upload_id": upload_id,
            "chunk_start": int(start_index),
            "chunk_end": int(end_index),
            "chunk_index": int(ci),
            "char_offset": int(local_offset + remaining),
        }
        complete = False
        break

    content_out = "".join(out_parts)
    cursor_str = _sign_agentic_read_cursor_v2(cursor_next) if cursor_next else None
    return content_out, ({"cursor": cursor_str} if cursor_str else None), complete
