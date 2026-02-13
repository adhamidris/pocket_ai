# Canonical Table Scope Contract v2 (Phase 1)

This document defines the Phase 1 metadata contract that removes ambiguity from row applicability.

## Versioning

- Contract key: `table_scope_contract_version`
- Current version: `v2`
- Storage: `KnowledgeUploadTableRow.metadata` and row chunk metadata

## v2 Fields

Each table row should publish:

1. `observed_value_columns`
2. `qualifier_columns`
3. `scope_dimension_columns`
4. `inferred_scope_columns`
5. `scope_confidence`
6. `scope_reason`

### Field semantics

- `observed_value_columns`: columns that are non-empty for this row.
- `qualifier_columns`: non-scope context columns for this row (for example descriptor/currency axes).
- `scope_dimension_columns`: candidate scope axis columns.
- `inferred_scope_columns`: final inferred applicability scope.
- `scope_confidence`: confidence for `inferred_scope_columns` (0-1 when available).
- `scope_reason`: deterministic reason code/mode for scope inference.

## Backward Compatibility (Temporary)

Phase 1 dual-writes legacy aliases:

- `applies_to_columns` -> alias of `inferred_scope_columns`
- `applicability_mode` -> alias of `scope_reason`
- `applicability_confidence` -> alias of `scope_confidence`

Chunk metadata keeps compatibility aliases too:

- `table_row_applies_to_columns`
- `table_row_applicability_mode`
- `table_row_applicability_confidence`

while also emitting v2 keys:

- `table_row_contract_version`
- `table_row_observed_value_columns`
- `table_row_qualifier_columns`
- `table_row_scope_dimension_columns`
- `table_row_inferred_scope_columns`
- `table_row_scope_reason`
- `table_row_scope_confidence`

## Consumer Migration Notes

All internal consumers must read v2-first and fall back to legacy aliases only during migration.

Updated in Phase 1:

- Ingestion row chunk builder (`KnowledgeIngestionService`)
- RAG orchestrator diagnostics + applicability guardrails
- RAG evaluation harness scope metrics
- MCP table row serializers
- Ingestion benchmark snapshots/comparisons

## Deprecation Plan

1. Phase 2-4: continue dual-write + v2-first reads.
2. Phase 5 CI gates: enforce v2 field presence on table rows/chunks.
3. Phase 7 cleanup: remove legacy alias reads/writes and keep v2 only.
