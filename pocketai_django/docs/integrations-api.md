# Knowledge Integrations API

The dashboard now relies on REST-style endpoints so non-technical admins can fetch an inventory of connectors, kick off OAuth, and configure sheet privacy in a wizard flow.

## GET `/api/integrations/`
Accepts optional `business_id` (query or header) and returns `{ businessId, dashboardUrl, integrations, providers, stats }`.

- `integrations` is a list of summary objects `{ id, name, type, status, lastSyncedAt, nextSyncAt, resourceCount, syncError, defaultVisibility, defaultSyncFrequency, hasCredentials, schedule, staleResourceCount, staleResources, actions, account }`.
- `schedule` exposes `{ frequency, nextRunAt, lastRunAt, status, paused }` so the UI can surface cadence + diagnostics.
- `staleResources` lists the first few mismatched sheets (rename/delete) and corresponding messages, allowing the dashboard to show “Reconnect sheet” prompts.
- `providers` enumerates available connectors with their CTA URLs so the UI can render "Connect" cards and tooltips.

## POST `/api/integrations/`
Creates a lightweight `KnowledgeIntegration` row (`name`, `type`, `businessId`). The response wraps the serialized integration so the UI can optimistically append it to the list.

## GET `/api/integrations/<uuid:integration_id>/sheets/`
Replaces the old Google-specific resource endpoint. Query params: optional `business_id`, `limit`, and `q` search text. Response mirrors the previous payload with `integration`, `defaultVisibility`, `defaultSyncFrequency`, `availableResources`, and `selectedResources`. The backend routes to the provider-specific adapter (currently Google Drive) so the same endpoint can serve OneDrive/Notion later.

## POST `/api/integrations/<uuid:integration_id>/sheets/`
Persists selection + privacy metadata. Body expects `resources`, `defaultVisibility`, and `defaultSyncFrequency`. Validation errors (missing drive file id, invalid frequency, incomplete masking) return `400` with `error=VALIDATION_ERROR` for inline UI feedback. Each resource must include `columnPrivacy { sharedColumns[], internalOnlyColumns[], excludedColumns[] }`. When the business’s `table_privacy.masking_required` flag is enabled (or specific `required_masking_columns` are configured), the backend blocks saves until every required column appears in `internalOnlyColumns` or `excludedColumns`. During sync those settings get mirrored into each `KnowledgeUpload.metadata["table_privacy"]`, ensuring ingestion applies the same masking rules the UI collected.

## Privacy Controls & Table Hygiene
- Business metadata (`business.metadata["table_privacy"]`) now drives enforcement:
  - `masking_required=True` forces every selected sheet to classify at least one column as internal/excluded.
  - `required_masking_columns=["ssn","account_number"]` means those columns must be masked per resource before any sync runs.
- When sheets sync, the derived `table_privacy` payload includes `sensitive_columns`, `internal_only_columns`, and `excluded_columns`, so downstream ingestion, audit logs, and “who can view what” policies stay consistent with the UI state.

## Dedup & Delta Strategy
- Each export writes to disk with a deterministic `<resource-id>/<timestamp>.csv` path and a SHA-256 checksum. If the checksum matches the last synced version we mark the resource `unchanged` (0 bytes written) and skip queuing ingestion, preventing duplicate jobs.
- Uploads track `integration_sync` metadata (`last_synced_at`, `sync_frequency`, `checksum_sha256`). Advisory locks ensure only one worker processes a given integration at a time.
- Renamed/deleted sheets (or quota failures) update per-resource `stale_since` metadata and surface in the integrations list so admins can reconnect/rescope before the next run.

## Binary & Non-Text Content
- Google Sheets exports are normalized to CSV (or XLSX when requested) and fed into the ingestion pipeline as text. Images, drawings, and binary blobs in sheets are ignored; their metadata is captured only as references in `KnowledgeUploadFile.metadata`.
- If future connectors ship binary payloads (e.g., Excel with embedded charts), `IntegrationSyncService` records the checksum but flags the upload with `metadata["integration_sync"]["stale_reason"]` so reviewers know that non-text content was skipped rather than ingested.

## Scheduling & Control Surface
- **Worker selection:** We standardize on the `sync_knowledge_integrations` management command so teams can run a long-lived watcher or schedule periodic syncs using existing tooling.
  - Long-running (systemd/container): `python manage.py sync_knowledge_integrations --watch --sleep 300` keeps a single process alive, polling due integrations per tenant cadence while honoring advisory locks.
  - Cron/supercronic: `*/30 * * * * source /app/.venv/bin/activate && cd /app && python manage.py sync_knowledge_integrations --ignore-schedule` ensures a periodic sweep even if the watcher is offline.
  - Celery Beat: add an entry that periodically invokes `call_command("sync_knowledge_integrations")` from a Celery task; the command itself coordinates locking so multiple workers can’t process the same integration concurrently.
- **Sync now API:** `POST /api/integrations/google/sync/` triggers the same service path used by the scheduler (locks + dedupe). The response includes `rowsIngested`, per-resource statuses, and the newly computed `nextSyncAt` so the UI can render “Sync started / finishing at HH:MM”.
- **Metrics to UI:** `/api/integrations/` summaries now bundle `metrics` (`resourcesAttempted`, `successCount`, `failureCount`, `bytesWritten`, `rowsIngested`, `durationMs`, `lastRunAt`) plus `schedule.nextRunAt`. This enables dashboards to show “Last synced 3m ago · 2 sheets · 1.4K rows” alongside CTA buttons.

## POST `/api/integrations/google/start`
Unchanged OAuth kickoff endpoint; include `businessId` when present so the callback can mark the right integration `status="syncing"` and embed the state nonce.

Concrete request/response snapshots live in `apps/api/tests/test_google_integrations.py`.
