# Plan: MCP Scope Clarification Engine Hardening (Comprehensive)

## Goal
Build a robust, non-domain-specific scope-clarification architecture that preserves agentic quality while providing deterministic UX:
- Broad queries trigger clarification only when truly broad.
- Category clicks behave consistently for all categories (not only top N).
- Scope selections remain anchored to the original intent across turns.
- Segment-specific asks (e.g., "plus") produce segment-faithful evidence and responses.
- Tool labels reflect real executed capability (search vs read) with no hidden behavior.

## Non-Goals
- Replacing the entire MCP stack or RAG engine.
- Domain-specific hardcoding for banking labels.
- Disabling agentic behavior globally.

## Current Findings (from engine audit)
1. Scope broadness is over-triggered for some queries that are user-specific enough (e.g., includes an intent anchor like accounts), causing unnecessary MCQ prompts.
2. MCQ contract is asymmetric: top categories are first-class (`category_key` + mapped refs), non-top categories are second-class (text match + fallback search).
3. Deterministic click-to-read only happens when category-key mapping exists; otherwise click routes to scoped search.
4. Scoped retrieval is query-scoped but not always hard-filtered by selected segment/category applicability, so evidence can feel general.
5. Read pagination can stop before full table coverage while answers are still phrased broadly/completely.
6. Prompt continuity relies on metadata hydration/persistence; mostly correct today but vulnerable if scope updates are not persisted on all code paths.

## Target Architecture Principles
1. Equal category treatment: every visible/selectable category must be executable via deterministic contract.
2. Capability purity: search discovers, read reads. No hidden auto-read under search semantics.
3. Intent continuity by contract: base query + selected scope persist as structured state, not inferred from prose.
4. Applicability-aware evidence: selected scope and segment constrain retrieval/read projection when possible.
5. Coverage transparency: when evidence is partial, answer must be explicit and verifiable.
6. Non-domain specificity: logic should work for any taxonomy/segment schema, not only financial terms.

## Workstreams

### WS0: Safety Baseline and Observability First
1. Add explicit structured events for scope lifecycle:
   - scope_clarification.presented
   - scope_selection.accepted/rejected
   - scope_selection.route_decided (mapped_read | scoped_search)
   - scope_selection.fallback_reason
   - scope_answer.coverage (rows_read/rows_total)
2. Add click telemetry: category key/label source, top-vs-expanded bucket, and parse outcomes.
3. Freeze baseline dashboards before logic changes.

Acceptance Criteria:
- Every MCQ turn emits a complete scope lifecycle trail with correlation id.
- Can answer “why search happened after click?” from logs in <2 minutes.

### WS1: Clarification Trigger Reform (Reduce False Positives)
1. Replace/augment static generic-token heuristic with scope-specificity scoring:
   - Use category-entropy and candidate concentration from retrieval results.
   - If user query narrows strongly to one dominant category family, skip MCQ.
2. Introduce confidence threshold bands:
   - confident_specific -> direct scoped search/read flow
   - ambiguous_broad -> MCQ
3. Keep fallback to MCQ when ambiguity truly remains.

Acceptance Criteria:
- Queries like “plus fees for accounts” do not trigger MCQ when concentration indicates one dominant scope.
- No regression on genuinely broad asks (“plus fees”).

### WS2: Uniform MCQ Contract (All Categories Are First-Class)
1. Generate `category_key` for all categories (up to max configured list).
2. Generate mapped refs (or explicit fallback marker) for all categories.
3. Keep UI top-3 preview + More for ergonomics only; backend contract remains equal for all.
4. Ensure “More” expansion uses same deterministic category-key payload path as top chips.

Acceptance Criteria:
- Clicking any expanded category can route deterministically (not text-fuzzy only).
- No behavioral split between top categories and expanded categories.

### WS3: Deterministic Selection Router v2
1. Introduce scope selection state machine:
   - states: pending -> selected -> answered
   - transitions with validation + reasoned fallback
2. Deterministic route policy:
   - mapped refs valid -> read_knowledge
   - mapped refs stale/missing/low confidence -> scoped search (explicit reason)
3. Persist resolution snapshot atomically with route decision.

Acceptance Criteria:
- Route choice is deterministic and reproducible from persisted state.
- No re-clarification loop on valid scope selection turns.

### WS4: Scope-Filtered Retrieval (Hard Filter Layer)
1. Add pre-rerank scope filter module applied on scoped-search fallback:
   - Category filter (selected category key)
   - Segment applicability filter (when inferred scope metadata exists)
2. Controlled relaxation policy:
   - strict pass first
   - if under-recall, relax with `scope_filter_relaxed=true` and reason
3. Keep this generic by reading schema metadata (scope columns, qualifiers), not domain tokens.

Acceptance Criteria:
- Scoped search result set reflects selected category/segment with clear diagnostics.
- Relaxation is explicit and measurable, never silent.

### WS5: Read Projection and Coverage Manager
1. Add scope-aware table projection mode:
   - Default: qualifier columns + selected segment columns.
   - Optional compare mode: include all segment columns on explicit user request.
2. Pagination controller for table reads:
   - Continue reads until coverage objective met or budget/tool limit reached.
   - Return coverage object: rows_read, rows_total, complete, next_cursor.
3. Feed coverage to answer composer.

Acceptance Criteria:
- Segment-focused asks produce segment-focused evidence view.
- Answers state partial coverage when complete=false.

### WS6: Response Grounding and Applicability Guards v2
1. Enforce answer policy from evidence metadata:
   - Do not claim segment-specificity when evidence is `scope_abstain`/ambiguous.
2. Add answer lint checks before final emit:
   - segment-claim vs applicability mismatch
   - full-coverage claim vs coverage object
3. Auto-downgrade phrasing when evidence is generic.

Acceptance Criteria:
- Reduced “sounds general” responses for scoped asks.
- No unsupported segment claims in QA regression set.

### WS7: Context Reliability (Prompt/Metadata)
1. Strengthen metadata persistence invariants:
   - Every scope mutation must set `scope_clarification_updated`.
2. Add compact-safe scope snapshot:
   - lossless scope contract persisted independent of prompt-compacted tool output.
3. Add integrity checks:
   - detect missing scope note despite persisted scope resolution.

Acceptance Criteria:
- Scope continuity survives refresh/reload/long conversations.
- No silent loss of scope resolution context.

### WS8: Test Matrix and Rollout
1. Unit Tests:
   - trigger specificity scoring
   - uniform category contract
   - deterministic router v2
   - applicability guardrails
2. Integration Tests:
   - full click-turn pipeline (top + expanded categories)
   - scoped search fallback with hard filter + relaxation
3. E2E Tests:
   - UI More expansion, click behavior, route labels
4. Load/Soak:
   - concurrency on MCQ + deterministic click turns
5. Progressive rollout via flags:
   - `MCP_SCOPE_CLARIFICATION_MCQ_ENABLED` (existing)
   - `MCP_SCOPE_UNIFORM_CATEGORY_CONTRACT_ENABLED`
   - `MCP_SCOPE_HARD_FILTER_ENABLED`
   - `MCP_SCOPE_READ_PROJECTION_ENABLED`
   - `MCP_SCOPE_GROUNDING_GUARDRAILS_V2_ENABLED`

Acceptance Criteria:
- Regression suite green.
- Rollout metrics stable for at least 48h per phase gate.

## Trade-offs
1. Stricter scope filtering may reduce recall in noisy data; mitigated by explicit relaxation policy.
2. Full-category mapping increases metadata payload size; mitigated by compact keyed contract and lazy UI rendering.
3. Coverage-driven pagination can increase latency on large tables; mitigated by coverage targets + budget-aware stop.
4. Stronger grounding rules may produce more caveated answers; this is preferable to unsupported certainty.

## Suggested Delivery Sequence
1. WS0 + WS2 + WS3 first (stabilize contract and click routing symmetry).
2. WS1 next (fix over-clarification false positives).
3. WS4 + WS5 (quality/faithfulness uplift).
4. WS6 + WS7 (response trust and continuity hardening).
5. WS8 continuously from WS0 onward.

---

# Business POV

## Why this is worth doing
The current system works functionally but feels inconsistent to users: sometimes deterministic, sometimes fuzzy, sometimes too broad for a scoped question. That inconsistency erodes trust faster than small factual misses. This plan improves trust, traceability, and quality without sacrificing agentic behavior.

## Scenarios and Expected UX
1. Visitor asks: “What are plus fees for accounts?”
- Expected UX: no unnecessary MCQ when one scope is dominant; assistant answers directly with scoped evidence.
- Success metric: reduction in false `needs_clarification` rate for specific queries.

2. Visitor asks broad: “Tell me about plus fees.” then clicks a non-top category from expanded list.
- Expected UX: same deterministic quality as top categories; no second-class behavior.
- Success metric: parity in click-to-answer latency/quality between top and expanded categories.

3. Visitor selects category with stale/missing mapped refs.
- Expected UX: explicit scoped search fallback (correct label), then read and answer.
- Success metric: fallback reason appears in diagnostics 100% of fallback turns.

4. Visitor asks segment-specific question where source rows are multi-segment.
- Expected UX: answer is segment-aware and honest about applicability/ambiguity.
- Success metric: near-zero unsupported segment claims in QA set.

5. Large table category with paginated reads.
- Expected UX: assistant either completes coverage or clearly states partial coverage and offers continuation.
- Success metric: coverage-complete rate increases; partial answers are explicitly marked.

## Business Outcomes
1. Higher user trust due to consistent deterministic behavior on clicks.
2. Fewer support escalations about “why did it search again?” or “this sounds generic.”
3. Faster incident triage because route reasons and scope lifecycle are fully observable.
4. Safer scaling because behavior is contract-driven, testable, and feature-flagged.
