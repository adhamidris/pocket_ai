# KNOWN_GAPS — Release Candidate (UI-only)

Scope: React Native mobile app. This document lists backend-dependent areas not wired in the RC UI build, with proposed contracts for integration.

## 1) Transport / Networking
- Missing: API client, auth headers, retry/backoff, network error normalization, pagination helpers.
- Contract:
  - Base URL(s) per env; timeout; telemetry sampling headers.
  - Error schema: { code: string; message: string; details?: any }.
  - List endpoints: cursor/offset pagination; total hints.

## 2) Auth / SSO
- Missing: Sign-in/up, token refresh, SSO (OIDC/SAML), session persistence, logout, device trust.
- Contract:
  - Token shape (access/refresh, expiry); reauth triggers.
  - Profile minimal fields for UI header/avatar.

## 3) Persistence / Sync
- Missing: Offline caches, delta sync, conflict policy, background refresh.
- Contract:
  - Per-module cache keys and TTL; versioning for invalidation.
  - Sync events to drive `SyncCenterSheet` counts.

## 4) Analytics Pipeline
- Missing: Network sink for `track(event, props)`, batching, user/session IDs, consent guard.
- Contract:
  - Event schema registry per module; PII flags; sampling.
  - Export endpoints for aggregated dashboards.

## 5) Export Packaging
- Missing: Export job start/poll/download; formats (CSV/JSON/Parquet); notifications.
- Contract:
  - Async job contract { id, status, progress, links }.

## 6) Billing Processor
- Missing: Plan, entitlements, checkout, coupons, invoices, dunning webhooks.
- Contract:
  - Plan → entitlement map; invoice item schema; payment method vault refs.

## 7) Secrets Vault
- Missing: Create/rotate/list secrets, KMS integration, access policy.
- Contract:
  - Secret metadata (name, scope, lastRotatedAt); write-once flows; audit entries.

## 8) Actions Executor
- Missing: Run queue, approvals, rate limits, result storage, rollback hooks.
- Contract:
  - Run state machine; result blob schema; guard evaluation results.

## 9) Conversations Backend
- Missing: Ingest, routing, SLA engine, transcript search, attachments storage.
- Contract:
  - Conversation summary fields (ids, lastMessage, SLA state); thread events feed.

## 10) Assistant & Knowledge
- Missing: Retrieval, routing to tools, citations, safety filters, answer streaming.
- Contract:
  - Answer chunk protocol with citations; safety flags; tool invocation traces.

## 11) Channels & Portal
- Missing: OAuth/App creds, webhooks, message send/receive, rate-limit & queue data.
- Contract:
  - Channel connection objects; portal session state; CSAT submit payload.

## 12) Security / Privacy
- Missing: Privacy modes policy source; audit log append; data residency policy.
- Contract:
  - Audit event shape; residency badges from org policy; retention rules validation.

## 13) Settings & Theming
- Missing: Theme publish pipeline, asset upload, CDN invalidation.
- Contract:
  - Theme token payload; brand asset URLs; publish status updates.

## 14) Contracts Needed (Cross-cutting)
- Types per module exposed as OpenAPI/TS types.
- Event schemas for analytics and audit.
- Deep-link route params and persistence rules.
- Entitlement keys (feature → limits) and evaluation logic.

## References
- QA audit screens in app: ComponentGallery, DeepLinkAudit, AccessibilityAudit, I18nAudit, ThemeAudit, OfflineAudit, PerfAudit, StateCoverageAudit, TelemetryAudit, EntitlementAudit, SecPrivacyAudit, ScriptedWalks, RC_Preflight, LaunchRollout.


