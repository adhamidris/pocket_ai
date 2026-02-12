## Plan

### Objective
Replace the current mixed ingestion/retrieval behavior with a canonical, scale-ready architecture that preserves coverage without flooding retrieval with near-duplicate evidence.

### Hard Decisions (No Safe Rollout)
1. No feature flags.
2. No dual pipeline.
3. No branch-specific behavior by tenant or document type.
4. Canonical pipeline becomes the only path after merge.
5. Legacy guardrail heuristics are deleted, not parked.

### Target Architecture
1. Raw Layer (audit only):
   - Keep extractor output lossless for debugging and compliance.
   - Never use raw blocks directly for ranking.
2. Canonical Layer (searchable truth):
   - `table_row` and `table_summary` are primary units for table content.
   - `narrative_paragraph` remains primary for non-tabular prose.
   - Residual table-adjacent text is attached to nearest canonical unit as `annotations`, not indexed as standalone chunks.
3. Retrieval Layer:
   - Rank canonical units only.
   - Use evidence-group consolidation for same table/doc hits.
   - Keep bounded intent routing, but remove hard caps that truncate valid top-k evidence unexpectedly.

### Scope of Refactor
1. Ingestion canonicalization:
   - Replace overlap suppression logic with row/section anchoring + annotation attachment.
   - Remove standalone indexing for low-signal `table_residual` fragments.
2. Retrieval simplification:
   - Remove/disable representation-forcing and table-context forcing heuristics that bypass ranking intent.
   - Keep deterministic dedupe/evidence-group compaction.
3. Contract cleanup:
   - Single, documented chunk contract for all downstream tools.
   - Add explicit metadata for provenance (`source_type`, `anchor_id`, `coverage_reason`).
4. QA harness:
   - Gold query set for table-heavy, mixed, and narrative docs.
   - Pass/fail gates for recall, precision, latency, and token payload size.

### Execution Phases

#### Phase 1: Canonical Data Model Rewrite (1-2 days)
1. Define canonical chunk taxonomy and metadata schema.
2. Add migration code to regenerate chunk payloads from stored extraction artifacts.
3. Remove direct indexing of free-floating residual fragments.

Exit gate:
- Every indexed chunk maps to a canonical anchor (row/summary/paragraph).

#### Phase 2: Ingestion Pipeline Replacement (2-3 days)
1. Rewrite reconciliation stage:
   - classify residual text as `annotation` or `narrative`.
   - attach annotations to canonical rows/tables.
2. Preserve orphan facts by forcing annotation retention when no equivalent row text exists.
3. Drop cell-noise snippets that carry no standalone answer value.

Exit gate:
- Footnotes and timing notes are retained as attached evidence, not exploded into chunk spam.

#### Phase 3: Retrieval Policy Cleanup (1-2 days)
1. Remove representation balance and context-forcing shortcuts from ranking path.
2. Keep only:
   - semantic ranking,
   - exact-match boosts,
   - evidence-group dedupe.
3. Replace intent hard cap with budget-aware cap that honors user/tool limit unless token budget is exceeded.

Exit gate:
- `limit=10` yields up to 10 ranked groups when budget permits.

#### Phase 4: Corpus Rebuild + Benchmark (1-2 days)
1. Re-ingest representative corpus slices (table-heavy, mixed, narrative).
2. Run fixed benchmark set and compare against current branch baseline.
3. Fail merge if any hard metric regresses.

Exit gate:
- All quality and performance thresholds pass.

#### Phase 5: Legacy Code Deletion (same day as merge)
1. Delete retired heuristics and dead env toggles.
2. Remove unused debug-only branches.
3. Update technical docs with new invariant and troubleshooting flow.

Exit gate:
- No dormant fallback logic remains in retrieval/ingestion core.

### Hard Metrics (Required to Ship)
1. Recall:
   - 0 missed gold facts in table-heavy benchmark set.
2. Precision:
   - >=30% reduction in duplicate/near-duplicate evidence returned per answer.
3. Payload efficiency:
   - >=20% reduction in average chars sent to LLM for table-heavy queries.
4. Latency:
   - p95 retrieval latency no worse than current by more than 10%.
5. Stability:
   - Re-ingest of same file yields stable canonical counts (+/-5% tolerance).

### Explicit Trade-offs
1. Short-term disruption:
   - chunk IDs and counts will change after rebuild.
2. One-time re-ingestion cost:
   - temporary operational load while corpus is regenerated.
3. Strict merge gate:
   - if precision/recall metrics miss targets, release is blocked.

---

## Business POV

### Why this is the right business move now
You are pre-production. This is the cheapest point to remove architectural debt that will otherwise multiply support tickets, token cost, and inconsistent answers at tenant scale.

### Scenario 1: Bank tariff PDFs at onboarding scale
Expected user experience:
- Users get direct fee answers from one clean row-level evidence path.
- Less contradiction between repeated asks.
What improves:
- Higher first-answer trust and fewer manual QA escalations.
Success metric:
- First-pass QA acceptance rate rises and re-ingestion debugging drops.

### Scenario 2: Mixed product docs (prose + tables)
Expected user experience:
- Narrative questions use narrative chunks.
- Price/rule questions use table rows with attached notes.
What improves:
- Better answer consistency without needing prompt tricks.
Success metric:
- No drop in narrative query success while duplicate table evidence falls.

### Scenario 3: Growth in tenant/document volume
Expected user experience:
- Performance stays predictable as corpus grows.
What improves:
- Lower LLM context waste and less ranking instability.
Success metric:
- Token spend per answer stays controlled while precision remains high.

### Scenario 4: Debuggability for support and compliance
Expected user experience:
- Clear provenance for every answer snippet.
What improves:
- Faster RCA when a tenant reports “wrong evidence.”
Success metric:
- Mean time to diagnose ingestion/retrieval issues decreases materially.

---

## Phase 4 Execution Report (2026-02-08)

### Commands Executed
1. Current branch benchmark (9to6):
   - `.venv/bin/python manage.py run_rag_eval --force-reingest --baseline --output /tmp/rag_eval_phase4_current.json`
   - Re-run for stability:
     `.venv/bin/python manage.py run_rag_eval --force-reingest --baseline --output /tmp/rag_eval_phase4_current_rerun.json`
2. 9to5 baseline benchmark (worktree compare):
   - `git worktree add /tmp/pocketai_9to5 9to5`
   - `OTEL_SDK_DISABLED=true OTEL_TRACES_EXPORTER=none /Users/adham/Desktop/pocket_ai-main\ 2/pocketai_django/.venv/bin/python manage.py run_rag_eval --force-reingest --baseline --output /tmp/rag_eval_phase4_9to5.json`
   - Re-run for stability:
     `OTEL_SDK_DISABLED=true OTEL_TRACES_EXPORTER=none /Users/adham/Desktop/pocket_ai-main\ 2/pocketai_django/.venv/bin/python manage.py run_rag_eval --force-reingest --baseline --output /tmp/rag_eval_phase4_9to5_rerun.json`
3. Canonical stability check on CIB upload:
   - `.venv/bin/python manage.py rebuild_canonical_knowledge_chunks --upload-id 07590e2a-0e7d-4990-ba6f-26d54aa88006`
   - repeated twice

### Key Observations (Using Re-run Pair for Fairness)
1. `fees-credit-cards` remained strong on recall:
   - top1/top3/mrr unchanged at `1.0`.
2. `travel` and `cards` regressed materially vs 9to5:
   - `travel` top3 `1.0 -> 0.3333`, mrr `1.0 -> 0.3333`.
   - `cards` top3 `0.6667 -> 0.3333`, mrr `0.5 -> 0.3333`.
3. `not_found_accuracy` regressed in multiple sets:
   - `travel/insurance/cards/jobs`: `1.0 -> 0.0`.
4. Table-heavy payload efficiency improved for `fees-credit-cards`:
   - average snippets/query `3.9 -> 1.8` (~`53.85%` reduction).
   - average chars/query `939.8 -> 363.4` (~`61.33%` reduction).
5. Ingestion stability for CIB canonical rebuild is good:
   - repeated rebuilds produced stable counts (`24` chunks, `2` tables, `22` table rows).

### Hard-Metric Gate Status
1. Recall (0 missed facts in table-heavy benchmark set):
   - **Partial pass** on `fees-credit-cards`, **fail** if `jobs` is included in table-heavy scope.
2. Precision (>=30% duplicate/near-duplicate reduction):
   - **Inconclusive in current harness** (no explicit near-duplicate KPI emitted as a first-class metric).
3. Payload efficiency (>=20% chars reduction for table-heavy queries):
   - **Pass** for `fees-credit-cards` (`~61%` reduction).
4. Latency (p95 no worse than +10%):
   - **Fail** for at least one critical set (`travel`, and `fees-credit-cards` in re-run pair).
5. Stability (+/-5% reingest variance):
   - **Pass** on validated CIB rebuild sample (exactly stable).

### Phase 4 Verdict
- **Phase 4 is not fully closed yet.**
- Corpus rebuild + benchmark execution is complete, but hard gates are not all passing.
- Blocking regressions are currently concentrated in:
  1. `not_found` classification behavior,
  2. non-table mixed/narrative retrieval quality (`travel`, `cards`),
  3. p95 latency consistency.

## Phase 5 Execution Report (2026-02-08)

### Removed from retrieval/ingestion core
1. Removed table-balanced routing toggle and branch:
   - Deleted `RAG_TABLE_BALANCED_ROUTING_ENABLED` from settings.
   - Deleted `table_balanced_routing_enabled` branch in `KnowledgeSearchService`.
2. Removed context-forcing full-read heuristics:
   - Deleted `_forced_full_table_reads` and `_should_force_read_for_tables`.
   - Deleted auto-injection of forced reads into pending read requests.
3. Removed fused representation balancing in MCP search fusion:
   - Deleted post-fusion representation balancing pass and its hidden env-based tuning.
   - Kept deterministic dedupe and evidence grouping.

### Docs and contract updates
1. Updated technical doc retrieval invariants:
   - Ranking-first retrieval.
   - No representation slot quotas.
   - No forced full-table read injection.
2. Added troubleshooting flow for ingestion-first RCA before retrieval tuning.

### Post-change expectation
1. Lower hidden retrieval complexity and less non-deterministic behavior.
2. Cleaner observability: results reflect ranking/coverage decisions, not late-stage guardrails.
3. Next quality improvements should target measurable ranking/classification gaps (Phase 4 blockers), not ad-hoc overrides.
