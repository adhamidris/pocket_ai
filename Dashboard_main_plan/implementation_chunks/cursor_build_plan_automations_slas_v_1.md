# Cursor Build Plan — Automations & SLAs (Rules, Business Hours, Targets) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first, prop‑driven, no backend

> Build the **Automations** module: rules engine (IF/THEN), business hours & holidays, SLA targets, autoresponders, simulator, and cross‑app hooks. Follow each *Prompt Block* in order; after each step run and verify with the *Acceptance Checks*.

---

## Assumptions
- Dashboard/Conversations/CRM/Agents/Knowledge/Channels v0 exist with navigation and `track()`.
- Offline/Sync Center stubs exist; Security & Privacy Center shell (§31) exists for linking.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Automations/*`
- **Components:** `mobile/src/components/automations/*`
- **Types:** `mobile/src/types/automations.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix with `auto-` (e.g., `auto-home`, `auto-rule-row-<id>`, `auto-hours-save`, `auto-sla-save`, `auto-sim-run`)
- Spacing 4/8/12/16/24; touch ≥ 44×44dp; RTL ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define models for rules, conditions, actions, business hours, holidays, SLAs, and autoresponders.

### Prompt Block
Create `mobile/src/types/automations.ts`:
```ts
export type CondKey = 'intent'|'channel'|'tag'|'vip'|'priority'|'timeOfDay'|'dayOfWeek'|'withinHours'|'messageContains'|'unassigned'|'waitingMinutes'|'csatEnabled';
export type Op = 'is'|'in'|'contains'|'gte'|'lte'|'eq'|'neq'|'true'|'false';
export interface Condition { key: CondKey; op: Op; value?: any }

export type ActionKey = 'autoReply'|'routeToAgent'|'routeToSkill'|'setPriority'|'addTag'|'escalate'|'deflect'|'close'|'requestCSAT'|'scheduleCallback';
export interface Action { key: ActionKey; params?: Record<string,any> }

export interface Rule {
  id: string;
  name: string;
  when: Condition[];
  then: Action[];
  enabled: boolean;
  order: number;
}

export interface BusinessHoursDay { day: 0|1|2|3|4|5|6; open: boolean; ranges: { start: string; end: string }[] } // '09:00'
export interface Holiday { id: string; date: string; name: string; }
export interface BusinessCalendar { timezone: string; days: BusinessHoursDay[]; holidays: Holiday[] }

export type Priority = 'low'|'normal'|'high'|'vip';
export type Channel = 'whatsapp'|'instagram'|'facebook'|'web'|'email';
export interface SlaTarget { priority: Priority; channel?: Channel; frtP50Sec: number; frtP90Sec: number; resolutionHrs?: number }
export interface SlaPolicy { id: string; name: string; targets: SlaTarget[]; pauseOutsideHours: boolean }

export interface AutoResponder { id: string; name: string; active: boolean; message: string; channels: Channel[]; onlyOutsideHours?: boolean; intentFilter?: string[] }

export interface SimulationInput { when: Condition[]; nowIso: string; }
export interface SimulationResult { matchedRuleIds: string[]; actions: Action[]; slaAtRisk?: boolean; notes?: string[] }
```

### Deliverables
- `types/automations.ts`

### Acceptance Checks
- Types compile; no circular dependencies.
ok
---

## STEP 2 — Primitive Components
**Goal:** Reusable UI pieces for rules, hours, SLA, and simulator.

### Prompt Block
In `mobile/src/components/automations/`, create:
- `RuleCard.tsx` (props: `{ rule: Rule; onToggle:()=>void; onEdit:()=>void; onReorder:(dir:'up'|'down')=>void }`)
- `ConditionRow.tsx` (`cond: Condition; onChange:(c:Condition)=>void`)
- `ActionRow.tsx` (`action: Action; onChange:(a:Action)=>void`)
- `HoursGrid.tsx` (`calendar: BusinessCalendar; onChange:(c:BusinessCalendar)=>void`) — 7‑day grid + add range modal.
- `HolidayPicker.tsx` (`holidays: Holiday[]; onChange:(h:Holiday[])=>void`)
- `SlaTargetEditor.tsx` (`policy: SlaPolicy; onChange:(p:SlaPolicy)=>void`)
- `ResponderCard.tsx` (`res: AutoResponder; onToggle:()=>void; onEdit:()=>void`)
- `SimulatorPanel.tsx` (`input: SimulationInput; result?: SimulationResult; onRun:()=>void`)
- `ListSkeleton.tsx`, `EmptyState.tsx`, `BreachBadge.tsx` (simple red/yellow dot + label)

### Deliverables
- 10 primitives exported

### Acceptance Checks
- All components render with demo props; touch targets ≥44dp.
ok
---

## STEP 3 — Automations Home
**Goal:** Hub for rules list, business hours, SLA, autoresponders, and simulator entry.

### Prompt Block
Create `mobile/src/screens/Automations/AutomationsHome.tsx`:
- Header `Automations & SLAs` with overflow: **Audit Log**, **Import/Export**, **Docs** (stubs).
- Sections:
  - **Rules**: list of `RuleCard`s with enable/reorder; button **New Rule**.
  - **Business Hours**: summary chips (timezone, open days); **Edit** button.
  - **SLA Policy**: summary of targets; **Edit** button; **pause outside hours** toggle.
  - **Auto‑responders**: list of `ResponderCard`s + **New**.
  - **Simulator**: compact `SimulatorPanel` with **Run** button.
- Pull‑to‑refresh to reshuffle demo data.
- `track('automations.view')` on mount.
Register in navigator as `Automations`.

### Deliverables
- `AutomationsHome.tsx`
- Navigator update

### Acceptance Checks
- Home renders; rule toggles and reorders update local state; nav to editors works.
ok
---

## STEP 4 — Rule Builder
**Goal:** Create and edit IF/THEN rules with preview.

### Prompt Block
Create `RuleBuilder.tsx`:
- Form: **Name**, **WHEN** list of `ConditionRow` (add/remove), **THEN** list of `ActionRow` (add/remove).
- Preset selectors for common conditions (Intent, Channel, VIP, WaitingMinutes, WithinHours) and actions (AutoReply, Assign Skill/Agent, Set Priority, Add Tag, Escalate, Request CSAT, Deflect).
- Live **Preview** panel that shows pseudo‑English and **Order** info.
- Buttons: **Save**, **Enable**, **Test in Simulator** (push input to simulator screen).
- Save updates local rules list; navigate back.

### Deliverables
- `RuleBuilder.tsx`

### Acceptance Checks
- Creating/editing rules updates the list; preview text updates live.
ok
---

## STEP 5 — Business Hours & Holidays
**Goal:** Edit timezone, weekly hours, and holiday calendar.

### Prompt Block
Create `BusinessHoursScreen.tsx`:
- Fields: **Timezone** picker, **HoursGrid** editor, **HolidayPicker** with add/remove.
- Toggle **Closed today** per day; validate overlapping ranges.
- Button **Save Hours** (updates local calendar) and **Sync to SLA** (optional toggle that adjusts SLA pause flag).
- Link to **Security & Privacy Center** for regional notices (stub navigation).

### Deliverables
- `BusinessHoursScreen.tsx`

### Acceptance Checks
- Saving updates summary on home; basic validation prevents overlaps.
ok
---

## STEP 6 — SLA Targets Editor
**Goal:** Configure FRT P50/P90 and Resolution by priority/channel.

### Prompt Block
Create `SlaEditor.tsx`:
- Choose **Policy name**; **pause outside hours** toggle.
- Table‑like editor of `SlaTarget` rows: Priority, optional Channel, FRT P50 (sec), FRT P90 (sec), Resolution (hrs optional).
- Buttons: **Add Target**, **Delete**, **Save Policy**.
- Small **Guidance** note with suggested defaults; show `BreachBadge` preview if target < current demo metrics.

### Deliverables
- `SlaEditor.tsx`

### Acceptance Checks
- Adding/removing targets works; policy summary on home updates.
ok
---

## STEP 7 — Auto‑Responders
**Goal:** Outside‑hours and intent‑specific responses.

### Prompt Block
Create `AutoRespondersScreen.tsx`:
- List of `ResponderCard`s; **New** opens editor with: **name**, **active**, **message** (multiline), **channels**, **only outside hours**, optional **intent filters**.
- Save updates local list; toggle active from list.

### Deliverables
- `AutoRespondersScreen.tsx`

### Acceptance Checks
- Creating/toggling responders reflects on home; validation for empty message.
ok
---

## STEP 8 — Simulator
**Goal:** Test rules against synthetic events and hours/SLA context.

### Prompt Block
Create `SimulatorScreen.tsx`:
- Inputs: **Intent**, **Channel**, **VIP?**, **Priority**, **WaitingMinutes**, **TimeOfDay**/**DayOfWeek**, **Within business hours**.
- On **Run**, compute `SimulationResult` by matching against the local rules array in order and consider hours/SLA (UI‑only logic).
- Show matched rule IDs, resulting actions, and whether **SLA at risk** given current targets and inputs; include `notes`.
- Button **Apply as New Rule** (preloads RuleBuilder with WHEN from input and THEN from result actions).

### Deliverables
- `SimulatorScreen.tsx`

### Acceptance Checks
- Running updates results; applying as new rule preloads builder.
ok
---

## STEP 9 — Import/Export & Audit (UI‑Only)
**Goal:** Basic portability and visibility.

### Prompt Block
Create `ImportExportAudit.tsx`:
- **Export**: dumps current rules/hours/SLA/responders to JSON (stub, copy to clipboard/console).
- **Import**: paste JSON → preview diff → apply (replaces local demo lists).
- **Audit Log**: list recent changes (create/edit/enable/reorder/delete; hours save; policy save) with timestamp and user `me`.
Link from home overflow.

### Deliverables
- `ImportExportAudit.tsx`

### Acceptance Checks
- Export shows JSON; Import updates local state; audit entries append on key actions.
ok
---

## STEP 10 — Cross‑App Hooks
**Goal:** Tie Automations into other modules.

### Prompt Block
- From **ConversationsList** overflow: **Create rule from filter…** → opens `RuleBuilder` with WHEN prefilled (e.g., Waiting>30, Unassigned, Channel=X).
- From **Dashboard Alerts** (SLA at Risk): tap → opens **SlaEditor**.
- From **Agents RoutingRules**: link **Escalate** action target to Automations **escalate** action suggestions.
- From **Knowledge FailureLog**: quick action **Auto‑reply with FAQ** → opens RuleBuilder with AutoReply prefilled.

### Deliverables
- Navigation hooks wired

### Acceptance Checks
- Each entry point preloads expected fields; back navigation returns to origin.
ok
---

## STEP 11 — Offline & Queue Stubs
**Goal:** Prepare edits for offline‑first.

### Prompt Block
- Reuse global **OfflineBanner** on Automations screens.
- Queue local mutations (rule enable/reorder/save, hours save, SLA save, responder toggle); show small clock near the edited item while queued; clear after timeout.
- Add **Sync Center** button in Automations header.

### Deliverables
- Queued visuals; header button

### Acceptance Checks
- Offline toggle shows banner; queued icons appear then clear.
ok
---

## STEP 12 — Performance & Accessibility
**Goal:** Keep heavy editors smooth; meet WCAG basics.

### Prompt Block
- Virtualize long rule lists; memoize `RuleCard`, `ConditionRow`, `ActionRow`.
- Debounce text inputs (rule name, autoresponder message) and number inputs in SLA editor.
- Add `accessibilityLabel` for every control; ensure hit areas ≥44dp; contrast for badges and toggles.

### Deliverables
- Perf & a11y tweaks

### Acceptance Checks
- Editors feel responsive; screen readers can traverse.
ok
---

## STEP 13 — Analytics Stubs
**Goal:** Instrument key automations events.

### Prompt Block
Using `lib/analytics.ts` instrument:
- `track('automations.view')` on home mount
- `track('rule.create')`, `track('rule.update')`, `track('rule.enable', { enabled })`, `track('rule.reorder')`
- `track('hours.save', { timezone })`
- `track('sla.save', { targets: n })`
- `track('responder.toggle', { active })`
- `track('simulator.run', { matched: n })`
- `track('automations.export')`, `track('automations.import')`

### Deliverables
- Analytics calls added

### Acceptance Checks
- Console logs events during interactions in dev.
ok
---

## STEP 14 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow specs for Automations & SLAs.

### Prompt Block
- Verify: rules CRUD with preview and order, business hours & holidays with timezone, SLA targets editor, autoresponders list/editor, simulator, import/export & audit, cross‑app hooks, offline/queue visuals, perf/a11y, analytics.
- Create `mobile/docs/KNOWN_GAPS_Automations.md` listing deferred items (real evaluator, cron verification of hours, holiday imports, multi‑policy support, rule conflict detector, approval workflows, environment export, etc.).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.
ok
---

## Paste‑Ready Micro Prompts
- "Define Automations TypeScript models (conditions/actions/rules, hours, holidays, SLA, responders, simulator)."
- "Build RuleCard, ConditionRow, ActionRow, HoursGrid, HolidayPicker, SlaTargetEditor, ResponderCard, SimulatorPanel primitives."
- "Create AutomationsHome with sections for Rules, Business Hours, SLA Policy, Auto‑responders, and Simulator; register in navigator."
- "Implement RuleBuilder with live pseudo‑English preview and Save/Enable/Test buttons."
- "Create BusinessHoursScreen with timezone, HoursGrid, HolidayPicker, validation and Save."
- "Create SlaEditor with target rows, pause outside hours toggle, and guidance notes."
- "Implement AutoRespondersScreen with list and editor (message, channels, only outside hours, intent filters)."
- "Build SimulatorScreen that matches rules in order and flags SLA risk given inputs."
- "Create ImportExportAudit with JSON export/import and change audit list."
- "Wire cross‑app hooks from Conversations, Dashboard, Agents, and Knowledge."
- "Queue edits when offline and add Sync Center button to header."
- "Memoize heavy rows, debounce inputs, add accessibility labels and ≥44dp hit areas."
- "Instrument analytics events for rules/hours/sla/responders/simulator/import-export."
- "Run final audit and create KNOWN_GAPS_Automations.md."

---

## Done‑Definition (Automations & SLAs v0)
- AutomationsHome summarizes active rules, hours, SLA, responders, and simulator entry.
- Rules can be created, ordered, enabled/disabled, and previewed; Business Hours & Holidays editable with timezone; SLA targets configurable; responders toggleable.
- Simulator runs and shows matched rules/actions and SLA considerations.
- Import/export and audit exist; cross‑app hooks work; offline queue visuals present.
- Accessibility labels present; editors responsive; analytics stubs fire for key interactions.

