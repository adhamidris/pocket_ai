from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.conversations.models import AgentRequest, AgentRun, AgentRunEvent, ConversationMessage
from apps.conversations.portal_session_event_bus import publish_portal_agent_request_event, publish_portal_conversation_event
from apps.conversations.portal_session_serializers import (
    serialize_agent_request_for_portal,
    serialize_agent_run_event_for_portal,
    serialize_agent_run_for_portal,
    serialize_conversation_message_for_portal,
)
from core.tenancy import tenant_bypass


@receiver(post_save, sender=AgentRunEvent)
def _publish_portal_agent_run_event(sender, instance: AgentRunEvent, created: bool, **kwargs) -> None:
    if not created:
        return
    run: AgentRun | None = getattr(instance, "run", None)
    if run is None or getattr(run, "conversation_id", None) is None:
        with tenant_bypass():
            run = AgentRun.objects.select_related("conversation").filter(id=instance.run_id).first()
    if not run or not run.conversation_id:
        return

    payload = {
        "run": serialize_agent_run_for_portal(run),
        "event": serialize_agent_run_event_for_portal(instance),
    }
    conversation_id = run.conversation_id
    transaction.on_commit(
        lambda: publish_portal_conversation_event(
            conversation_id=conversation_id,
            event_name="agentRunEvent",
            payload=payload,
        )
    )


@receiver(post_save, sender=ConversationMessage)
def _publish_portal_conversation_message(sender, instance: ConversationMessage, created: bool, **kwargs) -> None:
    if not created:
        return
    meta = instance.metadata if isinstance(getattr(instance, "metadata", None), dict) else {}
    source = str(meta.get("source") or "").strip().lower()
    if source not in {"agent_run", "voice_call"}:
        return
    if not instance.conversation_id:
        return

    payload = {"message": serialize_conversation_message_for_portal(instance)}
    conversation_id = instance.conversation_id
    transaction.on_commit(
        lambda: publish_portal_conversation_event(
            conversation_id=conversation_id,
            event_name="conversationMessage",
            payload=payload,
        )
    )


@receiver(post_save, sender=AgentRequest)
def _publish_portal_agent_request(sender, instance: AgentRequest, created: bool, **kwargs) -> None:
    # Publish on both create and update (UI listens to updatedAt changes).
    from_id = getattr(instance, "from_agent_profile_id", None)
    to_id = getattr(instance, "to_agent_profile_id", None)
    if not from_id and not to_id:
        return
    payload = {"request": serialize_agent_request_for_portal(instance)}

    def _publish() -> None:
        published = set()
        for agent_id in (from_id, to_id):
            if not agent_id or agent_id in published:
                continue
            published.add(agent_id)
            publish_portal_agent_request_event(agent_profile_id=agent_id, payload=payload)

    transaction.on_commit(_publish)

