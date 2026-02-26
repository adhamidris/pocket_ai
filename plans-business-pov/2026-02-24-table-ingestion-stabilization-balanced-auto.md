# Plan: Table Ingestion Stabilization (Balanced, Auto Arbitration)

## Decision Inputs (MCQ)
- 1) Balanced outcome: keep key rows, suppress low-signal fragments.
- 2) Extractor policy: keep auto arbitration (recommended).
- 3) Scope: global rollout.
- 4) Rollout: direct rollout.

## Technical Plan
1. Keep multi-extractor arbitration as-is (`auto`) to avoid global recall regressions on docs where Azure under-covers table regions.
2. Add a row-signal filter in table-row chunk payload generation:
   - Keep rows with strong value signal (numeric/scope/fee value).
   - Suppress low-signal continuation/fragment rows that create index bloat.
3. Add threshold controls:
   - `RAG_TABLE_ROW_SIGNAL_MIN_PAIRS`
   - `RAG_TABLE_ROW_SIGNAL_MIN_SCORE`
4. Surface filter diagnostics in row chunk metadata for observability.
5. Add unit tests covering:
   - Low-signal row suppression.
   - Retention of value-bearing rows.
   - Backward-safe behavior under threshold tuning.
6. Run targeted ingestion tests.

## Business POV
### Scenario 1: Dense fee schedule PDF (like Cheques-EN)
- Before: many fragmented rows inflate chunks and indexing cost, creating noisy visualizer output.
- After: chunk count drops materially while preserving fee-bearing rows and scoped values.
- User impact: cleaner retrieval context and lower ingestion variance across similar docs.

### Scenario 2: Clean Azure-friendly statement table
- Before: usually stable chunk counts and low issue counts.
- After: no meaningful regression expected; strong rows still indexed.
- User impact: same quality, with better safety against occasional row-fragment bursts.

### Scenario 3: Table with narrative-only continuation lines
- Before: low-information lines become row chunks and pollute index.
- After: continuation lines are deprioritized/suppressed; summary chunks remain for structure.
- User impact: better answer focus on actionable values.

### Scenario 4: Production tuning under pressure
- Before: behavior changes can be hard to stabilize across diverse table layouts.
- After: thresholds can be tuned without changing core behavior.
- User impact: safer runtime tuning while keeping robust defaults always-on.

## Success Metrics
- Reduce chunk outliers (p95 chunk count for fee-table PDFs).
- Maintain/raise retrieval quality on fee lookup queries.
- Keep no-result and clarification rates stable.
