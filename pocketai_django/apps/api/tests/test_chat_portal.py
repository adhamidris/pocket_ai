from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest import mock
import unittest

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

    def append_message(
        self,
        *,
        session_token: str,
        sender: ConversationSender,
        body: str,
        metadata: dict | None = None,
        content_blocks=None,
        conversation=None,
        message_id=None,
    ):
        message = SimpleNamespace(
            id=message_id or uuid.uuid4(),
            sender=sender,
            body=body,
            metadata=metadata,
            content_blocks=content_blocks or [],
        )
        self.messages.append(message)
        return message

    def get_conversation(self, session_token: str, include_messages: bool = True):
        return self.conversation

    def get_session_state(self, session_token: str, conversation=None):
        return self.session

    def store_extractions(self, session_token: str, items):
        self.extractions = list(items)

    def update_message(self, *, session_token: str, message_id: uuid.UUID, body=None, metadata=None, content_blocks=None, conversation=None):
        for message in self.messages:
            if message.id == message_id:
                if body is not None:
                    message.body = body
                if metadata is not None:
                    message.metadata = metadata
                if content_blocks is not None:
                    message.content_blocks = content_blocks
                return message
        raise AssertionError("Message not found")


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


@unittest.skip("Legacy stream_send flow removed; portal turns handle streaming now.")
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

        first_text_delta = next((data for evt, data in events if evt == "block_delta"), None)
        persisted_event = next((data for evt, data in events if evt == "turnPersisted"), None)
        self.assertIsNotNone(first_text_delta)
        self.assertIsNotNone(persisted_event)

        delta_payload = json.loads(first_text_delta)
        ops = delta_payload.get("ops") if isinstance(delta_payload, dict) else None
        streamed_text_parts: list[str] = []
        if isinstance(ops, list):
            for op in ops:
                if not isinstance(op, dict):
                    continue
                op_type = op.get("op")
                if op_type == "append_code":
                    text = op.get("text")
                    if isinstance(text, str) and text:
                        streamed_text_parts.append(text)
                    continue
                if op_type != "append_inline":
                    continue
                nodes = op.get("nodes") or op.get("node") or []
                if isinstance(nodes, dict):
                    nodes = [nodes]
                if not isinstance(nodes, list):
                    continue
                for node in nodes:
                    if isinstance(node, dict):
                        text = node.get("text")
                        if isinstance(text, str) and text:
                            streamed_text_parts.append(text)
        self.assertEqual("".join(streamed_text_parts), self.plan.response_text)

        persisted_payload = json.loads(persisted_event)
        self.assertFalse(persisted_payload.get("pending"))
        self.assertIn("answer_confidence", persisted_payload)
        self.assertIn("ingestion_warnings", persisted_payload)
        self.assertAlmostEqual(persisted_payload["answer_confidence"], 0.62)
        self.assertIn("content_blocks", persisted_payload)

    def test_stream_send_persists_structured_response_blocks_as_content_blocks(self) -> None:
        payload = {"session_token": "abc", "body": "hello"}
        request = self.factory.post(
            "/stream",
            data=json.dumps(payload),
            content_type="application/json",
        )

        table_block = {
            "type": "table",
            "title": "Users",
            "columns": [
                {"key": "name", "label": "Name"},
                {"key": "role", "label": "Role"},
            ],
            "rows": [
                {"cells": ["Alice", "Admin"]},
                {"cells": ["Bob", "User"]},
            ],
            "note": "Example table",
        }
        kv_block = {
            "type": "kv",
            "title": "Summary",
            "entries": [
                {"key": "Total", "value": "2"},
                {"key": "Active", "value": "2"},
            ],
            "note": "Example kv",
        }

        plan = AiOrchestratorPlan(
            response_text=self.plan.response_text,
            citations=self.plan.citations,
            planned_actions=self.plan.planned_actions,
            extractions=self.plan.extractions,
            diagnostics=self.plan.diagnostics,
            ingestion_warnings=self.plan.ingestion_warnings,
            response_blocks=(table_block, kv_block),
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

        stub_orchestrator = StubOrchestrator(plan, self.stub_service.conversation)

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

        persisted_event = next((data for evt, data in events if evt == "turnPersisted"), None)
        self.assertIsNotNone(persisted_event)
        persisted_payload = json.loads(persisted_event)
        content_blocks = persisted_payload.get("content_blocks")
        self.assertIsInstance(content_blocks, list)

        persisted_table = next((block for block in content_blocks if isinstance(block, dict) and block.get("type") == "table"), None)
        self.assertIsNotNone(persisted_table)
        self.assertEqual(persisted_table["payload"]["title"], "Users")
        self.assertEqual(persisted_table["payload"]["columns"][0]["label"], "Name")
        self.assertEqual(persisted_table["payload"]["rows"][0]["cells"][0], "Alice")

        persisted_kv = next((block for block in content_blocks if isinstance(block, dict) and block.get("type") == "kv"), None)
        self.assertIsNotNone(persisted_kv)
        self.assertEqual(persisted_kv["payload"]["title"], "Summary")
        self.assertEqual(persisted_kv["payload"]["entries"][0]["key"], "Total")
        self.assertEqual(persisted_kv["payload"]["entries"][0]["value"], "2")

        started_blocks = [json.loads(data).get("block") for evt, data in events if evt == "block_start"]
        started_types = [b.get("type") for b in started_blocks if isinstance(b, dict)]
        self.assertIn("table", started_types)
        self.assertIn("kv", started_types)

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

        tool_use_events = [json.loads(data) for evt, data in events if evt == "block_tool_use"]
        tool_result_events = [json.loads(data) for evt, data in events if evt == "block_tool_result"]
        self.assertGreaterEqual(len(tool_use_events), 1)
        self.assertGreaterEqual(len(tool_result_events), 1)

        started = next(
            (
                e.get("block", {}).get("payload")
                for e in tool_use_events
                if isinstance(e.get("block"), dict) and isinstance(e.get("block", {}).get("payload"), dict)
                and e.get("block", {}).get("payload", {}).get("phase") == "started"
            ),
            None,
        )
        self.assertIsNotNone(started)
        self.assertEqual(started["remote"]["connection_name"], "GitHub MCP")
        self.assertEqual(started["remote"]["remote_tool"], "search")
        self.assertNotIn("endpoint_url", started["remote"])
        self.assertEqual(started["input"]["token"], "[REDACTED]")

        finished = next(
            (
                e.get("block", {}).get("payload")
                for e in tool_result_events
                if isinstance(e.get("block"), dict) and isinstance(e.get("block", {}).get("payload"), dict)
                and e.get("block", {}).get("payload", {}).get("event_id") == "evt_1"
            ),
            None,
        )
        self.assertIsNotNone(finished)
        output_preview = finished.get("output_preview") if isinstance(finished, dict) else None
        self.assertIsInstance(output_preview, dict)
        self.assertEqual(output_preview["token"], "[REDACTED]")
        self.assertIn("remote", output_preview)
        self.assertNotIn("endpoint_url", output_preview["remote"])
        self.assertNotIn("connection_id", output_preview["remote"])
        self.assertEqual(output_preview["remote"]["connection_name"], "GitHub MCP")
        self.assertEqual(output_preview["remote"]["remote_tool"], "search")

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

        spinner_event = next((data for evt, data in events if evt == "spinnerStatus"), None)
        self.assertIsNotNone(spinner_event)
        persisted_event = next((data for evt, data in events if evt == "turnPersisted"), None)
        self.assertIsNotNone(persisted_event)


class ChatPortalDebugSerializationTests(TestCase):
    def test_serialize_tool_trace_entry_includes_exact_llm_io(self) -> None:
        entry = {
            "tool": "search_knowledge",
            "status": "ok",
            "arguments": {"query": "fees", "limit": 5},
            "llm_request": {
                "tool": "search_knowledge",
                "arguments": {"query": "fees", "limit": 5, "queries": ["fees egp", "fees usd"]},
            },
            "llm_response": {
                "tool_call_id": "call_1",
                "content": '{"tool":"search_knowledge","status":"ok","refs":[{"id":"abc"}]}',
            },
        }

        payload = chat_portal._serialize_tool_trace_entry(entry)
        self.assertEqual(payload.get("tool"), "search_knowledge")
        self.assertIn("llm_request", payload)
        self.assertIn("llm_response", payload)
        self.assertEqual(payload["llm_request"]["arguments"]["query"], "fees")
        self.assertEqual(payload["llm_request"]["arguments"]["queries"], ["fees egp", "fees usd"])
        self.assertEqual(payload["llm_response"]["content"], entry["llm_response"]["content"])
        self.assertEqual(payload["llm_response"]["content_json"]["status"], "ok")
        self.assertEqual(payload["llm_response"]["content_json"]["refs"][0]["id"], "abc")

    def test_serialize_debug_tools_payload_keeps_llm_io_in_tool_trace(self) -> None:
        stream_context = SimpleNamespace(
            llm_usage=None,
            tool_trace=(),
            knowledge_payload=(),
            knowledge_reads=(),
            tool_context=SimpleNamespace(
                tool_trace=[
                    {
                        "tool": "read_knowledge",
                        "status": "truncated",
                        "arguments": {"refs": [{"id": "u1"}], "max_chars": 2000},
                        "llm_request": {"tool": "read_knowledge", "arguments": {"refs": [{"id": "u1"}]}},
                        "llm_response": {
                            "tool_call_id": "call_9",
                            "content": '{"tool":"read_knowledge","status":"truncated","evidence":[{"id":"u1"}]}',
                        },
                    }
                ],
                knowledge_results=[],
                knowledge_reads=[],
                search_history=[],
                coverage_ledger=[],
                table_aggregate_rows=[],
                prompt_budget_entries=[],
            ),
        )

        payload = chat_portal._serialize_debug_tools_payload(stream_context)
        self.assertIsInstance(payload, dict)
        self.assertIn("tool_trace", payload)
        self.assertEqual(len(payload["tool_trace"]), 1)
        trace = payload["tool_trace"][0]
        self.assertEqual(trace["tool"], "read_knowledge")
        self.assertEqual(trace["llm_request"]["tool"], "read_knowledge")
        self.assertEqual(trace["llm_response"]["tool_call_id"], "call_9")
        self.assertEqual(trace["llm_response"]["content_json"]["status"], "truncated")
