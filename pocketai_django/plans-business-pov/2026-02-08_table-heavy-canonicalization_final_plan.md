## Plan

### Current Status (as of 2026-02-08)
1. Completed:
   - Phase 0: Baseline/observability for table overlap and residual leakage.
   - Phase 1: Azure DI reliability hardening (retry/backoff + failure-class metadata).
   - Phase 2: Canonical envelope merge + residual classification.
   - Phase 3: residual chunks isolated as fallback-tier with dedupe/capping reconciliation.
   - Phase 4: rerank penalty plus query-aware residual rescue with diagnostics and regression tests.
   - Phase 5: direct default cutover completed with focused burn-in on ingestion/retrieval table-heavy paths.
2. Validation notes:
   - Focused suites green:
     - `apps.knowledge.tests.test_knowledge_ingestion_phase_one.KnowledgeIngestionChunkingTests`
     - `apps.rag.tests.test_knowledge_search_service.KnowledgeSearchServiceResidualRerankTests`
   - Broader legacy suites contain pre-existing failures unrelated to this phase closure (RLS/UUID fixture setup and older table-path expectations).

### Finalization Execution (No Feature Flags, Pre-Production Aggressive)

#### Step 1: Complete Phase 3 (Chunking Policy Refinement)
1. Enforce residual cap:
   - Keep at most one compact `table_residual` chunk per merged table region.
2. Add semantic-equivalence dedupe:
   - Drop residual chunk when an equivalent `table_row` chunk already exists.
3. Preserve legitimate narrative:
   - Keep non-table narrative headings/intro text untouched.

Exit criteria:
- Table-heavy docs: no repeated residual fragments.
- Mixed docs: narrative chunks remain present and unchanged in intent-bearing sections.

#### Step 2: Complete Phase 4 (Retrieval Calibration)
1. Keep baseline residual penalty active.
2. Add query-aware rescue:
   - If canonical table confidence is low but residual exact-match is strong, allow controlled residual promotion.
3. Emit diagnostics:
   - Record when residual promotion was applied and why.

Exit criteria:
- Better precision on row-level table questions.
- No measurable recall regression on mixed/non-table queries.

#### Step 3: Complete Phase 5 (Direct Cutover + Burn-In)
1. Treat canonicalization path as default path for all ingest jobs.
2. Run fixed burn-in suite on representative fixtures:
   - table-heavy,
   - mixed narrative + tables,
   - simple narrative docs.
3. Reingest representative tenant corpus samples and compare against baseline gates.

Exit criteria:
- All hard gates pass.
- Any regression is reversible via normal git rollback (no runtime gating expected).

### Hard Quality Gates
1. Tenant isolation and privacy invariants remain intact.
2. Table-heavy corpus:
   - stable table count/row count across reingests,
   - residual leakage low and non-duplicative.
3. Mixed/non-table corpus:
   - no drop in answer quality/coverage for narrative questions.
4. Retrieval:
   - residual chunks do not dominate primary ranking unless rescue criteria are explicitly met.

---

## Business POV

### Scenario 1: Bank tariff PDF (dense single table)
Expected UX:
- Users get direct row-level fee answers without noisy duplicated snippets.
- Fewer “wrong row” responses from table-fragment noise.
Risk:
- Over-suppression could hide rare out-of-grid text.
Success metric:
- Higher first-answer correctness for fee/tariff queries.

### Scenario 2: Product guide with narrative + embedded tables
Expected UX:
- Narrative questions still retrieve narrative chunks first.
- Table questions remain table-first.
Risk:
- Residual heuristics accidentally classify narrative as tabular.
Success metric:
- No degradation in narrative query answer coverage.

### Scenario 3: Temporary Azure DI instability
Expected UX:
- Ingestion remains predictable with explicit retry/failure telemetry.
- Operators can distinguish timeout vs throttle vs hard failure quickly.
Risk:
- Retry windows increase ingest latency.
Success metric:
- Lower failed-ingest rate and faster root-cause triage.

### Scenario 4: Pre-production scale onboarding
Expected UX:
- Stable ingestion behavior across varied tenant documents before launch.
- Clear diagnostics reduce debugging cycle time.
Risk:
- Edge-format PDFs still produce occasional residuals.
Success metric:
- Reduced QA churn and fewer retrieval regressions during onboarding.
