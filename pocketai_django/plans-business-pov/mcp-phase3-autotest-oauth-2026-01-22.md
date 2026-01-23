# MCP Phase 3 — Auto-Test UX + OAuth Provider Bootstrap (2026-01-22)

## Plan
1. Fix “OAuth failed: provider_not_configured” for marketplace OAuth entries by bootstrapping `OAuthProvider` from env settings (best-effort) and improving UI error messaging.
2. Surface background auto-test job state in the MCP connections API (`testJob` per connection).
3. Update MCP Connector UI to show “Testing” state and auto-poll while a background job is queued/running (without blocking manual testing forever).
4. Document production wiring for the job worker (`run_mcp_connection_test_jobs`) in the MCP QA checklist.
5. Add regression tests for job enqueue/coalesce + 429 Retry-After requeue.

## Business POV (Scenarios)

### Scenario 1 — “Connect Gmail (OAuth) from Marketplace”
**Who:** Operations admin  
**Expected UX:** Click Connect → OAuth popup succeeds → connection appears and shows “Testing” → tools populate automatically.  
**Success measure:** No manual “Test” required; tool cache is populated within seconds when the worker is running.

### Scenario 2 — “OAuth popup opens but provider isn’t configured”
**Who:** Any tenant admin in a fresh deployment  
**Expected UX:** Error message clearly explains what’s missing (OAuth provider config) and what env vars/admin setup is required.  
**Success measure:** Support tickets become self-resolvable (“set MCP_OAUTH_* env vars” or configure `OAuthProvider` in admin).

### Scenario 3 — “Job worker is down in production”
**Who:** Platform operator  
**Expected UX:** UI keeps showing “Testing” briefly, then nudges the user to click Test manually; ops checklist points to the worker command.  
**Success measure:** Degraded-but-usable UX; no infinite spinner; clear operator action to restore auto-test.

