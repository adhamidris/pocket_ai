# Load & Cost Controls (MCP / RAG)

This project enforces server-side budgets and rate limits to protect multi-tenant latency and cost, and provides a lightweight management command to load-test the retrieval layer without involving an LLM provider.

## Quick Load Test

Run a basic concurrent test against MCP tools:

```bash
python manage.py run_mcp_load_test --business-id <uuid> --mode search --iterations 200 --concurrency 8 --queries "invoice 9125779195" "refund policy"
```

By default the command exports a JSON artifact to `var/logs/mcp_load_test_latest.json` (override via `--output`).

To use it as a CI/release gate, pass `--enforce` (non-zero exit when thresholds are violated):

```bash
python manage.py run_mcp_load_test --business-id <uuid> --mode search_read --iterations 200 --concurrency 8 --queries "pricing" --enforce
```

Modes:
- `search`: calls `search_knowledge`.
- `search_read`: calls `search_knowledge` then `read_knowledge` on the top snippet (if any).
- `read_knowledge`: calls `read_knowledge` for a fixed `--document-id`.

## Throttling Behavior

When a tool is throttled, it returns:
- `status="throttled"`
- `error_code="rate_limited"` or `error_code="prompt_budget_exceeded"` (etc.)
- `throttle_notice={...}`

The assistant should stop looping and ask the user to narrow the request or retry later.

## Key Env Vars

Tool rate limits (per business/tenant):
- `MCP_TOOL_RATE_LIMIT_WINDOW_SECONDS`
- `MCP_DISABLE_TOOL_RATE_LIMITS` (set `true` for CI load tests to avoid throttling skew)
- `MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE`
- `MCP_READ_KNOWLEDGE_CALLS_PER_MINUTE`

Note: In agentic mode the LLM-facing toolset is `search_knowledge` + `read_knowledge`.

Orchestrator hard caps:
- `MCP_MAX_TOOL_ITERATIONS`
- `RAG_MAX_CHUNK_READS_PER_TURN`
- `RAG_MAX_CHUNK_PAGES_PER_TURN`
- `RAG_MAX_CHAR_BUDGET_PER_TURN`
- `RAG_MAX_CHAR_BUDGET_PER_MINUTE`
- `RAG_CHAR_BUDGET_WINDOW_SECONDS`

Privacy-safe logging (default off):
- `MCP_LOG_PII=false`
- `MCP_LOG_SNIPPET_PREVIEWS=false`

Load test threshold gates (used when `--enforce` is passed):
- `MCP_LOAD_TEST_P95_MAX_MS`
- `MCP_LOAD_TEST_MAX_ERROR_RATE`
- `MCP_LOAD_TEST_MAX_THROTTLED_RATE`
