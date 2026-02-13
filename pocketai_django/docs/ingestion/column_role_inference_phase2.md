# Column Role Inference (Phase 2)

## Goal

Infer table column roles using structure/statistics only (no domain dictionaries), then persist role confidence so downstream scope inference is explainable and stable across industries.

## Contract

Each table now carries role metadata in `KnowledgeUploadTable.data_dictionary`:

- `column_role_inference_version`: inference contract version (`v1`)
- `column_roles`: per-column payloads with:
  - `column_index`
  - `column_key`
  - `role` (`descriptor`, `qualifier`, `scope_dimension`, `note`)
  - `confidence`
  - `role_scores`
  - `signals`
- `column_roles_by_type`: grouped column labels by role
- `column_role_summary`: role counts and average confidence

Extraction-level metadata also records:

- `column_role_inference.version`
- `column_role_inference.table_count`
- `column_role_inference.tables_with_roles`
- `column_role_inference.columns_profiled`

## Inference Strategy

The inference module uses only generic signals:

- column non-empty ratio
- distinct-value ratio and dominant-value ratio
- numeric/amount pattern ratio
- average text length and long/short text ratio
- sparse row participation and singleton participation across candidate scope columns

No tenant, language, or industry-specific header keywords are required.

## Runtime Flow

1. Extract table rows/cells (Azure/geometry/VLM/other path).
2. Run table-level role inference once on extracted tables.
3. Persist inferred roles and confidence in table `data_dictionary`.
4. Row-level scope contract reads cached `column_roles` first, then falls back to on-the-fly inference if missing.

## Backward Compatibility

Phase 2 does not remove v1/v2 scope aliases. `applies_to_columns` compatibility fields remain available while internal scope logic shifts to role-aware columns.

## Why This Is Multi-Tenant Safe

- No banking assumptions.
- No keyword lists required for qualifier/scope separation.
- Confidence and signal payloads are persisted for auditability and benchmark gating across document families.
