## Plan

### Problem Statement
Table-heavy PDFs can produce mixed artifacts:
- one or more structured table outputs,
- plus residual page text chunks that still contain table-row facts.

This creates retrieval ambiguity (duplicate evidence paths) and uneven quality across extractors, especially when managed services (for example Azure DI) are slow or partially unavailable.

### Goals
1. Make table extraction outcomes more deterministic and explainable for table-heavy pages.
2. Preserve recall (do not lose facts) while reducing duplicate row content across text and table chunks.
3. Avoid regressions for simple narrative documents and mixed narrative+table documents.
4. Keep tenant isolation and observability while moving to one default ingestion path.

### Non-goals
1. Perfect visual reconstruction of every complex PDF layout.
2. Hard dependency on a single extractor vendor.
3. Running parallel legacy/new ingestion paths behind runtime flags.

### Architecture Direction (Long-term Maintainable)
Introduce a dedicated, explicit stage between extraction and chunking:
- `table_canonicalization_and_residual_reconciliation`

This stage owns:
1. Candidate-set selection (already improved) with structural signals.
2. Canonical table envelope generation per page (merge/smooth fragmented regions when signals indicate one logical table).
3. Residual text reconciliation:
   - classify each page block as `table_internal`, `table_adjacent`, or `narrative`,
   - keep `table_adjacent` as fallback evidence but mark as low-priority retrieval tier,
   - never delete residual-only facts unless equivalent table-row facts exist.

### Phased Execution

#### Phase 0: Baseline + Observability (1-2 days)
1. Add ingestion metrics persisted per upload:
   - `table_bbox_coverage_ratio`
   - `residual_text_blocks_count`
   - `residual_text_with_numeric_signals_count`
   - `table_row_unique_evidence_count`
2. Add debug fields in `ingestion_metadata.table_extraction` for canonicalization decisions.
3. Add dashboard query for table-heavy uploads with high residual ratio.

Exit criteria:
- We can quantify residual table leakage and compare before/after by business and file class.

#### Phase 1: Extractor Reliability Hardening (1-2 days)
1. Apply explicit retry/backoff policy for Azure DI calls (respect response pacing guidance).
2. Separate timeout classes in metadata:
   - `timeout`,
   - `throttle/retryable`,
   - `hard_failure`.
3. Keep fallback extractors active, but annotate quality confidence by source path.

Exit criteria:
- Fewer transient Azure failures.
- No silent fallback without metadata.

#### Phase 2: Canonicalization Core (2-4 days)
1. Add canonical envelope builder:
   - detect fragmented micro-table patterns,
   - merge into logical envelope when row/title/coverage heuristics agree.
2. Replace raw overlap suppression input with canonical envelope regions.
3. Add residual classifier:
   - detect row-like residuals (currency/percent/value-shape + proximity),
   - tag as `table_residual`.

Exit criteria:
- For known table-heavy fixtures, table count and row coverage become stable across runs.

#### Phase 3: Chunking Policy Refinement (1-2 days)
1. Chunk priorities:
   - `table_row` = primary,
   - `table_summary` = secondary,
   - `table_residual` = fallback tier.
2. De-duplicate only when semantic-equivalent table-row chunk exists.
3. Keep one compact residual chunk per region to prevent total recall loss.

Exit criteria:
- Reduced duplicate retrieval while preserving answers for residual-only facts.

#### Phase 4: Retrieval Scoring Calibration (1-2 days)
1. Apply mild retrieval penalty to `table_residual` vs canonical `table_row` chunks.
2. Add query-aware override:
   - if answer confidence low and residual exact match high, allow residual promotion.
3. Add evaluation set with row-level questions, mixed queries, and adversarial phrasing.

Exit criteria:
- Better top-k precision on table questions without recall drop.

#### Phase 5: Direct Default Cutover (1 day + QA burn-in)
1. Ship canonicalization and residual reconciliation as the default ingestion path for all tenants (no runtime gating).
2. Run a fixed pre-production burn-in suite on representative fixtures (table-heavy, mixed, simple narrative) before merge.
3. If regression appears, revert via standard code rollback (git revert), not runtime toggles.

Exit criteria:
- New default path passes all hard quality gates and fixture comparisons.

### Test Strategy
1. Unit tests:
   - fragmented-vs-coherent candidate selection,
   - canonical envelope merge decisions,
   - residual block classification.
2. Snapshot tests on representative PDFs:
   - pure table-heavy sheet,
   - mixed narrative + tables,
   - simple narrative document.
3. Retrieval eval tests:
   - row fact queries,
   - narrative queries,
   - hybrid queries.
4. Non-regression gates:
   - no cross-tenant leakage,
   - no drop in narrative recall on non-table docs.

### Why This Is Maintainable
1. One explicit stage with clear responsibilities instead of scattered ad-hoc suppressions.
2. Observable decisions recorded in metadata for debugging.
3. One default code path with deterministic behavior and strict automated acceptance gates.
4. Extractor-agnostic design (works with Azure, pdfplumber, geometry, future engines).

### External Practice Signals (used for direction)
1. Azure DI layout regions may represent core content and can miss adjacent/related lines.
2. Azure DI recommends explicit retry/pacing patterns due to service limits and variable latency.
3. Layout-aware chunking is preferred over plain OCR text concatenation for semantic retrieval.
4. Open-source ingestion stacks (for example Unstructured) also separate table extraction strategy from chunking strategy, reinforcing staged architecture.

Reference links:
- https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0
- https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0
- https://learn.microsoft.com/en-us/azure/search/search-how-to-semantic-chunking
- https://docs.unstructured.io/open-source/concepts/models

## Business POV

### Scenario 1: Bank tariff PDF (single dense table, many row facts)
Expected UX:
- Assistant answers fee/rate questions using clean row evidence instead of mixed noisy text.
- Fewer contradictory answers between similar questions.
Potential regression:
- If canonicalization is too aggressive, some border notes could be down-ranked.
Success metric:
- Increase exact row-answer hit rate and reduce duplicate evidence citations per answer.

### Scenario 2: Product brochure with narrative sections and embedded small tables
Expected UX:
- Narrative questions still resolve from prose; table questions resolve from table rows.
- Users do not need to reformulate prompts to “hit the right chunk type.”
Potential regression:
- Misclassifying narrative as table-adjacent could lower prose recall.
Success metric:
- Narrative QA success remains flat or improves versus baseline.

### Scenario 3: Temporary Azure DI latency/timeout window
Expected UX:
- Ingestion still completes via fallback, with clear quality metadata and no silent degradation.
- Operations team can identify affected uploads quickly.
Potential regression:
- Slightly longer ingestion time during retries.
Success metric:
- Lower failed-ingestion rate and lower “mystery quality” incidents from fallback runs.

### Scenario 4: Large tenant onboarding many policy PDFs pre-launch
Expected UX:
- Consistent ingestion behavior across document styles with predictable retrieval quality.
- Faster QA cycles because ingestion decisions are inspectable.
Potential regression:
- Initial rollout may need threshold tuning on a subset of templates.
Success metric:
- Reduced manual re-ingestion/debug time and improved first-pass acceptance in QA.
