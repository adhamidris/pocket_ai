# MCP Validation Plan (Agentic Mode)

Target four high‑signal scenarios to spot regressions in retrieval, hallucination, and budget handling. Run against the MCP path with streaming tools enabled.

## 1) Numeric Fact Lookup (fees/limits)
- **Input**: “What’s the annual fee for the Platinum card?”
- **Expected tools**: `search_knowledge` → `read_knowledge`.
- **Acceptance**:
  - Response cites the correct number with evidence‑based context.
  - No constraint errors; tool trace shows `read_knowledge` with refs.

## 2) Long Document Follow‑up (cursor continuation)
- **Input**: “List all benefits of the Corporate card.”
- **Expected tools**: `search_knowledge` → `read_knowledge` (may return `partial` with cursor).
- **Acceptance**:
  - If partial, the assistant continues using the cursor instead of re‑searching.
  - No fabricated items when evidence is truncated.

## 3) Low‑Budget Scenario (throttling)
- **Setup**: `RAG_MAX_CHAR_BUDGET_PER_TURN=2000`
- **Input**: “List all benefits of the Corporate card.”
- **Expected tools**: `search_knowledge` → `read_knowledge` (likely partial).
- **Acceptance**:
  - Assistant responds with what is available and asks for narrower scope.
  - No additional reads after a constraint error unless the user clarifies.

## 4) Not‑Found / Identifier Miss
- **Input**: “Policy ID ABC-9999-XYZ” (nonexistent)
- **Expected tools**: `search_knowledge` intent=identifier; no `read_knowledge` unless a valid ref is returned.
- **Acceptance**:
  - Assistant states no match and asks for a clearer ID or related name.

## How to Run
- Use the chat portal with MCP enabled. Capture tool_trace, coverage_ledger, and diagnostics from plan output for each turn.
- For low‑budget test, set: `RAG_MAX_CHAR_BUDGET_PER_TURN=2000`.
