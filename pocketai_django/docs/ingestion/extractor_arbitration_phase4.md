# Extractor Arbitration and VLM Guardrails (Phase 4)

## Goal

Prevent unstable table replacements by treating VLM output as a candidate that must pass regression gates before acceptance.

## Candidate Arbitration

The pipeline keeps extractor candidate scoring (`azure`, `geometry`, `pdfplumber`, `heuristic`) and then runs optional VLM repair on selected tables.

VLM is no longer implicitly trusted. Every VLM table is compared against its non-VLM baseline with explicit guardrails.

## Acceptance Gates

A VLM candidate is rejected if any of these regressions are detected:

- row coverage regression
- row order regression
- schema coverage regression
- value/cell coverage regression
- scope row regression
- scope quality regression
- scope axis violation increase

Default thresholds:

- row recall >= `0.99`
- row order LCS ratio >= `0.7` (for 3+ baseline rows)
- schema recall >= `0.9`
- non-empty cell recall >= `0.9`
- scope recall: must not regress when baseline has scope

## Diagnostics

`table_repairs` metadata now includes guardrail outcomes:

- `attempted`
- `repaired`
- `rejected`
- `guardrails_enabled`
- `guardrail_diagnostics[]`
- `rejected_tables[]`

Rejected candidates emit issue code:

- `table_vlm_rejected_regression`

with reasons and metrics in issue details.

## Configuration

New settings:

- `RAG_TABLE_VLM_GUARDRAILS_ENABLED`
- `RAG_TABLE_VLM_GUARDRAIL_MIN_ROW_RECALL`
- `RAG_TABLE_VLM_GUARDRAIL_MIN_ORDER_RATIO`
- `RAG_TABLE_VLM_GUARDRAIL_MIN_SCHEMA_RECALL`
- `RAG_TABLE_VLM_GUARDRAIL_MIN_CELL_RECALL`

## Multi-Tenant Safety

All checks are structural/statistical and domain-agnostic. No banking-specific rules are used.
