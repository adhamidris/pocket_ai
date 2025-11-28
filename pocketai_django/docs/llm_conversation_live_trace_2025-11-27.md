# Live Portal Conversation Trace – Sales Units (2025-11-27)

This file complements `docs/llm_conversation_backend_flow.md` by walking through a **real portal turn** and showing which methods actually ran, in the order they appeared in the logs. Use it as a ground-truth example when debugging or comparing future runs.

## Scenario Snapshot
- Conversation: `5d53755a-3ccc-49fe-b25a-e499981e351f`; Business: `9a063980-081d-48c0-a05e-29ea2d0ac241`; Agent: `nancy`.
- User ask: totals for six products across three stores (Arabic names included).
- Orchestrator: MCP selected with `DeepSeekToolsProvider`.
- Outcome: streamed answer with citations; planner returned **0 actions / 0 extractions**; totals aggregated from a spreadsheet upload.

## Log Timeline With Code Touchpoints
- **Session bootstrap** – `[17:05:47] POST /api/chat/portal/sessions/ 200` via `apps/api/chat_portal.py:bootstrap_session` and `ChatPortalService.bootstrap_session`. Conversation + session token created.
- **Streaming request** – `[19:05:57] POST /api/chat/stream/send/` hits `apps/api/chat_portal.py:409 stream_send`.
  - Customer message persisted through `ChatPortalService.append_message` (`sender=CUSTOMER`).
  - `_business_prefers_mcp` → MCP selected; `PortalTraceLogger.log("orchestrator.selected", mode=mcp provider=DeepSeekToolsProvider)`.
- **Turn start** – `McpOrchestratorService.stream_turn` (`apps/services/mcp/orchestrator.py:720`) begins; status `thinking` enqueued via `on_status_change`.
- **Primary prompt + initial stream** – `llm_provider.chat` invoked with tools enabled; `structured_log` emits `mcp.trace stage=prompt.primary` and `llm.trace stage=request`. `_flush_stream_buffer` drops filler (“I'll help you find the sales units…”) → `mcp.trace stage=sanitizer.dropped_sentence`.
- **Tool loop (in order)** inside `stream_turn` and `tools.execute_tool`:
  - `list_tables` (`apps/services/mcp/tools.py:1480 _list_tables_handler`) with query `sales units products stores` → `matched_uploads=0` (`mcp.trace stage=table.list`).
  - `search_knowledge` (`apps/services/mcp/tools.py:914 _search_knowledge_handler`) → 3 snippets (`mcp.trace stage=tool.search_knowledge` + `search.performance`); status surfaced as `searching_knowledge`.
  - `read_document` (`apps/services/mcp/tools.py:1208 _read_document_handler`) reading upload `04491ccd-52ad-4277-8f2f-6a29e43892b0`, page 113, `mode=full_page`; status `reading_document` (“Purchasing Data – Purchasing Sales Data”).
  - `table_aggregate` (`apps/services/mcp/tools.py:1668 _table_aggregate_handler`) on upload `82f161a0-88a3-457e-9a03-36a8497c577e`, `mode=row_total`, `match_column=column_2`, `match_values` = six product names, `columns` = three stores. Returned 6 matched rows with totals 40, 4,093, 218, 2,026, 187, 116 (`total=6680.0`) → `mcp.trace stage=table.aggregate`.
- **Final answer stream** – Second, tools-disabled call in `stream_turn` streams the cleaned answer; status transitions to `responding`, then `stream_complete` (SSE `delta` chunks sent by `event_stream` in `apps/api/chat_portal.py`).
- **Planner + persistence** – Background thread `finalize_stream_context` (`apps/api/chat_portal.py:534`) calls `McpOrchestratorService.run_planner_only` (`apps/services/mcp/orchestrator.py:1028`) → `planned_actions=0`, `extractions=0` (`planner.completed` log). AI message persisted via `ChatPortalService.append_message` (`sender=AI`) with citations (pages 67, 87, 112, 113 of “Purchasing Data – Purchasing Sales Data”).
- **Dispatch** – `plan.ready actions=0 extractions=0`, `response.dispatched` → SSE `turnPersisted` with `message_id=ad11c895-7a43-4384-b153-6b9818a617e6`. No `actionsComplete` emitted (empty plan).

## Tool Payload Highlights (from logs)
- `search_knowledge`: query included product/store names; 3 snippets returned, no identifier gating; latency breakdown logged under `search.performance`.
- `read_document`: `chunk_reads_used=1`, `chunk_pages_used=1`, neighbor window `1`, `token_budget` default; snippet label “Purchasing Data – Purchasing Sales Data”.
- `table_aggregate`: `row_limit=50`, `match_count=6`, `evaluated_rows=148`, `requested_columns` = three store columns, `total` = 6680.0; cache miss.

## Log Emitters (for comparison)
- `portal.trace …` → `PortalTraceLogger` inside `apps/api/chat_portal.py:stream_send` and `finalize_stream_context`.
- `mcp.trace …` → `structured_log` calls in `apps/services/mcp/orchestrator.py` and `apps/services/mcp/tools.py`.
- `llm.trace …` → `structured_log` in `apps/services/llm_provider.py` during provider calls.

## How This Matches the General Flow Doc
- Follows the **MCP path** described in `docs/llm_conversation_backend_flow.md`: customer message persisted → MCP `stream_turn` → knowledge tools (search → read → aggregate) → final answer stream → planner-only pass → persistence → SSE `turnPersisted`.
- Sub-scenario exercised: **table aggregation** (Scenario C in the main doc) after a search + full-page read.
- Planner produced no actions/extractions, so `ActionDispatcher` and `store_extractions` were skipped; SSE stopped after `turnPersisted`.

