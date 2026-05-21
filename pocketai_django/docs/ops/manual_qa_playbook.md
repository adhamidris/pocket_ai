# Manual QA Playbook

## Daily Smoke Checklist
1. **Ingest Sample Catalog** – Upload the latest JSON fixture (or a sample spreadsheet) via the dashboard and confirm ingestion issues surface on the snippet ledger.
2. **Identifier Queries** – From the chat portal, send 2–3 slug/SKU lookups (mix of dashed + underscored IDs). Observe that the response indicates the document + alias short-circuit path; aliases should appear under the ledger entry.
3. **Natural Questions** – Ask contextual questions (pricing, coverage, restrictions). Verify multi-stage retrieval returns structured snippets and no stale table rows.
4. **Not Found Flow** – Send an intentionally wrong identifier (e.g., `policy nb-sec-44`). Ensure the bot explicitly says the record was not found and offers escalation.
5. **Alias Inspection** – From the Django admin `KnowledgeAlias` view, filter by the latest business and confirm short/hyphenated aliases exist with normalized forms.
6. **Latency Spot Check** – Run `python manage.py run_rag_eval --set <slug>` locally and confirm p50/p95 latencies are below the budget.

## Troubleshooting Steps
- **Alias Missing:** Use the admin to inspect `KnowledgeFeedbackCase` and replay the failing query. If alias extraction missed the value, re-ingest with the fixture and verify alias metadata contains the normalized version.
- **Truncation Warnings:** Check `KnowledgeUpload.ingestion_metadata['truncated_entities']` and the `KnowledgeDriftSample` “ingestion” rows. Increase `INGEST_MAX_JSON_ENTITIES` for the business or split the upload.
- **Fallback Misfires:** Review the chat transcript and the `KnowledgeDriftSample` retrieval metrics. If alias hits are low, verify pgvector indexes/embeddings exist and re-run `run_rag_eval` to reproduce.
- **Slow Responses:** Inspect the JSON export (`var/logs/rag_eval_latest.json`) for latency percentiles and compare against `RAG_EVAL_THRESHOLDS`. Enable DEBUG logging on `apps.rag.search.pipeline` to view stage timings.
- **Portal Tool Trace:** Tool calls + top search results render under each assistant message (collapsed by default). Disable via `PORTAL_DEBUG_TOOL_TRACE=0` if needed.

## Portal Feedback Loop
1. In the chat portal, operators click “Report incorrect not found” or POST `/api/chat/feedback/` with:
   ```json
   {
     "session_token": "...",
     "feedback_type": "not_found_incorrect",
     "query_text": "lux-ax-77",
     "expected_aliases": ["lux-ax-77"],
     "notes": "policy exists in travel catalog"
   }
   ```
2. The backend stores a `ConversationFeedback` + `KnowledgeFeedbackCase`. Ops can view them in the admin and use the “Replay in RAG harness” action for instant verification.
3. Once resolved, mark the case inactive to keep future runs lean.

## Emergency Runbook
- **Baseline Regression Detected (CI failure):**
  1. Fetch the JSON artifact from CI and compare against the previous run (metrics under `reports[].metrics`).
  2. Use `python manage.py run_rag_eval --set <slug> --force-reingest` locally to reproduce. The command exits non-zero and prints violating metrics.
  3. Inspect `apps/services/evaluation/datasets.py` to ensure fixtures weren’t modified unexpectedly.
  4. If caused by ingestion changes, re-run the ingestion worker (`python manage.py process_knowledge_ingestion --watch`) to rebuild embeddings.
- **Alias Drift Alert (log warning):** Pull the latest `KnowledgeDriftSample` rows from the admin or via Django shell to view alias length distributions and truncation rates. Update the JSON flattening heuristics or increase entity limits per business metadata.

## Training Tips
- Walk new operators through both docs (`docs/rag/rag_evaluation.md`, this playbook) and the Django admin entries for evaluation runs.
- Encourage logging every manual replay so the feedback cases double as regression tests.
- Keep an eye on `var/logs/rag_eval_latest.json`—it’s the single source of truth for CI artifacts and is safe to attach to tickets.
