from __future__ import annotations

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.mcp.orchestrator import McpOrchestratorService


class _EnumerationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages,
        *,
        tools=None,
        on_stream_delta=None,
        on_reasoning_delta=None,
        on_tool_call_start=None,
        on_tool_call_delta=None,
        response_format=None,
        should_cancel=None,
    ):
        self.calls += 1
        if self.calls == 1:
            tool_call = {
                "id": "call_search_1",
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "arguments": json.dumps({"query": "list all credit cards"}, ensure_ascii=False),
                },
            }
            if on_tool_call_start:
                on_tool_call_start(tool_call)
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]}}
        return {"message": {"role": "assistant", "content": "Here are the available credit cards."}}


class McpEnumerationAutoStructureTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="enum-auto@example.com", first_name="Enum")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Enumeration Bank",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Enumerator",
            role="AI Specialist",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="enum-auto-session",
        )

    @override_settings(MCP_ENUMERATION_AUTO_STRUCTURE_ENABLED=True, MCP_ENUMERATION_MAX_DOCUMENTS=3)
    @patch("apps.mcp.orchestrator.tools.execute_tool")
    def test_auto_injects_document_structure_for_enumeration(self, execute_tool_mock) -> None:
        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            self.assertIsNotNone(context)
            if name == "search_knowledge":
                context.reserve_search()
                return {
                    "tool": "search_knowledge",
                    "status": "ok",
                    "query": "list all credit cards",
                    "snippets": [
                        {
                            "id": "snippet-1",
                            "title": "Credit Cards",
                            "public_label": "Credit Cards",
                            "read_state": "summary",
                            "chunk_id": "chunk-1",
                            "upload_id": "upload-1",
                            "is_table_chunk": True,
                        }
                    ],
                }
            if name == "get_document_structure":
                self.assertEqual(arguments.get("document_id"), "upload-1")
                return {
                    "tool": "get_document_structure",
                    "status": "ok",
                    "document": {"document_id": "upload-1", "display_name": "Credit Cards"},
                    "tables": [],
                    "total_tables": 0,
                    "total_items": 0,
                }
            raise AssertionError(f"Unexpected tool call: {name}")

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _EnumerationProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="List all credit cards",
        )

        self.assertGreaterEqual(provider.calls, 2)
        calls = [call.args[0] for call in execute_tool_mock.call_args_list]
        self.assertIn("search_knowledge", calls)
        self.assertIn("get_document_structure", calls)

        tool_trace = list(context.tool_trace)
        structure_traces = [entry for entry in tool_trace if entry.get("tool") == "get_document_structure"]
        self.assertEqual(len(structure_traces), 1)
        self.assertEqual(structure_traces[0].get("origin"), "auto")

    @override_settings(
        MCP_ENUMERATION_AUTO_STRUCTURE_ENABLED=True,
        MCP_ENUMERATION_MAX_DOCUMENTS=3,
        MCP_ENUMERATION_AUTO_FETCH_ENABLED=True,
        MCP_ENUMERATION_AUTO_FETCH_MAX_ROWS=200,
        MCP_ENUMERATION_AUTO_FETCH_MAX_TABLES=3,
    )
    @patch("apps.mcp.orchestrator.tools.execute_tool")
    def test_auto_fetches_table_rows_for_numeric_attributes(self, execute_tool_mock) -> None:
        calls: list[tuple[str, dict]] = []

        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            self.assertIsNotNone(context)
            calls.append((name, dict(arguments)))
            if name == "search_knowledge":
                context.reserve_search()
                return {
                    "tool": "search_knowledge",
                    "status": "ok",
                    "query": "list all credit cards and their issuance fees",
                    "snippets": [
                        {
                            "id": "snippet-1",
                            "title": "Credit Cards",
                            "public_label": "Credit Cards",
                            "read_state": "summary",
                            "chunk_id": "chunk-1",
                            "upload_id": "upload-1",
                            "is_table_chunk": True,
                        }
                    ],
                }
            if name == "get_document_structure":
                return {
                    "tool": "get_document_structure",
                    "status": "ok",
                    "document": {"document_id": "upload-1", "display_name": "Credit Cards"},
                    "tables": [
                        {
                            "table_id": "table-1",
                            "title": "Cards",
                            "order_index": 1,
                            "columns": ["Card Name", "Issuance Fee", "Annual Fee"],
                            "row_count": 5,
                        }
                    ],
                    "total_tables": 1,
                    "total_items": 5,
                }
            if name == "table_aggregate":
                return {
                    "tool": "table_aggregate",
                    "status": "ok",
                    "document_id": "upload-1",
                    "rows": [
                        {
                            "row_index": 1,
                            "table_order_index": 1,
                            "cells": [
                                {"column": "Card Name", "value": "Gold"},
                                {"column": "Issuance Fee", "value": "100"},
                            ],
                        }
                    ],
                }
            raise AssertionError(f"Unexpected tool call: {name}")

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _EnumerationProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="List all credit cards and their issuance fees",
        )

        tool_names = [entry[0] for entry in calls]
        self.assertIn("table_aggregate", tool_names)
        table_call = next(args for name, args in calls if name == "table_aggregate")
        self.assertEqual(table_call.get("document_id"), "upload-1")
        self.assertEqual(table_call.get("table_order_index"), 1)
        self.assertEqual(table_call.get("max_rows"), 5)
        columns = table_call.get("columns")
        self.assertTrue(isinstance(columns, list))
        self.assertIn("Card Name", columns)
        self.assertIn("Issuance Fee", columns)

        tool_trace = list(context.tool_trace)
        auto_fetch_traces = [entry for entry in tool_trace if entry.get("tool") == "table_aggregate"]
        self.assertTrue(any(entry.get("origin") == "auto" for entry in auto_fetch_traces))
