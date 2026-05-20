from __future__ import annotations

import logging

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log


def _log_stream_mismatch(
    target: list[str],
    final_text: str,
    *,
    stage: str,
    conversation: Conversation,
    logger_obj: logging.Logger,
) -> None:
    normalized_final = str(final_text or "")
    current_text = "".join(target)
    if current_text == normalized_final:
        return
    structured_log(
        "mcp",
        "stream.final_text_mismatch",
        {
            "stage": stage,
            "streamed_chars": len(current_text),
            "final_chars": len(normalized_final),
            "streamed_is_prefix": bool(current_text and normalized_final.startswith(current_text)),
            "streamed_empty": not bool(current_text),
        },
        context={
            "conversation": conversation.id,
            "business": conversation.business_profile_id,
        },
        logger_obj=logger_obj,
    )
