Report 1
========

Latency Contributors
--------------------
- `apps/services/chat_portal.py:274-285`: `_get_active_conversation_by_token` always selected the conversation with eager message prefetching, so every request reloaded the full transcript even when only metadata was needed.
- `apps/api/chat_portal.py:423-660`: `stream_send` re-called portal service methods that each rehydrated the same conversation, adding multiple ORM hits per turn.
- `apps/api/chat_portal.py:600-608` & `apps/services/mcp/orchestrator.py:1046-1114`: every streamed reply triggered a second non-streaming `run_planner_only`, doubling LLM latency regardless of whether actions/extractions were required.
- `apps/services/ai_orchestrator.py:3207-3738`: `load_contents` and `_serialize_structured_tables_with_rows` pulled entire tables/rows/cells into memory, making document reads scale with table size rather than the small preview actually surfaced.
- `apps/services/mcp/tools.py:955-1183` plus identifier registry helpers: each knowledge/read call re-queried identifier mappings and recorded duplicate identifier events, adding extra SQL round trips on identifier-locked conversations.

Fixes Implemented
-----------------
- Added `_conversation_queryset` and `_resolve_conversation` helpers so hot-path service methods accept an already-loaded `Conversation`, avoiding redundant message-prefetch work (`apps/services/chat_portal.py`).
- Updated `stream_send` to reuse the active conversation for persistence, session lookups, metadata updates, and extraction storage, eliminating duplicate service fetches (`apps/api/chat_portal.py`).
- Reduced RAG ingestion overhead by trimming table prefetching and limiting row/column sampling to what the UI needs (`apps/services/ai_orchestrator.py`).
- Introduced per-turn identifier mapping caches and event deduplication so MCP tools stop re-running the same queries/logs when guardrails remain unchanged (`apps/services/mcp/tools.py`, `apps/services/mcp/types.py`).

Report 2
==========

- Dual LLM passes (`apps/api/chat_portal.py:588-666`, `apps/services/mcp/orchestrator.py:1023-1116`) keep the “turnPersisted” event blocked on `run_planner_only`. *Fix taken:* deferred work still outstanding; no change yet.
- Final SSE payload waits for sanitize/persist/action dispatch (`apps/api/chat_portal.py:588-754`, `apps/api/chat_portal.py:930-982`). *Fix taken:* deferred; not yet implemented.
- MCP `search_knowledge` lacks per-session cache so identical queries repeat ANN + rerank (`apps/services/mcp/tools.py:998-1290`). *Fix taken:* Report 2 adds bounded caches via `_search_cache_key` stored on `ToolExecutionContext` to reuse payloads where possible.
- `read_document` reloads identical windows repeatedly (`apps/services/mcp/tools.py:1271-1541`). *Fix taken:* Report 2 introduces `_read_cache_key` caching to reuse snippets before reissuing DB reads.
- `table_aggregate` materializes entire tables before filtering (`apps/services/mcp/tools.py:1741-2105`). *Fix taken:* Report 2 adds parameter-aware cache keys and queryset filters so only relevant rows/cells load.
- Heartbeat/event loop busy waits (0.1s polling) hog Django worker (`apps/api/chat_portal.py:820-878`). *Fix taken:* Report 2 increases queue timeout and breaks once stream completes to reduce idle spin.
- Background heartbeat endpoint sleeps in-thread (`apps/api/chat_portal.py:1016-1038`). *Fix taken:* still pending.

Report 3
========

- `apps/api/chat_portal.py:912-980` keeps the SSE stream waiting for `finalize_stream_context` (planner run + DB writes) before emitting `turnPersisted`, so users sit on planner latency even after deltas finish. *Fix taken:* pending; planner/finalization still synchronous.
- `apps/api/chat_portal.py:600-609` always invokes `run_planner_only` even when the streaming turn produced no tool trace or backend work, doubling LLM latency for simple Q&A. *Fix taken:* pending; planner fast-path still to be added.
- `apps/api/chat_portal.py:412-444` previously fetched the conversation twice (once in `append_message`, again in `get_conversation`). *Fix taken:* ✅ `stream_send` now loads the conversation once (without message prefetch) and reuses it when persisting the customer message.
- `apps/services/llm_provider.py:1695-1735` (via `load_mcp_provider`) instantiates a new provider per request, paying httpx/client setup costs every turn. *Fix taken:* pending; provider memoization not yet added.
- `apps/services/mcp/tools.py:1650-2148` (`table_aggregate`) still materializes many rows/cells per call, keeping GIL-bound loops in the request thread. *Fix taken:* pending; Report 2’s caching only reduces repeats, not the per-call load.
- `apps/services/mcp/tools.py:1650-1714` (`list_tables`) prefetches every table for each upload before slicing, which grows linearly with tenant data. *Fix taken:* pending; needs slimmed queries/pagination.

Report 4
========

Latency Contributors
--------------------
- `apps/api/chat_portal.py:602-609` & `apps/services/mcp/orchestrator.py:1032-1108`: the planner pass ran synchronously for every turn, guaranteeing a second LLM call even for small-talk turns. *Fix taken:* planner now runs in `run_planner_async`, queued after persistence so the stream can finish immediately.
- `apps/api/chat_portal.py:898-981`: SSE `turnPersisted` waited on `finalize_queue.get()` (planner + sanitization + persistence), so the user never saw completion until all back-office work finished. *Fix taken:* the portal now persists/sanitizes up front and emits `turnPersisted` immediately, then ships `turnUpdated` later if planner metadata changes.
- `apps/api/chat_portal.py:895-929`: when the provider yields no deltas, the server blocks on `worker.join()` before streaming the provisional final payload, so cold starts still stall. *Fix taken:* pending; still joins before replaying buffered text when no deltas arrive.
- `apps/services/llm_provider.py:1695-1738`: `load_mcp_provider` instantiated a new tools provider every request, paying HTTP client setup and prompt warm-up per turn. *Fix taken:* `_MCP_PROVIDER_SINGLETON` memoizes the provider under a lock so workers reuse the same client.
- `apps/services/mcp/tools.py:1628-2171`: `table_aggregate` loads `row_limit*4` rows and walks every cell in Python before filtering, making each aggregation CPU-heavy. *Fix taken:* pending; still materializes large datasets per call.
- `apps/services/mcp/tools.py:1628-1712`: `list_tables` prefetches every table for each upload before slicing, so tenants with many tables see query time spike. *Fix taken:* pending; still needs paginated fetches/lightweight previews.
- `apps/services/mcp/orchestrator.py:1370-1406`: table-cache metadata writes run synchronously every turn even when unchanged, holding a DB write lock on the conversation. *Fix taken:* pending; cache persistence still happens inline.
- `apps/services/mcp/tools.py:1058-1173`: identifier guardrail mapping queries rerun on every `search_knowledge` with the same identifiers, adding ORM overhead to each turn. *Fix taken:* pending; caching exists per turn but not across requests.

Fixes Implemented
-----------------
- Planner/finalizer split: `apps/api/chat_portal.py` now persists the assistant message immediately and defers `run_planner_only` to `run_planner_async`, which pushes status/action events without blocking the stream.
- SSE completion: `event_stream` emits `turnPersisted` right after persistence, while a new `turnUpdated` event notifies clients when the async planner or action dispatcher updates metadata.
- Provider reuse: `apps/services/llm_provider.py` memoizes the MCP provider instance (DeepSeek/OpenAI), avoiding repetitive HTTP client instantiation and reducing per-turn startup latency.

Report 5
========

Latency Contributors
--------------------
- `apps/services/mcp/tools.py:1741-2220` & `var/logs/deepseek_calls.log:1863-1890`: `_table_row_cache_key` hashed raw `match_column_input`, `match_values`, `sheet_name`, and `query` strings. When DeepSeek iterated on the same filter ("Net Sales", "net sales", "net_sales", etc.) each spelling/casing tweak became a new fingerprint, so `_load_table_rows_for_cache` reran the heavy ORM query six times in one turn (log window 22:18:37-22:19:39 EET). Those duplicated payloads inflated the tool-loop prompt from ~17 k to 37 k tokens per retry and stretched wall time from ~0:50 to ~1:25 before the final answer streamed.

Fixes Implemented
-----------------
- `_table_row_cache_key` now canonicalizes every input before hashing—`match_column`/`match_values` reuse `_normalize_column_name`, while `sheet_name` and `query` are trimmed + lowercased. `_table_aggregate_handler` feeds those canonical strings into the key so “Net Sales” vs “net sales” vs “net   sales” reuse the same cached payload. After the first `table_aggregate` hydrates rows, later retries hit the cache and skip redundant DB loads, restoring table turns to the prior ~50 s baseline without changing functionality.
Report 6
========

Latency Contributors
--------------------
- `apps/api/chat_portal.py:260-320`: The planner pass still triggered for every turn unless the exact low-intent regex matched, so short, tool-free answers paid for a second MCP call and planner warm-up. No per-session memory meant consecutive small-talk turns repeated the same latency spike.
- `apps/api/chat_portal.py:720-1181`: Streaming UX stayed silent until full sentences arrived or the worker joined; placeholders were suppressed entirely and `turnPersisted` waited for planner metadata, so visitors stared at spinners whenever the model paused mid-sentence.
- `apps/services/mcp/orchestrator.py:115-814`: Each turn rebuilt tool caches from scratch. Identical `search_knowledge` calls reran ANN + rerank, identifier mappings hit the ORM again, and table row hydration repeated across turns. Streaming also buffered until sentence boundaries, so deltas stalled on long clauses.
- `apps/services/mcp/tools.py:1655-2249`: `list_tables` fetched every table per upload before slicing, and `table_aggregate` still materialized large row sets, making table-heavy tenants wait seconds even when repeating the same filters.
- `apps/services/mcp/prompts.py:200-249`: Cached table snippets injected full contributor payloads into every prompt, inflating token counts and slowing LLM responses without adding new evidence once the cache warmed.

Fixes Implemented
-----------------
- Added `_is_low_volume_stream` heuristics, per-session planner skip guards, and tracing so small, tool-free replies bypass `run_planner_only` until the visitor issues a substantive request.
- Surfaced sanitized placeholder deltas, enforced partial flushes every ~0.4s/96 chars, and emitted provisional `turnPersisted` events immediately; final metadata now arrives via `turnUpdated`, keeping the bubble responsive.
- Introduced session-scoped caches for search results, identifier mappings, table rows, and `list_tables` data, hydrated at turn start and persisted afterward; streaming flush logic was updated to use these caches and keep deltas flowing.
- Reworked `list_tables` to use `values()` + targeted table pulls, added bounded caches to `table_aggregate`, and limited row hydration per session so table tools reuse ORM work instead of reloading sheets each call.
- Trimmed prompt injections to compact table summaries (row label + total + top contributors), reducing prompt size and improving response latency without losing determinism.
- Rebuilt the streaming state machine (docs/portal_stream_state.md, apps/api/chat_portal.py, frontend/static/js/chat-portal.js) so SSE emits `turnPending`/`turnPersisted`/`turnUpdated`, spinner text reflects sanitized `placeholder_thinking`, and planner metadata flows asynchronously—cutting visible idle time without dropping accuracy.
