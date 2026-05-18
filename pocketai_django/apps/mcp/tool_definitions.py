"""
LLM-facing MCP tool schemas.

This module keeps declarative tool schemas separate from the runtime handlers
in apps.mcp.tools.
"""

from __future__ import annotations

import copy
from typing import Mapping

from django.conf import settings


DEFAULT_MAX_SEARCH_QUERY_VARIANTS = 1


def _search_query_variant_limit() -> int:
    try:
        value = int(
            getattr(
                settings,
                "MCP_SEARCH_MAX_QUERY_VARIANTS",
                DEFAULT_MAX_SEARCH_QUERY_VARIANTS,
            )
        )
    except (TypeError, ValueError):  # pragma: no cover - defensive
        value = DEFAULT_MAX_SEARCH_QUERY_VARIANTS
    return max(1, value)


def _search_queries_schema_description() -> str:
    limit = _search_query_variant_limit()
    if limit == 1:
        return "List of search queries. Use up to 1 short, specific variant."
    return f"List of search queries. Use up to {limit} short, specific variants."


try:
    _PROMPT_TOOL_OUTPUT_MAX_CHARS = int(getattr(settings, "MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS", 12000) or 12000)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _PROMPT_TOOL_OUTPUT_MAX_CHARS = 12000
try:
    # Prefer the read_knowledge-specific knob, but retain the historical read_document name
    # as a backwards-compatible alias.
    _READ_KNOWLEDGE_MAX_CHARS_MARGIN = int(
        getattr(settings, "MCP_READ_KNOWLEDGE_MAX_CHARS_MARGIN", None)
        or getattr(settings, "MCP_READ_DOCUMENT_MAX_CHARS_MARGIN", 800)
        or 800
    )
except (TypeError, ValueError):  # pragma: no cover - defensive
    _READ_KNOWLEDGE_MAX_CHARS_MARGIN = 800
_READ_KNOWLEDGE_SAFE_PROMPT_MAX_CHARS = max(500, _PROMPT_TOOL_OUTPUT_MAX_CHARS - max(0, _READ_KNOWLEDGE_MAX_CHARS_MARGIN))
READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX = max(500, min(20000, _READ_KNOWLEDGE_SAFE_PROMPT_MAX_CHARS))
READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT = max(500, min(8000, READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX))

try:
    _MCP_PROMPT_MAX_SNIPPETS = int(getattr(settings, "MCP_PROMPT_MAX_SNIPPETS", 4) or 4)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _MCP_PROMPT_MAX_SNIPPETS = 4

# Single source of truth: how many snippet items the LLM is allowed to see per tool call.
MCP_PROMPT_MAX_SNIPPETS_CAP = max(1, _MCP_PROMPT_MAX_SNIPPETS)

# Keep the tool schema aligned with the runtime cap so the LLM can request up to the true limit.
SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX = MCP_PROMPT_MAX_SNIPPETS_CAP
try:
    _SEARCH_KNOWLEDGE_DEFAULT_LIMIT = int(getattr(settings, "MCP_SEARCH_KNOWLEDGE_DEFAULT_LIMIT", 10) or 10)
except (TypeError, ValueError):  # pragma: no cover - defensive
    _SEARCH_KNOWLEDGE_DEFAULT_LIMIT = 10
SEARCH_KNOWLEDGE_LIMIT_SCHEMA_DEFAULT = max(
    1,
    min(int(_SEARCH_KNOWLEDGE_DEFAULT_LIMIT), SEARCH_KNOWLEDGE_LIMIT_SCHEMA_MAX),
)

# Prefetch cap: allows cursor caching to store more candidates than the per-page limit.
# This is intentionally higher than MCP_PROMPT_MAX_SNIPPETS_CAP so pagination has results to page through.
SEARCH_PREFETCH_ABSOLUTE_CAP = 200


def _function_schema(
    *,
    name: str,
    description: str,
    properties: Mapping[str, Mapping[str, object]],
    required: tuple[str, ...] = (),
) -> Mapping[str, object]:
    """Helper to keep tool definitions concise and consistent."""

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        },
    }

GATEWAY_TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    _function_schema(
        name="mcp_search_tools",
        description=(
            "Search available external MCP tools and return a small list of candidates. "
            "Results include tool_id (stable identifier), connection_name, remote_tool, description, "
            "and required_args (names + types only)."
        ),
        properties={
            "query": {
                "type": "string",
                "description": "Natural-language description of what you want to do (e.g. 'list GitHub repos').",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of tools to return (1-10).",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
            },
            "connection_id": {
                "type": "string",
                "description": "Optional: restrict search to a specific MCP connection id.",
            },
        },
        required=("query",),
    ),
    _function_schema(
        name="mcp_call_tool",
        description=(
            "Call an external MCP tool by tool_id. "
            "Provide arguments as an object. Returns status + output and may include approval metadata."
        ),
        properties={
            "tool_id": {
                "type": "string",
                "description": "Tool identifier from mcp_search_tools results[].tool_id.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "arguments": {
                "type": "object",
                "description": "Arguments for the selected tool.",
                "additionalProperties": True,
            },
        },
        required=("tool_id", "arguments"),
    ),
)


TOOL_DEFINITIONS: tuple[Mapping[str, object], ...] = (
    _function_schema(
        name="retrieve_earlier_context",
        description=(
            "Retrieve detailed context from earlier conversation history that was compacted. "
            "Use this when you need specific facts or messages from earlier turns."
        ),
        properties={
            "query": {"type": "string", "description": "What to search for (keywords or a short question)."},
            "segment_id": {
                "type": "string",
                "description": "Optional: exact compacted segment id returned by a previous retrieve_earlier_context call.",
            },
            "timeframe": {
                "type": "string",
                "description": "Which part of history to search.",
                "enum": ["first_10_turns", "turns_10_to_20", "recent", "oldest", "all"],
            },
            "include_full_segment": {
                "type": "boolean",
                "description": "When true, return the full compacted segment messages (may be large).",
            },
            "max_messages": {
                "type": "integer",
                "description": "Maximum messages to return (applies to both matched and full segment output).",
                "minimum": 1,
                "maximum": 50,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=(),
    ),
    _function_schema(
        name="portal_emit_blocks",
        description=(
            "Emit structured block events for the portal UI. Use this to stream the final visitor-facing answer "
            "instead of plain text. Send incremental block_start/block_delta/block_end events."
        ),
        properties={
            "events": {
                "type": "array",
                "description": "Ordered list of block events to apply.",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Event type: block_start, block_delta, or block_end.",
                        },
                        "block": {
                            "type": "object",
                            "description": "Block payload for block_start (block_id, type, payload, parent_block_id).",
                        },
                        "block_id": {"type": "string", "description": "Target block id for block_delta/block_end."},
                        "ops": {
                            "type": "array",
                            "description": "Operations for block_delta (append_inline or append_code).",
                            "items": {"type": "object"},
                        },
                    },
                    "required": ["type"],
                },
            }
        },
        required=("events",),
    ),
    _function_schema(
        name="request_user_input",
        description=(
            "Request missing information from the end user. "
            "Use this when running background tasks that must pause until the user responds."
        ),
        properties={
            "prompt": {
                "type": "string",
                "description": "Primary question/prompt for the user (freeform).",
            },
            "questions": {
                "type": "array",
                "description": "Optional list of crisp questions to ask the user.",
                "items": {"type": "string"},
            },
            "schema": {
                "type": "object",
                "description": "Optional structured schema for the user's response (UI hint only).",
                "additionalProperties": True,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "additionalProperties": True,
            },
        },
        required=(),
    ),
    _function_schema(
        name="start_agent_run",
        description=(
            "Create a background AgentRun (background agent) anchored to this conversation. "
            "Use this when the visitor asks for a long-running or multi-step task so the chat can continue "
            "while the work happens in the Activity panel."
        ),
        properties={
            "goal": {
                "type": "string",
                "description": "Clear task goal for the background run.",
            },
            "title": {
                "type": "string",
                "description": "Optional short title shown in the Activity panel.",
            },
            "followup_mode": {
                "type": "string",
                "enum": ["handoff", "supervisor"],
                "description": (
                    "Optional: how results should be reported back into chat. "
                    "`handoff` posts the run output directly; `supervisor` is reserved for manager-style synthesis."
                ),
            },
            "success_criteria": {
                "type": "array",
                "description": "Optional list of success criteria (1-10).",
                "items": {"type": "string"},
            },
            "constraints": {
                "type": "object",
                "description": "Optional execution constraints (timeouts, max steps, max tool calls).",
                "additionalProperties": True,
            },
            "output_schema": {
                "type": "object",
                "description": "Optional expected output schema (JSON Schema-like).",
                "additionalProperties": True,
            },
            "approval": {
                "type": "object",
                "description": "Optional approval policy metadata for downstream tools.",
                "additionalProperties": True,
            },
            "visibility": {
                "type": "string",
                "enum": ["initiator", "managers", "workspace"],
                "description": "Who can view this run (teams/roles are pending).",
            },
            "plan": {
                "type": "object",
                "description": "Optional planner output to display in the Activity panel.",
                "additionalProperties": True,
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata for routing/output destinations.",
                "additionalProperties": True,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "additionalProperties": True,
            },
        },
        required=("goal",),
    ),
    _function_schema(
        name="list_agent_runs",
        description=(
            "List background runs (agent workforce) for this conversation. "
            "Returns status, title, and summary for each run so you can track progress and results."
        ),
        properties={
            "status_filter": {
                "type": "string",
                "enum": ["all", "active", "completed", "waiting"],
                "description": "Filter runs by status. 'active' = queued/running, 'waiting' = needs user/approval, 'completed' = finished/failed/cancelled.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "description": "Max runs to return (default 10).",
            },
            "refresh": {
                "type": "boolean",
                "description": "Bypass cache and fetch latest runs (default false).",
            },
        },
        required=(),
    ),
    _function_schema(
        name="get_agent_run",
        description=(
            "Get detailed status and result of a specific background run. "
            "Use this after list_agent_runs to check on a particular task."
        ),
        properties={
            "run_id": {
                "type": "string",
                "description": "UUID of the agent run to retrieve.",
            },
            "include_events": {
                "type": "boolean",
                "description": "Include recent execution events (default false).",
            },
        },
        required=("run_id",),
    ),
    _function_schema(
        name="continue_agent_run",
        description=(
            "Continue an existing background run (background agent) with a follow-up message. "
            "Use this to send additional instructions to a completed or waiting run instead of creating a new one. "
            "The background agent will resume with its full conversation history."
        ),
        properties={
            "run_id": {
                "type": "string",
                "description": "UUID of the agent run to continue.",
            },
            "message": {
                "type": "string",
                "description": "Follow-up instruction or message for the background agent.",
            },
        },
        required=("run_id", "message"),
    ),
    _function_schema(
        name="list_tasks",
        description="List saved automations for the current business, optionally filtered by owning assistant or status.",
        properties={
            "agent_id": {"type": "string", "description": "Optional agent UUID."},
            "status": {"type": "string", "enum": ["draft", "active", "paused", "all"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        required=(),
    ),
    _function_schema(
        name="draft_task",
        description=(
            "Create an inactive automation draft. Use for persistent scheduled, webhook, or email inbox tasks. "
            "Drafts must be approved by the user before activation. Only store fields visible in the task UI."
        ),
        properties={
            "agent_id": {"type": "string", "description": "Optional owning agent UUID. Defaults to the current agent."},
            "name": {"type": "string", "description": "Short task name."},
            "goal": {"type": "string", "description": "What the task should accomplish."},
            "trigger_type": {"type": "string", "enum": ["schedule", "webhook", "email_inbox"]},
            "trigger_config": {"type": "object", "additionalProperties": True},
            "source_config": {"type": "object", "additionalProperties": True},
            "visibility": {"type": "string", "enum": ["initiator", "managers", "workspace"]},
        },
        required=("name", "goal"),
    ),
    _function_schema(
        name="update_task",
        description="Update an existing saved automation draft or paused automation.",
        properties={
            "task_id": {"type": "string", "description": "Automation/task UUID."},
            "name": {"type": "string"},
            "goal": {"type": "string"},
            "trigger_type": {"type": "string", "enum": ["schedule", "webhook", "email_inbox"]},
            "trigger_config": {"type": "object", "additionalProperties": True},
            "source_config": {"type": "object", "additionalProperties": True},
            "visibility": {"type": "string", "enum": ["initiator", "managers", "workspace"]},
        },
        required=("task_id",),
    ),
    _function_schema(
        name="request_task_activation",
        description=(
            "Activate a saved task only after explicit user approval. "
            "If approved is false or omitted, returns an approval-needed payload instead of activating."
        ),
        properties={
            "task_id": {"type": "string", "description": "Automation/task UUID."},
            "approved": {"type": "boolean", "description": "Set true only after the user explicitly approves activation."},
        },
        required=("task_id",),
    ),
    _function_schema(
        name="pause_task",
        description="Pause an active saved automation.",
        properties={
            "task_id": {"type": "string", "description": "Automation/task UUID."},
            "reason": {"type": "string"},
        },
        required=("task_id",),
    ),
    _function_schema(
        name="search_memory",
        description="Search scoped long-term memory for relevant facts, preferences, decisions, or automation state.",
        properties={
            "query": {"type": "string", "description": "Search text."},
            "scope": {"type": "string", "enum": ["workspace", "agent", "automation", "run", "conversation", "crm_contact", "crm_company"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        required=("query",),
    ),
    _function_schema(
        name="save_memory",
        description="Save a scoped memory item. Sensitive or behavior-changing memories are routed to review.",
        properties={
            "content": {"type": "string", "description": "Memory content."},
            "kind": {"type": "string", "enum": ["fact", "preference", "policy", "decision", "instruction", "relationship", "state_note", "artifact_ref", "extracted_data"]},
            "scope": {"type": "string", "enum": ["workspace", "agent", "automation", "run", "conversation"]},
            "key": {"type": "string"},
            "sensitivity": {"type": "string", "enum": ["normal", "sensitive", "secret"]},
            "visibility": {"type": "string", "enum": ["private", "shared"]},
        },
        required=("content",),
    ),
    _function_schema(
        name="forget_memory",
        description="Archive a memory item that is stale, wrong, or no longer needed.",
        properties={"memory_id": {"type": "string", "description": "UUID of the memory item to archive."}},
        required=("memory_id",),
    ),
    _function_schema(
        name="search_knowledge",
        description="Search the knowledge base using a natural-language query.",
        properties={
            "cursor": {
                "type": "string",
                "description": "Opaque cursor from a prior search_knowledge response to fetch the next page.",
            },
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": _search_query_variant_limit(),
                "description": _search_queries_schema_description(),
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
        },
        required=(),
    ),
    _function_schema(
        name="search_conversation_files",
        description="Search files uploaded in this chat session (PDFs).",
        properties={
            "query": {
                "type": "string",
                "description": "What to look for in uploaded files.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of snippets to return (1-8).",
                "minimum": 1,
                "maximum": 8,
                "default": 5,
            },
        },
        required=("query",),
    ),
    _function_schema(
        name="read_conversation_file",
        description=(
            "Read extracted text from uploaded chat files. "
            "In agentic mode prefer ids[] from search_conversation_files."
        ),
        properties={
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of chunk IDs from search_conversation_files results (agentic mode).",
                "minItems": 1,
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum total characters to return across all ids.",
                "minimum": 500,
                "maximum": 20000,
                "default": 8000,
            },
        },
        required=("ids",),
    ),
    _function_schema(
        name="pdf_generate",
        description=(
            "Generate a PDF from provided text/markdown and attach it to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "content": {"type": "string", "description": "Main content to render into the PDF."},
            "title": {"type": "string", "description": "Optional title displayed at the top."},
            "filename": {
                "type": "string",
                "description": "Optional output filename (e.g., 'summary.pdf').",
            },
            "format": {
                "type": "string",
                "enum": ["text", "markdown"],
                "default": "markdown",
                "description": "Input content format. Markdown is rendered as plain text (no HTML).",
            },
            "page_size": {
                "type": "string",
                "enum": ["letter", "a4"],
                "default": "letter",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {"type": "string"},
                },
            },
        },
        required=("content",),
    ),
    _function_schema(
        name="pdf_merge",
        description=(
            "Merge multiple PDFs (uploaded or generated in this chat) and attach the merged PDF to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "file_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 2,
                "description": "List of PDF file IDs to merge (ConversationFile IDs).",
            },
            "filename": {"type": "string", "description": "Optional output filename (e.g., 'merged.pdf')."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_ids",),
    ),
    _function_schema(
        name="pdf_extract_pages",
        description=(
            "Extract specific pages from a PDF and attach the new PDF to this chat session. "
            "The chat portal will render a downloadable attachment card automatically (do not paste raw download URLs)."
        ),
        properties={
            "file_id": {"type": "string", "description": "PDF file ID (ConversationFile ID)."},
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 1,
                "description": "1-based page numbers to extract.",
            },
            "filename": {"type": "string", "description": "Optional output filename (e.g., 'pages.pdf')."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_id", "pages"),
    ),
    _function_schema(
        name="pdf_extract_text",
        description="Extract text from a PDF (optionally specific pages).",
        properties={
            "file_id": {"type": "string", "description": "PDF file ID (ConversationFile ID)."},
            "pages": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "description": "Optional list of 1-based page numbers to extract.",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 500,
                "maximum": 50000,
                "default": 12000,
                "description": "Maximum characters to return.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("file_id",),
    ),
    _function_schema(
        name="read_knowledge",
        description=(
            "Read canonical evidence from the knowledge base (agentic contract). "
            "Provide refs from search_knowledge; include cursors only when continuing a partial read."
        ),
        properties={
            "refs": {
                "type": "array",
                "minItems": 1,
                "description": "List of refs to read. Each item is {id} or {id,cursor}.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Ref id from search_knowledge refs[].id.",
                        },
                        "cursor": {
                            "type": "string",
                            "description": "Opaque continuation cursor from a previous read_knowledge response.",
                        },
                        "row_start": {
                            "type": "integer",
                            "minimum": 0,
                            "description": (
                                "For table refs only: 0-based row offset within the returned table body "
                                "(excluding header/separator rows)."
                            ),
                        },
                        "row_limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": "For table refs only: maximum number of rows to return for this read.",
                        },
                    },
                    "required": ["id"],
                },
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {
                    "spinner_text": {
                        "type": "string",
                        "description": "Short portal spinner label for this tool call.",
                    }
                },
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum total characters to return across all refs (bounded by server caps).",
                "minimum": 500,
                "maximum": READ_KNOWLEDGE_MAX_CHARS_SCHEMA_MAX,
                "default": READ_KNOWLEDGE_MAX_CHARS_SCHEMA_DEFAULT,
            },
        },
        required=("refs", "max_chars"),
    ),
    _function_schema(
        name="email_search",
        description="Search the connected email mailbox (Google/Microsoft). Results are bounded and text-only.",
        properties={
            "query": {"type": "string", "description": "Search query (provider syntax may vary)."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
            "limit": {
                "type": "integer",
                "description": "Maximum number of results (1-25).",
                "minimum": 1,
                "maximum": 25,
                "default": 5,
            },
            "after": {"type": "string", "description": "Optional: ISO date/time lower bound."},
            "before": {"type": "string", "description": "Optional: ISO date/time upper bound."},
            "from": {"type": "string", "description": "Optional: filter sender email address."},
            "to": {"type": "string", "description": "Optional: filter recipient email address."},
            "subject": {"type": "string", "description": "Optional: filter subject contains."},
        },
        required=("query",),
    ),
    _function_schema(
        name="email_get_message",
        description="Fetch a specific email message by id (text-only).",
        properties={
            "message_id": {"type": "string", "description": "Provider message id."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
        },
        required=("message_id",),
    ),
    _function_schema(
        name="email_get_thread",
        description="Fetch a specific email thread by id (text-only).",
        properties={
            "thread_id": {"type": "string", "description": "Provider thread id."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
        },
        required=("thread_id",),
    ),
    _function_schema(
        name="email_create_draft",
        description="Create an email draft (text-only body).",
        properties={
            "to": {
                "type": "array",
                "description": "Primary recipients (email addresses).",
                "items": {"type": "string"},
            },
            "cc": {"type": "array", "items": {"type": "string"}},
            "bcc": {"type": "array", "items": {"type": "string"}},
            "subject": {"type": "string"},
            "body_text": {"type": "string", "description": "Plain text email body."},
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: specific connected mailbox id."},
        },
        required=("to", "subject", "body_text"),
    ),
    _function_schema(
        name="email_send_draft",
        description="Send a previously created draft (may require approval depending on policy).",
        properties={
            "draft_id": {
                "type": "string",
                "description": "Optional: provider draft id returned by email_create_draft. If omitted, the system will try to send the most recent pending draft in this conversation.",
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
            "email_account_id": {"type": "string", "description": "Optional: UUID of a specific connected mailbox id (usually omit)."},
        },
        required=(),
    ),
    _function_schema(
        name="initiate_phone_call",
        description=(
            "Initiate a single outbound phone call. "
            "Creates a queued CallSession that will be executed by the voice_call_worker."
        ),
        properties={
            "phone_number": {
                "type": "string",
                "description": "Destination number in E.164 format (e.g., +201234567890).",
            },
            "objective": {
                "type": "string",
                "description": "Short, concrete purpose for the call (what the agent must accomplish).",
            },
            "call_type": {
                "type": "string",
                "description": "Type of call (service or marketing). Marketing calls may be blocked by policy.",
                "enum": ["service", "marketing"],
                "default": "service",
            },
            "language": {
                "type": "string",
                "description": "Call language (en or ar).",
                "enum": ["en", "ar"],
                "default": "en",
            },
            "max_duration_minutes": {
                "type": "integer",
                "description": "Upper bound for call duration (enforced by policy).",
                "minimum": 1,
                "maximum": 60,
                "default": 10,
            },
            "context_items": {
                "type": "array",
                "description": "Optional structured notes to attach to the call session.",
                "items": {"type": "object", "additionalProperties": True},
            },
            "__ui": {
                "type": "object",
                "description": "UI-only metadata (ignored by the tool).",
                "properties": {"spinner_text": {"type": "string"}},
            },
        },
        required=("phone_number", "objective"),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — Google Calendar
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="calendar_list_events",
        description="List upcoming events from the connected Google Calendar.",
        properties={
            "time_min": {"type": "string", "description": "Start of time range (ISO 8601 datetime). Defaults to now."},
            "time_max": {"type": "string", "description": "End of time range (ISO 8601 datetime)."},
            "query": {"type": "string", "description": "Free-text search query."},
            "max_results": {"type": "integer", "description": "Maximum events to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    _function_schema(
        name="calendar_get_event",
        description="Get details of a specific Google Calendar event by ID.",
        properties={
            "event_id": {"type": "string", "description": "Google Calendar event ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("event_id",),
    ),
    _function_schema(
        name="calendar_create_event",
        description="Create a new event on Google Calendar.",
        properties={
            "summary": {"type": "string", "description": "Event title."},
            "start_time": {"type": "string", "description": "Start datetime (ISO 8601, e.g. 2025-01-15T09:00:00-05:00)."},
            "end_time": {"type": "string", "description": "End datetime (ISO 8601)."},
            "description": {"type": "string", "description": "Event description (optional)."},
            "attendees": {"type": "array", "items": {"type": "string"}, "description": "Email addresses of attendees."},
            "location": {"type": "string", "description": "Event location (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("summary", "start_time", "end_time"),
    ),
    _function_schema(
        name="calendar_update_event",
        description="Update an existing Google Calendar event.",
        properties={
            "event_id": {"type": "string", "description": "Google Calendar event ID to update."},
            "summary": {"type": "string", "description": "New event title (optional)."},
            "start_time": {"type": "string", "description": "New start datetime (optional)."},
            "end_time": {"type": "string", "description": "New end datetime (optional)."},
            "description": {"type": "string", "description": "New description (optional)."},
            "location": {"type": "string", "description": "New location (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("event_id",),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — Google Drive
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="drive_search_files",
        description="Search files in the connected Google Drive.",
        properties={
            "query": {"type": "string", "description": "Search query (file name or content)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="drive_get_file",
        description="Get file content from Google Drive (text-based files). Returns metadata for binary files.",
        properties={
            "file_id": {"type": "string", "description": "Google Drive file ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("file_id",),
    ),
    _function_schema(
        name="drive_list_files",
        description="List files in a Google Drive folder (or root if no folder specified).",
        properties={
            "folder_id": {"type": "string", "description": "Google Drive folder ID (optional, omit for root)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — OneDrive
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="onedrive_search_files",
        description="Search files in the connected OneDrive.",
        properties={
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="onedrive_get_file",
        description="Get file content from OneDrive (text-based files). Returns metadata for binary files.",
        properties={
            "file_id": {"type": "string", "description": "OneDrive item ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("file_id",),
    ),
    _function_schema(
        name="onedrive_list_files",
        description="List files in a OneDrive folder (or root if no folder specified).",
        properties={
            "folder_id": {"type": "string", "description": "OneDrive folder ID (optional, omit for root)."},
            "max_results": {"type": "integer", "description": "Maximum files to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — Slack
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="slack_list_channels",
        description="List Slack channels the user can access.",
        properties={
            "max_results": {"type": "integer", "description": "Maximum channels to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=(),
    ),
    _function_schema(
        name="slack_read_channel",
        description="Read recent messages from a Slack channel.",
        properties={
            "channel_id": {"type": "string", "description": "Slack channel ID."},
            "limit": {"type": "integer", "description": "Maximum messages to return (1-100).", "minimum": 1, "maximum": 100, "default": 20},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("channel_id",),
    ),
    _function_schema(
        name="slack_send_message",
        description="Send a message to a Slack channel.",
        properties={
            "channel_id": {"type": "string", "description": "Slack channel ID."},
            "text": {"type": "string", "description": "Message text to send."},
            "thread_ts": {"type": "string", "description": "Thread timestamp to reply in thread (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("channel_id", "text"),
    ),
    _function_schema(
        name="slack_search_messages",
        description="Search messages across the connected Slack workspace.",
        properties={
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Maximum results (1-50).", "minimum": 1, "maximum": 50, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    # ═══════════════════════════════════════════════════════════════════════
    # NATIVE INTEGRATIONS — HubSpot
    # ═══════════════════════════════════════════════════════════════════════
    _function_schema(
        name="hubspot_search_contacts",
        description="Search contacts in the connected HubSpot CRM.",
        properties={
            "query": {"type": "string", "description": "Search query (name, email, company, etc.)."},
            "max_results": {"type": "integer", "description": "Maximum results (1-100).", "minimum": 1, "maximum": 100, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
    _function_schema(
        name="hubspot_get_contact",
        description="Get details of a specific HubSpot contact by ID.",
        properties={
            "contact_id": {"type": "string", "description": "HubSpot contact ID."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("contact_id",),
    ),
    _function_schema(
        name="hubspot_create_contact",
        description="Create a new contact in HubSpot CRM.",
        properties={
            "email": {"type": "string", "description": "Contact email address (required)."},
            "first_name": {"type": "string", "description": "First name (optional)."},
            "last_name": {"type": "string", "description": "Last name (optional)."},
            "phone": {"type": "string", "description": "Phone number (optional)."},
            "company": {"type": "string", "description": "Company name (optional)."},
            "job_title": {"type": "string", "description": "Job title (optional)."},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("email",),
    ),
    _function_schema(
        name="hubspot_search_deals",
        description="Search deals in the connected HubSpot CRM.",
        properties={
            "query": {"type": "string", "description": "Search query (deal name, etc.)."},
            "max_results": {"type": "integer", "description": "Maximum results (1-100).", "minimum": 1, "maximum": 100, "default": 10},
            "integration_account_id": {"type": "string", "description": "Optional: specific integration account id."},
            "__ui": {"type": "object", "description": "UI-only metadata (ignored by the tool).", "properties": {"spinner_text": {"type": "string"}}},
        },
        required=("query",),
    ),
)


def get_tool_definitions() -> tuple[Mapping[str, object], ...]:
    """
    Return tool schemas with runtime-tuned descriptions.

    Some guidance text depends on current settings (for example
    MCP_SEARCH_MAX_QUERY_VARIANTS), so we patch those fields per request.
    """

    definitions: list[Mapping[str, object]] = copy.deepcopy(list(TOOL_DEFINITIONS))
    queries_description = _search_queries_schema_description()

    for tool_def in definitions:
        function_block = tool_def.get("function")
        if not isinstance(function_block, Mapping):
            continue
        if str(function_block.get("name") or "").strip() != "search_knowledge":
            continue
        parameters = function_block.get("parameters")
        if not isinstance(parameters, Mapping):
            break
        properties = parameters.get("properties")
        if not isinstance(properties, Mapping):
            break
        queries_schema = properties.get("queries")
        if isinstance(queries_schema, dict):
            queries_schema["description"] = queries_description
        break
    return tuple(definitions)
