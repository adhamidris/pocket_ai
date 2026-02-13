# Phase 6: Rollout by Feature Flags (Historical)

Phase 6 originally added tenant-cohort rollout controls, live dashboard checks against a frozen baseline, and a one-command rollback path.
As of the phase-7 cleanup follow-up refactor, the table pipeline v2 path is now globally active for all tenants.

## Current Behavior

- Table candidate selection always uses the phase-4 scorer/arbitration path.
- VLM repair/guardrails run based on global service settings (for example `RAG_TABLE_VLM_ENABLED`), not per-tenant pipeline toggles.

## Historical Note

A previous per-tenant pipeline gate existed during early rollout. It has been removed from active feature defaults and no longer participates in runtime ingestion decisions.

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

Phase-6 bundle toggled by `enable/rollback` now effectively targets:

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

- `rag_shadow_ingestion` and `rag_eval_logging` disabled for scoped tenants
- ingestion remains on global v2 table pipeline mode
- dashboard can be re-run immediately to confirm stabilization

## Dashboard Interpretation

Dashboard uses Phase-5 quality gates (`row_recall`, `row_order_stability`, `scope_f1`, `critical_value_coverage`, `scope_metadata_coverage`) by comparing each recent upload snapshot against the provided baseline snapshot.

Gate outputs include:

- per-upload pass/fail
- failed check histogram
- regression histogram
- per-business and overall pass rates
