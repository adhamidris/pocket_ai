from __future__ import annotations

import logging

from apps.conversations.models import Conversation
from apps.rag.observability.logging import structured_log

from ...types import ToolExecutionContext


logger = logging.getLogger(__name__)


class McpTurnVerificationMixin:

    def _apply_verification_override(
        self,
        *,
        conversation: Conversation,
        user_message: str,
        draft_answer: str,
        tool_context: ToolExecutionContext,
    ) -> str:
        verification_message = self._run_verification(
            conversation=conversation,
            user_message=user_message,
            draft_answer=draft_answer,
            tool_context=tool_context,
        )
        verification_payload = (
            self._parse_verification_payload(verification_message)
            if verification_message
            else None
        )
        if verification_payload:
            tool_context.verification = dict(verification_payload)
        elif verification_message:
            raw_verification = str(verification_message.get("content") or "").strip()
            if raw_verification:
                tool_context.verification = {
                    "verdict": "parse_error",
                    "missing_points": [],
                    "final_response": "",
                    "notes": self._clip_text(raw_verification, 320),
                }
        if not getattr(tool_context, "verification", None):
            return draft_answer
        snapshot = dict(getattr(tool_context, "verification") or {})
        structured_log(
            "mcp",
            "verification.result",
            {
                "verdict": snapshot.get("verdict"),
                "missing_points": len(snapshot.get("missing_points") or ()),
                "override": bool(str(snapshot.get("final_response") or "").strip()),
            },
            context={
                "conversation": conversation.id,
                "business": conversation.business_profile_id,
            },
            logger_obj=logger,
        )
        verdict = snapshot.get("verdict")
        override = str(snapshot.get("final_response") or "").strip()
        if verdict in {"needs_clarification", "unsupported"} and override:
            return override
        return draft_answer
