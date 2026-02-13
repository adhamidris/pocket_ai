# Phase 6: Rollout by Feature Flags

Phase 6 adds tenant-cohort rollout controls, live dashboard checks against a frozen baseline, and a one-command rollback path.

## What Is Gated

New per-business feature flag:

- `rag_table_pipeline_v2` (default `false`)

When `rag_table_pipeline_v2` is enabled for a tenant:

- table candidate selection uses the phase-4 scorer/arbitration path
- VLM repair/guardrail path runs (subject to existing service settings)

When disabled:

- selection falls back to deterministic legacy precedence (`azure` -> `pdfplumber` -> `geometry` -> `heuristic`)
- VLM repair is skipped with explicit metadata in `table_extraction.table_repairs.skip_reason`

## Rollout Command

Command:

```bash
python manage.py manage_table_ingestion_rollout
```

Actions:

- `--action plan`: inspect current cohort/flag state
- `--action enable`: enable Phase-6 bundle for scope
- `--action dashboard`: evaluate live uploads vs baseline snapshot and compute pass rate
- `--action rollback`: disable Phase-6 bundle for scope

Scope selectors:

- `--cohort <name>`
- `--business-id <uuid>` (repeatable)
- `--all`

Phase-6 bundle toggled by `enable/rollback`:

- `rag_table_pipeline_v2`
- `rag_shadow_ingestion`
- `rag_eval_logging`

## Canary Rollout Steps

1. Inspect current target cohort:

```bash
python manage.py manage_table_ingestion_rollout --action plan --cohort canary
```

2. Enable canary cohort:

```bash
python manage.py manage_table_ingestion_rollout --action enable --cohort canary
```

3. Run dashboard comparison against baseline snapshot:

```bash
python manage.py manage_table_ingestion_rollout \
  --action dashboard \
  --cohort canary \
  --baseline-json docs/ingestion_snapshots/cib_teller_ebca6c00_vlm_on_2026-02-13.json \
  --window-hours 168 \
  --max-uploads-per-business 3 \
  --enforce \
  --min-pass-rate 0.95 \
  --output docs/ingestion_snapshots/phase6_canary_dashboard.json
```

4. Progressively expand cohort after stable pass rate.

## Rollback Playbook

Immediate rollback command:

```bash
python manage.py manage_table_ingestion_rollout --action rollback --cohort canary
```

Expected rollback behavior:

- `rag_table_pipeline_v2` disabled for scoped tenants
- ingestion returns to deterministic legacy selection mode
- dashboard can be re-run immediately to confirm stabilization

## Dashboard Interpretation

Dashboard uses Phase-5 quality gates (`row_recall`, `row_order_stability`, `scope_f1`, `critical_value_coverage`, `scope_metadata_coverage`) by comparing each recent upload snapshot against the provided baseline snapshot.

Gate outputs include:

- per-upload pass/fail
- failed check histogram
- regression histogram
- per-business and overall pass rates
