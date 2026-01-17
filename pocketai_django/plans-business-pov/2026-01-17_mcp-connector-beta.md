# MCP Connector (BETA) — Plan + Business POV

## Plan
1) Audit current dashboard + agent model
2) Research MCP marketplace + protocol details
3) Define MCP connection + assignment schema
4) Implement backend CRUD + enable/disable
5) Implement agent assignment defaults/opt-outs
6) Build Dashboard tab UI (BETA)
7) Add tests, migrations, QA checklist

## Business POV (what changes and why)
The MCP Connector (BETA) adds a first-class “tools marketplace + bring-your-own-server” experience so teams can extend Pocket AI agents with MCP-enabled capabilities, while keeping tenant isolation and safe secret handling.

### Scenarios (expected UX)
1) Add from Marketplace (fast path)
   - Admin opens **MCP Connector (BETA)** on the dashboard, browses curated MCP options, clicks **Add**.
   - A minimal setup modal appears with prefilled name + guidance; admin enters server URL (and optional auth) and saves.
   - Result: connection is created, shown as enabled, and immediately available to all agents by default.

2) Manual add (custom / self-hosted)
   - Admin chooses **Add custom MCP server**, pastes an HTTPS SSE endpoint and selects auth type.
   - Result: connection is stored per workspace, secrets are encrypted, and the connection can be enabled/disabled without deleting it.

3) Agent opt-out (selective rollout)
   - Admin keeps the MCP connection enabled globally, but opts out one agent (e.g., “Sales”) while leaving others enabled (e.g., “Support”).
   - Result: opted-out agents do not see or call the MCP tools; other agents remain unaffected.

4) Safe disable (instant stop)
   - Admin toggles **Disable** on a connection during an incident.
   - Result: Pocket AI stops connecting/calling that MCP server immediately; the configuration remains saved for later re-enable.

5) Security/tenant isolation guardrail
   - A user attempts to add an MCP URL pointing to a private/internal address range.
   - Result: Pocket AI blocks the request with a clear validation error; no server-side connection attempt is made.

### Success measures
- Time-to-first-connection (from dashboard open → enabled connection) under a few minutes.
- Clear visibility of which agents have access (default all + explicit opt-outs).
- No credential leakage in UI/API payloads, logs, or audit trails.
- Safe disable works immediately and predictably.

### Potential regressions to watch
- Added latency when tool lists are refreshed/validated (mitigated via caching + explicit “Test connection” actions in beta).
- Misconfiguration confusion (mitigated via strong URL/auth validation and UX copy).
