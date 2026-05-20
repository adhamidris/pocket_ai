from __future__ import annotations

import logging
from typing import Mapping

from apps.conversations.models import Conversation
from apps.rag.rag_logging import structured_log

from ..runtime.rag_observability import build_retrieval_observability


logger = logging.getLogger(__name__)


def _attach_retrieval_observability(
    *,
    final_response: object,
    conversation: Conversation,
    query_scope_observability: Mapping[str, object],
    final_status: str,
) -> object:
    if isinstance(final_response, dict):
        diagnostics_out = final_response.get("diagnostics")
        if not isinstance(diagnostics_out, dict):
            diagnostics_out = {}
        diagnostics_out.setdefault("query_scope", dict(query_scope_observability))
        retrieval_observability = build_retrieval_observability(
            query_scope=query_scope_observability,
            diagnostics=diagnostics_out,
            completeness=(
                final_response.get("completeness")
                if isinstance(final_response.get("completeness"), Mapping)
                else {}
            ),
            refs=final_response.get("refs"),
            snippets=final_response.get("snippets"),
            status=str(final_response.get("status") or final_status or ""),
        )
        if retrieval_observability:
            final_response["retrieval_observability"] = retrieval_observability
            diagnostics_out["retrieval_observability"] = retrieval_observability
        if diagnostics_out:
            final_response["diagnostics"] = diagnostics_out
        structured_log(
            "mcp",
            "search.observability",
            retrieval_observability,
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )

    return final_response
