# Automations & SLAs — Known Gaps (UI-only build)

This module is implemented UI-first with local demo state. The following items are intentionally deferred and/or stubbed:

- Rules engine evaluator: no backend rule evaluation, persistence, or conflict resolution; preview/simulator are local only.
- Multi-policy support: only a single `SlaPolicy` instance is modeled; no policy scoping or inheritance.
- Business hours: no timezone validation service, no cron verification, no regional holiday imports.
- Holidays: manual entry only; no ICS/Google Calendar import or shared calendars.
- SLA enforcement: no real-time timers, breach detectors, or alert generation; `BreachBadge` is illustrative.
- Autoresponders: no channel delivery or templating; intent filter matching is stubbed.
- Import/Export: JSON copy to console; no secure export formats, versioning, or environment promotion.
- Audit log: in-memory only; no persistence, user attribution, or diff detail.
- Offline/queue: banners and clocks are visual only; no durable queue, retries, or conflict merges.
- Cross-app hooks: navigation is wired, but no data contracts or deep filtering beyond local params.
- Performance: lists are optimized for demo sizes; no pagination, virtualization tuning, or background prefetch.
- Accessibility: baseline labels and hitSlop added; no full screen reader scripts or RTL audits.
- Analytics: console-only via `track()`; no pipeline, session attribution, or privacy controls.
- Security/permissions: no RBAC, approvals for risky automations, or change review workflows.
- Testing: no unit/e2e coverage, no schema validation for imports, no fuzzing of simulator inputs.

These gaps are expected to be addressed during backend integration and subsequent phases.
