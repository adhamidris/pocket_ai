# Plan: Scope-Selection Capability Separation (MCQ)

## Goal
Remove capability ambiguity in MCQ scope-selection turns so tool execution semantics are correct by construction:
- `search_knowledge` executes retrieval only.
- `read_knowledge` executes content reads only.
- Scope-click deterministic flow picks the real capability before execution.

## Implementation Plan
1. Add deterministic scope-selection planning in MCP tools/runtime:
   - Inputs: pending scope state + latest scope selection metadata + user message.
   - Output: planned tool call (`read_knowledge` with refs OR `search_knowledge` with scoped query) plus route diagnostics.
2. Refactor orchestrator deterministic scope-entry:
   - Replace forced `search_knowledge` synthetic call with the planned call from step 1.
   - Preserve JSON-serialized arguments for deterministic call tracing.
3. Remove hidden auto-read behavior from `search_knowledge` scope paths:
   - Remove `scope_auto_read` branch where search internally executes read.
   - Remove fallback branch that performs read inside search (`scope_fallback_auto_read`).
   - Keep scoped search behavior and scope-resolution persistence.
4. Keep scope context continuity:
   - Persist scope resolution metadata (`base_query`, `resolved_query`, selected category/key, mapped refs where present).
   - Ensure pending scope state clears only when a real scope selection is committed.
5. Test updates and validation:
   - Update deterministic scope-selection tests to assert read-first planning when mappings are valid.
   - Update search tests to assert no read-prefetch fields are emitted by search for selection/fallback paths.
   - Run focused Django tests for `apps.mcp` + `apps.conversations` scope-selection coverage.

## Rollout Notes
- Feature-gated behavior remains under `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=true`.
- Non-MCQ path should remain unchanged.
- Frontend labeling can rely on real executed tool events once backend emits separated capabilities.

---

# Business POV

## Why this matters
Today, MCQ scope-click can execute read work under a search label. Even when the answer is correct, that harms trust because users perceive a fake or confusing flow. Separating capabilities fixes trust, observability, and debuggability at the same time.

## Scenarios and Expected UX
1. Broad query -> user selects category with valid mapped refs.
   - Before: UI may show Search while backend actually reads.
   - After: UI shows Read Knowledge, then answer appears from selected category evidence.
   - Success metric: drop in “why did it search again?” complaints.

2. Broad query -> user selects category but mappings are stale/low-confidence.
   - Before: mixed behavior hidden inside search payload.
   - After: explicit Search Knowledge run (scoped), then normal continuation.
   - Success metric: easier root-cause tracing with clear tool labels.

3. Regular non-MCQ direct query.
   - Before/After: unchanged behavior.
   - Success metric: no regression in answer quality, latency, or tool loop behavior.

4. Debugging support incident.
   - Before: logs require interpreting diagnostic internals to know real action.
   - After: action is explicit from executed capability.
   - Success metric: faster incident triage and fewer false bug reports.

