# Plan

1. Add native integration tool settings API:
- `GET /api/integrations/oauth/tools/<integration_type>/`
- `POST /api/integrations/oauth/tools/<integration_type>/`
- Return tool catalog + read/write metadata + enabled flags.
- Persist include/exclude preferences in the connected integration account metadata (`tool_settings`), defaulting to all enabled.

2. Make tool exposure deterministic in orchestration:
- Compute available native tools from:
  - current tenant
  - current actor user
  - connected integration account
  - per-tool enabled preferences
- Deny execution for disabled tools even if called directly (`tool_disabled`).

3. Upgrade Integrations UI:
- Enrich connected cards with tool counts (`enabled/total`).
- Add `Manage Tools` modal per connected native integration.
- Support presets (`Enable All`, `Read Only`) and per-tool include/exclude toggles.
- Keep `Disconnect` action available.

4. Test coverage:
- API tests for tools endpoint list/update behavior.
- Orchestrator tests for hidden disabled tools and disabled-tool policy denial.

# Business POV

Scenario 1: Non-technical admin wants confidence after connecting Google Calendar.
- Before: “Connected” badge only; unclear what the AI can do.
- After: card shows enabled tools count and a modal listing each tool in plain language.
- Success metric: fewer “does the AI have access?” support questions.

Scenario 2: Team wants read-only behavior without breaking integration.
- Before: write-capable tools remain available unless approval catches them later.
- After: admin can disable write tools directly (or apply `Read Only` preset), so those tools are not exposed to the LLM at all.
- Success metric: reduction in unintended write attempts and approval noise.

Scenario 3: Integration connected but user wants temporary operational limits.
- Before: only disconnecting removes risk, but also removes useful reads.
- After: admin can keep integration connected and selectively disable risky tools.
- Success metric: improved retention of connected integrations with safer operating posture.

Scenario 4: Platform safety and user settings conflict concerns.
- Before: behavior can feel opaque when multiple policies exist.
- After: policy remains deterministic:
  - hard safety and tenant/user checks always apply
  - disabled tools are hidden/denied
  - approval policy applies only to remaining enabled tools
- Success metric: clearer operator mental model and fewer policy-related incidents.
