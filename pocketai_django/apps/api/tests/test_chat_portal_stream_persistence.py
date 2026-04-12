from __future__ import annotations

import json
import uuid
from unittest import mock
import unittest

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession
from apps.api import chat_portal
from apps.conversations.models import Conversation, ConversationMessage, ConversationSender
from apps.rag.ai_orchestrator import AiOrchestratorPlan, StreamingTurnContext


User = get_user_model()


class ImmediateThread:
    def __init__(self, target, args=(), kwargs=None, daemon=False):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}
        self._alive = False

    def start(self):
        self._alive = True
        self._target(*self._args, **self._kwargs)
        self._alive = False

    def join(self, timeout=None):
        self._alive = False

    def is_alive(self):
        return False


@unittest.skip("Legacy stream_send persistence tests removed; portal turns handle streaming now.")
class ChatPortalStreamingPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.user = User.objects.create_user(email="portal-stream@example.com", password="changeme123", first_name="Portal")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Portal Stream Corp",
            industry="Support",
            metadata={"mcp_orchestrator_enabled": False},
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Portal Agent",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="session-portal-stream",
        )

    def test_stream_send_updates_existing_reserved_message(self) -> None:
        reserved_id = uuid.uuid4()
        ConversationMessage.objects.create(
            id=reserved_id,
            conversation=self.conversation,
            sender=ConversationSender.AI,
            body="placeholder",
            metadata={},
            content_blocks=[],
            sent_at=timezone.now(),
        )

        plan = AiOrchestratorPlan(
            response_text="Final answer",
            citations=tuple(),
            planned_actions=tuple(),
            extractions=tuple(),
            diagnostics={},
            ingestion_warnings=tuple(),
            response_blocks=tuple(),
        )

        class StubOrchestrator:
            def stream_turn(
                self,
                *,
                conversation,
                user_message,
                on_response_text_delta=None,
                on_stream_complete=None,
                **_,
            ):
                if on_response_text_delta:
                    on_response_text_delta(plan.response_text)
                if on_stream_complete:
                    on_stream_complete()
                return StreamingTurnContext(
                    conversation=conversation,
                    response_text=plan.response_text,
                    planned_actions=plan.planned_actions,
                    extractions=plan.extractions,
                    resolved_citations=plan.citations,
                    knowledge_payload=tuple(),
                    knowledge_reads=tuple(),
                    knowledge_status="ok",
                    knowledge_diagnostics={},
                    knowledge_loading=False,
                    placeholder_response=None,
                    prompt_bundle=None,
                    tool_trace=tuple(),
                    cached_snippet_count=0,
                    llm_source="provider",
                    streamed_chunks=(plan.response_text,),
                    response_blocks=plan.response_blocks,
                )

            def finalize_turn(self, *_):
                return plan

            def run_planner_only(self, **_):
                return plan

        class StubDispatcher:
            def __init__(self, *_, **__):
                pass

            def execute(self, **_):
                return []

        request = self.factory.post(
            "/chat/stream/send/",
            data=json.dumps(
                {
                    "session_token": self.conversation.session_token,
                    "body": "hello",
                }
            ),
            content_type="application/json",
        )

        with mock.patch.object(chat_portal, "load_default_provider", return_value=None), \
            mock.patch.object(chat_portal, "AiOrchestratorService", return_value=StubOrchestrator()), \
            mock.patch.object(chat_portal, "ActionDispatcher", StubDispatcher), \
            mock.patch.object(chat_portal.threading, "Thread", ImmediateThread), \
            mock.patch.object(chat_portal, "close_old_connections", lambda: None), \
            mock.patch.object(chat_portal.uuid, "uuid4", return_value=reserved_id):
            response = chat_portal.stream_send(request)
            # Exhaust generator to ensure persistence path runs.
            list(response.streaming_content)

        updated = ConversationMessage.objects.get(id=reserved_id)
        self.assertEqual(updated.sender, ConversationSender.AI)
        self.assertEqual(updated.body, "Final answer")
        self.assertTrue(isinstance(updated.content_blocks, list))
        self.assertTrue(len(updated.content_blocks) > 0)
