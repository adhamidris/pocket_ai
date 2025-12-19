MCP App (apps/mcp)
=================

Purpose
-------
This app runs the tool-based orchestration loop for chat. It decides which
tools to call, enforces budgets/guardrails, compacts context, and streams the
final visitor-facing response. MCP is the primary runtime path when
`RAG_USE_MCP_ORCHESTRATOR=True`.

Directory Map
-------------
- orchestrator.py
  Main tool loop, streaming, budgets, memory summary, and prompt compaction.
- prompts.py
  System prompt builder + transcript assembly rules for MCP.
- tools.py
  Tool schemas + handlers (search/read/list tables + CRM actions).
- types.py
  Shared types/exceptions + ToolExecutionContext.
- sanitizer.py
  Filters internal filler text from streaming responses.
- identifier_detection.py
  Light identifier parsing helpers (email/phone/order ids).
- identifier_registry.py
  Identifier gating rules, registry, and audit logging.
- identifier_eval.py
  Helper harness for identifier-related evaluation.
- tests/
  Tool loop, observability, and identifier guardrail tests.

Key Flows
---------
1) Tool loop (primary)
   User message -> McpOrchestratorService -> tool calls -> evidence packets
   -> final answer (no extra narration between tool calls).

2) Knowledge lookup
   search_knowledge -> KnowledgeSearchService (apps/rag) -> snippets
   -> read_knowledge -> compact evidence -> final response.

3) Guardrails
   IdentifierGate checks required keys before retrieval.
   Budgets and rate limits enforce safe tool usage.

Tool Catalog (LLM-facing)
-------------------------
- search_knowledge
  - Batch semantic + lexical search; returns snippets.
- read_knowledge
  - Single retrieval tool for text, tables, and datasets (routes internally).
- list_tables
  - Lists tabular uploads to discover document IDs.
- CRM actions
  - create_case, update_case_status, update_case_details, add_case_history,
    flag_escalation, create_customer, update_customer, create_lead,
    create_appointment.

Tool Schema Reference
---------------------
Schema lives in `apps/mcp/tools.py` as `TOOL_DEFINITIONS`.
Use it as the canonical source of parameter names, enums, and limits.

read_knowledge Parameters (high-level)
--------------------------------------
- document_id (required)
- intent: auto | text | table
- text: { page, offset, page_size, neighbor_window, token_budget }
- table: { sheet_name, match_column, match_value(s), filters, select_columns, sort_by, limit, ... }

Identifier Guardrails
---------------------
- Identifier requirements are stored per upload (identifier_registry.py).
- If required keys are missing, tools return constraint errors and the model
  must ask for the missing identifier before retrying.
- For identifier lookups, exact matching is enforced by default.

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

read_knowledge (text excerpt):
```json
{
  "tool": "read_knowledge",
  "document_id": "a1b2c3d4-1111-2222-3333-444455556666",
  "intent": "text",
  "text": { "page": 1, "neighbor_window": 1, "token_budget": 900 }
}
```

read_knowledge (table lookup by exact identifier):
```json
{
  "tool": "read_knowledge",
  "document_id": "aa6c05b8-3724-476b-85b2-7d6c322b67e5",
  "intent": "table",
  "table": {
    "sheet_name": "Data",
    "match_column": "Invoice Serial",
    "match_value": "9125779195",
    "select_columns": [
      "Invoice Serial",
      "Invoice Date",
      "Customer",
      "Customer Name",
      "Mat. Code",
      "Mat. Desc.",
      "Qty",
      "Value"
    ],
    "limit": 5
  }
}
```

read_knowledge response envelope (shape):
```json
{
  "tool": "read_knowledge",
  "status": "ok",
  "engine": "file_dataset",
  "evidence": {
    "rows": [
      { "Invoice Serial": "9125779195", "Invoice Date": "2025-11-16", "...": "..." }
    ],
    "snippets": []
  },
  "total_matches": 1,
  "truncated": false,
  "throttle_notice": null,
  "diagnostics": { "query_engine": "duckdb" }
}
```

Tool Response Envelope (read_knowledge)
---------------------------------------
Common fields:
- tool: "read_knowledge"
- status: ok | not_found | error | throttled | constraint_error | disambiguation_required
- engine: text_page | table_preview | file_dataset | db_preview | null
- evidence: { snippets: [...], rows: [...] }
- total_matches: int
- truncated: bool
- throttle_notice: { type, message } (optional)
- diagnostics: { ... } (optional)

Budgets + Memory
----------------
- Per-turn and per-minute character budgets enforced in the tool loop.
- Chunk/page read budgets prevent runaway context growth.
- Long-chat memory (summary + pinned identifiers) is injected via prompts.

Streaming Behavior
------------------
- The first response may include a short placeholder.
- After that, tool calls must emit empty content until final answer.
- sanitizer.py removes investigative filler for professional/formal agents.

Quick Start (Dev)
----------------
- Run a light load test:
  `python manage.py run_mcp_load_test --business-id <uuid> --mode search`
- Run full production gates (RAG + MCP):
  `python manage.py run_production_gates --business-id <uuid>`
- Latest load test artifact:
  `cat var/logs/mcp_load_test_latest.json`

ASCII Flow
----------
User msg
   ↓
MCP planner + tool loop (apps/mcp/orchestrator.py)
   ↓
search_knowledge / read_knowledge (apps/mcp/tools.py)
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
- Streaming filler text:
  - Verify sanitizer output in `mcp.trace` logs (sanitizer.dropped_sentence).

Debugging Bad Answers (Quick Checklist)
---------------------------------------
1) Confirm the tool call:
   - Look for `stage=tool.request` and `stage=tool.response` in `var/logs/rag.log`.
2) Verify identifiers:
   - Ensure requested identifier appears in `evidence.rows` and `total_matches > 0`.
3) Check disambiguation:
   - If `status=disambiguation_required`, the assistant must ask a clarifying question.
4) Inspect truncation:
   - If `truncated=true`, narrow filters or reduce columns/rows.
5) Validate routing:
   - Check `engine` and `diagnostics.query_engine` to confirm dataset vs table preview.
6) Evaluate fallback behavior:
   - If `status=not_found`, the assistant should not fabricate values.

Observability
-------------
- Main log stream: `var/logs/rag.log` (look for `mcp.trace` entries).
- Load test artifact: `var/logs/mcp_load_test_latest.json`.
- Evaluation gate: `python manage.py run_production_gates --business-id <uuid>`.

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/ops/load_testing.md`
- `docs/ops/manual_qa_playbook.md`
- `docs/rag/rag_rollout_ops.md`

Glossary (Quick)
----------------
- Tool loop: multiple tool calls before final answer.
- Evidence packet: compact tool output injected into prompts.
- Identifier gate: required key checks (invoice/order/email/etc).

Where To Start (Reading Order)
------------------------------
1) `apps/mcp/orchestrator.py` — core loop + budgets.
2) `apps/mcp/tools.py` — tool schemas + handlers.
3) `apps/mcp/prompts.py` — MCP system prompt + transcript rules.
4) `apps/mcp/identifier_registry.py` — gating + required keys.

High-Level Architecture
-----------------------
Knowledge (ingest/store)
   ↓
RAG (retrieve/score)
   ↓
MCP (tool loop + guards)
   ↓
LLM (final answer)
