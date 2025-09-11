# KNOWN GAPS — Channels & Publishing

UI-only implementation notes and deferred items for the Channels & Publishing area.

## Link & Shortener
- Short link toggle is UI-only; no real URL shortener integration.
- Rotation does not invalidate previous links server-side; no broadcast mechanism.
- No rate limiting on rotations; no audit trail of rotations.

## QR Codes
- QR is a placeholder drawing; no real PNG/SVG generation or file export.
- No image saving permissions flow; no share sheet.

## Channel Verification
- Verification is simulated; no API checks or OAuth flows.
- No domain/DNS verification integration or retry/backoff policy.
- Logs are local-only and non-persistent.

## Widget Snippets
- Snippets are examples only; no packaged widget script.
- No copy-to-clipboard native integration; toast is simulated.
- No SPA framework adapters or npm packages.

## UTM Builder
- UTM preview is local-only; no link management service.
- Shorten toggle generates a fake code; no analytics pipeline.

## Cross‑App & Portal
- “Test in Portal” uses in-app navigation only; no authenticated web portal.
- No deep link handoff to external hosted portal domain.

## Performance & Accessibility
- Basic memoization and debouncing only; no device profiling.
- A11y labels added for primary actions but not fully audited.

## Storage & Sync
- No persistence for link/channel states; in-memory only.
- Offline queue visuals are timers; no durable queue or conflict resolution.
- Sync Center is a UI shell; no sync engine or reconciliation.

## Security & Governance
- No permissions/roles around rotation or verification.
- No multi-domain whitelist management.
- No PII or secrets management for theming/links.


