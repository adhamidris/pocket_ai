# Investigation Report: Django MCP Flow & Latency

## 1. Overview of the New MCP Flow
The new MCP flow is orchestrated by `McpOrchestratorService` in `apps/services/mcp/orchestrator.py` and differs significantly from the legacy flow.

*   **Entry Point**: `ChatPortalService.stream_send` initiates `orchestrator.stream_turn` in a background thread.
*   **Execution Loop (`_execute_turn`)**:
    1.  **Initial Pass**: Calls the LLM (DeepSeek/OpenAI) with tools enabled. Streams text *if* no tools are called.
    2.  **Tool Loop**: If tools are requested, it enters a loop (max 10 iterations). It executes tools (`execute_tool`) and feeds results back to the LLM.
    3.  **Final Answer**: Once tools are done, it calls the LLM one last time (tools disabled) to generate the final response, which is streamed to the user.
*   **Streaming**: Text deltas are streamed directly from the LLM provider to the user via SSE (`event_stream`).
*   **Planner**: A separate, asynchronous pass (`run_planner_async`) runs *after* the turn is persisted to generate structured data (actions, extractions) without blocking the user-facing stream.

## 2. Latency Investigation (Jaeger Perspective)

Based on the code analysis and the provided Jaeger report references, here are the key latency contributors in the new flow:

### A. `table_aggregate` Tool (High Latency)
The `table_aggregate` tool is a significant bottleneck for table-heavy queries.
*   **Location**: `apps/services/mcp/tools.py` (`_table_aggregate_handler`)
*   **Mechanism**:
    1.  **Fetching**: It fetches rows using `KnowledgeUploadTableRow.objects.filter(...)` with `Prefetch` for cells. It limits the fetch to `row_limit * overscan_factor` (approx. 100-150 rows).
    2.  **Processing**: It iterates through these rows in **Python**, performing filtering (`_row_matches_sheet_hint`), cell mapping, and numeric parsing.
    3.  **Impact**: For large documents, hydrating ~150 complex row objects with all their cells and processing them in Python is CPU-bound and slow.
    4.  **Fix Verification**: The "canonical cache key" fix (Report 5) is present (`_table_row_cache_key`), which helps *repeated* identical queries, but the *first* un-cached aggregation is still expensive.

### B. Synchronous Database Writes
*   **Location**: `McpOrchestratorService._execute_turn` -> `_persist_table_cache`
*   **Mechanism**: At the very end of `_execute_turn`, the system writes the updated table cache back to `conversation.metadata` and saves the conversation.
*   **Impact**: This write happens inline, blocking the final completion of the stream request. If the metadata is large, this serialization and DB write adds perceptible latency.

### C. Multi-Turn LLM Overhead
*   **Mechanism**: The MCP loop involves sequential network round-trips to the LLM:
    `Initial LLM Call` -> `Tool Execution` -> `Second LLM Call` -> ... -> `Final Answer LLM Call`.
*   **Impact**: Each "hop" adds full network latency + token generation time. If DeepSeek "chatters" (narrates its steps) or requires multiple tool steps, latency stacks linearly.

## 3. "Filler" Leakage ("I'll search...")

The user reported "I'll search..." fillers leaking through. This was successfully reproduced and traced to the sanitizer configuration.

*   **Root Cause**: The sanitizer (`apps/services/mcp/sanitizer.py`) has a `filter_level`.
    *   `friendly` (Default): **ALLOWS** conversational fillers like "I'll search for that", "Let me check". It only filters "hard" robot-like traces (e.g., `tool call`).
    *   `professional`: **FILTERS** these phrases.
*   **Current State**: `McpOrchestratorService` defaults to `filter_level="friendly"` (derived from `agent.tone`).
*   **Result**: Since DeepSeek often outputs these conversational fillers, and the default filter level explicitly permits them, they are streamed to the user.

## 4. Recommendations

1.  **Fix Filler Leakage**:
    *   **Immediate**: Change the default filter level to `professional` for the MCP orchestrator, OR update `is_investigative_filler_with_level` to treat "investigative" phrases as filtered even in "friendly" mode (since they represent internal system state, not friendly chatter).

2.  **Optimize `table_aggregate`**:
    *   Push filtering down to the database level (Django ORM) where possible, instead of fetching 150 rows and filtering in Python.

3.  **Async Cache Persistence**:
    *   Move `_persist_table_cache` to a background task (like the planner) so it doesn't block the user-facing stream completion.