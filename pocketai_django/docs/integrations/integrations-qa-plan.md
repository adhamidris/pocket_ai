# Knowledge Integrations — QA & UX Validation Plan

## Objectives
- Validate the end-to-end Google Drive connector with real OAuth handshakes, sheet selection, privacy configuration, and sync execution.
- Observe at least three non-technical admins completing the wizard (“Connect → Select sheet → Review privacy → Done”) and collect friction notes.
- Establish smoke tests the release team can run before every deploy covering OAuth, sheet selection, sync, and stale/error recovery paths.

## Test Matrix
| Area | Scenario | Owner | Tooling / Notes |
| --- | --- | --- | --- |
| OAuth | Start flow from dashboard, ensure `state` nonce is stored, callback clears metadata, and linked account appears in `/api/integrations/`. | QA | Use staging Google workspace + ngrok callback; confirm status transitions `disconnected → syncing → connected`. |
| Sheet selection | GET `/api/integrations/<id>/sheets/` lists available resources, respects search, surfaces previous privacy configuration. | QA | Mock `discover_google_sheet_resources` where needed; verify validation errors (missing columns, duplicate entries). |
| Privacy enforcement | POST `/api/integrations/<id>/sheets/` without masking when `masking_required=true` returns `400` and message hints. | QA + Backend | Covered by unit tests; manually exercise UI path with console overrides. |
| Sync execution | `POST /api/integrations/google/sync/` triggers worker, updates schedule, returns rows + per-resource outcomes. | QA | Confirm uploads queued only when checksum changes; ingestion jobs appear in admin. |
| Error / stale states | Delete or rename a sheet after selection; run sync; expect stale warnings in `/api/integrations/` + dashboard. | QA + Support | Validate support messaging surfaces `staleResources` payload. |
| Metrics surfacing | Dashboard card shows `lastSyncedAt`, `rowsIngested`, `nextSyncAt`, error badges. | Frontend QA | Compare API payload vs UI rendering. |

## Usability Research
1. **Participant Profile:** Non-technical business admins (Ops/Support). Recruit 3–5 participants from customer council.
2. **Script Highlights:**
   - Task 1: Connect Google Drive and select the “Support Playbook” sheet. Note confusion around scopes or drive selection.
   - Task 2: Configure privacy (mask SSN, hide notes). Gauge understanding of masking terms.
   - Task 3: Trigger “Sync now” and interpret the confirmation page. Capture comprehension of “rows ingested” and “next run”.
3. **Success Metrics:** Completion time (<10 min), # of prompts for help (<2), self-reported confidence (>4/5).
4. **Artifacts:** Record screen, collect quotes, synthesize into FigJam board + actionable backlog tickets for any blockers.

## Smoke Test Checklist (Pre-release)
- [ ] Run `python manage.py sync_knowledge_integrations --integration-id <uuid> --ignore-schedule` on staging; ensure logs show row counts and no stale entries.
- [ ] Execute Cypress (or manual) flow: open dashboard → connect Google → select sheet → save privacy → confirm integration card updated.
- [ ] Post-sync API spot checks:
  - `/api/integrations/` includes `metrics.rowsIngested`.
  - `/api/integrations/<id>/sheets/` caches last sync timestamps.
  - `/api/integrations/google/sync/` returns `status="completed"` within 60s.
- [ ] Trigger credential expiry (revoke token) and verify dashboard surfaces “Reconnect” CTA with `sync_error` message.

## Reporting & Bug Triage
- Log findings in Linear under “Knowledge Integrations QA”.
- Severity rubric:
  - **P0:** OAuth fails entirely, or ingestion writes stale/unmasked data.
  - **P1:** Sync succeeds but UI metrics incorrect.
  - **P2:** Cosmetic issues or missing breadcrumbs.
- Gate release on zero P0/P1; document P2s with targeted fixes/owners.
