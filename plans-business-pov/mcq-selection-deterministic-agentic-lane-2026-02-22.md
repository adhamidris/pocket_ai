# Plan: MCQ Selection Deterministic Commit + Scoped Agentic Continuation

## Plan
1. Keep MCQ rollout strictly behind `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=true`.
2. When a turn includes `scope_selection` and pending scope state exists, force deterministic entry through `search_knowledge` (selection commit), instead of allowing model-side tool choice first.
3. For selection turns, block `present_scope_clarification` from the remaining tool loop to prevent duplicate selector loops in the same click turn.
4. Preserve agentic quality: after deterministic commit, allow normal tool-loop continuation (`search_knowledge`/`read_knowledge`/pagination) while staying in the selected scope.
5. Keep non-MCQ behavior unchanged when flag is off.
6. Add regression tests for:
   - deterministic forced commit in MCQ mode
   - no deterministic forcing when MCQ mode is disabled
   - existing scope-selection normalization and fallback behavior

## Business POV
### Scenario 1: Normal broad-fees clarification click
- User asks a broad query and gets MCQ.
- User clicks `Cheques`.
- Expected UX: no repeated selector; answer starts immediately and can deepen if needed.
- Success metric: repeated MCQ after click approaches zero.

### Scenario 2: High-complexity category requires deeper evidence
- Seed refs are loaded immediately, but may not fully cover all sub-fees.
- Expected UX: system still answers quickly and can continue with scoped follow-up reads/searches for completeness.
- Success metric: no drop in answer completeness compared to current agentic baseline.

### Scenario 3: Non-MCQ tenants / fallback mode
- Flag disabled or rollout not enabled.
- Expected UX: existing text clarification behavior remains exactly as before.
- Success metric: no regression in non-MCQ test suite and support tickets.

### Scenario 4: Stale or malformed click metadata
- User click arrives with inconsistent key/label.
- Expected UX: deterministic commit path attempts safe resolution; no infinite selector loop.
- Success metric: clear failure behavior and bounded retries with no UX dead loops.
