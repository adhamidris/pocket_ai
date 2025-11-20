# MCP Validation Plan (Phase 5)

Target four high-signal scenarios to spot regressions in retrieval, hallucination, and budget handling. Run against the MCP path with streaming tools enabled.

## 1) Table/Number Lookup (fees/limits)
- **Input**: User asks for a specific numeric fact (e.g., “What’s the annual fee for the Platinum card?”).
- **Expected tools**: `search_knowledge` (intent=identifier/table/short), then `read_document` `mode=full_page` for the relevant chunk/upload.
- **Acceptance**:
  - Response cites the correct number and mentions source context; no placeholder-only reply.
  - No constraint errors; tool trace shows `mode=full_page` or an explicit downgrade with `throttle_notice`.
  - Coverage ledger records the read snippet as `read_state=full/preview`.

## 2) Multi-Page Fact (page navigation)
- **Input**: “On page 3 of the pricing sheet, what are the ATM limits?”
- **Expected tools**: `search_knowledge` returns the upload; `read_document` requests `page=3` (or offset) with `mode=full_page`.
- **Acceptance**:
  - Response uses content from the requested page; if page unavailable, the assistant asks for clarification instead of guessing.
  - Tool trace includes page number/mode; no repeated reads of other pages.
  - Coverage ledger shows the requested page with correct read_state.

## 3) Low-Budget Scenario (throttling)
- **Setup**: Lower `RAG_MAX_CHAR_BUDGET_PER_TURN` to a small value (e.g., 2000) for this run.
- **Input**: “List all benefits of the Corporate card.”
- **Expected tools**: `search_knowledge`, then `read_document` initially attempts `full_page` but may throttle to `excerpt`.
- **Acceptance**:
  - If throttled, LLM_hint/throttle_notice appears; assistant responds with what’s available and asks for a narrower scope (do not invent missing benefits).
  - No additional reads after a constraint error unless the user clarifies.

## 4) Not-Found / Identifier Miss
- **Input**: “Policy ID ABC-9999-XYZ” (nonexistent).
- **Expected tools**: `search_knowledge` intent=identifier, limit narrowed; no `read_document` unless a valid doc is returned.
- **Acceptance**:
  - Assistant states no match and asks for a clearer ID or related name; no hallucinated policy details.
  - Tool payload hint from search is referenced (clarification request).

## How to Run
- Use the chat portal with MCP enabled. Capture tool_trace, coverage_ledger, and diagnostics from plan output for each turn.
- For low-budget test, temporarily set env vars: `RAG_MAX_CHAR_BUDGET_PER_TURN=2000` and `RAG_MAX_CHUNK_PAGES_PER_TURN=1`.
- Log: keep the assembled plan diagnostics and tool trace for each scenario and compare before/after changes.
