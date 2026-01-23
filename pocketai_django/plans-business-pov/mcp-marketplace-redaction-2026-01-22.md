# MCP Marketplace — Redaction & Audit Safety (2026-01-22)

## Plan
1. Add a shared redaction helper for MCP tool inputs (setupFields + secret-ish keys).
2. Apply redaction to MCP tool events (started + approval requested) and to tool approval persistence.
3. Ensure MCP tool artifacts persist only redacted request payloads (no raw setupFields values).
4. Add regression tests for redaction (unit) and approval storage (DB).
5. Run targeted MCP test suite.

## Business POV (Scenarios)

### Scenario 1 — “Shopify connection uses store_url + token”
**Who:** E-commerce admin  
**Goal:** Connect Shopify and let an agent run tools without re-entering setup fields.  
**Expected UX:** Tools work; the portal/tool history never shows `store_url`/`token` values (only `[REDACTED]`).  
**Success measure:** Zero secret leakage into tool cards, tool history, or artifacts; fewer “why is my token visible?” support tickets.

### Scenario 2 — “DB connector uses connection_string”
**Who:** Data analyst / engineer  
**Goal:** Connect Postgres and run queries via MCP tools.  
**Expected UX:** Approvals and tool history show the query context, but the `connection_string` is always `[REDACTED]`.  
**Success measure:** Audit trail remains useful while minimizing blast radius if an artifact export is shared internally.

### Scenario 3 — “Write tool approval required”
**Who:** Workspace admin (approver)  
**Goal:** Approve a write operation (e.g., create issue) safely.  
**Expected UX:** Approval shows what matters (operation/tool), but never stores or displays setupFields secrets.  
**Success measure:** Approvals are auditable without turning into a secret store.

