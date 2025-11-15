from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, TestCase

from apps.api import chat_portal
from apps.conversations.models import ConversationSender
from apps.services.ai_orchestrator import AiOrchestratorPlan, KnowledgeSnippet


class StubPortalService:
    def __init__(self) -> None:
        self.session = SimpleNamespace(status="open")
        self.conversation = SimpleNamespace(
            id=uuid.uuid4(),
            agent_profile=SimpleNamespace(id=uuid.uuid4(), business_profile=SimpleNamespace(id=uuid.uuid4(), metadata={})),
        )
        self.messages: list[SimpleNamespace] = []

    def append_message(self, *, session_token: str, sender: ConversationSender, body: str, metadata: dict | None = None):
        message = SimpleNamespace(id=uuid.uuid4(), sender=sender, body=body, metadata=metadata)
        self.messages.append(message)
        return message

    def get_conversation(self, session_token: str):
        return self.conversation

    def get_session_state(self, session_token: str):
        return self.session

    def store_extractions(self, session_token: str, items):
        self.extractions = list(items)


class ImmediateThread:
    def __init__(self, target, daemon=False):
        self._target = target
        self._alive = False

    def start(self):
        self._alive = True
        self._target()
        self._alive = False

    def join(self, timeout=None):
        self._alive = False

    def is_alive(self):
        return False


class ChatPortalStreamingTests(TestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()
        self.stub_service = StubPortalService()
        snippet = KnowledgeSnippet(
            id=uuid.uuid4(),
            title="Sheet",
            summary="Summary",
            source="sheet",
            content="Details",
            public_label="Sheet",
            structured_tables=tuple(),
            issues=tuple(),
            page_summaries=tuple(),
            read_state="summary",
            topic_hints=tuple(),
            is_pinned=False,
            upload_id=uuid.uuid4(),
            chunk_id=None,
            chunk_index=None,
            entity_type=None,
            entity_name=None,
            entity_business=None,
            is_table_chunk=False,
            aliases=tuple(),
            search_stage="hybrid",
            confidence_score=0.9,
            truncated=False,
            source_diagnostics={},
            partial_index=False,
            structured_table_count=0,
            issue_count=0,
            structured_table_hint=None,
        )
        ingestion_warning = {
            "upload_id": "u1",
            "label": "Sheet",
            "type": "table_rows_truncated",
            "severity": "warning",
            "details": "Partial",
        }
        self.plan = AiOrchestratorPlan(
            response_text="Final answer",
            citations=(snippet,),
            planned_actions=tuple(),
            extractions=tuple(),
            diagnostics={"answer_confidence": 0.62},
            ingestion_warnings=(ingestion_warning,),
        )

    def test_stream_send_includes_confidence_and_ingestion_warnings(self) -> None:
        payload = {"session_token": "abc", "body": "hello"}
        request = self.factory.post(
            "/stream",
            data=json.dumps(payload),
            content_type="application/json",
        )

        class StubOrchestrator:
            def __init__(self, *_, **__):
                self.plan = None

            def run_turn(self, **_):
                return self.plan

        stub_orchestrator = StubOrchestrator()
        stub_orchestrator.plan = self.plan

        class StubDispatcher:
            def __init__(self, *_, **__):
                pass

            def execute(self, **_):
                return []

        with mock.patch.object(chat_portal, "_service", return_value=self.stub_service), \
            mock.patch.object(chat_portal, "AiOrchestratorService", return_value=stub_orchestrator), \
            mock.patch.object(chat_portal, "ActionDispatcher", StubDispatcher), \
            mock.patch.object(chat_portal, "load_default_provider", return_value=None), \
            mock.patch.object(chat_portal.threading, "Thread", ImmediateThread):
            response = chat_portal.stream_send(request)

        chunks = list(response.streaming_content)
        final_payload = None
        for idx, chunk in enumerate(chunks):
            if chunk.startswith("event: final"):
                final_payload = json.loads(chunks[idx + 1].split("data: ", 1)[1])
                break
        self.assertIsNotNone(final_payload)
        self.assertIn("answer_confidence", final_payload)
        self.assertIn("ingestion_warnings", final_payload)
        self.assertAlmostEqual(final_payload["answer_confidence"], 0.62)
