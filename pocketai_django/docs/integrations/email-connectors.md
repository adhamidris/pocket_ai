# Email Connectors (Google + Microsoft)

Goal: let non-technical end users connect their business email via OAuth and then use PocketAI for **read + search + send** workflows (agentic automation) with production-safe defaults.

This design intentionally avoids “hosted third‑party MCP Gmail servers” for core email. Instead, PocketAI runs first‑party connectors that call **official provider APIs**:
- Google: Gmail API
- Microsoft: Microsoft Graph (Outlook/M365)

## UX: what the end user does
1) Click “Connect Gmail” or “Connect Microsoft”.
2) Complete OAuth consent.
3) Immediately use email tools inside chat (search/read) and create a draft for approval (send).

No client IDs, no secrets, no server URLs.

## Send safety policy (default + toggles)

### Default (recommended): Draft + approval
- When the user asks the agent to send an email, the system creates a **draft**.
- The user must approve before sending.
- This keeps “send to anyone” UX simple while still safe.

### Optional: Auto-send / Auto-approve (power users)
- Users can toggle auto-send **per connection**, with an optional per-agent override.
- When enabled, the system may send without asking *only* when the policy allows it.

### Step-up approvals (avoid hard blocking)
To prevent bad unattended sends without hurting UX, auto-send should “step up” to approval in risky cases, for example:
- New recipient or first-time domain
- External domain (non-company domain)
- Suspected bulk sends / many recipients
- Any denylisted domain/recipient (if enabled)

This is not a strict “you can’t send” allowlist. It’s a “you can auto-send safely, otherwise ask approval” rule set.

## Data storage policy (privacy by default)

### Default: live provider access, minimal storage
- Do **not** index or sync the whole mailbox.
- Search and reads are performed live via provider APIs.
- Store only minimal metadata required for operations:
  - provider user id / mailbox id
  - message/thread ids
  - timestamps, limited headers
  - tool/audit metadata (redacted)

### Optional: “Save thread to case/knowledge” (explicit user action)
When the user wants deep analysis and long-term access, they can explicitly save:
- Create a `KnowledgeUpload` (source_type `text`) containing a normalized thread transcript in `KnowledgeUploadText.content`.
- If a Case exists/was selected, create a `CaseDocumentLink` pointing to that `KnowledgeUpload`.

This keeps the default safe (no mailbox ingestion) while enabling intentional retention for important threads.

## Tenant isolation & auditability (non-negotiable)
- OAuth tokens are secrets: encrypt at rest, never log, never store in tool artifacts.
- All email actions must be scoped by `BusinessProfile` + user identity to prevent cross-tenant access.
- Audit events should capture “who did what” without storing raw email bodies/PII in logs.

## Implementation notes (high level)
- Google: platform-owned OAuth app + Gmail API calls (search/read/thread/draft/send).
- Microsoft: platform-owned Entra app + Graph calls (search/read/thread/draft/send).
- Expose a stable internal tool contract (`email_search`, `email_get_message`, `email_get_thread`, `email_create_draft`, `email_send_draft`) so the LLM stays consistent even if provider APIs differ.

## OAuth configuration (Phase 1)

Email connectors use **platform-owned** OAuth apps. End users should only see the OAuth consent screen.

### Redirect URIs
Redirect URI is dynamic and based on the host you use to access PocketAI. In development, this commonly means adding one (or both) of:
- `http://localhost:8000/api/email/oauth/callback/google/`
- `http://localhost:8000/api/email/oauth/callback/microsoft/`

If you access the app via a frontend dev server that proxies `/api` (e.g. `http://localhost:3000`), you must register the `:3000` variants instead.

### Environment variables
These are used to bootstrap OAuthProvider rows automatically (see `apps/api/email_oauth.py`).

Google:
- `EMAIL_OAUTH_GOOGLE_CLIENT_ID` (defaults to `GOOGLE_OAUTH_CLIENT_ID`)
- `EMAIL_OAUTH_GOOGLE_CLIENT_SECRET` (defaults to `GOOGLE_OAUTH_CLIENT_SECRET`)
- `EMAIL_OAUTH_GOOGLE_SCOPES` (space-separated; defaults are in `pocketai/settings.py`)

Microsoft:
- `EMAIL_OAUTH_MICROSOFT_CLIENT_ID`
- `EMAIL_OAUTH_MICROSOFT_CLIENT_SECRET`
- `EMAIL_OAUTH_MICROSOFT_SCOPES` (space-separated; defaults are in `pocketai/settings.py`)

### Admin-based configuration (alternative)
If you prefer DB-managed config, create `OAuthProvider` rows in Django admin with keys:
- `google_email`
- `microsoft_email`

and set `authorization_url`, `token_url`, `client_id`, and `client_secret` + `scopes`.
