from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.conversations.models import Conversation
from apps.conversations.portal import (
    ChatPortalService,
    PortalAuthorizationError,
)
from apps.conversations.portal_auth import (
    can_access_conversation,
    get_authorized_conversation,
    resolve_scope_for_user,
)


User = get_user_model()


class PortalOwnershipAndAuthTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="changeme123",
            first_name="Owner",
        )
        self.staff = User.objects.create_user(
            email="staff@example.com",
            password="changeme123",
            first_name="Staff",
            is_staff=True,
        )
        self.outsider = User.objects.create_user(
            email="outsider@example.com",
            password="changeme123",
            first_name="Outsider",
        )
        self.registration = RegistrationSession.objects.create(user=self.owner)
        self.business = BusinessProfile.objects.create(
            user=self.owner,
            registration_session=self.registration,
            name="Acme Support",
            industry="Support",
            status="active",
            metadata={FEATURE_FLAG_METADATA_KEY: {"sub_agents_v1": False}},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.owner,
            name="Sarah",
            slug="sarah",
        )
        self.service = ChatPortalService()

    def test_conversation_defaults_owner_to_business_user(self) -> None:
        conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="owner-default-conversation",
        )

        self.assertEqual(conversation.owner_user_id, self.owner.id)

    def test_create_new_session_uses_actor_user_id_when_valid(self) -> None:
        result = self.service.create_new_session(
            business_slug=self.business.slug,
            agent_slug=self.agent.slug,
            metadata={"actor_user_id": str(self.owner.id)},
        )

        conversation = Conversation.objects.get(id=result.session.conversation_id)
        self.assertEqual(conversation.owner_user_id, self.owner.id)

    def test_create_new_session_falls_back_to_business_owner_for_invalid_actor(self) -> None:
        result = self.service.create_new_session(
            business_slug=self.business.slug,
            agent_slug=self.agent.slug,
            metadata={"actor_user_id": str(uuid.uuid4())},
        )

        conversation = Conversation.objects.get(id=result.session.conversation_id)
        self.assertEqual(conversation.owner_user_id, self.business.user_id)

    def test_resolve_scope_for_user_requires_authorized_user(self) -> None:
        scope = resolve_scope_for_user(
            service=self.service,
            user=self.owner,
            business_slug=self.business.slug,
            agent_slug=self.agent.slug,
        )
        self.assertEqual(scope.business.id, self.business.id)
        self.assertEqual(scope.agent.id, self.agent.id)

        with self.assertRaises(PortalAuthorizationError):
            resolve_scope_for_user(
                service=self.service,
                user=self.outsider,
                business_slug=self.business.slug,
                agent_slug=self.agent.slug,
            )

    def test_get_authorized_conversation_by_id_enforces_owner_or_staff(self) -> None:
        conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            owner_user=self.owner,
            session_token="authorized-conversation",
        )

        resolved = get_authorized_conversation(
            service=self.service,
            user=self.owner,
            conversation_id=conversation.id,
            include_messages=False,
        )
        self.assertEqual(resolved.id, conversation.id)
        self.assertTrue(can_access_conversation(self.staff, conversation))

        with self.assertRaises(PortalAuthorizationError):
            get_authorized_conversation(
                service=self.service,
                user=self.outsider,
                conversation_id=conversation.id,
                include_messages=False,
            )
