# RAG Evaluation & Guardrails

## Golden Fixtures
- Curated JSON fixtures live in `apps/services/evaluation/fixtures/` and are registered in `apps/services/evaluation/datasets.py`.
- Each dataset declares fixtures + `GoldenQuery` definitions (identifier, natural, and not-found cases) with expected behaviors and entity targets.
- `KnowledgeFeedbackCase` rows (auto-created from portal feedback) are appended to the active golden set so flagged production misses are replayed automatically.
- Available sets: `travel`, `insurance`, `cards`, and the new table-heavy `jobs` sheet fixture for proper-noun questions that should resolve from CSV uploads.

## Running the Harness
- Execute `python manage.py run_rag_eval` to ingest fixtures, run alias + hybrid retrieval, compute metrics, and export `var/logs/rag_eval_latest.json`.
- Flags:
  - `--set <slug>` to scope to a single industry dataset (travel, insurance, cards, etc.).
  - `--force-reingest` to reprocess fixture uploads.
  - `--output <path>` to override the JSON artifact location.
- The command surfaces per-set summaries and fails (non-zero) if thresholds are violated.
- Full results (metrics, latencies, violations, per-query diagnostics) are written to JSON and persisted to `accounts_rag_evaluation_run` for dashboards.

## Metrics & Thresholds
- Core minimums: `identifier_top1 >= 0.90`, `not_found_accuracy >= 0.95`, `mrr >= 0.92`.
- Latency ceiling: `vector.p95 <= 350ms`.
- Additional telemetry recorded per run: precision@k, alias short-circuit rate, latency percentiles.
- Threshold config lives in `RAG_EVAL_THRESHOLDS` (settings/env). Adjust env vars to tune gates.

## Drift Monitoring
- Ingestion stats (alias length p50/p95, truncation rate) and retrieval stats (alias hit/fallback events) are stored in `accounts_knowledge_drift_sample`.
- Alerts fire via logs when truncation rate exceeds `RAG_DRIFT_TRUNCATION_THRESHOLD` (default 20%) or identifier alias-hit rate over the last 50 samples drops below `RAG_DRIFT_ALIAS_HIT_THRESHOLD` (default 0.85).
- Use the admin views for `KnowledgeDriftSample` and `RAGEvaluationRun` to inspect historical trends.

## Extending Fixtures
1. Drop a JSON fixture in `apps/services/evaluation/fixtures/`.
2. Register a `GoldenFixture` + `GoldenSet` entry in `datasets.py` and supply identifier / natural queries with target entities.
3. Run `python manage.py run_rag_eval --set <slug> --force-reingest` to validate and refresh CI artifacts.
4. Commit fixture + dataset updates under source control.

## Promoting Feedback Cases
- Portal operators can POST `/api/chat/feedback/` with `feedback_type="not_found_incorrect"`, `query_text`, and optional expected aliases/entities.
- The backend writes a `ConversationFeedback` plus a `KnowledgeFeedbackCase` (auto-active by default).
- Admins can replay cases via the “Replay in RAG harness” action inside the Django admin.
- Deactivate stale cases from the admin once the regression is fixed to keep the golden set focused.

## CI / Local Workflow
1. Ensure Postgres + Redis are available; run migrations to apply the new tables.
2. Run the harness locally before opening a PR: `python manage.py run_rag_eval --force-reingest`.
3. Inspect `var/logs/rag_eval_latest.json` and the `accounts_rag_evaluation_run` admin for diff vs last run.
4. CI jobs should upload the JSON artifact so regressions are diffable without a DB.

## Developer Tips
- Harness runs both the alias path and free-text pipeline; use the per-query diagnostics to inspect latency, path selection, and reranker outcomes.
- When adding a new industry, start with at least 3 identifier queries, 2 natural questions, and 1 explicit not-found case.
- For deterministic ID coverage, prefer alias strings that match production slugs/SKUs and include mixed-case/underscore variants to avoid drift surprises.
