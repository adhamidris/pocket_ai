# KNOWN GAPS — CRM Module

This file tracks deferred items and limitations for the UI-only CRM implementation.

## Data, Storage, and Sync
- No persistent storage; all data is generated locally in-memory.
- Offline queue is simulated with timers; no real retry/backoff.
- Sync Center is a UI shell; no real sync pipeline or conflict resolution.

## Dedupe & Merge
- Matching is mocked; no server-side fuzzy matching or merge audit log.
- Merge operation mutates local list only; no persistence or background job.

## Segments
- Rules are limited and evaluated client-side; no scheduling or auto-refresh.
- No server-evaluated segments; no large dataset pagination.

## Consent & Privacy
- Consent history is static demo data; no policy journaling or DSAR workflow.
- “Request Deletion” only logs analytics; no ticketing or approval flow.

## Import / Export
- Import expects prepared objects; no CSV parsing or validation pipeline.
- No file picker integration; no error handling for malformed data.

## Cross‑App Hooks
- “Start Conversation” uses demo activity; no cross-index search to thread.
- Prefill into Conversations is UI-only; no draft persistence.

## Accessibility & Localization
- i18n strings are placeholders; Arabic/English translation deferred.
- Limited dynamic RTL refinements beyond core views.

## Performance
- Virtualization tuned heuristically; no device-adaptive profiling.
- A–Z index is in-memory and depends on name initial only.

## Security
- No auth/roles enforcement on actions; no audit trails.


