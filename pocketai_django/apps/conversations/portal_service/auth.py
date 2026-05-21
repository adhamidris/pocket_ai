from __future__ import annotations

import dataclasses
import uuid

from apps.accounts.models import AgentProfile, BusinessProfile
from apps.conversations.models import Conversation
from apps.conversations.portal import (
    ChatPortalService,
    PortalAuthorizationError,
)


@dataclasses.dataclass(frozen=True, slots=True)
class PortalAuthorizedScope:
    business: BusinessProfile
    agent: AgentProfile


def is_authenticated_portal_user(user: object) -> bool:
    return bool(user and getattr(user, "is_authenticated", False))


def can_access_business(user: object, business: BusinessProfile) -> bool:
    if not is_authenticated_portal_user(user):
        return False
    if getattr(user, "is_staff", False):
        return True
    if getattr(user, "id", None) == getattr(business, "user_id", None):
        return True
    business_profiles = getattr(user, "business_profiles", None)
    if business_profiles is None:
        return False
    return bool(business_profiles.filter(id=business.id).exists())


def can_access_conversation(user: object, conversation: Conversation) -> bool:
    if not is_authenticated_portal_user(user):
        return False
    if getattr(user, "is_staff", False):
        return True
    if getattr(user, "id", None) == getattr(conversation, "owner_user_id", None):
        return True
    if getattr(user, "id", None) == getattr(conversation.business_profile, "user_id", None):
        return True
    business_profiles = getattr(user, "business_profiles", None)
    if business_profiles is None:
        return False
    return bool(business_profiles.filter(id=conversation.business_profile_id).exists())


def resolve_scope_for_user(
    *,
    service: ChatPortalService,
    user: object,
    business_slug: str,
    agent_slug: str,
) -> PortalAuthorizedScope:
    if not is_authenticated_portal_user(user):
        raise PortalAuthorizationError("Authentication is required.")
    business, agent = service.resolve_handle(business_slug, agent_slug)
    if not can_access_business(user, business):
        raise PortalAuthorizationError("You do not have access to this business.")
    return PortalAuthorizedScope(business=business, agent=agent)


def get_authorized_conversation(
    *,
    service: ChatPortalService,
    user: object,
    conversation_id: uuid.UUID | str,
    include_messages: bool = False,
) -> Conversation:
    if not is_authenticated_portal_user(user):
        raise PortalAuthorizationError("Authentication is required.")
    conversation = service.get_conversation_by_id(conversation_id, include_messages=include_messages)
    if not can_access_conversation(user, conversation):
        raise PortalAuthorizationError("You do not have access to this conversation.")
    return conversation
