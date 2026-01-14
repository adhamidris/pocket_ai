# Portal LLM Conversation Flow (Django Backend)

This document walks through a **single chat turn** in the SaaS portal – from the browser sending a message, through Django, into the LLM and RAG stack, and back out as streaming events – with sub-scenarios for **searching**, **reading**, and **aggregating** knowledge.

The goal is to let a “vibe coder” open a few key files and immediately see how everything hangs together.

---

## Scope

This doc covers only the **public chat portal → LLM → knowledge tools** workflow:

- Session bootstrap for the portal widget.
- A single **message send** using `POST /api/chat/stream/send/`.
- The **MCP orchestrator** path (tool-calling RAG with `search_knowledge`, `read_document`, `table_aggregate`).
- How streaming, planning, actions, and extractions are stitched together and persisted.

Out of scope:

- Business onboarding flows.
- Upload/ingestion pipelines.
- Admin dashboards or internal APIs.

---

## Key Actors & Modules

High-level components you’ll see in the flow:

- **Browser / Widget**
  - JS client that calls the API and consumes SSE events.
  - Lives in `pocketai_django/frontend/static/js/chat-portal.js`.

- **Django API Surface**
  - URL routing: `pocketai_django/apps/api/urls.py`.
  - Portal endpoints: `pocketai_django/apps/api/chat_portal.py`.
    - `resolve_portal_handle` – maps business + agent slugs.
    - `bootstrap_session` – creates / resumes conversation.
    - `messages_endpoint` – list + append messages (non-streaming).
    - `stream_send` – **main streaming LLM entry point**.
    - `events` – background heartbeat / status SSE.

- **Portal Orchestration**
  - `ChatPortalService` in `pocketai_django/apps/conversations/portal.py`.
  - Manages `Conversation`, messages, CSAT, feedback, and session lifecycle.

- **LLM Orchestrators**
  - **MCP (tool-calling, primary path)**:
    - `McpOrchestratorService` in `pocketai_django/apps/mcp/orchestrator.py`.
    - Tools + schemas: `pocketai_django/apps/mcp/tools.py`, `prompts.py`.
  - **Legacy orchestrator (fallback)**:
    - `AiOrchestratorService` in `pocketai_django/apps/rag/ai_orchestrator.py`.

- **LLM Providers**
  - Interfaces and concrete implementations (OpenAI, DeepSeek, stub) live in:
    - `pocketai_django/apps/llm/llm_provider.py`.

- **RAG / Knowledge Layer**
  - Search + read logic:
    - `KnowledgeSearchService` in `pocketai_django/apps/rag/ai_orchestrator.py`.
  - MCP tools wrapping that service:
    - `search_knowledge`, `read_document` (and internal table helpers) in `pocketai_django/apps/mcp/tools.py`.

- **Post-actions & Extractions**
  - `ActionDispatcher` and `AiOrchestratorPlan` in `pocketai_django/apps/rag/ai_orchestrator.py`.
  - Case/customer actions, lead/appointment creation, and structured extractions.

- **Tracing & Telemetry**
  - `PortalTraceLogger` in `pocketai_django/apps/api/chat_portal.py`.
  - `rag_log` / `structured_log` in `pocketai_django/apps/rag/rag_logging.py`.

If you want to vibe through the flow in code, the usual path is:

`apps/api/urls.py` → `apps/api/chat_portal.py:stream_send` → orchestrator (`apps/mcp/orchestrator.py`) → tools (`apps/mcp/tools.py`) → LLM provider + RAG services.

---

## End-to-End Turn Timeline (One Message)

At a high level, a **single user message** goes through these phases:

1. **Session bootstrap (once per browser tab)**
   - Widget resolves the business/agent handle and calls `bootstrap_session`.
   - Backend creates or resumes a `Conversation` and returns past messages + a `session_token`.

2. **Message send**
   - Widget sends `POST /api/chat/stream/send/` with `session_token`, `body`, and optional metadata.
   - Backend persists the customer message and decides which orchestrator to use (MCP vs legacy).

3. **Streaming orchestration**
   - Orchestrator runs `stream_turn(...)` with callbacks:
     - `on_response_text_delta(chunk)` – stream answer text out.
     - `on_status_change(state)` – high-level progress updates (`searching_knowledge`, `reading_document`, `planning_actions`, `responding`, `stream_complete`).
     - `on_placeholder_response(text)` – early UX placeholder (suppressed in portal; status is used instead).
     - `on_stream_complete()` – marks the streaming phase as done.
   - Browser receives SSE events: `context_progress`, `status`, and `delta` chunks.

4. **Planner pass**
   - Once the streamed answer is available, a **second non-streaming call** is made (`run_planner_only`) to ask the model for:
     - Structured **actions** (create case, update customer, table aggregates, etc.).
     - Structured **extractions** (IDs, entities, metrics).
   - The result is merged into an `AiOrchestratorPlan`.

5. **Persistence & finalization**
   - The final AI message is sanitized and stored.
   - `turnPersisted` SSE event is sent with the final text and `message_id`.
   - Background thread executes any planned actions and stores extractions.
   - Once done, portal sends `actionsComplete` (or `actionsError`) events.

6. **Heartbeat**
   - A separate `GET /api/chat/events/` SSE stream keeps the portal updated about conversation-level status and liveliness (`statusChanged`, `heartbeat`).

---

## Sequence Overview (Diagram)

Conceptual sequence for a single turn using the MCP orchestrator:

```text
Browser
  ↓ POST /api/chat/stream/send (session_token, body)
Django: apps/api/chat_portal.stream_send
  ↓ ChatPortalService.append_message (persist customer message)
  ↓ ChatPortalService.get_conversation
  ↓ Decide orchestrator (MCP vs legacy)
  ↓ McpOrchestratorService.stream_turn(...)
       - Emits status: searching_knowledge / reading_document / responding
       - Streams answer deltas via on_response_text_delta
       - Calls MCP tools (search_knowledge, read_document, table_aggregate, actions)
       - Builds StreamingTurnContext (answer_text + tool_context)
  ↓ thread: finalize_stream_context(...)
       - McpOrchestratorService.run_planner_only(...)
       - Builds AiOrchestratorPlan (response_text, citations, planned_actions, extractions)
       - ChatPortalService.append_message (AI message with metadata)
       - ActionDispatcher.execute(...) in background
       - ChatPortalService.store_extractions(...)
  ↓ SSE writer (event_stream)
       - Sends: context_progress, status, delta, final (provisional),
               turnPersisted, actionsComplete/actionsError
Browser
  - Renders streaming answer, inline “searching/reading/planning” statuses,
    and final persisted message with actions summary.
```

You can follow this exactly in `stream_send` inside `pocketai_django/apps/api/chat_portal.py:409`.

---

## 1. Session Setup (Pre-Chat)

### 1.1 Resolve business + agent

- **Endpoint**: `GET /api/chat/portal/resolve/<business_slug>/<agent_slug>/`
- **View**: `resolve_portal_handle` in `apps/api/chat_portal.py`.
- **Service**: `ChatPortalService.resolve_handle` in `apps/conversations/portal.py`.

Flow:

1. Widget calls resolve with a friendly URL slug (e.g. `my-store/pocket-agent`).
2. Service looks up `BusinessProfile` by slug and ensures an `AgentProfile` exists and has a matching slug.
3. Response returns basic business/agent summary to render the portal header.

### 1.2 Bootstrap session

- **Endpoint**: `POST /api/chat/portal/sessions/`
- **View**: `bootstrap_session`.
- **Service**: `ChatPortalService.bootstrap_session`.

Flow:

1. Widget sends `{ business_slug, agent_slug, session_token? }`.
2. Service:
   - Re-uses an active conversation if `session_token` is valid and unexpired.
   - Otherwise creates a new `Conversation` with a fresh `session_token`.
   - Adds a welcome AI message if this is a brand new conversation.
3. Response:
   - `business` and `agent` summaries.
   - `session` snapshot (status, token, expiry).
   - Any existing `messages` (useful after refresh).

At this point, the frontend has what it needs to start streaming turns via `/chat/stream/send/`.

---

## 2. Handling a User Message (stream_send)

### 2.1 Entry point

- **Endpoint**: `POST /api/chat/stream/send/`
- **View**: `stream_send` in `apps/api/chat_portal.py:409`.

Input payload looks like:

```json
{
  "session_token": "<uuid-or-random-token>",
  "body": "User's question in natural language",
  "metadata": { "...optional per-turn flags..." }
}
```

### 2.2 Persisting the customer message

Inside `stream_send`:

1. Parse JSON body (`_parse_json_body`).
2. Call `ChatPortalService.append_message(...)` with:
   - `sender=ConversationSender.CUSTOMER`
   - `body` (trimmed)
   - `metadata` (includes things like identifier hints).
3. `append_message`:
   - Loads the active `Conversation` by `session_token`.
   - Creates a `ConversationMessage` row.
   - Extracts identifiers (email, phone, ID patterns) into conversation metadata.
   - Updates conversation timestamps and status.

If anything goes wrong (invalid token, empty body), `stream_send` returns simple HTTP error statuses (400/404) instead of opening a stream.

### 2.3 Loading conversation + agent

- `ChatPortalService.get_conversation(session_token=...)` returns the active `Conversation`.
- `conversation.agent_profile` is required; if missing, the view returns `500` because we cannot route to an orchestrator.

### 2.4 Orchestrator selection (MCP vs legacy)

In `stream_send`, there is a feature flag helper:

- `_business_prefers_mcp(business_profile)` inspects:
  - Business metadata: `metadata["mcp_orchestrator_enabled"]`.
  - Global setting: `RAG_USE_MCP_ORCHESTRATOR`.

If MCP is enabled:

- Import `McpOrchestratorService` from `apps.mcp.orchestrator`.
- Load MCP provider via `load_mcp_provider()` from `apps/llm/llm_provider.py`.
- Create `McpOrchestratorService(agent=agent, provider=provider)`.
- Log `orchestrator.selected` with `mode=mcp`.

Otherwise (legacy):

- Load default provider via `load_default_provider()`.
- Create `AiOrchestratorService(agent=agent, provider=provider)`.
- Log `orchestrator.selected` with `mode=legacy`.

Either way, an `ActionDispatcher(agent=agent)` is initialized for post-turn actions.

### 2.5 Streaming infrastructure inside stream_send

`stream_send` creates several internal queues and callbacks:

- **Queues & markers**
  - `stream_queue` + `stream_sentinel` – for outbound SSE events (`delta`, `status`, `context_progress`).
  - `finalize_queue` + `finalize_sentinel` – signals when planning/persistence is complete.
  - `actions_queue` + `actions_sentinel` – emits `actionsComplete` / `actionsError`.
  - `stream_complete` – a `threading.Event` that marks when streaming is done.
  - `plan_holder` – dict used as a small shared state bag between worker threads.

- **Callbacks passed into the orchestrator**
  - `on_response_text_delta(chunk: str)`
    - Pushes each response chunk into `stream_queue`.
  - `on_status_change(state)`
    - Accepts either a string or a dict (`{"code": ..., "label": ..., "meta": {...}}`).
    - Normalized into:
      - `status` events (general UX surface).
      - `context_progress` events for specific codes:
        - `"searching_knowledge"`
        - `"reading_document"`
        - `"planning_actions"`
        - `"responding"`
  - `on_placeholder_response(text)`
    - MCP orchestrator can send an early “placeholder” answer; the portal suppresses this and uses statuses for UX instead.
  - `on_stream_complete()`
    - Marks the stream as complete:
      - Sets `stream_complete`.
      - Enqueues a final `status` event with state `"complete"`.
      - Pushes `stream_sentinel` into `stream_queue`.

### 2.6 Worker thread: orchestrate()

The heavy lifting (LLM + RAG) runs in a background thread so the main request thread can focus on streaming SSE responses:

```python
context = orchestrator.stream_turn(
    conversation=conversation,
    user_message=body,
    on_response_text_delta=on_response_text_delta,
    on_status_change=on_status_change,
    on_placeholder_response=on_placeholder_response,
    on_stream_complete=signal_stream_complete,
)
```

`stream_turn` returns a `StreamingTurnContext` with:

- `response_text` – cleaned answer text.
- `streamed_chunks` – what was already streamed via `delta`.
- `tool_context` – carries:
  - `knowledge_results` / `knowledge_reads`.
  - `tool_trace` (search/read/aggregate calls and statuses).
  - `coverage_ledger` (what’s been read and summarized).
  - Identifier gate checks, ingestion warnings, etc.

As soon as we have the `StreamingTurnContext`, `stream_send` starts another background thread `finalize_stream_context(context)` to run planner + persistence.

### 2.7 SSE event loop: event_stream()

The Django response from `stream_send` is a `StreamingHttpResponse` that yields from `event_stream()`:

1. **Read from `stream_queue` until `stream_sentinel`**
   - If the item is a dict with `type="context_progress"`:
     - Emit:
       - `event: context_progress`
       - `data: {"state": "...", "label": "...", "meta": {...}}`
   - If `type="status"`:
     - Emit:
       - `event: status`
       - `data: {...}`
   - Otherwise, treat the item as a text chunk:
     - Emit:
       - `event: delta`
       - `data: {"text": "<chunk>" }`

2. **Produce a provisional `final` event**
   - Determine if we need the full context (`need_context_for_final`) – for example, if nothing was streamed from the provider.
   - Build a `provisional_payload` with:
     - `text` – best current answer (streamed text, or fallback to `context.response_text`).
     - `message_id: null`
     - `session_status` – conversation status at this moment.
     - `pending: true`
   - Emit:
     - `event: final`
     - `data: provisional_payload`

3. **Wait for planner + persistence to finish**
   - Block on `finalize_queue.get()` (signaled by `finalize_stream_context`).
   - Retrieve `AiOrchestratorPlan` and final payload from `plan_holder`.

4. **Emit persisted result**
   - If everything worked:
     - Emit:
       - `event: turnPersisted`
       - `data: { "text": "<persisted-or-streamed-text>", "message_id": "<uuid>", "session_status": "...", "pending": false, ... }`
   - As `actions` finish in the background, read from `actions_queue`:
     - `type="actionsComplete"`:
       - `event: actionsComplete`
       - `data: { "message_id": "<uuid>", "actions": [ ... ], "label": "Follow-up tasks completed." }`
     - `type="actionsError"`:
       - `event: actionsError`
       - `data: { "message_id": "<uuid>", "error": "..." }`

This gives the portal a **multi-phase UX**:

- Live deltas for text.
- Progress badges for searching/reading/planning.
- A provisional “final” answer.
- A final, persisted answer with a stable `message_id`.
- Later updates once backend actions complete.

---

## 3. MCP Orchestrator Internals (stream_turn)

Although `stream_send` abstracts it away, most of the “AI magic” lives inside `McpOrchestratorService.stream_turn` in `apps/mcp/orchestrator.py`.

### 3.1 Transcript and tool context

On entry:

1. Build a `ToolExecutionContext` object to accumulate:
   - `knowledge_results` – snippets from search/read/aggregate tools.
   - `knowledge_reads` – which pages/rows were actually read.
   - `tool_trace` – per-tool call diagnostics.
   - `coverage_ledger` – high-level view of what knowledge parts were covered.
   - `identifier_*` – inputs/decisions from identity gating (email, customer IDs, etc.).
   - `ingestion_warnings` – flags about partial or risky data.
2. Build a **chat transcript** with:
   - System message(s) based on agent configuration and business context.
   - Conversation history (customer + AI messages).
   - The current user message.

### 3.2 First streaming pass (tools-enabled)

`stream_turn` makes a first call to the MCP provider:

```python
first_payload = provider.chat(
    transcript,
    tools=self.tool_definitions,
    on_stream_delta=_first_stream_chunk,
)
```

Behavior:

- The provider:
  - Streams early answer content via `_first_stream_chunk`.
  - Optionally outputs `tool_calls` in the assistant message.
- The orchestrator:
  - Buffers streamed tokens and filters out “investigative filler” sentences (e.g., “Let me check that for you...”) using `sanitize_with_diagnostics`.
  - If **no tool calls** are present:
    - We are in a **single-pass** scenario:
      - The streamed answer becomes the final answer.
      - `on_status_change({"code": "responding"})` is emitted.
      - Streaming completes and a `StreamingTurnContext` is returned directly.
  - If **tool calls are present**:
    - We enter the **tool loop** before the final answer.

### 3.3 Tool loop (search/read/aggregate + actions)

For each `tool_call` in the assistant message:

1. Extract:
   - `tool_name` – e.g., `"search_knowledge"`, `"read_document"`, `"table_aggregate"`, `"create_case"`, etc.
   - `arguments` – JSON payload for the tool.
2. UX status updates:
   - If the tool is a knowledge tool:
     - `search_knowledge`:
       - `on_status_change({"code": "searching_knowledge", "label": "Searching: <query preview>"})`.
     - `read_document`:
       - `on_status_change({"code": "reading_document", "label": "Reading: <short upload id or label>"})`.
3. Duplicate search short-circuit:
   - `_short_circuit_duplicate_search(...)` checks if an identical query was already run in this turn.
   - If so, it reuses cached results instead of hitting the DB/vector index again.
4. Execute tool:
   - `tools.execute_tool(tool_name, arguments, conversation=conversation, context=tool_context)`.
   - See [Sub-scenarios](#4-sub-scenarios-search-read-aggregate) below for details.
5. Record diagnostics:
   - Append an entry to `tool_context.tool_trace` summarizing:
     - Tool name, arguments, status, error_code, hint, mode, page, token_budget, throttle flags.
   - If this is a knowledge tool, call `_record_knowledge_outputs` to:
     - Add snippet payloads to `knowledge_results`.
     - Update `coverage_ledger`.
     - Track table aggregate rows and suppress redundant previews when needed.

After each round of tool calls:

- The orchestrator asks the model again with an updated transcript (includes tool results) and tools enabled.
- If the new assistant message includes more tool calls, repeat the loop.
- Otherwise, once no more tool calls are needed:
  - The tool loop ends.
  - The orchestrator starts the **final answer streaming** pass.

### 3.4 Final answer streaming pass

In the tool-based path:

- The orchestrator:
  - Resets streaming buffers.
  - Uses `_answer_stream_chunk` to stream sentences while still filtering filler.
  - Emits deltas via `on_response_text_delta` (which end up as `delta` SSE events).
  - Calls `on_status_change({"code": "responding"})` once the model is clearly answering.
  - Eventually calls `on_stream_complete` when all text is streamed.

The resulting cleaned answer text and accumulated tool context are wrapped into a `StreamingTurnContext` and returned to `stream_send`.

---

## 4. Sub-scenarios: Search, Read, Aggregate

This section zooms into the MCP tools used for RAG workflows.

### 4.1 Scenario A – Search-only answer (`search_knowledge`)

Use case:

- Visitor asks a question that can be answered from short knowledge snippets:
  - “What are the fees for the Platinum card?”
  - “Do you support installment payments?”

Flow:

1. Model emits a `tool_call` for `search_knowledge` with:
   - `query` – natural language question (often normalized by prompt).
   - `limit` – optional cap on snippet count.
2. `_search_knowledge_handler` in `apps/mcp/tools.py`:
   - Analyzes query intent (identifier vs free-text vs table-ish).
   - Derives whether aggregation-like terms are present (`total`, `sum`, `aggregate`, etc.).
   - Optionally builds an `identifier_filter` when email/ID is locked or provided:
     - Looks up `IdentifierColumnMapping` rows for the business.
     - Restricts search to specific uploads or identifier columns.
   - Calls `KnowledgeSearchService.search(...)` in `apps/rag/ai_orchestrator.py` with:
     - `business_profile`.
     - `query`.
     - `limit`.
     - `identifier_filter` (if applicable).
3. `KnowledgeSearchService.search`:
   - Normalizes query via `QueryNormalizer`:
     - Tokenization, filler-word filtering, alias candidates, identifier-likeness.
   - Runs alias-based search (e.g., ID, product code).
   - Runs hybrid vector + lexical search (pgvector + `TrigramSimilarity`).
   - Optionally uses cross-encoder reranking if configured.
   - Applies per-business limits, thresholds, and caching:
     - Session-level and business-level result caches.
   - Returns `KnowledgeSearchResult` with:
     - `snippets` – each snippet describes a chunk:
       - `id`, `upload_id`, `chunk_id`, `title`, `summary`, `content`, `page_number`, `page_mode`, etc.
     - `status` – `"ok"`, `"not_found"`, `"fallback"`, etc.
     - `diagnostics` – search route, alias hits, duration, snippet counts, etc.
4. `_search_knowledge_handler` post-processing:
   - Serializes snippets into a model-friendly payload (public label, coverage hints, `read_hint`).
   - Marks whether **full reading** is recommended (`read_required`) based on snippet-only sufficiency signals:
     - Summary/preview state plus evidence of incomplete context (truncation, partial tables).
   - Adds ingestion warnings by inspecting issues/diagnostics.
   - Updates identifier guardrails via `IdentifierRegistryService.record_event`.
5. The orchestrator:
   - Records snippets to `tool_context.knowledge_results` and `knowledge_reads`.
   - Emits appropriate `searching_knowledge` / `reading_document` statuses if follow-up reads are triggered.
   - Uses snippet summaries to bias the final answer.

From the portal’s POV, this scenario looks like:

- Quick “Searching knowledge…” badge.
- Short streaming answer referencing a handful of snippets (citations in metadata).
- No additional page reads or aggregation steps.

### 4.2 Scenario B – Deep page read (`read_document`)

Use case:

- Visitor’s question requires **full page context**:
  - Long policy explanation.
  - Legal fine print.
  - Complex eligibility matrix for a product.

Typical trigger:

- The model decides it needs more evidence from a summary/preview snippet (often flagged by `read_required: true`) and uses the provided `read_hint`:
  - `document_id` – typically `upload_id` or `chunk_id`.
  - `page` – page index.
  - `mode` – `"excerpt"` vs `"full_page"`.

Flow:

1. Model emits a `tool_call` for `read_document`:
   - `document_id` – chunk or upload UUID.
   - Optional:
     - `page` / `offset`.
     - `mode` (`"excerpt"` | `"full_page"`).
     - `token_budget`.
     - `chunk_neighbor` window.
2. `_read_document_handler` in `apps/mcp/tools.py`:
   - Validates `document_id`.
   - Resolves to:
     - A `KnowledgeUploadChunk` (chunk-level read) or
     - A `KnowledgeUpload` (upload-level read).
   - Applies **identifier gating** via `_identifier_guard`:
     - If sensitive (e.g., account statements) and identifiers are missing/mismatched:
       - Returns an error payload with:
         - `status`: `"identifier_required"` or similar.
         - `identifier_gate` snapshot (required/provided keys, hint).
       - The model is expected to **ask the user** for missing identifiers instead of guessing.
   - Reserves per-turn budgets:
     - `reserve_chunk_reads(1)` – count read_document calls.
     - `reserve_chunk_pages(1)` – number of pages touched.
3. Mode and throttling:
   - If `mode` is not explicitly set:
     - `_detect_full_page_intent` decides between `"excerpt"` and `"full_page"`:
       - Table-heavy questions and identifier-driven queries lean towards `"full_page"`.
   - Before reading a full page, `_maybe_throttle_full_page` checks:
     - Business-level budgets.
     - Recent page read costs.
   - If throttled:
     - Mode is downgraded to `"excerpt"`.
     - Throttle notice and diagnostics are recorded.
4. Page window load:
   - Calls `KnowledgeSearchService.load_page_window(...)` with:
     - `business_profile`.
     - `upload_id` or `chunk_id`.
     - `page_index`.
     - `neighbor` window (e.g., ±1 page).
     - `mode` and `token_budget`.
   - Returns a window of chunks representing:
     - Main page.
     - Neighbors (for context).
   - Snippets are serialized and filtered by locked identifier when applicable.
5. Tool result payload includes:
   - `snippets` – page-level text + structured table hints.
   - `knowledge_reads` – simple entries (`label`, `page`, `mode`).
   - `identifier_gate` / `required_identifiers` when reads are blocked.
   - Any throttle notices in `throttle_notice`.
6. Orchestrator:
   - Adds snippets to `tool_context.knowledge_results`, `knowledge_reads`.
   - Emits `reading_document` status:
     - First based on `document_id`.
     - Then refined using snippet `title` / `public_label` when available.

From the portal’s POV, this scenario looks like:

- “Searching knowledge…” → “Reading document …” badges.
- Slightly longer latency before coherent deltas start.
- Answer text that includes **full paragraphs** from relevant policy pages.

### 4.3 Scenario C – Table aggregation (`table_aggregate`)

Use case:

- Visitor asks for a **numeric summary** over a tabular knowledge source:
  - “What is the total monthly salary for all senior engineers?”
  - “What is the total yearly fee across tiers A, B, and C?”
  - “Sum the total insurance premiums for gold plans.”

Trigger signals:

- Aggregation-ish tokens in the user query:
  - `total`, `sum`, `aggregate`, `overall`, and Arabic equivalents.
- `KnowledgeSearchService` detects table-intent via `table_query_keywords`:
  - `table`, `sheet`, `excel`, `csv`, `grid`, etc.
- `search_knowledge` surfaces snippets with table metadata and suggests aggregation.

Flow:

1. Model emits a `tool_call` for `table_aggregate`:
   - `document_id` – the upload containing the table.
   - `mode` – e.g., `"row_total"` or `"column_sum"`.
   - `query` – optional natural-language filter.
   - `match_column` / `match_value` – what rows to filter.
   - `value_column` – which numeric column to aggregate.
   - `columns` – optional subset of columns to include in the preview.
   - `sheet_name` – when the upload has multiple sheets.
2. `_table_aggregate_handler` in `apps/mcp/tools.py`:
   - Resolves upload and uses the knowledge layer’s structured exports:
     - Rows, cells, normalized column names, numeric hints.
   - Optionally hydrates a **table cache** from conversation metadata (previous aggregates).
   - Filters rows by:
     - `match_column` / `match_value` (case/normalization aware).
     - Or `query` matched against row/cell text.
   - For each matching row:
     - Determines numeric contributions per column.
     - Distinguishes total columns vs ordinary numeric columns.
     - Computes per-row totals and contributions.
   - Aggregates across rows:
     - Sums numeric values into a `total_value`.
     - Collects `matched_rows` with preview cells and contribution details.
3. Result payload:

```json
{
  "tool": "table_aggregate",
  "status": "ok" | "not_found",
  "document_id": "<upload uuid>",
  "mode": "row_total" | "...",
  "query": "original or normalized query",
  "match_column": "optional",
  "match_value": "optional",
  "columns": ["..."],
  "match_count": 3,
  "total": 12345.67,
  "display_total": "12,345.67",
  "rows": [ { "row_index": ..., "row_total": ..., "cells": [...] }, ... ],
  "snippets": [ ... ],
  "duration_ms": 42,
  "evaluated_rows": 27,
  "row_limit": 10,
  "cache_hit": false,
  "hint": "No matching rows found."
}
```

4. `_build_table_aggregate_snippet` converts matched rows into human-readable snippets:
   - `summary` – e.g., `"Table row – total 12,345.67"`.
   - Lines for total columns and top contributing columns.
   - `structured_tables` payload for precise details.
   - `source_diagnostics.table_aggregate = true` with row/table indexes.
5. Orchestrator’s `_record_knowledge_outputs`:
   - Adds these snippets to `knowledge_results` and `coverage_ledger`.
   - Stores compact table aggregate rows in `tool_context.table_aggregate_rows`.
   - Calls `_suppress_table_previews` to hide other noisy table snippets from prompts.

From the portal’s POV:

- You might see:
  - “Searching knowledge…” → “Reading document…” → “Responding…”.
  - Answer text that **summarizes totals** and points to specific rows.
  - In metadata, `citations` and aggregate diagnostics (e.g., table row and upload IDs).

---

## 5. Planner & Post-actions

After streaming is done, `finalize_stream_context` (in `stream_send`) handles **planner** and **actions**.

### 5.1 Planner-only pass (`run_planner_only`)

In `finalize_stream_context`:

1. Call `orchestrator.run_planner_only(...)` with:
   - `conversation`.
   - `user_message`.
   - `answer_text` (from `StreamingTurnContext`).
   - `tool_context` (knowledge results, coverage, identifier diagnostics, etc.).
2. MCP orchestrator’s `_run_planner`:
   - Emits `planning_actions` status via `on_status_change`.
   - Builds a planner transcript with:
     - User message.
     - Final answer text.
     - A **tool-context note** summarizing:
       - Knowledge reads.
       - Constraint/throttle errors.
       - Coverage ledger.
       - Ingestion warnings.
   - Calls provider with a **JSON-schema response format** (`_final_response_schema`).
   - The model returns a payload like:

```json
{
  "response_text": "same answer text or refined",
  "actions": [
    {
      "action": "create_case",
      "payload": { "...case fields..." }
    }
  ],
  "extractions": [
    {
      "type": "CUSTOMER",
      "payload": { "...structured data..." }
    }
  ]
}
```

3. `_merge_planner_into_assistant`:
   - Combines streamed answer content with planner `actions` and `extractions`.
4. `_build_plan_from_assistant`:
   - Produces an `AiOrchestratorPlan` with:
     - `response_text` – final answer (kept aligned with streamed content).
     - `planned_actions` – list of `PlannedAction` enums + payloads.
     - `extractions` – structured entities.
     - `citations` – built from `tool_context.knowledge_results`.
     - `diagnostics` – knowledge/tool identifiers, sanitized fillers, identifier gate snapshots, etc.
     - `ingestion_warnings` – from tool context.

If the planner provider fails, `run_planner_only` gracefully falls back to a plan that only includes the streamed answer and citations.

### 5.2 Persisting the AI message

In `finalize_stream_context` (inside `stream_send`):

1. Choose the text to persist:
   - Prefer `plan.response_text`.
   - Fallback to concatenated `streamed_chunks`.
   - Fallback to `(no content)` if empty.
2. Sanitize via `sanitize_with_diagnostics`:
   - Removes any unsafe content.
   - Optionally annotates `answer_confidence`.
3. Build AI message metadata:
   - `citations` – titles of knowledge snippets.
   - `actions` – serialized queued actions from `plan.planned_actions`.
   - `diagnostics` – planner/orchestrator diagnostics.
   - `answer_confidence` – when available.
   - `ingestion_warnings` – normalized warnings.
4. Persist message:
   - Call `ChatPortalService.append_message(..., sender=ConversationSender.AI, ...)`.
   - Get latest session state via `get_session_state`.
5. Store final payload in `plan_holder`:

```python
final_payload = {
    "text": response_text,
    "message_id": str(ai_message.id),
    "session_status": session_state.status,
    "answer_confidence": ...,
    "ingestion_warnings": [...],
}
```

This payload becomes the body of the `turnPersisted` SSE event.

### 5.3 Background actions & extractions

If there are any `planned_actions` or `extractions`, `finalize_stream_context` starts a `run_post_actions` thread:

1. Execute actions:
   - `ActionDispatcher.execute(conversation, planned_actions=plan.planned_actions)`:
     - Maps MCP `ActionType` enums to service calls:
       - `CREATE_CASE`, `UPDATE_CASE_STATUS`, `ADD_CASE_HISTORY`.
       - `CREATE_CUSTOMER`, `UPDATE_CUSTOMER`.
       - `CREATE_LEAD`, `CREATE_APPOINTMENT`.
       - `FLAG_ESCALATION`.
   - Logs results and pushes them to `actions_queue` as `actionsComplete` events once done.
2. Store extractions:
   - `ChatPortalService.store_extractions(session_token=session_token, items=...)`:
     - Writes `ConversationExtraction` rows for each extracted entity.
3. Update AI message metadata with executed action results:
   - Replaces queued `actions` with **actual results** (status/error per action).
   - Calls `ChatPortalService.update_message(...)` to persist the new metadata.
4. Error handling:
   - Any exceptions are logged.
   - `actionsError` event is enqueued with an error message.

From the portal’s perspective:

- The user sees the final answer quickly.
- Actions (e.g., case creation) may complete a little later, with a subtle “follow-up tasks completed” notification.

---

## 6. Streaming Events Cheat Sheet

Events emitted by `stream_send`’s SSE stream:

- `context_progress`
  - Shape: `{"state": "searching_knowledge" | "reading_document" | "planning_actions" | "responding", "label": "...", "meta": {...}}`
  - Used for contextual progress indicators (e.g., “Searching knowledge…” badge).

- `status`
  - Shape: `{"state": "searching_knowledge" | "reading_document" | "planning_actions" | "responding" | "complete", "label": "...", "meta": {...}}`
  - Generic status events; useful for logs and UI badges.

- `delta`
  - Shape: `{"text": "<partial answer chunk>"}` (stringified JSON).
  - Incremental answer text; streamed as soon as the provider yields content.

- `final`
  - Shape: `{"text": "<best answer so far>", "message_id": null, "session_status": "...", "pending": true}`
  - “Best guess” answer **before** storage and post-actions.

- `turnPersisted`
  - Shape: `{"text": "<final persisted answer>", "message_id": "<uuid>", "session_status": "...", "pending": false, ...}`
  - Stable point for local caching, transcripts, and feedback (CSAT, thumbs up/down).

- `actionsComplete`
  - Shape: `{"message_id": "<uuid>", "actions": [ ... ], "label": "Follow-up tasks completed."}`
  - Indicates background actions have finished.

- `actionsError`
  - Shape: `{"message_id": "<uuid>", "error": "<error text>" }`
  - Indicates something went wrong while running actions.

Separate **heartbeat** stream from `/api/chat/events/`:

- `statusChanged`
  - One-shot event with `{ "status": "<conversation status>" }` on connect.
- `heartbeat`
  - Empty `{}` payload every ~15 seconds.

---

## 7. File Map for Vibe-Coding

If you want to trace or modify this flow, these are the “anchor” files:

- API & portal:
  - `pocketai_django/apps/api/urls.py`
  - `pocketai_django/apps/api/chat_portal.py:409` (`stream_send`)
  - `pocketai_django/apps/services/chat_portal.py` (`ChatPortalService`)

- Orchestrators:
  - `pocketai_django/apps/mcp/orchestrator.py` (`McpOrchestratorService`)
  - `pocketai_django/apps/rag/ai_orchestrator.py` (`AiOrchestratorService`, `StreamingTurnContext`, `KnowledgeSearchService`, `ActionDispatcher`, `AiOrchestratorPlan`)

- MCP tools & prompts:
  - `pocketai_django/apps/mcp/tools.py` (`search_knowledge`, `read_document`, internal table helpers, action tools)
  - `pocketai_django/apps/mcp/prompts.py`

- LLM providers:
  - `pocketai_django/apps/llm/llm_provider.py` (`OpenAIChatProvider`, `DeepSeekChatProvider`, `load_default_provider`, `load_mcp_provider`)

- Observability:
  - `pocketai_django/apps/api/chat_portal.py` (`PortalTraceLogger`)
  - `pocketai_django/apps/rag/rag_logging.py`

Open these in order and you’ll see the **full backend conversation loop** from HTTP request to vector search, document reads, table aggregation, LLM planning, and final SSE response.
