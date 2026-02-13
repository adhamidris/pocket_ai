# Scope Engine Rewrite (Phase 3)

## Goal

Infer row applicability on the correct axis only: `scope_dimension_columns`.

This phase replaces ad-hoc row expansion with a deterministic precedence engine and explicit reason taxonomy.

## Engine Contract

Scope engine version: `v3`

Canonical reason codes:

- `scope_explicit_span`
- `scope_repeated_value_span`
- `scope_sparse_expansion`
- `scope_abstain`

Historical legacy mapping (for migration context only):

- `scope_explicit_span` -> `explicit_span`
- `scope_repeated_value_span` -> `inferred_span_extension`
- `scope_sparse_expansion` -> `inferred_sparse_expansion`
- `scope_abstain` -> `explicit_cells`

## Deterministic Precedence

For each row, evaluated in this exact order:

1. `scope_explicit_span`
2. `scope_repeated_value_span`
3. `scope_sparse_expansion`
4. `scope_abstain`

The first matched rule wins and sets both `scope_reason` and `scope_confidence`.

## Strict Axis Rule

When `scope_dimension_columns` exist, `inferred_scope_columns` are constrained to that set.

This prevents qualifier/context columns (for example `Tariff`) from leaking into applicability output.

## Metadata

Row metadata now includes:

- `scope_engine_version`
- `scope_reason` (canonical v3 code)
- `scope_confidence`
- `inferred_scope_columns` (scope-axis only)

Phase 7 removes legacy alias writes/reads from runtime paths; v2 fields are now the only internal contract.

## Why This Is Multi-Tenant Safe

- No domain-specific rules.
- Uses structure-only patterns (spans, repeated values, sparse matrix behavior).
- Emits deterministic reason codes and confidence for observability and benchmark scoring.
