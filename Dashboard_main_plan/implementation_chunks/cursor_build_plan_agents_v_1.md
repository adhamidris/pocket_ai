# Cursor Build Plan — Agents (AI & Human) (Ultra‑Chunked)
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first, prop‑driven, routing‑ready

> Build the **Agents** area as two flavors: **AI agents** (with Allowed Actions/MCP allowlist & behavior settings) and **Human agents** (with status, schedule, skills, capacity). Keep everything UI‑only with local demo state. Follow each *Prompt Block* in order; validate using *Acceptance Checks*.

---

## Assumptions
- **Dashboard**, **Conversations**, and **CRM** v0 exist with navigation in place.
- UI primitives (`Box`, `Text`, `ui/tokens`) are available.
- `track()` analytics util exists; Offline/Sync Center stubs exist.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Agents/*`
- **Components:** `mobile/src/components/agents/*`
- **Types:** `mobile/src/types/agents.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix with `agents-` (e.g., `agents-list`, `agents-row-<id>`, `agents-detail`, `agents-allowlist-item-<key>`)
- Spacing 4/8/12/16/24; touch ≥ 44×44dp; RTL ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define models for AI/Human agents, schedules, skills, routing rules, and allowlist.

### Prompt Block
Create `mobile/src/types/agents.ts`:
```ts
export type AgentKind = 'ai'|'human';
export type AgentStatus = 'online'|'away'|'offline'|'dnd';
export type Skill = 'sales'|'support'|'billing'|'tech'|'logistics'|'custom';
export interface SkillTag { name: Skill|string; level?: 1|2|3|4|5 }

export interface ScheduleSlot { day: 0|1|2|3|4|5|6; start: string; end: string; } // '09:00'
export interface CapacityHint { concurrent: number; backlogMax?: number }

export interface AllowedAction { key: string; label: string; description?: string; enabled: boolean; risk?: 'low'|'medium'|'high' }

export interface BaseAgent { id: string; kind: AgentKind; name: string; avatarUrl?: string; status: AgentStatus; skills: SkillTag[]; notes?: string; }

export interface HumanAgent extends BaseAgent { kind: 'human'; email?: string; phone?: string; schedule?: ScheduleSlot[]; capacity?: CapacityHint; assignedOpen?: number; }

export interface AiBehavior { temperature?: number; tone?: 'neutral'|'friendly'|'formal'; systemPrompt?: string; }
export interface AiAgent extends BaseAgent { kind: 'ai'; behavior: AiBehavior; allowlist: AllowedAction[]; knowledgeSources?: string[]; deflectionTarget?: number; }

export type AnyAgent = HumanAgent | AiAgent;

// Routing
export type RouteCondition = 'intent'|'channel'|'vip'|'priority'|'businessHours'|'overflow';
export interface RoutingRule {
  id: string;
  name: string;
  when: { cond: RouteCondition; op?: 'is'|'in'|'eq'|'gte'|'lte'; value?: any }[];
  then: { action: 'assign_agent'|'assign_skill_group'|'escalate'|'queue'|'deflect'; target?: string };
  enabled: boolean;
}

export interface EscalationPolicy { id: string; name: string; rules: { afterMinutes:number; to:'human_supervisor'|'vip_queue'|'on_call'; requiresApproval?:boolean }[] }
```

### Deliverables
- `types/agents.ts`

### Acceptance Checks
- Types compile and export correctly; no circular import issues.
ok
---

## STEP 2 — Primitive Components
**Goal:** Base UI building blocks for Agents.

### Prompt Block
In `mobile/src/components/agents/`, create:
- `StatusBadge.tsx` (`status: AgentStatus`)
- `SkillChip.tsx` (`tag: SkillTag; onPress?`)
- `CapacityPill.tsx` (`capacity?: CapacityHint; assignedOpen?: number`)
- `PerformanceStat.tsx` (`label:string; value:string|number; delta?:number`)
- `ListSkeleton.tsx` (rows placeholder)
- `EmptyState.tsx` (icon+message)

### Deliverables
- 6 primitives exported

### Acceptance Checks
- Can render primitives in isolation with tokens; touch targets ≥44dp where tappable.
ok
---

## STEP 3 — Agent Row & Cards
**Goal:** List row and mini-cards for reuse.

### Prompt Block
Create:
- `AgentRow.tsx` (props: `{ item: AnyAgent; onPress:(id:string)=>void; onLongPress?:(id:string)=>void }`) showing avatar, name, **StatusBadge**, **CapacityPill** (for human), **deflection target** pill (for AI), and **SkillChip** cluster.
- `AgentMiniCard.tsx` (compact card for related lists)

### Deliverables
- `AgentRow.tsx`, `AgentMiniCard.tsx`

### Acceptance Checks
- Rows render with both AI and Human variants without layout breaks.
ok
---

## STEP 4 — Agents List Screen
**Goal:** Scaffold roster with filters, sort, search.

### Prompt Block
Create `mobile/src/screens/Agents/AgentsList.tsx`:
- Demo roster with ~12 agents (mix AI/Human, varied status/skills).
- Filters: **Kind** (AI/Human), **Status** (online/away/offline/dnd), **Skill** dropdown, **Only overloaded** (assignedOpen > capacity.concurrent).
- Sort: **Name A→Z**, **Status**, **Open load** (desc).
- Search by name/skill.
- `FlatList` of `AgentRow`.
- On press → `AgentDetail` with `{ id }`.
- Pull‑to‑refresh (reorders demo data).
Register in navigator as `Agents`.

### Deliverables
- `AgentsList.tsx`
- Navigator update

### Acceptance Checks
- Filters/sort/search apply quickly; list virtualization is smooth.
ok
---

## STEP 5 — Agent Detail (Shared Shell)
**Goal:** One detail screen that adapts to AI/Human.

### Prompt Block
Create `mobile/src/screens/Agents/AgentDetail.tsx`:
- Header: avatar, name, **StatusBadge**, **SkillChip**s, **CapacityPill** or deflection pill based on kind.
- Tabs: **Profile**, **Schedule/Behavior**, **Performance**, **Notes**.
- Top actions: **Edit**, **Change Status**, **Assign Test Conversation** (navigates to Conversations with filter for this agent, UI-only), **Delete** (confirm).
- Load demo agent by `id` and branch UI by `kind`.

### Deliverables
- `AgentDetail.tsx`

### Acceptance Checks
- Navigating from list opens correct variant; tabs switch; top actions show stubs.
ok
---

## STEP 6 — Human Agent: Schedule & Capacity
**Goal:** Manage weekly schedule and capacity hints.

### Prompt Block
Create components:
- `WeekSchedule.tsx` (props: `slots: ScheduleSlot[]; onChange:(slots)=>void`) — simple 7‑day grid; add/edit slot modal; validate overlaps.
- `CapacityEditor.tsx` (`value?: CapacityHint; onChange`) — concurrent/backlog numeric inputs.
Wire into `AgentDetail` when `kind==='human'` under **Schedule/Behavior** tab.

### Deliverables
- `WeekSchedule.tsx`, `CapacityEditor.tsx`

### Acceptance Checks
- Adding/removing slots updates local state; basic overlap warnings appear.
ok
---

## STEP 7 — AI Agent: Behavior & Allowlist (MCP)
**Goal:** Configure AI behavior and allowed actions.

### Prompt Block
Create components:
- `BehaviorEditor.tsx` (`value: AiBehavior; onChange`) — temperature slider (0–1), tone select, system prompt text area.
- `AllowlistEditor.tsx` (`items: AllowedAction[]; onToggle:(key:string)=>void`) — list with switch per action; **risk badges** (low/med/high) and an info tooltip.
- `KnowledgeLinks.tsx` (`sources?: string[]`) — simple list with add/remove UI.
Wire these under **Schedule/Behavior** tab when `kind==='ai'`.

### Deliverables
- `BehaviorEditor.tsx`, `AllowlistEditor.tsx`, `KnowledgeLinks.tsx`

### Acceptance Checks
- Toggling allowlist items flips enabled state; behavior fields update local state.
ok
---

## STEP 8 — Routing Rules (UI‑Only)
**Goal:** Create rule builder and list.

### Prompt Block
Create `RoutingRulesScreen.tsx`:
- **Rules list** with enable/disable toggles; create button.
- **Rule builder**: WHEN (multi‑row): choose `cond`, `op`, `value` → THEN (action + target). Live **preview** count is stubbed.
- **Reorder** rules via drag handle (simple up/down buttons okay).
Add entry from Agents header overflow and also from AgentDetail ("See routing").

### Deliverables
- `RoutingRulesScreen.tsx`

### Acceptance Checks
- Creating/enabling/disabling/reordering rules updates local list; no crashes.
ok
---

## STEP 9 — Overflow & Escalation Policies
**Goal:** Define overflow targets & time‑based escalations.

### Prompt Block
Create `PoliciesScreen.tsx`:
- **Overflow**: choose target (skill group, specific agent, VIP queue) when capacity reached.
- **Escalation policies**: list and editor using `EscalationPolicy` model; per‑step minutes + destination; `requiresApproval` toggle.
Link from Agents header overflow and from Routing builder.

### Deliverables
- `PoliciesScreen.tsx`

### Acceptance Checks
- Editing a policy updates local state; UI reflects requiresApproval flag.
ok
---

## STEP 10 — Performance View (Per‑Agent)
**Goal:** Micro‑cards with basic stats (UI stubs).

### Prompt Block
Under **Performance** tab in `AgentDetail` show:
- **FRT P50/P90**, **AHT**, **Resolution %**, **CSAT** (for human); **Deflection %** and **Low‑confidence rate** (for AI) — use `PerformanceStat` tiles with deltas.
- Small sparkline placeholder next to each stat (optional simple bar sequence).

### Deliverables
- Performance tab UI complete

### Acceptance Checks
- Stats render based on kind; no UI jumps.
ok
---

## STEP 11 — Row Interactions (Quick Actions)
**Goal:** Long‑press on agent rows for quick status/skills.

### Prompt Block
Create `AgentRowActionsSheet.tsx` with: **Change Status** (online/away/offline/dnd), **Add/Remove Skill**, **Adjust Capacity** (human), **Toggle Allowlist item** (AI quick list).
Wire to long‑press on `AgentRow`.

### Deliverables
- `AgentRowActionsSheet.tsx` + integration

### Acceptance Checks
- Sheet opens; toggles mutate local demo state; visual badges update.
ok
---

## STEP 12 — Cross‑App Hooks
**Goal:** Make Agents ↔ Conversations handoff natural.

### Prompt Block
- In `AgentDetail` top actions add **"View assigned conversations"** → navigate to `Conversations` with filter for this agent (UI‑only param).
- In `ConversationsList` row actions add **"Assign to…"** → opens a bottom sheet with a searchable list of agents (use `AgentMiniCard`), then sets a local `assignedTo` on that conversation.

### Deliverables
- Two‑way hooks wired

### Acceptance Checks
- Both navigations work and update local UI without crashes.
ok
---

## STEP 13 — Offline & Sync Stubs
**Goal:** Prepare agent edits for offline.

### Prompt Block
- Reuse `OfflineBanner` on Agents screens.
- Queue local mutations: status change, skill add/remove, capacity edit, allowlist toggle. Show small clock icon near changed field while queued; clear after timeout.
- Add **Sync Center** button in Agents header.

### Deliverables
- Queued state visuals; header button

### Acceptance Checks
- Offline toggle shows banner; queued icons appear then clear.
ok
---

## STEP 14 — Accessibility & Performance
**Goal:** WCAG AA basics and smoothness.

### Prompt Block
- Add `accessibilityLabel` to toggles and chips (e.g., "Status online", "Skill: support level 3").
- Ensure hit areas ≥44dp; add `hitSlop` for small chips/handles.
- Virtualize long lists; memoize `AgentRow` and heavy editors.

### Deliverables
- A11y tweaks + perf memoization

### Acceptance Checks
- Screen readers can navigate controls; lists scroll 60fps.
ok
---

## STEP 15 — Analytics Stubs
**Goal:** Instrument critical interactions.

### Prompt Block
Using `lib/analytics.ts`:
- On Agents list mount: `track('agents.view')`
- On status change: `track('agent.status', { id, status })`
- On allowlist toggle: `track('agent.allowlist', { id, key, enabled })`
- On capacity/skills edit: `track('agent.edit', { id, field })`
- On rule change: `track('routing.rule', { action:'create'|'enable'|'disable'|'reorder' })`
- On escalation policy edit: `track('routing.escalation', { id })`

### Deliverables
- Analytics calls added

### Acceptance Checks
- Console shows events in dev when interacting with Agents.
ok
---

## STEP 16 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow specs (Agents & Routing) and stability.

### Prompt Block
- Verify: roster list, filters/sort/search, AgentDetail tabs (Profile, Schedule/Behavior, Performance, Notes), Human schedule/capacity, AI behavior/allowlist, Routing rules, Policies, Row actions, Cross‑app hooks, Offline queue.
- Create `mobile/docs/KNOWN_GAPS_Agents.md` listing deferred items (directory service, real on‑call rotation, ML skill inference, real stats feed, multi‑tenant permissions, etc.).

### Deliverables
- Final fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.
ok
---

## Paste‑Ready Micro Prompts
- "Define Agent TypeScript models (AI/Human, schedule, capacity, allowlist, routing, escalation)."
- "Build StatusBadge, SkillChip, CapacityPill, PerformanceStat primitives."
- "Create AgentRow and AgentMiniCard showing status, skills, capacity/deflection and chips."
- "Scaffold AgentsList with filters (kind/status/skill/overloaded), sort, and search."
- "Create AgentDetail that adapts to AI/Human with tabs and top actions."
- "Build WeekSchedule and CapacityEditor and wire them into Human agent tab."
- "Build BehaviorEditor, AllowlistEditor, KnowledgeLinks for AI agent tab."
- "Create RoutingRulesScreen with rule list and builder; enable/disable/reorder rules."
- "Create PoliciesScreen with overflow and escalation policy editors."
- "Add Performance stats tiles to AgentDetail based on kind."
- "Add AgentRowActionsSheet for quick status/skills/capacity/allowlist toggles."
- "Wire Agents↔Conversations handoff (view assigned, assign to…)."
- "Queue agent edits when offline and add Sync Center access."
- "Add a11y labels and memoization; ensure smooth scroll."
- "Instrument analytics for views, edits, allowlist, routing, policies."
- "Run final audit and create KNOWN_GAPS_Agents.md."

---

## Done‑Definition (Agents v0)
- Agents list filters/sorts/searches smoothly; rows adapt to AI/Human.
- AgentDetail shows correct tabs and editors per kind; edits update local state.
- Routing & Policies screens exist and function locally; reordering works.
- Cross‑app hooks work; offline queue visuals show for edits.
- Accessibility labels present; interactions within reach; lists run at 60fps.
- Analytics stubs fire for key agent interactions.
