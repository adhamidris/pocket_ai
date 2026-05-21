from __future__ import annotations

import uuid
from typing import Iterable

from apps.conversations.models import Conversation
from apps.conversations.portal_service.types import PortalSessionSummary


class PortalSessionSummaryMixin:
    def _generate_session_title(self, first_message: str, max_length: int = 50) -> str:
        """
        Generate a meaningful session title from the first customer message.
        
        Truncates at word boundary and adds ellipsis if needed.
        """
        text = (first_message or "").strip()
        if not text:
            return "New conversation"
        
        # Remove newlines and extra whitespace
        text = " ".join(text.split())
        
        if len(text) <= max_length:
            return text
        
        # Truncate at word boundary
        truncated = text[:max_length]
        last_space = truncated.rfind(" ")
        if last_space > max_length // 2:
            truncated = truncated[:last_space]
        
        return truncated.rstrip(".,!?;:") + "..."

    def _classify_conversation_session(self, conversation: Conversation) -> tuple[str, uuid.UUID | None, str, str]:
        metadata = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
        assistant = getattr(conversation, "custom_assistant", None)
        meta_type = str(metadata.get("type") or metadata.get("purpose") or metadata.get("source") or "").strip().lower()
        assistant_id = getattr(assistant, "id", None)
        assistant_name = (
            (getattr(assistant, "name", "") or "")
            or str(metadata.get("custom_assistant_name") or metadata.get("customAssistantName") or "").strip()
        )
        assistant_agent = getattr(assistant, "agent_profile", None) if assistant is not None else None
        assistant_agent_name = (
            (getattr(assistant_agent, "name", "") or "")
            or str(metadata.get("custom_assistant_agent_name") or metadata.get("customAssistantAgentName") or "").strip()
        )
        is_custom_assistant_session = bool(
            assistant
            or getattr(conversation, "custom_assistant_id", None)
            or meta_type == "custom_assistant_session"
            or str(metadata.get("custom_assistant_id") or "").strip()
        )
        return ("custom_assistant" if is_custom_assistant_session else "chat", assistant_id, assistant_name, assistant_agent_name)

    def _build_session_summaries(
        self,
        conversations: Iterable[Conversation],
        *,
        limit: int | None = None,
    ) -> tuple[PortalSessionSummary, ...]:
        summaries: list[PortalSessionSummary] = []
        for conv in conversations:
            if self._is_internal_agent_notification_surface(conv):
                continue
            first_messages = getattr(conv, "first_customer_messages", [])
            first_msg = first_messages[0] if first_messages else None
            session_type, custom_assistant_id, custom_assistant_name, custom_assistant_agent_name = self._classify_conversation_session(conv)
            is_custom_assistant_session = session_type == "custom_assistant"

            if first_msg:
                title = self._generate_session_title(first_msg.body)
                preview = (first_msg.body or "")[:100]
            elif is_custom_assistant_session:
                title = "New session"
                preview = custom_assistant_name or "Custom Assistant"
            else:
                title = "New conversation"
                preview = ""

            summaries.append(
                PortalSessionSummary(
                    conversation_id=conv.id,
                    session_token=conv.session_token,
                    title=title,
                    started_at=conv.started_at,
                    last_activity_at=conv.last_activity_at,
                    status=conv.status,
                    message_count=getattr(conv, "message_count", 0),
                    preview=preview.strip(),
                    session_type=session_type,
                    custom_assistant_id=custom_assistant_id,
                    custom_assistant_name=custom_assistant_name,
                    custom_assistant_agent_name=custom_assistant_agent_name,
                )
            )
            if limit is not None and len(summaries) >= limit:
                break
        return tuple(summaries)
