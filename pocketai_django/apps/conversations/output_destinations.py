from __future__ import annotations

import uuid

from django.utils import timezone

from apps.conversations.models import (
    AgentAutomation,
    AgentWatcher,
    Conversation,
    ConversationChannel,
    ConversationMessage,
    ConversationSender,
)


def ensure_automation_thread(automation: AgentAutomation) -> Conversation:
    """
    Ensure the automation has a dedicated destination conversation thread.

    This thread becomes the default landing spot for automation runs.
    """

    if automation.conversation_id and getattr(automation, "conversation", None):
        return automation.conversation
    if automation.conversation_id:
        existing = Conversation.objects.filter(id=automation.conversation_id, business_profile=automation.business_profile).first()
        if existing:
            automation.conversation = existing
            return existing

    metadata = dict(automation.metadata or {}) if isinstance(automation.metadata, dict) else {}
    title = (automation.name or "Automation")[:160]
    thread_meta = {
        "type": "automation_thread",
        "automation_id": str(automation.id),
        "automation_name": title,
    }
    metadata.setdefault("thread", thread_meta)

    conversation = Conversation.objects.create(
        business_profile=automation.business_profile,
        agent_profile=automation.agent_profile,
        channel=ConversationChannel.API,
        metadata={"type": "automation_thread", "automation_id": str(automation.id), "automation_name": title},
    )
    ConversationMessage.objects.create(
        conversation=conversation,
        sender=ConversationSender.SYSTEM,
        body=f"Automation thread created for: {title}",
        metadata={"type": "automation_thread_intro", "automation_id": str(automation.id)},
        content_blocks=[],
    )
    AgentAutomation.objects.filter(id=automation.id).update(conversation_id=conversation.id, updated_at=timezone.now(), metadata=metadata)
    automation.conversation = conversation
    automation.conversation_id = conversation.id
    return conversation


def ensure_watcher_thread(watcher: AgentWatcher) -> Conversation:
    """
    Ensure the watcher has a dedicated destination conversation thread.

    This thread becomes the default landing spot for watcher runs.
    """

    if watcher.conversation_id and getattr(watcher, "conversation", None):
        return watcher.conversation
    if watcher.conversation_id:
        existing = Conversation.objects.filter(id=watcher.conversation_id, business_profile=watcher.business_profile).first()
        if existing:
            watcher.conversation = existing
            return existing

    title = (watcher.name or "Watcher")[:160]
    conversation = Conversation.objects.create(
        business_profile=watcher.business_profile,
        agent_profile=watcher.agent_profile,
        channel=ConversationChannel.API,
        metadata={"type": "watcher_thread", "watcher_id": str(watcher.id), "watcher_name": title},
    )
    ConversationMessage.objects.create(
        conversation=conversation,
        sender=ConversationSender.SYSTEM,
        body=f"Watcher thread created for: {title}",
        metadata={"type": "watcher_thread_intro", "watcher_id": str(watcher.id)},
        content_blocks=[],
    )
    AgentWatcher.objects.filter(id=watcher.id).update(conversation_id=conversation.id, updated_at=timezone.now())
    watcher.conversation = conversation
    watcher.conversation_id = conversation.id
    return conversation


def parse_summary_destination_id(destination_config: object) -> uuid.UUID | None:
    """
    Extract an optional summary destination conversation UUID.

    Supports both snake_case and camelCase keys.
    """

    if not isinstance(destination_config, dict):
        return None
    raw = (
        destination_config.get("postSummaryToConversationId")
        or destination_config.get("post_summary_to_conversation_id")
        or destination_config.get("summaryConversationId")
        or destination_config.get("summary_conversation_id")
    )
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def parse_summary_max_chars(destination_config: object, *, default: int = 800) -> int:
    if not isinstance(destination_config, dict):
        return max(100, min(int(default), 4000))
    raw = destination_config.get("summaryMaxChars") or destination_config.get("summary_max_chars") or default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return max(100, min(int(value), 4000))
