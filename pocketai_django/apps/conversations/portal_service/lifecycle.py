from __future__ import annotations

from django.contrib.auth import get_user_model
from django.utils import timezone

from pocketai.language import normalize_language_code

from apps.accounts.models import AgentProfile, BusinessProfile
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender, ConversationStatus


class PortalConversationLifecycleMixin:
    def _get_or_create_conversation(
        self,
        *,
        business: BusinessProfile,
        agent: AgentProfile,
        existing_session_token: str | None,
        metadata: dict,
    ) -> Conversation:
        incoming_metadata = dict(metadata or {})
        now = timezone.now()
        owner_user = self._resolve_owner_user(business=business, metadata=incoming_metadata)
        conversation = None
        if existing_session_token:
            conversation = Conversation.objects.filter(
                business_profile=business,
                session_token=existing_session_token,
            ).first()
            # Session cookies can outlive DB resets or tenant cleanup. Treat a stale
            # token as a cache miss and create a fresh conversation instead of 404ing.

        if conversation is None:
            expires_at = now + self.session_ttl if self.session_ttl else None
            conversation = Conversation.objects.create(
                business_profile=business,
                agent_profile=agent,
                owner_user=owner_user,
                metadata=incoming_metadata,
                expires_at=expires_at,
            )
            # self._ensure_welcome_message(conversation) # Disabled to support empty state
            conversation.refresh_from_db()
            return conversation

        updated_metadata = {**(conversation.metadata or {}), **incoming_metadata}
        update_fields: list[str] = []
        if conversation.owner_user_id is None and owner_user is not None:
            conversation.owner_user = owner_user
            update_fields.append("owner_user")
        if updated_metadata != conversation.metadata:
            conversation.metadata = updated_metadata
            update_fields.append("metadata")
        if self.session_ttl:
            next_expires = now + self.session_ttl
            if conversation.expires_at != next_expires:
                conversation.expires_at = next_expires
                update_fields.append("expires_at")
        elif conversation.expires_at is not None:
            conversation.expires_at = None
            update_fields.append("expires_at")
        if update_fields:
            conversation.save(update_fields=list(dict.fromkeys(update_fields)))
        return conversation

    def _resolve_owner_user(self, *, business: BusinessProfile, metadata: dict) -> object:
        actor_user_id = (
            metadata.get("actor_user_id")
            or metadata.get("actorUserId")
            or metadata.get("owner_user_id")
            or metadata.get("ownerUserId")
        )
        if actor_user_id:
            user_model = get_user_model()
            owner_user = user_model.objects.filter(id=actor_user_id).first()
            if owner_user is not None:
                if getattr(owner_user, "is_staff", False):
                    return owner_user
                if owner_user.business_profiles.filter(id=business.id).exists():
                    return owner_user
        return business.user

    def _ensure_welcome_message(self, conversation: Conversation) -> None:
        agent_name = conversation.agent_profile.name if conversation.agent_profile else "Pocket AI"
        ConversationMessage.objects.create(
            conversation=conversation,
            sender=ConversationSender.AI,
            body=f"Hi, I'm {agent_name}. How can I help today?",
            metadata={"type": "welcome"},
        )

    def _touch_conversation_after_message(self, conversation: Conversation, message: ConversationMessage, *, metadata_updated: bool = False) -> None:
        update_fields: list[str] = ["last_activity_at"]
        if message.sender == ConversationSender.CUSTOMER:
            if conversation.first_customer_message_at is None:
                conversation.first_customer_message_at = message.sent_at
                update_fields.append("first_customer_message_at")
            if conversation.status in {
                ConversationStatus.NEW,
                ConversationStatus.RESOLVED,
                ConversationStatus.CLOSED,
                ConversationStatus.EXPIRED,
            }:
                conversation.status = ConversationStatus.LIVE
                conversation.closed_at = None
                update_fields.extend(["status", "closed_at"])
        if message.sender == ConversationSender.AI and conversation.first_ai_message_at is None:
            conversation.first_ai_message_at = message.sent_at
            update_fields.append("first_ai_message_at")
        now = timezone.now()
        if self.session_ttl:
            next_expires = now + self.session_ttl
            if conversation.expires_at != next_expires:
                conversation.expires_at = next_expires
                update_fields.append("expires_at")
        elif conversation.expires_at is not None:
            conversation.expires_at = None
            update_fields.append("expires_at")
        if metadata_updated:
            update_fields.append("metadata")
        conversation.save(update_fields=list(dict.fromkeys(update_fields)))

    def _capture_ui_language_preference(self, *, conversation: Conversation, language: str | None) -> bool:
        normalized = normalize_language_code(language)
        if not normalized:
            return False
        convo_meta = conversation.metadata if isinstance(getattr(conversation, "metadata", None), dict) else {}
        if convo_meta.get("ui_language") == normalized:
            return False
        updated_meta = dict(convo_meta)
        updated_meta["ui_language"] = normalized
        conversation.metadata = updated_meta
        return True
