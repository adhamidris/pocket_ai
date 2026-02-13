# Phase 5: Quality Gates in CI

Phase 5 adds deterministic, enforceable quality gates for table-ingestion comparisons.

## Metrics

`compare_snapshots()` now computes and persists `quality_gate_metrics`:

- `row_recall`: shared row keys / baseline row keys
- `row_order_stability`: LCS ratio over shared row order
- `scope_precision`, `scope_recall`, `scope_f1`: micro metrics over `(row_key, scope_label)` pairs
- `critical_value_coverage`: matched `table_row_fee_value` ratio against baseline
- `critical_value_presence_coverage`: non-empty critical value presence ratio
- `scope_metadata_coverage`: ratio of rows with scope that include both reason/mode and confidence

## Default Thresholds

Defaults are centralized in `QUALITY_GATE_THRESHOLD_DEFAULTS`:

- `min_row_recall = 0.99`
- `min_row_order_stability = 0.95`
- `min_scope_f1 = 0.95`
- `min_critical_value_coverage = 0.95`
- `min_scope_metadata_coverage = 1.0`

A comparison passes only when all thresholds pass and no regression flags are present.

## CLI Enforcement

`compare_table_ingestion_snapshots` now supports fail-fast enforcement:

```bash
python manage.py compare_table_ingestion_snapshots \
  --baseline-json docs/ingestion/phase5_quality_gate_baseline.json \
  --candidate-json docs/ingestion/phase5_quality_gate_candidate.json \
  --enforce-quality-gates
```

Optional threshold overrides:

- `--min-row-recall`
- `--min-row-order-stability`
- `--min-scope-f1`
- `--min-critical-value-coverage`
- `--min-scope-metadata-coverage`

## CI Gate

Workflow: `.github/workflows/ingestion-quality-gates.yml`

The workflow executes the comparison command with `--enforce-quality-gates`, uploads JSON/Markdown scorecards as artifacts, and fails the job on regression or threshold violations.
