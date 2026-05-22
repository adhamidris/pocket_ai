from __future__ import annotations

import logging
from typing import Mapping

from apps.conversations.models import Conversation
from apps.rag.observability.logging import structured_log

from ...types import ToolExecutionContext


logger = logging.getLogger(__name__)


class McpTurnFinalizationMixin:
    def _log_unfulfilled_read_required(
        self,
        *,
        conversation: Conversation,
        tool_context: ToolExecutionContext,
    ) -> None:
        unmet_read_required_count = 0
        read_required_reasons: set[str] = set()
        table_results_present = False
        for entry in getattr(tool_context, "knowledge_results", []):
            if not isinstance(entry, Mapping):
                continue
            if entry.get("read_required"):
                unmet_read_required_count += 1
                reasons = entry.get("read_required_reasons")
                if isinstance(reasons, list):
                    for reason in reasons:
                        if isinstance(reason, str) and reason:
                            read_required_reasons.add(reason)
            if entry.get("search_stage") in {"table_direct", "table_blended"}:
                table_results_present = True
        no_reads = not getattr(tool_context, "knowledge_reads", [])
        if no_reads and unmet_read_required_count:
            structured_log(
                "mcp",
                "read_required.unfulfilled",
                {
                    "read_required_count": unmet_read_required_count,
                    "table_results_present": table_results_present,
                    "reasons": sorted(read_required_reasons),
                },
                context={
                    "conversation": conversation.id,
                    "business": conversation.business_profile_id,
                },
                logger_obj=logger,
            )
