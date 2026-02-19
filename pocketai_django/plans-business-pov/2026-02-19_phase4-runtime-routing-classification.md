# Phase 4 Plan — Runtime Routing & Classification

## Plan
1. Extend runtime table-intent context to load tenant lexicon snapshots (entity + attribute terms) and pass them into `QueryClassifier`.
2. Add an isolated `IntentFallbackService` module (`apps/rag/intent_fallback.py`) to perform LLM fallback classification when table-intent detection is exploratory and low-confidence.
3. Add clarification-state handling:
   - mark low-confidence exploratory table intents with `requires_clarification`
   - return `needs_clarification` search status before expensive retrieval
   - expose a direct clarification question in diagnostics.
4. Wire intent diagnostics into cache keys and response diagnostics (`intent_source`, fallback flags, clarification question) to avoid stale mixed behavior.
5. Update query rewriting to consume tenant lexicon terms so follow-up detection can leverage tenant vocabulary overlap.
6. Update MCP search hints so `needs_clarification` returns the runtime clarification question directly to the agent.
7. Add targeted tests for fallback parsing, lexicon-aware classification, lexicon-aware rewriting, clarification status handling, and MCP hint propagation.

## Business POV
1. Scenario: ambiguous table question in a new tenant domain  
Expected UX: system asks a clear clarification question instead of returning wrong rows.

2. Scenario: tenant uses industry-specific terms not present in global heuristics  
Expected UX: classifier and rewriter leverage tenant lexicon first, improving match quality without hard-coded domain dictionaries.

3. Scenario: low-confidence route caused by sparse table signals  
Expected UX: LLM fallback can recover intent class when heuristic route is exploratory and weak.

4. Success metrics for this phase
- Lower “wrong table answer” incidents on ambiguous prompts.
- Higher first-try relevance on tenant-specific vocabulary.
- Observable diagnostics for why routing chose clarify/fallback/heuristic paths.
- Stable retrieval latency because clarification exits early when intent is unclear.
