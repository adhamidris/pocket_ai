MCP App (apps/mcp)
=================

Purpose
-------
This app runs the tool-based orchestration loop for chat. It decides which
tools to call, enforces budgets/guardrails, compacts context, and streams the
final visitor-facing response. MCP is the primary portal runtime path.

**Current status:** agentic mode is the default (`rag_agentic_mode` feature flag),
with the read v2 contract enabled via `MCP_AGENTIC_READ_V2_ENABLED=true`.

Directory Map
-------------
- orchestrator.py
  Primary MCP orchestrator service shell and streaming public API.
- orchestrator_turn_execution.py
  Main MCP turn execution loop, tool dispatch, approval handling, and final answer pass.
- prompts.py
  System prompt builder + transcript assembly rules for MCP.
- prompt_context_notes.py
  Conversation memory, compacted history, recent refs, files, and automation context notes for MCP prompts.
- tool_definitions.py
  LLM-facing tool schemas and runtime schema limits.
- tools.py
  Tool registry + handlers not yet split out.
- gateway_tools.py
  External MCP gateway search/call handlers.
- file_tools.py
  Conversation file search/read handlers and PDF utility handlers.
- email_tools.py
  Email account resolution and Gmail/Microsoft email handlers.
- native_integration_tools.py
  Calendar, Drive, OneDrive, Slack, and HubSpot native integration handlers.
- integration_tool_catalog.py
  Native/email integration catalog and enablement policy helpers.
- agent_run_tools.py
  Background AgentRun request/list/get/continue handlers.
- task_tools.py
  Persistent task automation list/draft/update/activate/pause handlers.
- memory_tools.py
  Memory search/save/forget handlers.
- context_retrieval_tools.py
  Long-chat compacted context retrieval handler.
- search_cursor.py
  Search pagination cursor signing/cache helpers.
- tool_runtime_helpers.py
  Runtime logging, audit, and small cache helpers for MCP tools.
- knowledge_scope.py
  Agent knowledge-scope helpers for MCP knowledge tools.
- knowledge_query_helpers.py
  Query intent and read-sufficiency helpers for MCP knowledge tools.
- knowledge_result_helpers.py
  Knowledge result shaping helpers for MCP search/read tools.
- knowledge_identifier_helpers.py
  Identifier and table-column helpers for MCP knowledge tools.
- knowledge_observability.py
  Observability helpers for MCP knowledge tool payloads.
- knowledge_read_guards.py
  Read throttling and diagnostic warning helpers for MCP knowledge tools.
- knowledge_agentic_response.py
  Agentic search response conversion for MCP knowledge tools.
- knowledge_search_fusion.py
  Search result fusion helpers for MCP knowledge search.
- knowledge_search_tool.py
  Compatibility bridge for the public search_knowledge handler.
- knowledge_search/
  search_knowledge handler, service cache, pagination, and search result shaping.
- knowledge_read_tool.py
  Compatibility bridge for the public read_knowledge handler.
- knowledge_read/
  Agentic read_knowledge engine, wrapper validation, and table snippet helpers.
- portal_block_stream.py
  Streaming helper for portal response block tool calls.
- orchestrator_prompt_governor.py
  Prompt budgeting, compaction, and provider chat wrapper for the MCP orchestrator.
- orchestrator_prompt_tool_compaction.py
  Large tool-result prompt compaction helpers used by the prompt governor.
- orchestrator_planning.py
  Final response planning, verification parsing, citations, and plan assembly.
- orchestrator_knowledge_context.py
  MCP knowledge result tracking, read-reference repair, and cross-turn seen-item persistence.
- orchestrator_approval_policy.py
  Native integration policy, email approval previews, and email send audit helpers.
- orchestrator_native_approval.py
  Native integration availability, approval-mode overrides, and native policy decisions.
- orchestrator_email_drafts.py
  Email draft pending-state helpers and email argument sanitization.
- orchestrator_tool_trace_summary.py
  Privacy-safe tool input/output trace summaries for portal/debug events.
- orchestrator_phone_approval.py
  Phone-call approval payload, preview, reuse, and wait handling.
- orchestrator_tool_schema.py
  Tool schema lookup, setup defaults, and argument validation helpers.
- orchestrator_response_helpers.py
  Response block parsing, assistant message coercion, and prompt/tool-note logging helpers.
- orchestrator_runtime_controls.py
  Runtime feature flags, character budgets, constraint payloads, and generic approval waiting.
- orchestrator_remote_tools.py
  Remote MCP call execution, retry/idempotency helpers, and tool-call parsing.
- agentic_read_cursor.py
  Signed cursor helpers for agentic read_knowledge pagination.
- types.py
  Shared types/exceptions + ToolExecutionContext.
- sanitizer.py
  Filters internal filler text from streaming responses.
- identifier_detection.py
  Light identifier parsing helpers (email/phone/order ids).
- tests/
  Tool loop and observability tests.

Key Flows
---------
1) Tool loop (primary)
   User message -> McpOrchestratorService -> tool calls -> evidence packets
   -> final answer (no extra narration between tool calls).

2) Knowledge lookup (agentic default)
   `search_knowledge` returns refs with compact previews/read hints (`refs[]`),
   then the model calls `read_knowledge(refs[], max_chars=...)` to fetch canonical evidence.

3) Safety limits
   Budgets and rate limits enforce safe tool usage.

Tool Catalog (LLM-facing, agentic mode)
---------------------------------------
Knowledge tools:
- `search_knowledge`
- `read_knowledge`
- `search_conversation_files`, `read_conversation_file`

Workflow tools:
- Email connectors: `email_search`, `email_get_message`, `email_get_thread`,
  `email_create_draft`, `email_send_draft`
- Voice: `initiate_phone_call` (dev-only)
- Background runs: `start_agent_run`, `list_agent_runs`, `get_agent_run`, `continue_agent_run`
- MCP gateway: `mcp_search_tools`, `mcp_call_tool`
- Portal output (deprecated): `portal_emit_blocks` (disabled for portal turns; portal streams **server-built blocks**)
- Input control: `request_user_input`
- PDF utilities: `pdf_generate`, `pdf_merge`, `pdf_extract_pages`, `pdf_extract_text`

Tool Schema Reference
---------------------
Schema lives in `apps/mcp/tool_definitions.py` as `TOOL_DEFINITIONS`.
Use it as the canonical source of parameter names, enums, and limits.

Examples
--------
search_knowledge (batched query):
```json
{
  "tool": "search_knowledge",
  "query": "refund policy",
  "queries": ["refund", "returns policy", "استرجاع"]
}
```

read_knowledge (agentic batch read):
```json
{
  "tool": "read_knowledge",
  "refs": [{"id": "chunk-uuid-1"}, {"id": "chunk-uuid-2"}],
  "max_chars": 12000
}
```

Budgets + Memory
----------------
- Per-turn and per-minute character budgets enforced in the tool loop.
- Chunk/page read budgets prevent runaway context growth.
- Long-chat memory (summary + pinned identifiers) is injected via prompts.

Streaming Behavior
------------------
- The first response may include a short placeholder.
- After that, tool calls must emit empty content until final answer.
- Portal turns stream **server-built blocks**; model-driven `portal_emit_blocks` is disabled to avoid mixed-mode streaming.
- sanitizer.py removes investigative filler for professional/formal agents.

ASCII Flow
----------
User msg
   ↓
MCP planner + tool loop (apps/mcp/orchestrator.py)
   ↓
search_knowledge → read_knowledge (apps/mcp/knowledge_search_tool.py, apps/mcp/knowledge_read_tool.py)
   ↓
RAG retrieval (apps/rag) + knowledge access (apps/knowledge)
   ↓
Compact evidence + guardrails
   ↓
Final response

Configuration Touchpoints
-------------------------
- MCP_MAX_TOOL_ITERATIONS
- MCP_PROMPT_TABLE_MAX_CELLS / MCP_PROMPT_TABLE_MAX_CELLS_EXACT
- MCP_LONG_CHAT_MEMORY_ENABLED / MCP_MEMORY_SUMMARY_MAX_CHARS
- MCP_*_CALLS_PER_MINUTE (tool rate limits)
- RAG_MAX_CHAR_BUDGET_PER_TURN / RAG_MAX_CHAR_BUDGET_PER_MINUTE

Prompt + Tone Controls
----------------------
- MCP system prompt: `apps/mcp/prompts.py`
- Tone labels: `apps/accounts/agents.py`
- Placeholder filtering: `apps/mcp/sanitizer.py`

Troubleshooting
---------------
- Tool loops or no answer:
  - Check `MCP_MAX_TOOL_ITERATIONS` and tool error logs in `var/logs/rag.log`.
- Throttled or constraint errors:
  - Inspect `ToolConstraintError` + `throttle_notice` in tool output.
- Partial reads:
  - If `read_knowledge` returns `partial` with `deferred`, re-read only the deferred ids
    using the provided cursor and suggested max_chars.
- Streaming filler text:
  - Verify sanitizer output in `mcp.trace` logs (sanitizer.dropped_sentence).

Debugging Bad Answers (Quick Checklist)
---------------------------------------
1) Confirm the tool call:
   - Look for `stage=tool.request` and `stage=tool.response` in `var/logs/rag.log`.
2) Verify identifiers:
   - Ensure identifier gating isn’t blocking access (constraint errors / required keys).
3) Check disambiguation:
   - Clarification-like diagnostics may still appear for compatibility, but the portal runtime should prefer best-effort evidence over blocking on a clarification turn.
4) Inspect partial reads:
   - If `status=partial` with `deferred`, re-read only the deferred ids (use `cursor`).
5) Validate routing:
   - Confirm `read_knowledge` vs dataset tools based on source type (document vs CSV/XLSX).
6) Evaluate fallback behavior:
   - If `status=not_found`, the assistant should not fabricate values.
