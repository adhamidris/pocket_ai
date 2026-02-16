# Plan

1. Add table-ref anchor manifests at search time (default-on)
- In `search_knowledge` agentic conversion, when row refs are promoted into a single table ref, persist a short-lived manifest keyed by ref id/table id with:
  - `matched_row_index`
  - `table_id`
  - lightweight coverage metadata (`estimated_rows`, `estimated_columns`)
- Keep this in tool context for same-turn reads and deterministic behavior.

2. Consume anchor manifests in `read_knowledge` V2 for first table read
- For table refs without a cursor, start from `max(0, matched_row_index - 1)` when a manifest exists.
- Preserve existing cursor semantics for continuation reads.
- Add fallback: if anchored slice is empty/non-informative, retry from row `0` once in the same call.

3. Replace rough row budgeting with serialized-size budgeting
- In table row assembly, switch from heuristic row char estimate to incremental real payload-size checks before appending each row.
- Keep response under `max_chars` using the real serialized envelope shape.
- Introduce compact table metadata-by-default (still include essential scope fields), with full metadata only when needed for disambiguation/debug paths.

4. Canonicalize table previews to one source
- For table chunks, generate preview from one canonical representation only (structured key-value form).
- Remove layered duplication (`summary + sample_text`) for table previews.
- Keep truncation behavior and preview caps unchanged.

5. Tests (no feature flags, direct replacement)
- Add unit tests for:
  - table ref manifest creation on promotion
  - anchored first read start row and fallback-to-zero behavior
  - serialized-size row fitting (no oversized first payload)
  - canonical preview generation without duplicate lines
- Add integration test for the target scenario:
  - same-day cash deposit fee query returns anchored rows first and avoids extra cursor unless truly needed.

6. Validation and acceptance
- Run focused suites for `apps/mcp/tests`, `apps/rag/tests`, and table ingestion/read tests.
- Verify acceptance criteria:
  - first read includes answer-bearing rows for promoted table refs
  - fewer truncated first reads caused by budgeting mismatch
  - preview text has no duplicate semantic line blocks
  - no regression in broad/vague table queries

# Business POV

## Why this matters
Portal users ask precise fee/tariff questions and expect direct answers in one pass. Current behavior burns tokens on early irrelevant rows and repetitive preview text, causing avoidable follow-up reads and slower answers.

## Practical scenarios

### 1) Precise fee lookup (current pain)
- **Question:** “What’s the fee for cash deposit with same day value date?”
- **Today:** First read starts at row 0, misses target row block, then needs `next_cursor`.
- **After:** First read starts near matched row and usually returns the answer block immediately.
- **Success signal:** Reduced second-read rate for exact fee questions.

### 2) Vague/broad question (kept behavior)
- **Question:** “What are cash deposit fees?”
- **Today:** Broad retrieval can still be useful.
- **After:** Broad retrieval remains; only read targeting/budgeting/preview formatting changes.
- **Success signal:** No drop in answer quality for ambiguous prompts.

### 3) High-volume QA runs
- **Today:** Oversized first table payloads create truncation churn and token waste.
- **After:** Real-size budgeting packs rows that truly fit, reducing wasted reads and latency.
- **Success signal:** Lower average read chars per successful answer and fewer cursor continuations.

### 4) Human QA review of search previews
- **Today:** Repetitive preview lines make relevance assessment harder.
- **After:** Single canonical table preview makes matches easier to inspect and trust.
- **Success signal:** Faster QA triage and fewer “preview looked duplicated/noisy” reports.

## Risks / trade-offs
- Real-size budgeting adds modest CPU overhead during row assembly.
- Anchored start can occasionally be wrong; one-shot fallback to row 0 mitigates misses.
- Canonical previews reduce redundancy; some users may perceive less “extra context,” but relevance signal becomes clearer.

## How we know it worked
- Functional:
  - exact-table fee questions resolve in first read more often
  - response previews are non-duplicative and readable
- Efficiency:
  - lower `%` of read calls requiring `next_cursor`
  - lower average tool-output chars for successful first answers
- Safety/regression:
  - broad-query behavior unchanged in integration tests
