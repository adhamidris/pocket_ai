from __future__ import annotations

import uuid

from django.utils import timezone

from apps.conversations.models import ConversationFile


def portal_file_block(file: ConversationFile, *, label: str | None = None) -> dict[str, object]:
    """
    Build a stable, renderable content block representing a conversation-scoped file.

    Note: This block intentionally does NOT include a signed download URL so we
    don't persist short-lived tokens in message transcripts.
    """

    from apps.conversations.rich_blocks import new_block_id

    payload: dict[str, object] = {
        "file_id": str(file.id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": int(file.size_bytes or 0),
        "page_count": int(file.page_count or 0),
        "kind": str(file.kind or ""),
        "status": str(file.status or ""),
        "created_at": file.created_at.isoformat() if getattr(file, "created_at", None) else None,
    }
    if label:
        payload["label"] = str(label)
    return {
        "block_id": new_block_id(),
        "type": "file",
        "created_at": timezone.now().isoformat(),
        "payload": payload,
    }


def portal_file_text_block(
    *,
    file_id: uuid.UUID,
    filename: str,
    page_count: int | None,
    text: str,
    title: str | None = None,
    collapsed: bool = True,
) -> dict[str, object]:
    """
    Build a renderable block for extracted text (collapsed/expand UX).
    """

    from apps.conversations.rich_blocks import new_block_id

    payload: dict[str, object] = {
        "file_id": str(file_id),
        "filename": filename,
        "page_count": int(page_count or 0),
        "title": title or "",
        "text": text or "",
        "collapsed": bool(collapsed),
    }
    return {
        "block_id": new_block_id(),
        "type": "file_text",
        "created_at": timezone.now().isoformat(),
        "payload": payload,
    }
