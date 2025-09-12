# Cursor Build Plan — MCP / Action Pack (Agent Actions, Guardrails, Approvals) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first only (no backend), local demo state & simulators

> Build the **MCP / Action Pack** surfaces that define what the AI agent is allowed to do inside your product, with **allowlists, guardrails, approvals, rate limits, simulations, and audit**. This is a UI contract for a future action-execution backend. Follow each *Prompt Block* in order; after each step, run and verify using the *Acceptance Checks*.

---

## Assumptions
- Agents module exists (per-agent settings). Security & Privacy (audit log) exists. Automations & SLAs exist. Assistant overlay exists for "Suggest action" deep links.
- This is **UI-only**; no secrets or real calls—just local state and simulated outcomes.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Actions/*`
- **Components:** `mobile/src/components/actions/*`
- **Types:** `mobile/src/types/actions.ts`
- **Testing IDs:** prefix `act-` (e.g., `act-home`, `act-catalog-row-<id>`, `act-allowlist-save`, `act-sim-run`)
- Spacing 4/8/12/16/24; ≥44dp targets; RTL-ready; a11y labels on every control.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define actions, params, permissions, constraints, simulations, runs, and approvals.

### Prompt Block
Create `mobile/src/types/actions.ts`:
```ts
export type ActionKind = 'read'|'create'|'update'|'delete'|'notify'|'export'|'trigger';
export type ParamType = 'string'|'number'|'boolean'|'enum'|'date'|'json'|'email'|'phone'|'url';

export interface ParamSpec { key:string; label:string; type:ParamType; required?:boolean; enumVals?:string[]; min?:number; max?:number; regex?:string; help?:string; }

export interface ActionSpec {
  id:string; name:string; kind:ActionKind; version:number;
  summary:string; longHelp?:string; category:'conversations'|'crm'|'orders'|'knowledge'|'automations'|'settings'|'analytics'|'custom';
  params: ParamSpec[];
  effects: string[];                 // human-readable effects description
  riskLevel: 'low'|'medium'|'high';  // for approvals
}

export interface AllowRule { actionId:string; agentIds?:string[]; intents?:string[]; channels?:string[]; timeOfDay?:{start:'HH:mm'; end:'HH:mm'}; weekdays?:number[]; requireApproval?:boolean; approverRole?:'owner'|'admin'|'supervisor'; rateLimit?:{ count:number; perSeconds:number }; guard?:{ maxRecords?:number; maxAmount?:number; businessHoursOnly?:boolean } }

export interface CapabilityPack { id:string; name:string; industryTags?:string[]; actionIds:string[]; recommended:boolean }

export interface SimInput { actionId:string; params:Record<string,any>; context?:Record<string,any> }
export interface SimResult { ok:boolean; preview:string; // what would change
  warnings?:string[]; errors?:string[]; estimatedLatencyMs?:number; affectedRecords?:number }

export type RunState = 'queued'|'approved'|'running'|'completed'|'failed'|'rejected'|'rate_limited';
export interface ActionRun { id:string; actionId:string; requestedBy:'ai'|'agent'|'system'; createdAt:number; approvedBy?:string; state:RunState; params:Record<string,any>; result?:SimResult }

export interface SecretRef { key:string; note?:string; lastRotatedAt?:number } // UI placeholder only
```

### Deliverables
- `types/actions.ts`

### Acceptance Checks
- Types compile; no circular deps.

---

## STEP 2 — Primitive Components
**Goal:** Build core widgets used across catalog, allowlist, simulator, and monitor.

### Prompt Block
In `mobile/src/components/actions/`, create:
- `ActionRow.tsx` (`spec:ActionSpec; onOpen:()=>void`) — shows name, category, risk badge, summary.
- `ParamEditor.tsx` (`spec:ActionSpec; value:Record<string,any>; onChange:(v)=>void`) — dynamic fields from `ParamSpec` with validation.
- `AllowRuleRow.tsx` (`rule:AllowRule; onEdit:()=>void; onToggle:()=>void; onDelete:()=>void`)
- `RiskBadge.tsx` (`risk:'low'|'medium'|'high'`)
- `RateLimitEditor.tsx` (`value?:{count:number;perSeconds:number}; onChange:(v)=>void`)
- `GuardEditor.tsx` (`value?:AllowRule['guard']; onChange:(v)=>void`) — max records/amount/hours toggles.
- `ApprovalBadge.tsx` (`require:boolean; role?:string`)
- `SimResultCard.tsx` (`res?:SimResult`)
- `RunRow.tsx` (`run:ActionRun; onOpen?:()=>void`)
- `SecretRow.tsx` (`ref:SecretRef; onRotate:()=>void`) — UI-only placeholder.
- `ListSkeleton.tsx`, `EmptyState.tsx`

### Deliverables
- 12 primitives exported

### Acceptance Checks
- All primitives render with demo props; ≥44dp hit targets; a11y labels present.

---

## STEP 3 — Actions Home (Catalog + Allowlist + Monitor)
**Goal:** Central hub to browse actions, edit allowlist, and view recent runs.

### Prompt Block
Create `mobile/src/screens/Actions/ActionsHome.tsx`:
- Header `MCP / Action Pack` with overflow: **Import/Export**, **Docs**, **Secrets** (stubs).
- Tabs: **Catalog**, **Allowlist**, **Monitor**.
  - **Catalog**: searchable list of `ActionRow` grouped by category; open details.
  - **Allowlist**: list of `AllowRuleRow`s with **New Rule** button.
  - **Monitor**: list of `RunRow`s (recent simulated/approved runs) with filters by state/actor.
- `track('actions.view')` on mount.
Register as `Actions` in navigator.

### Deliverables
- `ActionsHome.tsx` + navigation

### Acceptance Checks
- Tabs switch; search filters catalog; monitor shows demo runs.

---

## STEP 4 — Action Details & Try It
**Goal:** Explain the action, params, effects; run a simulation.

### Prompt Block
Create `ActionDetail.tsx`:
- Header: name, risk badge, version.
- Sections: **Summary**, **Parameters** (render `ParamEditor`), **Effects**, **Safety tips**.
- Buttons: **Simulate** (runs `SimInput` → `SimResultCard`), **Propose Allow Rule** (prefills rule editor), **Run (UI-only)** (creates a demo `ActionRun` in `queued` or `approved` depending on allowlist).

### Deliverables
- `ActionDetail.tsx`

### Acceptance Checks
- Invalid params show errors; simulate renders preview; Run creates a `RunRow` entry.

---

## STEP 5 — Rule Builder (Allowlist Editor)
**Goal:** Create/modify allow rules with guards, approvals, and rate limits.

### Prompt Block
Create `AllowRuleBuilder.tsx`:
- Pick **Action** (from catalog) or prefilled if navigated from detail.
- Sections: **Who** (agentIds, intents, channels), **When** (time/day), **Safety** (`GuardEditor`), **Rate limits** (`RateLimitEditor`), **Approvals** (toggle + role).
- **Save** updates local list; **Enable** toggle; **Test in Simulator** preloads ActionDetail with params.

### Deliverables
- `AllowRuleBuilder.tsx`

### Acceptance Checks
- Save appends/updates rule; enable/disable flips state; validation works.

---

## STEP 6 — Capability Packs (Industry presets)
**Goal:** Bundle common actions for quick setup by industry.

### Prompt Block
Create `CapabilityPacks.tsx`:
- Cards for packs (Retail/E‑comm, Services, SaaS, Hospitality). Each shows included actions and risk info.
- **Apply Pack** creates/updates allow rules for the listed actions with safe defaults (business-hours + low risk allowed; high risk set to approval required).
- **Revert** removes rules created by the pack.

### Deliverables
- `CapabilityPacks.tsx`

### Acceptance Checks
- Applying shows new rules; reverting removes them; idempotent behavior.

---

## STEP 7 — Approvals & Inboxes (UI‑only)
**Goal:** Approve/Reject queued runs; show reasons.

### Prompt Block
Create `ApprovalsInbox.tsx`:
- List runs in `queued` with details (action, params summary, risk, requester).
- Actions: **Approve**, **Reject** (reason required). Update `RunRow` state and append to Monitor tab.
- Banner tip: approvals are UI-only for now.

### Deliverables
- `ApprovalsInbox.tsx`

### Acceptance Checks
- Approving/rejecting updates state; Monitor reflects changes.

---

## STEP 8 — Runtime Guardrails (Simulated)
**Goal:** Enforce rate limits, guards, and business hours in UI logic.

### Prompt Block
- Add a lightweight evaluator used by **Run** and **Simulate**:
  - If outside business hours and rule.guard.businessHoursOnly → block with warning.
  - If params exceed `maxRecords` or `maxAmount` → block with error.
  - If exceeded rate limit (count/perSeconds) → mark as `rate_limited`.
- Show inline hints to adjust rule or schedule for later.

### Deliverables
- Evaluator utils + wiring

### Acceptance Checks
- Triggering each guard shows the proper message/state.

---

## STEP 9 — Secrets Placeholder & Environment Notes
**Goal:** UI-only view of required secrets per action.

### Prompt Block
Create `SecretsScreen.tsx`:
- List `SecretRow`s for actions that typically need credentials (e.g., email SMTP, WhatsApp API). Rotate button logs event.
- Info copy: secrets are not stored in app; backend vault will be used later.
Link from ActionsHome overflow **Secrets**.

### Deliverables
- `SecretsScreen.tsx`

### Acceptance Checks
- Rows render; rotate shows toast/log.

---

## STEP 10 — Import/Export & Versioning (UI-only)
**Goal:** Port rules/packs between environments.

### Prompt Block
Create `ImportExportScreen.tsx`:
- **Export**: dumps catalog (selected), allow rules, packs applied into JSON (console/copy).
- **Import**: paste JSON → preview diff → apply.
- Version note for action specs; show conflicts if versions differ.

### Deliverables
- `ImportExportScreen.tsx`

### Acceptance Checks
- Export emits JSON; import updates local lists; conflicts flagged.

---

## STEP 11 — Cross‑App Hooks
**Goal:** Connect Actions with Assistant, Automations, Conversations, Knowledge.

### Prompt Block
- From **Assistant answers** → `ToolSuggestion key:'open_rule_builder'` preloads AllowRuleBuilder for the suggested action.
- From **Automations RuleBuilder** → **Then: Trigger action** opens ActionDetail to pick action and return a stub.
- From **Conversations** → overflow **Take action…** opens ActionDetail with context params (e.g., conversationId, contactId).
- From **Knowledge FailureLog** → **Create Note** or **Train now** map to actions (UI only) via ActionDetail.

### Deliverables
- Navigation hooks

### Acceptance Checks
- Each deep link carries context and prefills params.

---

## STEP 12 — Monitor & Incident View
**Goal:** Observe runs and rollback.

### Prompt Block
Create `RunMonitorScreen.tsx`:
- Filter by state/risk/actor; tap a run to open **RunDetail** with timeline (requested → approved → running → completed/failed), params, preview/result, and **Rollback** (UI tip if action supports it).
- Add **Create Incident** button that opens a stub incident record (UI-only) with link to Security Audit.

### Deliverables
- `RunMonitorScreen.tsx`, `RunDetail.tsx`

### Acceptance Checks
- State transitions update visuals; rollback shows a placeholder dialog.

---

## STEP 13 — Performance, Accessibility, Offline
**Goal:** Keep lists smooth; support screen readers; queue edits.

### Prompt Block
- Virtualize Catalog/Monitor lists; memoize rows.
- Add `accessibilityLabel` and role semantics for risk, approvals, and actions.
- Reuse **OfflineBanner**; queue rule edits and approvals with small clock icon; clear after timeout.

### Deliverables
- Perf & a11y tweaks; offline queue visuals

### Acceptance Checks
- 60fps scroll; a11y reads risk and approval states; queued icons appear then clear.

---

## STEP 14 — Analytics (Meta-events)
**Goal:** Instrument key interactions.

### Prompt Block
Using `lib/analytics.ts` add:
- `track('actions.view')`, `track('actions.catalog.search')`
- `track('actions.detail.simulate', { actionId })`, `track('actions.detail.run', { actionId })`
- `track('actions.rule.save')`, `track('actions.rule.enable', { actionId })`, `track('actions.rule.rate_limit')`
- `track('actions.pack.apply', { packId })`, `track('actions.pack.revert', { packId })`
- `track('actions.approval', { runId, decision })`
- `track('actions.monitor.open', { runId })`
- `track('actions.import')`, `track('actions.export')`

### Deliverables
- Instrumentation calls

### Acceptance Checks
- Console logs events in dev during interactions.

---

## STEP 15 — Final Review & Gaps
**Goal:** Ensure parity with BusinessFlow vision for MCP/Actions.

### Prompt Block
- Verify: Catalog, Detail+Simulate, Allowlist Builder (guards, approvals, rates), Capability Packs, Approvals Inbox, Guardrail evaluator, Secrets placeholder, Import/Export, Cross-app hooks, Monitor+RunDetail, Offline/perf/a11y, Analytics.
- Create `mobile/docs/KNOWN_GAPS_Actions.md` listing deferred backend work (real execution engine, idempotency keys, true approvals workflow, secrets vault integration, structured effects & rollback contracts, concurrency control, per-tenant limits, observability, sandbox/test envs, audit immutability).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.

---

## Paste‑Ready Micro Prompts
- "Define action models (ActionSpec, ParamSpec, AllowRule, CapabilityPack, SimInput/Result, ActionRun)."
- "Build primitives: ActionRow, ParamEditor, AllowRuleRow, RiskBadge, RateLimitEditor, GuardEditor, ApprovalBadge, SimResultCard, RunRow, SecretRow."
- "Create ActionsHome with tabs Catalog/Allowlist/Monitor; register in navigator."
- "Create ActionDetail with Simulate and Run (UI-only) buttons and long help."
- "Create AllowRuleBuilder with Who/When/Safety/Rate/Approvals and Save/Enable/Test."
- "Create CapabilityPacks with Apply/Revert to seed allow rules by industry."
- "Create ApprovalsInbox and wire state transitions to Monitor."
- "Implement guardrail evaluator for business hours, limits, and rate limiting; show hints."
- "Create SecretsScreen placeholder and link from Actions overflow."
- "Create ImportExportScreen for JSON dump/restore and version notes."
- "Add cross-app hooks from Assistant, Automations, Conversations, Knowledge."
- "Create RunMonitor and RunDetail with timeline and rollback placeholder."
- "Add virtualization, a11y labels, OfflineBanner usage, and queued edit icons."
- "Instrument analytics meta-events; produce KNOWN_GAPS_Actions.md."

---

## Done‑Definition (MCP / Action Pack v0)
- You can browse actions, read details, validate parameters, simulate outcomes, and (UI-only) run actions with guardrails.
- Allowlist rules with approvals/rate limits/guards can be created and toggled; industry packs seed sensible defaults.
- Approvals inbox and monitor views show state changes; secrets and import/export shells exist.
- Cross-app deep links work from Assistant/Automations/Conversations/Knowledge.
- Accessibility and performance are solid; offline queues are visible; analytics events fire.

