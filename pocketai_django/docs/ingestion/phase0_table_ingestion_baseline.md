# Phase 0: Table Ingestion Baseline

This document freezes repeatable commands and artifacts for Phase 0:

- Capture upload-level table ingestion snapshots.
- Compare a candidate run against a baseline using the same row window.
- Keep benchmark cases in one manifest (`phase0_table_ingestion_benchmark_manifest.json`).

## Commands

### 1) Capture a snapshot

```bash
cd pocketai_django
python manage.py capture_table_ingestion_snapshot \
  --upload-id ebca6c00-97b1-4776-85a9-f4b4b1f9e4b0 \
  --label cib_teller_vlm_on_baseline \
  --focus-row-start 6 \
  --focus-row-end 17 \
  --term "USD 5" \
  --term "USD 2" \
  --term "same day value date"
```

Outputs JSON + Markdown under `docs/ingestion_snapshots/`.

### 2) Compare baseline JSON vs candidate upload

```bash
cd pocketai_django
python manage.py compare_table_ingestion_snapshots \
  --baseline-json docs/ingestion_snapshots/cib_teller_ebca6c00_vlm_on_2026-02-13.json \
  --candidate-upload-id ae4bfb72-d3f6-48c1-81bf-69797ac074c3 \
  --candidate-label cib_teller_vlm_off_candidate \
  --label cib_teller_on_vs_off \
  --focus-row-start 6 \
  --focus-row-end 17 \
  --term "USD 5" \
  --term "USD 2"
```

Outputs:

- candidate snapshot JSON/Markdown
- comparison JSON/Markdown with deltas and regression flags

### 3) Compare two existing JSON snapshots

```bash
cd pocketai_django
python manage.py compare_table_ingestion_snapshots \
  --baseline-json docs/ingestion_snapshots/cib_teller_ebca6c00_vlm_on_2026-02-13.json \
  --candidate-json docs/ingestion_snapshots/cib_teller_ae4bfb72_vlm_off_2026-02-13.json \
  --label cib_teller_on_vs_off
```

## Required Phase 0 Metrics

Each comparison report includes:

- `table_count_delta`
- `table_data_row_count_delta`
- `row_chunk_count_delta`
- `multi_scope_rate_delta`
- `ambiguous_scope_rate_delta`
- `inferred_scope_rate_delta`
- `table_bbox_coverage_ratio_delta`
- `residual_text_ratio_delta`
- `table_row_unique_evidence_count_delta`

## Baseline Manifest

The benchmark case registry lives at:

- `docs/ingestion/phase0_table_ingestion_benchmark_manifest.json`

Current status:

- `banking_fees_and_charges` case is baselined (`CIB-Teller EN`).
- Remaining 5 families are intentionally staged as `pending_capture` until sample docs are ingested.

This keeps Phase 0 generic across industries and prevents banking-specific hardcoding.
