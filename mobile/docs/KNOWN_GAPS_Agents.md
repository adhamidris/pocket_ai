# KNOWN GAPS — Agents & Routing

UI-only implementation notes and deferred items for the Agents area.

## Data, Storage, and Sync
- No persistent storage; all agent data generated locally.
- Offline queue is simulated with timers; no durable queue, backoff, or conflict resolution.
- Sync Center is a shell; no real sync pipeline or server reconciliation.

## Directory & Roster
- No directory service integration (HRIS/IdP) for human agents.
- No avatar fetching/caching; initials-only fallback.
- No shift assignment or on-call rotation engine.

## Skills & Capacity
- Skills are static tags; no ML-based skill inference or proficiency modeling.
- Capacity hints do not affect routing; no real-time occupancy calculation.

## AI Behavior & Allowlist (MCP)
- Allowlist toggles are UI-only; no enforcement layer or auditing.
- Behavior editor does not validate prompts or safety policies; no versioning.

## Routing & Policies
- Rules are evaluated nowhere; there’s no server-side routing engine.
- No validation, shadow evaluation, or rule testing harness.
- Escalation policies are not executed; requiresApproval is not enforced.
- No integration with business hours calendars/holidays.

## Conversations Integration
- “Assign to…” updates local state only; no real ownership transfer.
- “View assigned” uses a UI prefill; no server-side filter.

## Performance & Telemetry
- Stats (FRT/AHT/CSAT/Deflection) are stubbed; no data feed.
- No timelines/sparklines sourced from telemetry; purely placeholders.
- No device-adaptive performance profiling or list virtualization tuning beyond heuristics.

## Access Control & Security
- No auth/roles or tenant scoping for editing agents, rules, or policies.
- No audit logs for changes (status, allowlist, routing edits).
- No PII scrubbing or secrets handling for prompts/knowledge links.

## Accessibility & Localization
- A11y labels added at a basic level; not fully audited with screen readers.
- i18n (AR/EN) deferred; strings are not localized yet.

## Testing & Tooling
- No unit/e2e tests for rules/policies editing flows.
- No form validation for overlaps beyond minimal schedule checks.
- No error boundaries or retry UX for remote operations (future).

## Observability
- Analytics events are console stubs; no pipeline or dashboards.

## Misc
- Knowledge links are unvalidated; no URL checks or previews.
- No rate limits or safeguards for rapid status/allowlist toggling.


