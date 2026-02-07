# Retrieval Quality Solid Patch (Plan + Business POV)

## Plan
1. Tighten table-row expansion trigger to query-relevant table signals only.
2. Propagate meaningful relevance scores into expanded table rows.
3. Add a final snippet rerank pass on the hybrid-only path.
4. Stop global table over-promotion in agentic search shaping (promote per-table only).
5. Add result-fingerprint dedup for effectively identical searches in-turn.
6. Improve table read hints (`suggested_max_chars`) for large tables.
7. Add focused regression tests for ranking, shaping, dedup, and hints.

## Business POV
### Scenario 1: “Tell me all credit card issuance fees”
- **Before**: Search may return many irrelevant table refs with `score=0`, and model may read a wrong table first.
- **After**: Credit-card-related rows/tables get stronger relative scores and cleaner ordering.
- **User impact**: Correct table is found/read earlier; fewer wrong answers from unrelated fee tables.
- **Success metric**: Higher first-read relevance rate; fewer multi-search loops per answer.

### Scenario 2: Similar follow-up searches in one turn
- **Before**: Slightly reworded search can consume another search budget slot even when results are effectively identical.
- **After**: Same top-result fingerprint is recognized and treated as duplicate-result reuse.
- **User impact**: More budget preserved for truly new searches.
- **Success metric**: Lower average `searches_used` per turn for repeated-intent flows.

### Scenario 3: Large table reads (many rows)
- **Before**: Small default read hint (`750`) causes repeated truncation/cursor chaining.
- **After**: Hint scales with estimated table size, so models request practical `max_chars` earlier.
- **User impact**: Fewer fragmented reads and faster complete answers.
- **Success metric**: Reduced number of truncated `read_knowledge` calls per table answer.

### Scenario 4: Mixed table-result pages
- **Before**: Global promotion can collapse too aggressively, hiding row-level precision when only one row matched.
- **After**: Promotion applies per table group only; single matched rows remain explicit `table_row`.
- **User impact**: Better precision and interpretability in retrieval traces.
- **Success metric**: Lower mismatch between matched row intent and returned ref kind.
