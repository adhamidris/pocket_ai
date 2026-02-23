from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.accounts.models import AgentProfile, BusinessProfile, RegistrationSession, User
from apps.conversations.models import Conversation
from apps.llm.llm_provider import PromptGenerationError
from apps.mcp.orchestrator import McpOrchestratorService
from apps.mcp import tools as mcp_tools


class _SearchTwiceProvider:
    """
    Fake provider that attempts to call search_knowledge twice in the same user turn.

    The MCP orchestrator should execute the first search and block the second
    search call based on the per-turn search budget.
    """

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

        if self.calls in {1, 2}:
            tool_call = {
                "id": f"call_search_{self.calls}",
                "type": "function",
                "function": {
                    "name": "search_knowledge",
                    "arguments": json.dumps(
                        {"queries": ["credit card fees", "credit card fees charges costs"]},
                        ensure_ascii=False,
                    ),
                },
            }
            if on_tool_call_start:
                on_tool_call_start(tool_call)
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]}}

        content = "Annual fee example: 100 EGP. Tell me your card type if you want exact fees."
        return {"message": {"role": "assistant", "content": content}}


class _SingleAnswerProvider:
    def __init__(self, content: str = "Cheque fees are available in the selected category.") -> None:
        self.calls = 0
        self.content = content

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
        del messages, tools, on_stream_delta, on_reasoning_delta, on_tool_call_start, on_tool_call_delta, response_format, should_cancel
        self.calls += 1
        return {"message": {"role": "assistant", "content": self.content}}


class _StrictToolEnvelopeProvider:
    """
    Provider that mimics DeepSeek's strict request validation.

    It raises when any message content is not a string or when an assistant
    tool_call carries non-string function.arguments.
    """

    def __init__(self, content: str = "Cheque fees in Plus are now loaded.") -> None:
        self.calls = 0
        self.content = content
        self.requests: list[list[dict[str, object]]] = []

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
        del tools, on_stream_delta, on_reasoning_delta, on_tool_call_start, on_tool_call_delta, response_format, should_cancel
        self.calls += 1
        materialized = [dict(message) for message in messages]
        self.requests.append(materialized)

        for idx, message in enumerate(materialized):
            content = message.get("content")
            if not isinstance(content, str):
                raise PromptGenerationError(
                    "DeepSeek tools error (400): "
                    + json.dumps(
                        {
                            "error": {
                                "message": (
                                    "Failed to deserialize the JSON body into the target type: "
                                    f"messages[{idx}]: invalid type: map, expected a string"
                                )
                            }
                        }
                    )
                )

            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    raise PromptGenerationError(
                        "DeepSeek tools error (400): "
                        + json.dumps(
                            {
                                "error": {
                                    "message": (
                                        "Failed to deserialize the JSON body into the target type: "
                                        f"messages[{idx}]: invalid type: map, expected a string"
                                    )
                                }
                            }
                        )
                    )

        return {"message": {"role": "assistant", "content": self.content}}


class _PresentScopeProvider:
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
        del messages, tools, on_stream_delta, on_reasoning_delta, on_tool_call_delta, response_format, should_cancel
        self.calls += 1
        if self.calls == 1:
            tool_call = {
                "id": "call_present_scope",
                "type": "function",
                "function": {
                    "name": "present_scope_clarification",
                    "arguments": json.dumps({}, ensure_ascii=False),
                },
            }
            if on_tool_call_start:
                on_tool_call_start(tool_call)
            return {"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]}}
        return {"message": {"role": "assistant", "content": "Please choose one category."}}


class McpSearchContractTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create(email="mcp-search-contract@example.com", first_name="MCP")
        self.registration = RegistrationSession.objects.create(user=self.user)
        self.business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration,
            name="Search Contract Bank",
            industry="banking",
        )
        self.agent = AgentProfile.objects.create(
            business_profile=self.business,
            user=self.user,
            name="Searcher",
            role="AI Specialist",
        )
        self.conversation = Conversation.objects.create(
            business_profile=self.business,
            agent_profile=self.agent,
            session_token="search-contract-session",
        )

    @override_settings(MCP_MAX_SEARCHES_PER_TURN=1)
    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_search_knowledge_runs_once_per_turn(self, execute_tool_mock) -> None:
        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            self.assertEqual(name, "search_knowledge")
            self.assertIsNotNone(context)
            context.reserve_search()
            return {
                "tool": "search_knowledge",
                "status": "ok",
                "query": str((arguments.get("queries") or [""])[0]),
                "snippets": [
                    {
                        "id": "snippet-1",
                        "title": "Fees",
                        "public_label": "Fees",
                        "content": "Annual fee example: 100 EGP",
                        "read_state": "summary",
                        "read_required": False,
                        "search_stage": "hybrid",
                        "chunk_id": "chunk-1",
                        "upload_id": "upload-1",
                        "is_table_chunk": False,
                    }
                ],
            }

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _SearchTwiceProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="Tell me more about credit card fees",
        )

        self.assertEqual(provider.calls, 3)
        self.assertIsNotNone(context.tool_context)
        self.assertEqual(context.tool_context.searches_used, 1)

        execute_tool_mock.assert_called_once()

        search_traces = [t for t in context.tool_trace if t.get("tool") == "search_knowledge"]
        self.assertEqual(len(search_traces), 2)
        self.assertEqual(sum(1 for t in search_traces if t.get("origin") == "live"), 1)
        self.assertEqual(sum(1 for t in search_traces if t.get("origin") == "policy"), 1)

        self.assertNotIn("search limit", context.response_text.lower())
        self.assertNotIn("budget", context.response_text.lower())

    @override_settings(MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=True)
    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_scope_selection_turn_uses_agentic_handoff(self, execute_tool_mock) -> None:
        """
        Verify the agentic scope handoff: when the user clicks an MCQ category,
        the scope context is injected into the transcript so the LLM can decide
        which tools to call. The LLM should see the mapped refs and choose
        read_knowledge. present_scope_clarification must NOT be available.
        """
        selected_category = "cheques"
        selected_key = mcp_tools._scope_category_key(selected_category)
        mapped_ref_id = str(uuid.uuid4())
        self.conversation.metadata = {
            "mcp_scope_clarification": {
                "pending": {
                    "base_query": "plus fees",
                    "question": "Pick one category.",
                    "categories": [selected_category, "cash withdrawal fees"],
                    "category_refs": {
                        selected_key: {
                            "ref_ids": [mapped_ref_id],
                            "source": "retrieval_candidates",
                            "confidence": 0.95,
                        }
                    },
                }
            }
        }
        self.conversation.save(update_fields=["metadata"])

        called_tools: list[str] = []

        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            called_tools.append(str(name))
            self.assertIsNotNone(context)
            if name == "read_knowledge":
                return {
                    "tool": "read_knowledge",
                    "status": "ok",
                    "evidence": [
                        {
                            "id": mapped_ref_id,
                            "document_id": str(uuid.uuid4()),
                            "title": "Cheques-EN.pdf",
                            "kind": "text_excerpt",
                            "chars": 240,
                        }
                    ],
                    "hint": "Selection evidence loaded.",
                }
            return {
                "tool": str(name),
                "status": "ok",
            }

        execute_tool_mock.side_effect = _fake_execute_tool

        # Provider that simulates the LLM reading the scope context and choosing
        # to call read_knowledge with the mapped refs.
        class _ScopeAwareProvider:
            def __init__(self_inner, ref_id: str) -> None:
                self_inner.calls = 0
                self_inner.ref_id = ref_id
                self_inner.tool_schemas: list[str] = []

            def chat(
                self_inner,
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
                del on_reasoning_delta, on_tool_call_start, on_tool_call_delta, response_format, should_cancel
                self_inner.calls += 1
                # Record what tools were offered to the LLM
                if tools:
                    for tool_def in tools:
                        func = tool_def.get("function") if isinstance(tool_def, dict) else None
                        if isinstance(func, dict):
                            self_inner.tool_schemas.append(str(func.get("name") or ""))
                if self_inner.calls == 1:
                    # First call: LLM sees scope context and decides to read the mapped refs
                    return {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_read_scope_ref",
                                    "type": "function",
                                    "function": {
                                        "name": "read_knowledge",
                                        "arguments": json.dumps({
                                            "refs": [{"id": self_inner.ref_id}],
                                            "max_chars": 4000,
                                        }),
                                    },
                                }
                            ],
                        }
                    }
                # Second call: after reading, generate the final answer
                if on_stream_delta:
                    on_stream_delta("Cheque fees in Plus are now loaded.")
                return {"message": {"role": "assistant", "content": "Cheque fees in Plus are now loaded."}}

        provider = _ScopeAwareProvider(mapped_ref_id)
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="cheques",
            user_metadata={
                "scope_selection": {
                    "action": "select_category",
                    "category_key": selected_key,
                    "category_label": selected_category,
                }
            },
        )

        # The LLM chose read_knowledge (agentic decision, not forced)
        self.assertTrue(called_tools)
        self.assertEqual(called_tools[0], "read_knowledge")
        # present_scope_clarification must NOT have been offered (scope lock)
        self.assertNotIn("present_scope_clarification", provider.tool_schemas)
        self.assertNotIn("present_scope_clarification", called_tools)
        self.assertIn("cheque fees", context.response_text.lower())

    @override_settings(MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=True)
    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_scope_selection_agentic_handoff_injects_context_for_strict_provider(self, execute_tool_mock) -> None:
        """
        Verify that the agentic scope handoff injects a scope context system
        message into the transcript and that a strict envelope provider
        (DeepSeek-style, all message.content must be strings) can process
        the transcript without serialization errors.
        """
        selected_category = "cheques"
        selected_key = mcp_tools._scope_category_key(selected_category)
        mapped_ref_id = str(uuid.uuid4())
        self.conversation.metadata = {
            "mcp_scope_clarification": {
                "pending": {
                    "base_query": "plus fees",
                    "question": "Pick one category.",
                    "categories": [selected_category, "cash withdrawal fees"],
                    "category_refs": {
                        selected_key: {
                            "ref_ids": [mapped_ref_id],
                            "source": "retrieval_candidates",
                            "confidence": 0.95,
                        }
                    },
                }
            }
        }
        self.conversation.save(update_fields=["metadata"])

        execute_tool_mock.side_effect = lambda name, arguments, *, conversation, context=None: {
            "tool": str(name), "status": "ok",
        }

        provider = _StrictToolEnvelopeProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        context = orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="cheques",
            user_metadata={
                "scope_selection": {
                    "action": "select_category",
                    "category_key": selected_key,
                    "category_label": selected_category,
                }
            },
        )

        # The strict provider did NOT raise — all message.content fields were strings.
        self.assertEqual(provider.calls, 1)
        self.assertTrue(provider.requests)

        # The scope context system message should be present in the LLM request.
        # It's a separate system message injected alongside the main system prompt.
        first_request = provider.requests[0]
        scope_context_found = False
        for message in first_request:
            if not isinstance(message, dict):
                continue
            if message.get("role") != "system":
                continue
            content = str(message.get("content") or "")
            # Match on the injected context (starts with [Scope Selection Context]),
            # not the prompt instruction that references it.
            if content.startswith("[Scope Selection Context]"):
                scope_context_found = True
                # Verify it contains the category and ref ID
                self.assertIn("cheques", content.lower())
                self.assertIn(mapped_ref_id, content)
                break
        self.assertTrue(scope_context_found, "Scope handoff context message not found in LLM request")

    @override_settings(MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=False)
    @patch("apps.mcp.orchestrator.mcp_tools.execute_tool")
    def test_scope_selection_does_not_force_deterministic_commit_when_mcq_disabled(self, execute_tool_mock) -> None:
        self.conversation.metadata = {
            "mcp_scope_clarification": {
                "pending": {
                    "base_query": "plus fees",
                    "question": "Pick one category.",
                    "categories": ["cheques", "cash withdrawal fees"],
                }
            }
        }
        self.conversation.save(update_fields=["metadata"])

        called_tools: list[str] = []

        def _fake_execute_tool(name, arguments, *, conversation, context=None):
            called_tools.append(str(name))
            if name == "present_scope_clarification":
                return {
                    "tool": "present_scope_clarification",
                    "status": "ok",
                    "clarification_ui_mode": "text",
                    "hint": "Pick one category.",
                }
            if name == "search_knowledge":
                context.reserve_search()
                return {
                    "tool": "search_knowledge",
                    "status": "ok",
                    "snippets": [],
                }
            return {"tool": str(name), "status": "ok"}

        execute_tool_mock.side_effect = _fake_execute_tool

        provider = _PresentScopeProvider()
        orchestrator = McpOrchestratorService(agent=self.agent, provider=provider)
        orchestrator.stream_turn(
            conversation=self.conversation,
            user_message="cheques",
            user_metadata={
                "scope_selection": {
                    "action": "select_category",
                    "category_key": mcp_tools._scope_category_key("cheques"),
                    "category_label": "cheques",
                }
            },
        )

        self.assertTrue(called_tools)
        self.assertEqual(called_tools[0], "present_scope_clarification")
