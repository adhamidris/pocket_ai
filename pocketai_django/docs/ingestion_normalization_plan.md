# Ingestion Normalization Plan (Phase 1)

This note locks in the behavior, overrides, and touch points for normalizing tabular uploads before we refactor any code.

## Goals & Scope

- Deliver identical table payloads whether content originates from manual XLSX/CSV uploads or Google Sheets exports.
- Remove “NULL”/error artefacts before they surface in embeddings, entities, or answers.
- Keep the ingestion engine, privacy rules, and limit enforcement untouched—normalization is a preprocessing layer.
- Ensure the feature is tenant-aware, observable, and easily rolled back.

## Normalization Contract

| Concern | Decision |
| --- | --- |
| **Trigger** | Controlled by `settings.INGEST_NORMALIZE_TABLES` (default `True`). When disabled we bypass the helpers entirely. |
| **Null-like tokens** | The baseline policy treats the following case-insensitively as empty: `""`, whitespace, `None`, `"NULL"`, `"null"`, `"N/A"`, `"n/a"`, `"NA"`, `"na"`, `"NaN"`, `"nan"`, `"#N/A"`, `"#REF!"`, `"#DIV/0!"`, `"undefined"`. Tokens are configurable per tenant (see overrides). |
| **Cell coercion** | Formulas already resolve via `openpyxl.load_workbook(..., data_only=True)`. After that: trim whitespace → convert any null-like token to `""` → convert other scalars to strings. Booleans become `"TRUE"` / `"FALSE"`, numerics get `str(value)`. |
| **Row trimming** | Drop a row when all normalized cells are `""`. Track counts globally and per sheet. |
| **Column trimming** | Iterate schema left→right; drop trailing columns only if **all remaining rows** are empty for that column. Preserve column order; ensure at least one column survives. Capture counts in diagnostics. |
| **Sheet selection** | Ingest every sheet that has ≥1 non-empty data row after normalization. Empty sheets are skipped and reported in diagnostics. |
| **Header handling** | Use the first remaining row as headers, applying the same normalization. Empty headers become `column_{n}`. Column whitelist logic still runs after normalization. |
| **Diagnostics (`ingestion_metadata`)** | Add/merge a `normalization` struct: <br>```json
{
  "normalization": {
    "enabled": true,
    "policy_version": "v1",
    "null_tokens": ["NULL", "N/A", "NaN", "#REF!", "#DIV/0!"],
    "rows_dropped": {"total": 12, "by_sheet": {"Sheet1": 10, "Sheet3": 2}},
    "columns_trimmed": {"total": 3, "by_sheet": {"Sheet2": 3}},
    "empty_sheets_skipped": ["Archive", "tmp"],
    "tokens_replaced": 57
  }
}
```<br>Counts only include operations caused by normalization. Existing ingestion metrics remain unchanged. |

## Business / Tenant Overrides

- Source: `business_profile.metadata["table_policy"]` (existing dict) expanded with normalization keys.
- Supported overrides (all optional):
  - `enable_normalization` (bool) → opt a tenant out without touching global setting.
  - `null_tokens` (list[str]) → merged with defaults after canonicalization.
  - `sheet_whitelist` (list[str]) / `sheet_blacklist` → restrict ingestion to named sheets when required.
  - `drop_empty_columns` (bool) → default `True`.
- Upload-level overrides: `upload.metadata["table_policy"]` wins over business metadata for a single file (mirrors existing table limit overrides).
- Policy resolution precedence: upload → business → defaults.

## Helper / Module Design

- New module: `apps/services/table_normalization.py` (pure functions + dataclasses).
- Core types:
  - `TableNormalizationPolicy`: resolved from settings + metadata; exposes the token list, sheet filters, booleans.
  - `NormalizationResult`: holds cleaned rows plus diagnostics (`rows_dropped`, `columns_trimmed`, `tokens_replaced`, `skipped_sheets`).
- Key helpers (Phase 2 impl):
  1. `resolve_normalization_policy(upload: KnowledgeUpload | None) -> TableNormalizationPolicy`
  2. `normalize_sheet_rows(raw_rows: list[list[Any]], policy, sheet_name: str) -> NormalizedSheet`
  3. `build_table_from_rows(rows: list[list[str]], sheet_meta, policy) -> TablePayload`
  4. `merge_normalization_metadata(upload, diag)` → updates `upload.ingestion_metadata["normalization"]`.
- `_extract_xlsx` flow:
  1. Resolve policy once per upload.
  2. For each worksheet, gather `raw_rows` via `sheet.iter_rows(values_only=True)`.
  3. Call `normalize_sheet_rows` → returns cleaned rows and per-sheet diagnostics.
  4. Skip sheet if `rows` empty; otherwise feed rows into `build_table_from_rows`.
- `_extract_csv` flow:
  1. Parse CSV as today to `raw_rows`.
  2. Reuse the same `normalize_sheet_rows` (a faux “sheet” name like `"CSV"`).
  3. Construct `TablePayload` via `build_table_from_rows`.

## Settings & Feature Flag

- Add `INGEST_NORMALIZE_TABLES = bool(os.getenv("INGEST_NORMALIZE_TABLES", "true").lower() in {"1","true","yes"})` to `pocketai/settings.py`.
- Expose policy version under `settings.INGEST_NORMALIZATION_POLICY_VERSION = "v1"` for future migrations and telemetry.

## Deliverables for Subsequent Phases

This doc satisfies Phase 1. Phases 2–5 will:

1. Implement the helpers/tests described above.
2. Refactor `_extract_xlsx` / `_extract_csv` to use them.
3. Align CSV/Google ingestion outputs via fixtures.
4. Wire observability + feature flag + docs.
5. Monitor tenants and iterate on overrides (sheet whitelists, special tokens) post-release.

## Verification Artifacts

- Automated parity test (`apps/services/tests/test_ingestion_normalization_parity.py`) writes a sample workbook and its CSV export, then asserts matching column schemas, row contents, and normalization stats. This simulates the “manual upload vs. Google export” scenario described in Phase 3.

## Release Note Copy

Share the following snippet with customer success / tenant announcements when the feature flag is enabled:

> **Excel uploads now auto-cleaned:** We now normalize spreadsheets before ingestion so blanks, `NULL` values, and empty sheets match how Google Sheets sync behaves. No action is required—your existing uploads will ingest with fewer errors. If you need to opt out temporarily, set `table_policy.enable_normalization=false` for the affected upload or business (or contact support).

## Post-Rollout Evaluation Checklist (Phase 5)

1. **Monitor ingestion warnings:** Use the existing `apps.services.knowledge_ops.KnowledgeOpsDashboard` (ingestion section) or `KnowledgeUpload` admin filters (`ingestion_metadata__normalization__rows_dropped__gt=0`) to spot tenants still seeing large drop counts. Share screenshots with success teams.
2. **Compare error rates:** Weekly, pull `KnowledgeUpload` records where `ingestion_error` is not empty and correlate with the normalization log (`ingest.normalization ...`). Expect NULL-related failures to drop materially; flag regressions in Slack.
3. **Gather tenant feedback:** Add a line item to the onboarding QA checklist asking whether “Excel uploads ingested cleanly”; when issues appear, capture the file and update `business_profile.metadata["table_policy"]` with custom tokens/whitelists as needed.
4. **Decide on advanced controls:** If internal teams need parity with raw XLSX exports from Google, capture the use case. Only then prioritize the “export as XLSX” option or per-tenant sheet-selection policies.
5. **Report findings:** After two weeks, summarize metrics (rows dropped by cohort, ingestion error counts, customer tickets) in the release thread so leadership can sign off on the rollout.
