# Cursor Build Plan — Cross‑Module QA & Release Candidate (RC) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript)
**Mode:** UI/UX validation only (no backend). Focus on cross‑module QA, a11y/i18n, perf, offline, analytics, security/privacy UX, and RC preflight.

> Use this plan to harden the full product in UI‑only mode. Work top‑to‑bottom; after each *Prompt Block*, run the *Acceptance Checks*. Keep everything deterministic (seeded demo data) so Cursor can reproduce.

---

## Assumptions
- All module build plans exist (Dashboard, Conversations, CRM, Agents, Knowledge, Channels, Automations & SLAs, Analytics, Settings & Theming, Security & Privacy, Billing, Hosted Portal, Assistant & Voice, MCP/Actions, Help/Docs).
- `track()` analytics util prints to console in dev; Offline/Sync Center stubs exist; testIDs present per plan.

## Paths & Conventions
- **Harness Screens:** `mobile/src/screens/QA/*`
- **Utilities:** `mobile/src/qa/*`
- **Testing IDs:** prefix `qa-` for harness controls; reuse module testIDs for assertions.
- Run everything on 3 device classes: Small (iPhone SE/Android S compact), Medium (Pixel 6/iPhone 13), Large (tablet/landscape).

---

## STEP 1 — Seeded Fixtures & Switcher
**Goal:** Deterministic demo data for repeatable tests.

### Prompt Block
Create `mobile/src/qa/fixtures.ts` with deterministic seeds for: conversations, CRM contacts, agents (AI/Human), intents, knowledge sources + failure log rows, channel connections, automations rules, hours/holidays, SLA policy, analytics series, settings/theme, security policies, billing state, portal transcript, assistant answers, actions catalog, help articles.
Add `FixtureSwitcher.tsx` (QA overlay) to swap between **Baseline**, **High Volume**, **Low Data**, **Edge Cases** (e.g., long names, RTL‑only text, emoji, huge attachments) — updating global stores.

### Deliverables
- `fixtures.ts`, `FixtureSwitcher.tsx`

### Acceptance Checks
- Toggling profiles updates all modules’ demo state consistently.

---

## STEP 2 — Component Gallery
**Goal:** Visual scan of primitives in isolation.

### Prompt Block
Create `mobile/src/screens/QA/ComponentGallery.tsx` that renders a grid of primitives from each module (tiles, cards, editors, charts, bubbles, coachmarks). Include size sliders and theme toggles (light/dark/high‑contrast).

### Deliverables
- `ComponentGallery.tsx`

### Acceptance Checks
- No visual regressions; each primitive mounts without warnings.

---

## STEP 3 — Navigation & Deep‑Link Audit
**Goal:** Every cross‑app CTA navigates correctly and returns.

### Prompt Block
Create `DeepLinkAudit.tsx` with a scripted walk:
- Dashboard → Conversations (filters) → back
- Dashboard → Automations (SLA editor/RuleBuilder) → back
- Analytics tiles → Trends bucket → back
- Knowledge FailureLog → RuleBuilder prefilled → back
- Channels → Widget Snippet → Portal Preview → back
- Settings → Theme → Publish → Channels linkage → back
- Security → Audit Log/Privacy Modes → effects visible in CRM/Portal
- Billing upsell gates → PlanMatrix → Checkout (cancel) → back
- Assistant tool suggestions from overlay to each target
Display a pass/fail table with last route & params.

### Deliverables
- `DeepLinkAudit.tsx`

### Acceptance Checks
- All routes work; params visible; back stack sane (no duplicate screens).

---

## STEP 4 — Accessibility (WCAG‑ish) Pass
**Goal:** Labels, roles, focus order, contrast, and hit targets.

### Prompt Block
Create `AccessibilityAudit.tsx` that:
- Traverses key screens and validates `accessibilityLabel` presence on controls; verifies 44×44dp touch areas; checks focus order with a simple tab/next algorithm; overlays a contrast hint on text vs background using an approximate formula.
- Adds **A11y Hints** panel for fixes.

### Deliverables
- `AccessibilityAudit.tsx`

### Acceptance Checks
- Zero missing labels on critical actions; contrast warnings resolved or waived with rationale.

---

## STEP 5 — i18n & RTL Sweep
**Goal:** Locale switching, pseudo‑localization, bidi, number/date formats.

### Prompt Block
Create `I18nAudit.tsx`:
- Toggle locales: **en**, **ar**, **fr**; include **Pseudo‑Locale** (lengthen text and add diacritics).
- Flip **RTL**; verify mirrored layouts and bubbles; ensure date/time honor **Locale & Timezone** settings.

### Deliverables
- `I18nAudit.tsx`

### Acceptance Checks
- No clipped text in pseudo‑locale; RTL mirrors correctly; dates/times render per locale prefs.

---

## STEP 6 — Theming & Brand Parity
**Goal:** Apply multiple brand presets and validate surfaces.

### Prompt Block
Create `ThemeAudit.tsx` with preset apply buttons (Neutral/Bold/Soft + two custom brand fixtures). Validate:
- Buttons, chips, charts, portal bubbles, headers, coachmarks, and list rows.
- High‑contrast mode overlay.

### Deliverables
- `ThemeAudit.tsx`

### Acceptance Checks
- No illegible text; ContrastBadge passes where required; portal preview reflects tokens.

---

## STEP 7 — Offline & Queue Behaviors
**Goal:** Verify banners, queued edits, retries.

### Prompt Block
Create `OfflineAudit.tsx`:
- Toggle **Global Offline**; perform edits in Automations, Settings, Billing, Security; ensure queue clock icons appear and clear after a simulated sync; verify disabled sends in Portal/Assistant with toasts.

### Deliverables
- `OfflineAudit.tsx`

### Acceptance Checks
- All screens show the correct offline badges and queue visuals; no dead‑end UI.

---

## STEP 8 — Performance Budgets
**Goal:** 60fps targets under load; memory sanity.

### Prompt Block
Create `PerfAudit.tsx`:
- Generate long lists (Conversations 500 items, Audit log 1k rows, Analytics 12 weeks of daily points).
- Measure frame drop proxies via `InteractionManager`/timestamps; flag screens exceeding budgets.
- Image lazy‑loading & chart virtualization toggles.
Define budgets in comments: initial nav < 300ms, scroll > 55fps on mid device, render per item < 2ms avg.

### Deliverables
- `PerfAudit.tsx`

### Acceptance Checks
- Lists remain smooth; any flagged screens have TODOs or simplified variants available.

---

## STEP 9 — Error & Empty‑State Coverage
**Goal:** Every data surface has loading/empty/error states.

### Prompt Block
Create `StateCoverageAudit.tsx` that fuzzes per‑screen data to: **loading**, **empty**, **error**. Validate banners, retry buttons, and guide CTAs exist and point somewhere useful.

### Deliverables
- `StateCoverageAudit.tsx`

### Acceptance Checks
- No bare blanks or silent failures; CTAs navigate.

---

## STEP 10 — Analytics/Telemetry Validation
**Goal:** Ensure events fire once with sane payloads; privacy respected.

### Prompt Block
Create `TelemetryAudit.tsx`:
- For each module, trigger representative actions and capture the emitted `track()` payloads to a panel; check required fields, no PII when **Hide PII** is enabled, and total events ≤ expected counts (no double‑fires).
- Export log (JSON) stub.

### Deliverables
- `TelemetryAudit.tsx`

### Acceptance Checks
- All key events appear exactly once; PII masking works; export produces JSON.

---

## STEP 11 — Entitlements & Upsell Gating
**Goal:** Verify gates and unlocks per plan.

### Prompt Block
Create `EntitlementAudit.tsx`:
- Cycle plans (Free → Starter → Pro → Scale) and assert: gated screens show **UpsellInline**; after upgrade, surfaces render; Billing → downgrade re‑locks features.

### Deliverables
- `EntitlementAudit.tsx`

### Acceptance Checks
- Gates appear correctly per plan; upgrade/downgrade transitions update UI everywhere.

---

## STEP 12 — Security & Privacy UX Checks
**Goal:** Visual correctness of privacy flows.

### Prompt Block
Create `SecPrivacyAudit.tsx`:
- Toggle **Hide PII** and **Anonymize analytics**; confirm masking in CRM, Portal, Assistant citations, Analytics badges.
- Run Retention `-1` warnings; Consent templates published; Audit log entries append across actions; Residency badge shown.

### Deliverables
- `SecPrivacyAudit.tsx`

### Acceptance Checks
- Masking consistent; warnings/badges show; audit entries appear from other modules.

---

## STEP 13 — E2E Paths (Scripted Walks)
**Goal:** Happy paths & critical flows.

### Prompt Block
Create `ScriptedWalks.tsx` with buttons to auto‑run:
- **Day‑0 setup**: Connect channel → Publish link → Theme publish → Train knowledge → Set hours → Create rule → Test portal.
- **Ops day**: Handle backlog → SLA edit → Assistant Q&A → Create automation from intent → Verify analytics trend link → Approvals in Actions.
- **Support exit**: End portal chat → CSAT → Transcript.
Show a checklist UI with pass/fail ticks.

### Deliverables
- `ScriptedWalks.tsx`

### Acceptance Checks
- All steps complete without dead‑ends; failures show which screen/step.

---

## STEP 14 — Bug Bash Playbook
**Goal:** Run a structured team bash.

### Prompt Block
Create `BugBashPlaybook.md` (in `mobile/docs/`) with:
- Roles: Facilitator, Scribe, Fix captain.
- Scenarios matrix by module & device.
- Severity rubric (S1 blocker → S4 polish) and priority (P0→P3).
- Reporting template and triage SLA.
- Known tricky areas (lists under RTL, high‑contrast charts, offline queues).

### Deliverables
- `mobile/docs/BugBashPlaybook.md`

### Acceptance Checks
- Playbook renders and covers all modules; rubric clear.

---

## STEP 15 — RC Preflight Checklist
**Goal:** One place to mark readiness.

### Prompt Block
Create `RC_Preflight.tsx` with checkboxes and links:
- Build IDs stamped; testIDs present on all critical controls; crash‑free smoke on 3 devices.
- A11y pass ✓, i18n/RTL ✓, Theming ✓, Offline ✓, Perf budgets ✓, Telemetry ✓, Entitlements ✓, Security/Privacy UX ✓.
- Dev flags disabled; demo data labeled; About → licenses present; Help → What’s New updated.
- Rollback path documented (feature flags/kill switch placeholder).

### Deliverables
- `RC_Preflight.tsx`

### Acceptance Checks
- All boxes can be marked; unresolved items link to audit screens.

---

## STEP 16 — Launch & Rollout Plan (UI Stubs)
**Goal:** Simulate rollout steps and runbooks.

### Prompt Block
Create `LaunchRollout.tsx`:
- **Phased rollout** toggles (internal → pilot → GA) with notes.
- **Support runbook** link (stub) and **Incident response** link to Security Audit.
- **Post‑launch checks** (event volume baseline, funnel conversions, error rate proxies).

### Deliverables
- `LaunchRollout.tsx`

### Acceptance Checks
- State persists; links navigate to relevant audit screens.

---

## STEP 17 — Final Gaps & Debrief
**Goal:** Capture what’s left for backend wiring.

### Prompt Block
Create `mobile/docs/KNOWN_GAPS_RC.md` summarizing:
- All UI that assumes backend: transport, auth/SSO, persistence, analytics pipeline, export packaging, billing processor, secrets vault, actions executor.
- Contracts needed: types per module; event schemas; deep‑link params; entitlement keys.

### Deliverables
- `KNOWN_GAPS_RC.md`

### Acceptance Checks
- Document covers all integration points; referenced by RC Preflight.

---

## Paste‑Ready Micro Prompts
- "Create seeded fixtures and a FixtureSwitcher to flip profiles across all modules."
- "Build ComponentGallery to render all primitives under size/theme switches."
- "Implement DeepLinkAudit to walk cross‑app CTAs and verify params/back stack."
- "Add AccessibilityAudit to validate labels, touch targets, and contrast."
- "Add I18nAudit to switch locales, pseudo‑localize, and test RTL."
- "Create ThemeAudit to validate token application and high‑contrast."
- "Implement OfflineAudit to simulate offline and queued edits across modules."
- "Create PerfAudit to stress long lists/charts and enforce budgets."
- "Create StateCoverageAudit to fuzz loading/empty/error on each screen."
- "Implement TelemetryAudit to capture and validate analytics payloads with privacy masks."
- "Create EntitlementAudit to verify gates and unlocks across plans."
- "Create SecPrivacyAudit to ensure PII masking and audit entries show."
- "Create ScriptedWalks for Day‑0 setup, Ops day, and Support exit."
- "Write BugBashPlaybook.md with roles, severity, and device matrix."
- "Build RC_Preflight checklist linking to each audit screen."
- "Add LaunchRollout stubs for phased launch and runbooks."
- "Produce KNOWN_GAPS_RC.md listing backend contracts and integration points."

---

## Done‑Definition (RC v0)
- A QA harness exists with fixtures, galleries, and audits for navigation, a11y/i18n, theming, offline, perf, error states, telemetry, entitlements, and security/privacy UX.
- Scripted end‑to‑end paths succeed; a Bug Bash playbook and RC checklist exist; rollout stubs are present.
- All items remain UI‑only and ready to wire to backend services later.

