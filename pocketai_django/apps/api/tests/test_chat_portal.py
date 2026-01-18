from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings

from apps.api import chat_portal
from apps.conversations.models import ConversationSender
from apps.rag.ai_orchestrator import AiOrchestratorPlan, KnowledgeSnippet, StreamingTurnContext


class StubPortalService:
    def __init__(self) -> None:
        self.session = SimpleNamespace(status="open")
        business = SimpleNamespace(id=uuid.uuid4(), metadata={"mcp_orchestrator_enabled": False})
        agent = SimpleNamespace(id=uuid.uuid4(), business_profile=business, tone="friendly")
        self.conversation = SimpleNamespace(
            id=uuid.uuid4(),
            business_profile=business,
            agent_profile=agent,
            business_profile_id=business.id,
            is_active=True,
            status="open",
            session_token="abc",
        )
        self.messages: list[SimpleNamespace] = []

    def append_message(self, *, session_token: str, sender: ConversationSender, body: str, metadata: dict | None = None, conversation=None, message_id=None):
        message = SimpleNamespace(id=message_id or uuid.uuid4(), sender=sender, body=body, metadata=metadata)
        self.messages.append(message)
        return message

    def get_conversation(self, session_token: str, include_messages: bool = True):
        return self.conversation

    def get_session_state(self, session_token: str, conversation=None):
        return self.session

    def store_extractions(self, session_token: str, items):
        self.extractions = list(items)


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
            def __init__(self, plan, conversation):
                self.plan = plan
                self.conversation = conversation

            def stream_turn(
                self,
                *,
                conversation,
                user_message,
                on_response_text_delta=None,
                on_stream_complete=None,
                on_spinner_update=None,
                **_,
            ):
                if on_response_text_delta:
                    on_response_text_delta(self.plan.response_text)
                if on_stream_complete:
                    on_stream_complete()
                return StreamingTurnContext(
                    conversation=conversation,
                    response_text=self.plan.response_text,
                    planned_actions=self.plan.planned_actions,
                    extractions=self.plan.extractions,
                    resolved_citations=self.plan.citations,
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
                    streamed_chunks=(self.plan.response_text,),
                )

            def finalize_turn(self, *_):
                return self.plan

            def run_planner_only(self, **_):
                return self.plan

        stub_orchestrator = StubOrchestrator(self.plan, self.stub_service.conversation)

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
        events: list[tuple[str | None, str]] = []
        current_event: str | None = None
        for chunk in chunks:
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            if chunk.startswith("event:"):
                current_event = chunk.split("event:", 1)[1].strip()
            elif chunk.startswith("data:"):
                payload = chunk.split("data:", 1)[1].strip()
                events.append((current_event, payload))

        pending_event = next((data for evt, data in events if evt in {"turnPending", "final"}), None)
        persisted_event = next((data for evt, data in events if evt == "turnPersisted"), None)
        self.assertIsNotNone(pending_event)
        self.assertIsNotNone(persisted_event)

        pending_payload = json.loads(pending_event)
        self.assertTrue(pending_payload.get("pending"))
        self.assertEqual(pending_payload.get("text"), self.plan.response_text)

        persisted_payload = json.loads(persisted_event)
        self.assertFalse(persisted_payload.get("pending"))
        self.assertIn("answer_confidence", persisted_payload)
        self.assertIn("ingestion_warnings", persisted_payload)
        self.assertAlmostEqual(persisted_payload["answer_confidence"], 0.62)

    @override_settings(PORTAL_STREAM_STATE_MACHINE=True)
    def test_stream_send_emits_tool_events_redacted(self) -> None:
        payload = {"session_token": "abc", "body": "hello"}
        request = self.factory.post(
            "/stream",
            data=json.dumps(payload),
            content_type="application/json",
        )

        class StubOrchestrator:
            def __init__(self, plan, conversation):
                self.plan = plan
                self.conversation = conversation

            def stream_turn(
                self,
                *,
                conversation,
                user_message,
                on_response_text_delta=None,
                on_tool_event=None,
                on_stream_complete=None,
                **_,
            ):
                if on_tool_event:
                    on_tool_event(
                        {
                            "event_id": "evt_1",
                            "phase": "started",
                            "status": "running",
                            "tool_call_id": "tc_1",
                            "tool_name": "mcp_demo__tool",
                            "kind": "mcp_remote",
                            "remote": {
                                "connection_name": "GitHub MCP",
                                "endpoint_url": "https://should-not-leak.example/mcp",
                                "remote_tool": "search",
                            },
                            "input": {"query": "hello", "token": "super-secret"},
                        }
                    )
                if on_response_text_delta:
                    on_response_text_delta(self.plan.response_text)
                if on_tool_event:
                    on_tool_event(
                        {
                            "event_id": "evt_1",
                            "phase": "finished",
                            "status": "ok",
                            "tool_call_id": "tc_1",
                            "tool_name": "mcp_demo__tool",
                            "kind": "mcp_remote",
                            "remote": {
                                "connection_name": "GitHub MCP",
                                "endpoint_url": "https://should-not-leak.example/mcp",
                                "remote_tool": "search",
                            },
                            "duration_ms": 12,
                            "output": {
                                "status": "ok",
                                "result": "done",
                                "token": "still-secret",
                                "remote": {
                                    "connection_id": "should-not-leak",
                                    "connection_name": "GitHub MCP",
                                    "endpoint_url": "https://should-not-leak.example/mcp",
                                    "tool": "search",
                                },
                            },
                        }
                    )
                if on_stream_complete:
                    on_stream_complete()
                return StreamingTurnContext(
                    conversation=conversation,
                    response_text=self.plan.response_text,
                    planned_actions=self.plan.planned_actions,
                    extractions=self.plan.extractions,
                    resolved_citations=self.plan.citations,
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
                    streamed_chunks=(self.plan.response_text,),
                )

            def finalize_turn(self, *_):
                return self.plan

            def run_planner_only(self, **_):
                return self.plan

        stub_orchestrator = StubOrchestrator(self.plan, self.stub_service.conversation)

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
        events: list[tuple[str | None, str]] = []
        current_event: str | None = None
        for chunk in chunks:
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            if chunk.startswith("event:"):
                current_event = chunk.split("event:", 1)[1].strip()
            elif chunk.startswith("data:"):
                payload_raw = chunk.split("data:", 1)[1].strip()
                events.append((current_event, payload_raw))

        tool_events = [json.loads(data) for evt, data in events if evt == "toolEvent"]
        self.assertGreaterEqual(len(tool_events), 2)

        started = next((e for e in tool_events if e.get("phase") == "started"), None)
        self.assertIsNotNone(started)
        self.assertEqual(started["remote"]["connection_name"], "GitHub MCP")
        self.assertEqual(started["remote"]["remote_tool"], "search")
        self.assertNotIn("endpoint_url", started["remote"])
        self.assertEqual(started["input"]["token"], "[REDACTED]")

        finished = next((e for e in tool_events if e.get("phase") == "finished"), None)
        self.assertIsNotNone(finished)
        self.assertEqual(finished["output"]["token"], "[REDACTED]")
        self.assertIn("remote", finished["output"])
        self.assertNotIn("endpoint_url", finished["output"]["remote"])
        self.assertNotIn("connection_id", finished["output"]["remote"])
        self.assertEqual(finished["output"]["remote"]["connection_name"], "GitHub MCP")
        self.assertEqual(finished["output"]["remote"]["remote_tool"], "search")

    @override_settings(PORTAL_STREAM_STATE_MACHINE=True)
    def test_stream_send_emits_turn_pending_with_state_machine_enabled(self) -> None:
        payload = {"session_token": "abc", "body": "hello"}
        request = self.factory.post(
            "/stream",
            data=json.dumps(payload),
            content_type="application/json",
        )

        class StubOrchestrator:
            def __init__(self, plan, conversation):
                self.plan = plan
                self.conversation = conversation

            def stream_turn(
                self,
                *,
                conversation,
                user_message,
                on_response_text_delta=None,
                on_stream_complete=None,
                on_spinner_update=None,
                **_,
            ):
                if on_response_text_delta:
                    on_response_text_delta(self.plan.response_text)
                if on_spinner_update:
                    on_spinner_update("Reading data")
                if on_stream_complete:
                    on_stream_complete()
                return StreamingTurnContext(
                    conversation=conversation,
                    response_text=self.plan.response_text,
                    planned_actions=self.plan.planned_actions,
                    extractions=self.plan.extractions,
                    resolved_citations=self.plan.citations,
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
                    streamed_chunks=(self.plan.response_text,),
                )

            def finalize_turn(self, *_):
                return self.plan

            def run_planner_only(self, **_):
                return self.plan

        stub_orchestrator = StubOrchestrator(self.plan, self.stub_service.conversation)

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
        events: list[tuple[str | None, str]] = []
        current_event: str | None = None
        for chunk in chunks:
            if isinstance(chunk, bytes):
                chunk = chunk.decode("utf-8")
            if chunk.startswith("event:"):
                current_event = chunk.split("event:", 1)[1].strip()
            elif chunk.startswith("data:"):
                payload = chunk.split("data:", 1)[1].strip()
                events.append((current_event, payload))

        turn_pending = next((data for evt, data in events if evt == "turnPending"), None)
        self.assertIsNotNone(turn_pending)
        persisted_event = next((data for evt, data in events if evt == "turnPersisted"), None)
        self.assertIsNotNone(persisted_event)
