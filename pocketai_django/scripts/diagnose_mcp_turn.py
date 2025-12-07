"""
Ad-hoc diagnostics script for MCP read/planner guards.

Run:
    python manage.py shell < scripts/diagnose_mcp_turn.py

It spins up a fresh portal session for aug-pharma/nancy, submits an identifier
question, and prints the tool context with clear separators so you can inspect
read-enforcement blockers and the tool trace.
"""

from __future__ import annotations

import json
from typing import Any

from apps.api.chat_portal import _business_prefers_mcp
from apps.conversations.models import ConversationSender
from apps.services.chat_portal import ChatPortalService
from apps.services.llm_provider import load_mcp_provider
from apps.services.mcp import McpOrchestratorService

SEPARATOR = "\n" + "=" * 80 + "\n"


def print_section(title: str, payload: Any, *, limit: int | None = None) -> None:
    print(SEPARATOR)
    print(title.upper())
    print(SEPARATOR)
    if isinstance(payload, (list, tuple)) and limit is not None:
        subset = list(payload)[:limit]
        print(json.dumps(subset, indent=2, ensure_ascii=False, default=str))
        remaining = len(payload) - len(subset)
        if remaining > 0:
            print(f"... (+{remaining} more)")
        return
    if isinstance(payload, dict):
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(payload)


def main() -> None:
    service = ChatPortalService()
    bootstrap = service.bootstrap_session(business_slug="aug-pharma", agent_slug="nancy")
    session_token = bootstrap.session.session_token
    conversation = service.get_conversation(session_token=session_token)

    if not _business_prefers_mcp(conversation.business_profile):
        print_section("WARNING", "Business not configured for MCP; aborting.")
        return

    provider = load_mcp_provider()
    orchestrator = McpOrchestratorService(agent=conversation.agent_profile, provider=provider)

    body = "what product holds the product code of 20667"
    service.append_message(
        session_token=session_token,
        sender=ConversationSender.CUSTOMER,
        body=body,
    )

    context = orchestrator.stream_turn(
        conversation=conversation,
        user_message=body,
        on_response_text_delta=lambda chunk: None,
        on_status_change=lambda status: print_section("status", status),
        on_placeholder_response=None,
        on_stream_complete=lambda: print_section("stream", "complete"),
    )

    read_needed = orchestrator._requires_full_read(context.tool_context)
    print_section("READ ENFORCEMENT", read_needed)
    print_section("KNOWLEDGE RESULTS", getattr(context.tool_context, "knowledge_results", []), limit=3)
    print_section("KNOWLEDGE READS", getattr(context.tool_context, "knowledge_reads", []))
    print_section("TOOL TRACE", getattr(context.tool_context, "tool_trace", []), limit=5)


main()
