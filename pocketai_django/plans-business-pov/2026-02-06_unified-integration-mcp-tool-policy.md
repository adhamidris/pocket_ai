# Unified Integration + MCP Tool Policy — Business POV

## Plan
1. Introduce a native integration tool registry that maps each tool to `integration_type`, `operation_type` (`read`/`write`), and runtime requirements (`requires_connected_account`, `requires_actor_user_binding`).
2. Replace hardcoded exposure filters with a per-turn tool exposure builder that composes:
- stable core tools,
- native integration tools only when a matching connected account exists for the same tenant + actor user,
- MCP gateway tools only when enabled MCP connections exist.
3. Add one orchestrator-level native integration policy resolver that returns deterministic decisions:
- `allow`,
- `allow_with_confirmation`,
- `deny`,
with machine-readable reason codes.
4. Apply approval handling through the same `ConversationToolApproval` path for both MCP and native integration tools, while preserving tenant-safe execution metadata.
5. Standardize native integration policy/account/token user-facing errors to:
- `not_connected`,
- `account_mismatch`,
- `token_expired`,
- `approval_required`.
6. Add focused regression tests for dynamic exposure and policy behavior under different approval modes.

## Business POV
### Scenario 1: User connects Google Calendar and starts chat
- Before: Calendar connection succeeds in dashboard, but tools may not be exposed in chat due static allowlist drift.
- After: Calendar tools appear automatically for that same signed-in user and tenant, with no manual allowlist updates.
- Success metrics: fewer "connected but unavailable" tickets, faster time-to-first-tool-use.

### Scenario 2: Multiple team members under one tenant
- Before: Tool exposure can be ambiguous if connection visibility isn’t tied tightly to the actor user.
- After: Tools are exposed only when the current actor has a connected account for that integration type; cross-user leakage is prevented.
- Success metrics: zero cross-user tool access incidents in QA/security logs.

### Scenario 3: Write actions under different approval modes
- Before: approval behavior differs by tool family, creating user confusion.
- After: one policy model applies consistently:
- `auto` => no confirmation,
- `approve_writes` => writes confirm, reads auto,
- `approve_all` => all confirm.
- Success metrics: predictable approval prompts; lower accidental write risk.

### Scenario 4: MCP + native coexist in one agent
- Before: MCP visibility and native visibility are managed through separate logic, increasing drift risk.
- After: both are fed by a unified exposure build per turn; MCP tools only surface when connectors are actually available.
- Success metrics: reduced tool-schema drift defects; lower maintenance overhead as integrations scale.

### Potential regressions to watch
- Fewer tools visible for anonymous or non-bound sessions by design (actor binding enforced).
- If a tenant expects shared service accounts across users, explicit account selection/binding rules may need product UX guidance.
