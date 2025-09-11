# Cursor Build Plan — Security & Privacy Center (Retention, Consent, Audit, Access) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first (no backend), policy stubs with local demo state

> Build the **Security & Privacy Center** to manage data retention, consent templates & records, audit logs, access controls (IP/Session/2FA), data residency, exports & deletion requests (DSRs), legal holds, privacy modes, and cross‑app hooks. Work step‑by‑step: paste each *Prompt Block* into Cursor, complete it, run, verify with *Acceptance Checks*, then proceed.

---

## Assumptions
- You have Settings, Knowledge (RedactionRules), CRM, Channels, Automations, Analytics screens in place.
- A simple analytics util `track()` exists; Offline/Sync Center stubs exist.
- This is **UI‑only**; everything persists in local demo state.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Security/*`
- **Components:** `mobile/src/components/security/*`
- **Types:** `mobile/src/types/security.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix `sec-` (e.g., `sec-home`, `sec-retention-save`, `sec-audit-row-<id>`, `sec-dsr-approve`)
- Spacing 4/8/12/16/24; touch ≥44×44dp; RTL‑ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define policies and records used by the center.

### Prompt Block
Create `mobile/src/types/security.ts`:
```ts
export type Region = 'us'|'eu'|'auto';
export type Risk = 'low'|'medium'|'high';

export interface DataRetentionPolicy {
  id: string;
  name: string;
  conversationsDays: number;        // -1 = indefinite (UI warning)
  messagesDays: number;             // truncate message bodies after N days
  auditDays: number;                 // audit/event log retention
  piiMasking: boolean;               // global masking on export/views (UI only)
  applyToBackups: boolean;           // UI stub
  updatedAt: number;
}

export interface ConsentTemplate {
  id: string; name: string; version: number;
  languages: { code: string; text: string }[];
  channels: ('whatsapp'|'instagram'|'facebook'|'web'|'email')[];
  lastPublishedAt?: number;
}
export type ConsentState = 'granted'|'denied'|'withdrawn'|'unknown';
export interface ConsentRecord { id:string; contactId:string; templateId:string; state:ConsentState; channel:string; ts:number }

export interface AuditEvent {
  id: string; ts: number;
  actor: 'me'|'system'|'ai'|'agent';
  action: string;                     // e.g., 'export.create','policy.update'
  entityType: 'conversation'|'contact'|'policy'|'rule'|'agent'|'export'|'deletion'|'login'|'session';
  entityId?: string;
  details?: string;
  risk?: Risk;
}

export interface IpRule { id:string; cidr:string; note?:string; enabled:boolean }
export interface SessionPolicy { enforce2FA:boolean; sessionHours:number; idleTimeoutMins:number; deviceLimit?:number }

export interface ExportJob { id:string; scope:'all'|'contact'|'conversation'|'dateRange'; params?:Record<string,any>; state:'queued'|'running'|'completed'|'failed'; progress:number; createdAt:number; finishedAt?:number; downloadUrl?:string }

export interface DeletionRequest { id:string; subject:'contact'|'conversation'|'account'; refId?:string; status:'pending'|'approved'|'processing'|'completed'|'rejected'; submittedAt:number; dueAt?:number; reason?:string }

export interface ResidencySetting { region: Region; effectiveAt?: number; note?: string }

export interface LegalHold { id:string; scope:'contact'|'conversation'|'all'; refId?:string; reason:string; active:boolean; createdAt:number; createdBy:'me'|'system' }

export interface PrivacyMode { anonymizeAnalytics:boolean; hideContactPII:boolean; strictLogging:boolean }
```

### Deliverables
- `types/security.ts`

### Acceptance Checks
- Types compile; no circular deps.
ok
---

## STEP 2 — Primitive Components
**Goal:** Build reusable widgets for the center.

### Prompt Block
In `mobile/src/components/security/`, create:
- `PolicyCard.tsx` (`title:string; subtitle?:string; children`) — neutral card shell
- `RetentionSliders.tsx` (`value:DataRetentionPolicy; onChange:(v:DataRetentionPolicy)=>void`) — sliders for days with -1 option + warning badge
- `RiskBadge.tsx` (`level?:Risk`) — colored dot + label
- `AuditRow.tsx` (`evt:AuditEvent; onPress?:()=>void`)
- `IpRuleRow.tsx` (`rule:IpRule; onToggle:()=>void; onEdit:()=>void; onDelete:()=>void`)
- `SessionPolicyEditor.tsx` (`value:SessionPolicy; onChange:(v:SessionPolicy)=>void`)
- `ConsentTemplateRow.tsx` (`tpl:ConsentTemplate; onEdit:()=>void; onPublish:()=>void`)
- `ExportJobRow.tsx` (`job:ExportJob; onOpen?:()=>void`)
- `DeletionRow.tsx` (`req:DeletionRequest; onApprove:()=>void; onReject:()=>void; onView?:()=>void`)
- `ResidencyPicker.tsx` (`value:ResidencySetting; onChange:(v:ResidencySetting)=>void`)
- `PrivacyModeToggles.tsx` (`value:PrivacyMode; onChange:(v:PrivacyMode)=>void`)
- `ListSkeleton.tsx`, `EmptyState.tsx`

### Deliverables
- 13 primitives exported

### Acceptance Checks
- Primitives render with demo props; controls meet ≥44dp; labels have a11y text.
ok
---

## STEP 3 — Security & Privacy Home
**Goal:** Central hub with summaries and entry points.

### Prompt Block
Create `mobile/src/screens/Security/SecurityHome.tsx`:
- Header `Security & Privacy Center` with overflow: **Download audit**, **Policies JSON** (stubs).
- Sections:
  - **Data Retention**: summary (days + warnings) + **Edit**.
  - **Consent**: templates count and last published + **Manage**.
  - **Audit Log**: last 5 events (RiskBadge) + **View all**.
  - **Access Controls**: IP rules count, 2FA/session summary + **Edit**.
  - **Data Residency**: current region + **Change**.
  - **Exports & Deletion Requests**: counts + **Open**.
  - **Privacy Modes**: summary of toggles + **Edit**.
- Pull‑to‑refresh reshuffles demo state; `track('security.view')` on mount.
Register as `SecurityPrivacy` in navigator.

### Deliverables
- `SecurityHome.tsx`
- Navigator update

### Acceptance Checks
- Home renders summaries; tapping rows navigates to editors.
ok
---

## STEP 4 — Retention Policy Editor
**Goal:** Configure retention sliders and flags.

### Prompt Block
Create `RetentionEditor.tsx`:
- Show `RetentionSliders` for Conversations/Messages/Audit.
- Toggles: **PII masking on export/views**, **Apply to backups** (UI only).
- **Save** updates local policy and appends an AuditEvent (`policy.update`).
- Warning banners if any value = `-1`.

### Deliverables
- `RetentionEditor.tsx`

### Acceptance Checks
- Saving updates summary on home; audit log shows new event.
ok
---

## STEP 5 — Consent Center
**Goal:** Manage templates and view records.

### Prompt Block
Create `ConsentCenter.tsx`:
- **Templates** tab: list `ConsentTemplateRow`s; **New Template** opens editor modal (name, languages with text per code, channels multi‑select). Actions: **Publish** (sets `lastPublishedAt` and logs audit).
- **Records** tab: filterable table of `ConsentRecord`s (by state/channel/time); CTA: **Open Contact** (CRM detail).

### Deliverables
- `ConsentCenter.tsx`

### Acceptance Checks
- Creating/publishing templates updates list; records table filters; navigation to CRM works.
ok
---

## STEP 6 — Audit Log (Filters & Export)
**Goal:** Full audit with filters and export stub.

### Prompt Block
Create `AuditLogScreen.tsx`:
- Filters: **Time**, **Actor**, **Entity type**, **Risk**.
- List of `AuditRow`s (virtualized); tapping row shows details sheet.
- Export button: emits JSON/CSV stub and logs `audit.export`.

### Deliverables
- `AuditLogScreen.tsx`

### Acceptance Checks
- Filters change list; export shows toast and logs.
ok
---

## STEP 7 — Access Controls
**Goal:** IP allowlist and session/2FA policy.

### Prompt Block
Create `AccessControlsScreen.tsx`:
- **IP Allowlist**: list of `IpRuleRow` with Add/Edit (CIDR validate), Enable/Disable, Delete.
- **Session & 2FA**: `SessionPolicyEditor` with toggles for **Require 2FA**, **Session duration (hrs)**, **Idle timeout (mins)**, optional **Device limit**.
- **Save** updates local state and appends audit events (`iprule.*`, `sessionPolicy.update`).

### Deliverables
- `AccessControlsScreen.tsx`

### Acceptance Checks
- CIDR validation errors display; saves update home summary; audit entries appear.
ok
---

## STEP 8 — Data Residency
**Goal:** Pick region and effective date.

### Prompt Block
Create `DataResidencyScreen.tsx`:
- `ResidencyPicker` with **US/EU/Auto**; note on implications (UI text).
- Optional effective date picker (UI only).
- **Save** updates local `ResidencySetting` and logs audit.

### Deliverables
- `DataResidencyScreen.tsx`

### Acceptance Checks
- Changing region updates home summary; audit record logged.
ok
---

## STEP 9 — Exports (DSAR) Center
**Goal:** Create and monitor export jobs.

### Prompt Block
Create `ExportsCenter.tsx`:
- **New Export** button with scope options (**All**, **Contact**, **Conversation**, **Date range**) and param inputs.
- Job list of `ExportJobRow`s with progress bars; fake progress on create; on complete show **Open** (stub).
- Append `audit` events on create/complete/fail.

### Deliverables
- `ExportsCenter.tsx`

### Acceptance Checks
- Creating a job adds to list and progresses; audit shows entries.
ok
---

## STEP 10 — Deletion Requests (DSR)
**Goal:** Intake, approve, and complete deletion requests.

### Prompt Block
Create `DeletionRequestsScreen.tsx`:
- List of `DeletionRow`s; filters by status/subject/time.
- Actions: **Approve** → status `approved` then `processing` then `completed` (timeouts); **Reject** with reason; **View** opens CRM/Conversation as applicable.
- On state changes, append audit events (`deletion.*`).

### Deliverables
- `DeletionRequestsScreen.tsx`

### Acceptance Checks
- State machine works visually; audit entries appear; navigation hooks work.
ok
---

## STEP 11 — Legal Holds
**Goal:** Block deletion for scoped data.

### Prompt Block
Create `LegalHoldsScreen.tsx`:
- List of `LegalHold` rows with **active** toggle and **scope** (contact/conversation/all).
- **New Hold**: choose scope/refId, reason; once active, show badge on relevant CRM/Conversation screens (UI only).
- Audit events on create/toggle.

### Deliverables
- `LegalHoldsScreen.tsx`

### Acceptance Checks
- Activating a hold surfaces a badge on linked screens; audit shows entries.
ok
---

## STEP 12 — Privacy Modes
**Goal:** High‑level privacy toggles that affect UI elsewhere.

### Prompt Block
Create `PrivacyModesScreen.tsx`:
- `PrivacyModeToggles` for **Anonymize analytics**, **Hide contact PII**, **Strict logging**.
- Explain effects (UI text) and emit a local event to other modules to reflect (e.g., hide emails in CRM rows).
- Save updates state and logs `privacy.modes.update`.

### Deliverables
- `PrivacyModesScreen.tsx`

### Acceptance Checks
- Toggling **Hide PII** masks CRM contact PII visually; Analytics shows "Anonymized" badge when on.
ok
---

## STEP 13 — Cross‑App Hooks
**Goal:** Connect Security with CRM, Knowledge, Channels, Automations.

### Prompt Block
- From **CRM ContactDetail** → **Request Deletion** opens `DeletionRequestsScreen` prefilled.
- From **Knowledge RedactionRules** → **Open Security Center** goes to `SecurityHome` → **Privacy Modes**.
- From **Channels Verification Logs** → **Audit Log** screen.
- From **Automations** when enabling **Request CSAT** → nudge to **Consent Center** if no template is published.

### Deliverables
- Navigation hooks added

### Acceptance Checks
- Links navigate and return correctly; prefilled params apply.
ok
---

## STEP 14 — Offline & Queue Stubs
**Goal:** Prepare policy edits for offline.

### Prompt Block
- Reuse global **OfflineBanner** on Security screens.
- Queue local mutations: retention save, template publish, ip rule add/toggle, residency save, privacy mode save; show small clock icon and clear after timeout.
- Add **Sync Center** button in header.

### Deliverables
- Queued visuals; header button

### Acceptance Checks
- Offline shows banner; queued icons appear then clear.
ok
---

## STEP 15 — Performance & Accessibility
**Goal:** Smooth lists and WCAG basics.

### Prompt Block
- Virtualize long audit lists; memoize rows and editors.
- Debounce text inputs (CIDR, reasons, template text).
- Add `accessibilityLabel` to all toggles/inputs; ensure contrast; touch targets ≥44dp.

### Deliverables
- Perf & a11y tweaks

### Acceptance Checks
- Scrolling remains smooth; screen readers can operate controls.
ok
---

## STEP 16 — Analytics Stubs
**Goal:** Instrument center actions.

### Prompt Block
Using `lib/analytics.ts` instrument:
- `track('security.view')`
- `track('retention.save')`
- `track('consent.publish')`
- `track('audit.export')`
- `track('access.iprule', { action:'add'|'toggle'|'delete' })`
- `track('access.session.save')`
- `track('residency.save', { region })`
- `track('export.create')`, `track('export.complete')`
- `track('deletion.status', { status })`
- `track('privacy.modes.save')`

### Deliverables
- Instrumentation calls

### Acceptance Checks
- Console logs events during interactions in dev.
ok
---

## STEP 17 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow for Security & Privacy.

### Prompt Block
- Verify: Retention editor, Consent center (templates + records), Audit log (filters/export), Access controls (IP/Session/2FA), Data residency, Exports center, Deletion requests, Legal holds, Privacy modes, cross‑app hooks, offline/queue visuals, perf/a11y, analytics.
- Create `mobile/docs/KNOWN_GAPS_Security.md` listing deferred items (real 2FA, IP CIDR backend enforcement, export packaging/encryption, DSAR SLA timers, approval workflows, regional legal templates, data residency enforcement, tamper‑proof audit storage).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; document created.
ok
---

## Paste‑Ready Micro Prompts
- "Define Security types (retention, consent, audit, IP, session, export, deletion, residency, legal hold, privacy modes)."
- "Build RetentionSliders, RiskBadge, AuditRow, IpRuleRow, SessionPolicyEditor, ConsentTemplateRow, ExportJobRow, DeletionRow, ResidencyPicker, PrivacyModeToggles."
- "Create SecurityHome with summaries and sections; register in navigator."
- "Create RetentionEditor with -1 warnings and Save → audit event."
- "Create ConsentCenter with Templates tab (editor + publish) and Records tab (filterable)."
- "Create AuditLogScreen with filters and export stub."
- "Create AccessControlsScreen with IP allowlist and session/2FA policy; validate CIDR."
- "Create DataResidencyScreen with region picker and Save."
- "Create ExportsCenter to create jobs and show progress; audit on create/complete."
- "Create DeletionRequestsScreen with approve/reject and a simple state machine."
- "Create LegalHoldsScreen and surface a hold badge on CRM/Conversation screens."
- "Create PrivacyModesScreen and propagate Hide PII/Anonymize to other modules."
- "Wire cross‑app hooks from CRM/Knowledge/Channels/Automations."
- "Queue edits offline and add Sync Center access."
- "Memoize rows/lists; debounce inputs; add a11y labels and ≥44dp hit areas."
- "Instrument analytics events; create KNOWN_GAPS_Security.md."

---

## Done‑Definition (Security & Privacy v0)
- SecurityHome shows accurate summaries and navigates to each policy surface.
- Retention/Consent/Audit/Access/Residency/Exports/Deletion/LegalHold/PrivacyModes screens function locally without crashes.
- Cross‑app hooks work; offline queue visuals appear where edits occur.
- Accessibility labels present; performance smooth for large audit lists; analytics events fire for key actions.
