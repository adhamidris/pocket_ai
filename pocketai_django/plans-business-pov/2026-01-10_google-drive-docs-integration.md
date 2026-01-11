# Google Drive → Google Docs Sync — Business POV

## Plan
1. Confirm scope: Google Docs only vs “all Drive documents” (Docs + uploaded DOCX/PDF), and whether folder-based sync is required for v1.
2. Backend discovery: add `discover_google_doc_resources()` alongside `discover_google_sheet_resources()` (Drive search for `mimeType='application/vnd.google-apps.document'`), returning `{resource_id, drive_file_id, drive_file_name, modified_time, owner, owner_email, web_view_link, mime_type}`.
3. Resource model shape: extend integration resource configs with a `resource_kind` discriminator (`sheet` default for existing rows, `doc` for Docs) so we can store both types without breaking existing Sheets behavior.
4. API: add `GET/POST /api/integrations/<integration_id>/docs/` mirroring the Sheets collection shape but without column-privacy fields; filter `/sheets/` responses so `selectedResources` only includes `resource_kind=sheet` items (back-compat: missing kind ⇒ sheet).
5. Sync service: update `IntegrationSyncService` to route exports by `resource_kind`:
   - `sheet`: keep current exporter + table privacy enforcement + row count.
   - `doc`: export via Google Drive “files export” to `docx` (or `text/plain` as fallback), skip table privacy enforcement, and store doc metadata on the `KnowledgeUpload` for provenance.
6. Dashboard UI: keep a single **Google Drive** integration card, add a **Manage docs** action (or a tab in the existing modal) that lists Docs, supports search, selection, visibility, and sync frequency, and reuses the same OAuth connection.
7. Tests:
   - API tests for `/docs/` (list + save) and for `/sheets/` filtering when docs exist.
   - Sync tests ensuring doc resources export/ingest and do not trip sheet masking policies.
   - Regression tests for existing Sheets flows (resource selection + sync now).
8. Rollout + docs:
   - Update `docs/integrations/integrations-api.md` and the admin playbook to include Docs.
   - Gate UI exposure behind a feature flag if needed (enable per-tenant), then roll out gradually.

## Business POV
### Scenario 1: Ops admin keeps SOPs current (Google Docs)
- Before: SOPs are uploaded manually as PDFs/DOCX; updates drift and admins forget to re-upload.
- After: Admin connects Google Drive once, selects key SOP Docs, sets daily sync, and PocketAI always answers from the latest procedures.
- Success signals: fewer “policy out of date” tickets; higher answer accuracy on SOP questions; near-zero manual re-uploads.

### Scenario 2: Mixed knowledge sources (Sheets + Docs) in one place
- Before: Pricing lives in Sheets (synced) but policy/FAQs live in Docs (manual), creating inconsistent answers.
- After: One Google Drive integration manages both Sheets and Docs with separate “Manage sheets / Manage docs” flows; admins don’t need two OAuth connections.
- Success signals: fewer admin steps in onboarding; reduced “where do I connect this?” confusion; faster time-to-first-accurate-answer.

### Scenario 3: Sensitive content in Docs
- Before: Admins hesitate to ingest Docs that may contain PII; unclear safeguards.
- After: Docs can be set to `internal`/`private` visibility; retrieval remains privacy-by-default (no raw content in logs, existing PII protections apply).
- Success signals: increased adoption of Docs sync without an increase in privacy incidents; audit logs show controlled access without content leakage.

### Scenario 4: Credentials expire or access is revoked
- Before: Sync silently fails or partially updates; admins only notice when answers are wrong.
- After: Dashboard shows “Needs attention / reconnect” clearly; syncing pauses deterministically and resumes after reconnection, for both Sheets and Docs.
- Success signals: lower mean-time-to-detect credential issues; fewer stale resources; fewer support escalations.

