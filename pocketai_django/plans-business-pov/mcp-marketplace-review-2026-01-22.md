# MCP Marketplace & Connector Review (2026-01-22)

## Plan
1. Read `docs/product/saas_brief.md`, `docs/product/technical.md`, and `README.md` for platform invariants and intended flow.
2. Locate the Marketplace + MCP Connector UI, API endpoints, and OAuth plumbing in the repo.
3. Review tenant isolation, credential storage, auth flows, SSRF/network safety, and auditability.
4. Review the “plugin-like” UX: marketplace browse → connect (API key/OAuth) → test/cache tools → agent/tool permissions.
5. Summarize findings, tradeoffs, and prioritized recommendations (quick wins + longer-term design).

## Business POV (Scenarios)

### Scenario 1 — “Connect Gmail for internal ops”
**Who:** SMB operations lead  
**Goal:** Connect Gmail so an internal agent can read/send emails.  
**Expected UX:** Click “Gmail” → OAuth popup → connection appears → “Test connection” shows tools → agent can use it immediately.  
**Success measure:** <2 minutes from click to first successful tool call; no secrets visible/stored in plaintext.
**Risk to avoid:** Gmail access accidentally available to a public-facing agent/chat portal.

### Scenario 2 — “Add GitHub to a public support bot”
**Who:** SaaS support manager  
**Goal:** Use GitHub tools for *internal* workflows, but the public website bot should never expose repo contents.  
**Expected UX:** Clear guardrails and defaults that prevent public exposure; any write requires approval.  
**Success measure:** No unauthenticated portal visitor can trigger sensitive reads; approvals are auditable.
**Potential regression:** If defaults are too strict, internal workflows feel “blocked” unless explicitly enabled.

### Scenario 3 — “E-commerce adds Shopify/WooCommerce”
**Who:** E-commerce admin  
**Goal:** Connect store tools quickly (API key + store URL).  
**Expected UX:** Marketplace clearly indicates what’s supported today; setup fields collected are actually used.  
**Success measure:** Low “setup failed / not working” support tickets; “coming soon” is explicit when not wired.

### Scenario 4 — “Reliability under flaky upstream MCP servers”
**Who:** Any tenant admin  
**Goal:** Avoid the system feeling broken if an upstream MCP endpoint rate-limits or is temporarily down.  
**Expected UX:** Tool schema remains visible (stale is OK) with a clear “cache expired / retry later” message; retries are bounded.  
**Success measure:** No tool “disappears” due to transient failure; clear operator action (“re-test”) fixes most issues.

