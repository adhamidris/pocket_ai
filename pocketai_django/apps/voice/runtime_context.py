from __future__ import annotations

from asgiref.sync import sync_to_async

from apps.accounts.constants import DEFAULT_ASSISTANT_ROLE
from apps.conversations.models import ConversationMessage
from apps.voice.models import CallSession
from apps.voice.runtime_helpers import (
    _clip_text,
    _context_block_max_chars,
    _context_message_max_chars,
    _context_recent_messages,
    _context_summary_max_chars,
    _format_context_items,
)


class VoiceRuntimeContextMixin:

    async def _build_context_block(self) -> str:
        if self._context_loaded:
            return self._context_block
        self._context_loaded = True

        def _load() -> str:
            call_session = (
                CallSession.objects.select_related("agent_profile", "initiating_conversation")
                .filter(id=self.session_id)
                .first()
            )
            if not call_session:
                return ""
            parts: list[str] = []

            agent = call_session.agent_profile
            if agent:
                persona_lines = []
                if agent.name:
                    persona_lines.append(f"Name: {agent.name}")
                persona_lines.append(f"Role: {DEFAULT_ASSISTANT_ROLE}")
                if agent.tone:
                    persona_lines.append(f"Tone: {agent.tone}")
                parts.append("Assistant persona:\n" + "\n".join(persona_lines))

            context_items = _format_context_items(call_session.context_items)
            if context_items:
                parts.append("Call context items:\n" + context_items)

            convo = call_session.initiating_conversation
            summary = ""
            if convo and convo.summary:
                summary = _clip_text(str(convo.summary), _context_summary_max_chars())
                if summary:
                    parts.append("Conversation summary:\n" + summary)

            if convo:
                max_messages = _context_recent_messages()
                if max_messages > 0:
                    messages = (
                        ConversationMessage.objects.filter(conversation_id=convo.id)
                        .order_by("-sent_at", "-created_at")
                        .values_list("sender", "body")[: max_messages]
                    )
                    if messages:
                        lines = []
                        for sender, body in reversed(list(messages)):
                            sender_label = str(sender or "unknown")
                            body_text = _clip_text(str(body or ""), _context_message_max_chars())
                            if body_text:
                                lines.append(f"{sender_label}: {body_text}")
                        if lines:
                            parts.append("Recent conversation messages:\n" + "\n".join(lines))

            block = "\n\n".join(parts).strip()
            if not block:
                return ""
            max_chars = _context_block_max_chars()
            return "Context:\n" + _clip_text(block, max_chars)

        self._context_block = await sync_to_async(_load)()
        return self._context_block
