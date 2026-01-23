# Plan: First‑party Email Connectors (Google + Microsoft)

Goal: Let non-technical end users connect their business email (Gmail/Google Workspace + Microsoft 365/Outlook) via OAuth and then use agentic automation to **read/search/send** email reliably. Users should only do the OAuth consent step; no secrets, no “server URLs”, no MCP marketplace fragility.

Non-goals (for initial launch):
- “All email providers” (Yahoo/Zoho/custom IMAP) on day one.
- Attachments (sending/reading) on day one.
- Shared/team inboxes (delegation/shared mailbox) on day one.

Core invariants to preserve:
- Tenant isolation (no cross-business reads/writes, including jobs/caches).
- Privacy by default (minimize storage of email content; redact logs).
- Auditability (log actions without raw email bodies/PII in logs).
- Deterministic retrieval (bounded search/results; no unbounded thrash).

---

## Phase 0 — Product + Security Alignment (lock decisions)

1) Default send behavior
- Default to **draft + approval** for all users.
- Offer per-connection or per-agent “Auto-send” opt-in (explicit user toggle).

2) Data storage policy
- Default: **live provider read/search** (store only metadata/pointers).
- Optional later: “Save to case / index mailbox” (explicit user action) with retention controls.

3) Recipient policy (avoid “allowlist obstacles”)
- No allowlist needed for draft+approval mode.
- For auto-send mode: add a “step-up” rule (require approval) for risky recipients (new recipient, external domain, bulk send), rather than hard-blocking.

4) Provider scope minimization
- Request minimum OAuth scopes needed for read/search/send.
- Avoid broad scopes by default to reduce verification friction and user distrust.

Deliverables:
- Written policy doc (in-repo) describing send modes, storage defaults, and audit/redaction rules.

Acceptance:
- Clear UX rules for “what is automatic” vs “what needs approval”.

---

## Phase 1 — Shared Email Connector Foundation (provider-agnostic)

Work:
1) Models (tenant-scoped)
- `EmailAccount` (or similar) scoped to `business_profile` + `user`:
  - provider: `google` / `microsoft`
  - email_address, provider_user_id
  - encrypted refresh token + token metadata (scopes, expires_at, token_type)
  - status + last_error + timestamps

2) OAuth plumbing (shared patterns)
- Server-side OAuth start/callback endpoints.
- Strong CSRF/state protection + PKCE where appropriate.
- Strict redirect handling (no open redirects).

3) Credential safety
- Encrypt tokens at rest; never log tokens.
- Redact tool inputs/outputs and audit metadata.
- Rotation plan for encryption keys/versioning.

4) Internal “Email Tools” interface (stable for the LLM)
- `email_search(query, limit, after?, before?, from?, to?, subject?, provider?)`
- `email_get_message(message_id)`
- `email_get_thread(thread_id)`
- `email_create_draft(to, cc?, bcc?, subject, body_text|body_html)`
- `email_send_draft(draft_id)` (or `email_send(...)` depending on provider)

5) Budgets + determinism
- Hard caps on results, pages, and total provider calls per user message.
- Explicit error messages (rate limited / auth expired / not connected).

Deliverables:
- DB schema + encrypted token storage.
- Provider-agnostic service layer + tool contract (schemas).

Acceptance:
- Tenant-safe “connected account” record exists and can refresh tokens.
- Tools exist with bounded behavior (even before provider integration is complete).

---

## Phase 2 — Google (Gmail API) End-to-End

Work:
1) Google OAuth app setup (platform-owned)
- One OAuth client for PocketAI (per environment).
- Dev: Testing mode + test users; Prod: verification planning.

2) Minimal OAuth scopes (read/search/send)
- Gmail read/search + Gmail send, plus identity/email scope to identify the mailbox.

3) Gmail API implementation
- Search: use Gmail query syntax (server-side; return bounded results).
- Read: fetch message/thread; normalize to a safe internal representation.
- Send: create draft + send (with approval gating).

4) Failure modes
- Token refresh errors, revoked tokens, 401/403 handling.
- Rate limits with backoff and user-visible guidance.

Acceptance:
- Connect Gmail → immediately usable tools in chat portal.
- “Search email from 2017” works via provider search (bounded/paginated).
- “Send email” produces draft + approval; then sends and logs an audit event.

---

## Phase 3 — Microsoft 365 (Outlook via Graph) End-to-End

Work:
1) Microsoft Entra app registration (platform-owned)
- OAuth client and redirect URIs for each environment.

2) Minimal permissions
- Mail.Read + Mail.Send (and offline_access as needed), plus user identity.

3) Graph API implementation
- Search + read with Graph constraints.
- Draft + send workflow aligned with the same internal tool contract.

4) Failure modes
- Tenant admin consent considerations where applicable.
- Rate limits and transient failures with safe retries.

Acceptance:
- Connect Microsoft → usable tools without end-user secrets.
- Same “draft+approval” send behavior as Gmail.

---

## Phase 4 — Operations, Observability, and Scale Readiness

Work:
- Background jobs for:
  - token refresh health checks
  - connection “test” and status updates
- Metrics:
  - connect success rate, send success rate, provider error rates, p95 API latency
- Audit logs (metadata-only) for:
  - connect/disconnect, sends (requested/approved/sent), reads/searches (counts only)
- Optional later:
  - mailbox indexing (explicit opt-in) for speed/analytics; retention/deletion controls

Acceptance:
- No infinite polling UX; connection status is clear (connected/testing/error).
- Safe operating model: predictable load + bounded retrieval per message.

---

# Business POV (what improves, what might regress, how we measure success)

## Scenario 1 — “Connect my email” (non-technical user)
- User clicks “Connect Gmail” → Google consent screen → returns to PocketAI as “Connected”.
- Expected: no secrets, no server URLs, no manual steps besides OAuth.
- Success metric: >95% connect success rate for test users; median connect time < 45s.

## Scenario 2 — “Find an email from 2017 and summarize”
- User asks in chat: “Find the 2017 email thread with Client X and summarize what went wrong.”
- System performs live provider search with bounded paging, fetches only the minimum messages needed, and summarizes.
- Success metric: correct thread found with < N provider calls; response time acceptable (p95 bounded by budgets).

## Scenario 3 — “Send an email” with safety
- User asks: “Email X and tell them Y.”
- Default: tool creates a draft, shows it for approval, then sends after approval.
- Success metric: 0 accidental sends without approval (unless auto-send explicitly enabled).

## Scenario 4 — Auto-send opt-in (power users)
- User explicitly enables auto-send for their mailbox or a specific agent.
- System still “steps up” to approval for risky recipients (new/external/bulk).
- Success metric: reduced friction for trusted workflows; low incident rate.

## Scenario 5 — Account revoked / expired tokens
- User’s access gets revoked on Google/Microsoft.
- System should stop sending/reading, show “Reconnect required”, and never loop infinitely.
- Success metric: clear error state + 1-click reconnect; no repeated failures/thrashing.

