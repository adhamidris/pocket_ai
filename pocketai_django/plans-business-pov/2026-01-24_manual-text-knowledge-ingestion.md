# Plan: Manual Text Knowledge Ingestion

## Goal
Ensure "Manual Entry" knowledge added from the dashboard is ingested (chunked + embedded) so it appears in the Knowledge Visualizer and is discoverable/retrievable via RAG tools.

## Implementation Plan
1. Enable ingestion for `KnowledgeSourceType.TEXT` in the ingestion pipeline and job queue.
2. Update the dashboard "Manual Entry" create flow to queue ingestion and reflect correct status (processing until ingested).
3. Add an ops/backfill path for existing text uploads that were previously marked active but never chunked.
4. Add tests covering: job queue creation, text extraction path, and chunk persistence.
5. Run the relevant unit tests and ensure no tenant-scope regressions.

## Business POV (What Users Experience)

### Scenario 1: Add a short FAQ snippet
- User pastes a paragraph into "Manual Entry" and saves.
- Expected: status shows as "Processing" briefly, then "Active".
- Visualizer: shows chunks populated (and metadata), not empty.
- Success metric: the agent can answer questions referencing that snippet within 1–2 minutes (or immediately if worker is running).

### Scenario 2: Add a long internal playbook
- User pastes a multi-page playbook.
- Expected: ingestion completes without timeouts; chunks are created and searchable.
- Risk: large entries could increase ingestion latency.
- Success metric: chunk count is non-zero; `search_knowledge` returns hits for key phrases.

### Scenario 3: Worker not running
- User adds a manual entry but no background worker is active.
- Expected: the item stays in "Processing" (not misleadingly "Active") until the worker runs.
- Success metric: user can clearly see ingestion hasn’t completed (no false “active but unreadable” state).

### Scenario 4: Existing “active but empty” manual entries
- User has older manual entries that were previously saved as active.
- Expected: backfill/requeue can ingest them so they become retrievable.
- Success metric: after backfill, visualizer shows chunks and RAG can retrieve content.

