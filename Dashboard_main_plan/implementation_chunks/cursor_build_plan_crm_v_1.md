# Cursor Build Plan — CRM (Ultra‑Chunked)
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first, prop‑driven, privacy‑aware

> Follow these steps sequentially in Cursor. Paste each *Prompt Block* verbatim, complete it, run the app, validate with the *Acceptance Checks*, then move on. Keep changes scoped to each step’s deliverables.

---

## Assumptions
- **Dashboard** and **Conversations** v0 are shipped; navigation exists.
- UI primitives/tokens available: `Box`, `Text`, `ui/tokens`.
- We will reuse `Channel` type from `types/conversations.ts`.
- This is **UI‑only**: no backend. All data is local demo state with stubs.

## Paths & Conventions
- **Screens:** `mobile/src/screens/CRM/*`
- **Components:** `mobile/src/components/crm/*`
- **Types:** `mobile/src/types/crm.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix with `crm-` (e.g., `crm-list`, `crm-row-<id>`, `crm-detail`, `crm-segments`)
- **Touch targets:** ≥ 44×44dp. Spacing scale: 4/8/12/16/24.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define minimal models for contacts, consent, segments, and dedup.

### Prompt Block
Create `mobile/src/types/crm.ts`:
```ts
import type { Channel } from '../types/conversations';

export type ConsentState = 'granted'|'denied'|'unknown'|'withdrawn';
export interface Contact {
  id: string;
  name: string;
  email?: string;
  phone?: string;
  channels: Channel[];             // where they contacted us
  tags: string[];                  // arbitrary labels
  vip?: boolean;
  lastInteractionTs?: number;      // epoch ms
  lifetimeValue?: number;          // optional (UI only for now)
  consent: ConsentState;
  piiRedacted?: boolean;           // UI toggle only
}

export interface InteractionSummary {
  conversationId: string;
  lastMessageSnippet: string;
  lastUpdatedTs: number;
  channel: Channel;
  intentTags?: string[];
  sla?: 'ok'|'risk'|'breach';
}

export type RuleOp = 'is'|'is_not'|'contains'|'gt'|'lt'|'in';
export interface SegmentRule { field: 'tag'|'vip'|'consent'|'channel'|'lastInteractionTs'|'lifetimeValue'; op: RuleOp; value: any; }
export interface Segment { id: string; name: string; rules: SegmentRule[]; color?: string; }

export interface DedupeCandidate { masterId: string; dupId: string; reason: 'same_email'|'same_phone'|'name_similarity'; score: number; }
```

### Deliverables
- `types/crm.ts`

### Acceptance Checks
- Types compile; no circular imports.

---

## STEP 2 — Component Shells (Primitives)
**Goal:** Create reusable UI primitives for CRM.

### Prompt Block
In `mobile/src/components/crm/`, create:
- `TagChip.tsx` (props: `label:string; onPress?; selected?:boolean`)
- `ConsentBadge.tsx` (props: `state:ConsentState`)
- `VipBadge.tsx` (props: `active:boolean`)
- `ValuePill.tsx` (props: `value?:number`) // shows formatted currency or `—`
- `RedactionToggle.tsx` (props: `enabled:boolean; onToggle:()=>void`) // UI only
- `ListSkeleton.tsx` (N rows placeholder)
- `EmptyState.tsx`
Use `Box`/`Text` and tokens; provide `testID`s.

### Deliverables
- 7 primitive components

### Acceptance Checks
- Can render each component standalone without runtime errors.

---

## STEP 3 — Row & Card Components
**Goal:** Build list row and small card for reuse.

### Prompt Block
Create:
- `ContactRow.tsx` (props: `{ item: Contact; onPress:(id:string)=>void; onLongPress?:(id:string)=>void; interaction?: InteractionSummary }`)
  - Shows name, **ConsentBadge**, **VipBadge**, last interaction time‑ago & snippet, **TagChip**s, **ValuePill**, channel dots.
- `ContactMiniCard.tsx` (small card variant for grid/related lists)

### Deliverables
- 2 components exported

### Acceptance Checks
- Render with mock data; long names and many tags wrap gracefully.

---

## STEP 4 — CRM List Screen
**Goal:** Scaffold list with filters/sort/search + deep links from Conversations.

### Prompt Block
Create `mobile/src/screens/CRM/CRMList.tsx`:
- SafeArea + title `CRM`.
- Local demo array of ~200 `Contact` items (varied consent/tags/vip/channels/LTV).
- `FilterBar` (inline in screen): chips **VIP**, **Has Open Conversation**, dropdowns for **Channel**, **Tag**, **Consent**.
- `SortBar`: **Recent**, **Name A→Z**, **LTV High→Low**.
- Search input (name/email/phone/tag).
- `FlatList` of `ContactRow`.
- **Deep link support:** if opened with `{ contactId }`, scroll to and highlight that row.
- Empty & skeleton states; pull‑to‑refresh reorders demo data.
Register screen as `CRM`.

### Deliverables
- `CRMList.tsx`
- Navigator update

### Acceptance Checks
- Filters/sort/search work smoothly; list virtualization ok; deep link focuses target row.

---

## STEP 5 — Row Interactions (Quick Actions)
**Goal:** Long‑press sheet with common actions.

### Prompt Block
Create `RowActionsSheet.tsx` with actions: **Toggle VIP**, **Add/Remove Tag**, **Set Consent**, **Start Conversation**, **Merge…**, **Delete** (confirm).
Wire into `ContactRow` long‑press. For **Start Conversation**, navigate to `ConversationThread` if an interaction exists, else to `Conversations` with a prefill param (placeholder).

### Deliverables
- `RowActionsSheet.tsx`
- Updated `ContactRow.tsx`

### Acceptance Checks
- Sheet opens; each action triggers a stub; navigation works.

---

## STEP 6 — Contact Detail Screen
**Goal:** Profile with tabs for Activity & Notes.

### Prompt Block
Create `mobile/src/screens/CRM/ContactDetail.tsx`:
- Header: name, **VipBadge**, **ConsentBadge**, **RedactionToggle**.
- **Profile tab:** fields (email/phone/channels), editable tags, LTV pill, buttons: Call / Email (if present; use OS intents stubs).
- **Activity tab:** list of `InteractionSummary` items; tap → open `ConversationThread`.
- **Notes tab:** simple notes list + add note composer.
- **Top actions:** Edit, Merge, Delete (confirm), Start Conversation.

### Deliverables
- `ContactDetail.tsx`

### Acceptance Checks
- Navigating from list opens details; tabs switch; interactions open threads; redaction toggle changes UI text style (e.g., mask email/phone).

---

## STEP 7 — Import & Export (UI Stubs)
**Goal:** Basic CSV import/export surfaces.

### Prompt Block
Create `ImportExportSheet.tsx` with:
- **Import CSV**: file picker (stub), shows preview table, map CSV columns → fields, apply (updates local demo state).
- **Export CSV**: pick fields (checkboxes), generate blob (stub) and show toast `Export ready`.
Add a button in `CRMList` header to open this sheet.

### Deliverables
- `ImportExportSheet.tsx`
- Header button wiring

### Acceptance Checks
- Opening sheet works; mapping UI renders; export shows a success toast.

---

## STEP 8 — Dedupe & Merge Flow (UI‑only)
**Goal:** Manage duplicate contacts safely.

### Prompt Block
Create `DedupeCenter.tsx`:
- List of `DedupeCandidate` grouped by reason; each item → **Review**.
- **Review screen**: side‑by‑side compare of master vs duplicate fields with per‑field pickers (radio). Bottom sheet: confirm merge → shows success toast; UI removes dup locally.
Add `DedupeCenter` entry point from CRM header overflow menu.

### Deliverables
- `DedupeCenter.tsx` (+ small Review component or nested screen)

### Acceptance Checks
- Review flow selectable; merging updates local data; no crashes.

---

## STEP 9 — Segments Builder
**Goal:** Create, preview, and save dynamic segments.

### Prompt Block
Create `SegmentsScreen.tsx`:
- **Segments list** with create button.
- **Builder**: add rules (`field/op/value`), live **preview count** from current contacts, color picker for segment chip.
- Save segment → appears in list; can **apply** to CRM list as active filter.
Add `SegmentsScreen` entry in CRM header and a quick access button in `CRMList`.

### Deliverables
- `SegmentsScreen.tsx`
- Wiring to apply a segment to `CRMList`

### Acceptance Checks
- Creating a segment updates preview count and can be applied to filter contacts.

---

## STEP 10 — Consent & Privacy Surfaces
**Goal:** Make privacy controls visible and consistent.

### Prompt Block
- In `ContactDetail`, add **ConsentHistory** section (UI only timeline with changes).
- In `CRMList` filters, allow **Consent** filter chip.
- Add **“Request Deletion”** button (UI only) in `ContactDetail` that logs an event.
- Link to **Security & Privacy Center** (§31) from CRM header overflow.

### Deliverables
- Consent history UI; deletion request stub; link to center

### Acceptance Checks
- Consent filter chips filter; deletion request shows a toast; link navigates to center shell.

---

## STEP 11 — Offline & Queue Stubs
**Goal:** Prepare CRM edits for offline.

### Prompt Block
- Reuse global **OfflineBanner**.
- Queue local mutations: toggle VIP, add tag, set consent; show small clock icon near the field while queued.
- Add **Sync Center** button in CRM header (opens global sheet).

### Deliverables
- Queued state visuals; header button

### Acceptance Checks
- Toggling offline shows banner; queued icons appear then clear after timeout.

---

## STEP 12 — Performance & Virtualization
**Goal:** Keep large lists smooth.

### Prompt Block
- Use `FlatList` with `getItemLayout`, `initialNumToRender`, `removeClippedSubviews`.
- Implement **alpha index jump** (A–Z) sidebar: tapping a letter scrolls to first contact with that initial.
- Debounce search (≥250ms) and heavy filters.
- Memoize `ContactRow` with `React.memo` and stable keys.

### Deliverables
- Perf tweaks in `CRMList`

### Acceptance Checks
- 200+ contacts scroll at 60fps; A–Z jump works; search is responsive.

---

## STEP 13 — Accessibility & Ergonomics
**Goal:** WCAG AA and mobile reach.

### Prompt Block
- Provide `accessibilityLabel` for rows (e.g., "Contact <name>, VIP, consent <state>").
- Ensure all touchables meet ≥44dp, add `hitSlop` for small chips.
- Verify contrast for badges and pills using semantic tokens.
- Support **RTL**: right‑align labels where needed.

### Deliverables
- A11y tweaks across CRM components

### Acceptance Checks
- Screen readers announce name + key attributes; chips focusable; contrast ok.

---

## STEP 14 — Analytics Stubs
**Goal:** Instrument key CRM events.

### Prompt Block
Using `lib/analytics.ts`:
- On list mount: `track('crm.view')`
- On filter change: `track('crm.filter', { key, value })`
- On segment create/apply: `track('crm.segment', { action:'create'|'apply', id })`
- On VIP toggle: `track('crm.vip', { id, value })`
- On tag add/remove: `track('crm.tag', { id, tag, action })`
- On merge: `track('crm.merge', { masterId, dupId })`
- On request deletion: `track('crm.delete_request', { id })`

### Deliverables
- Analytics calls added

### Acceptance Checks
- Console logs events on interactions in dev.

---

## STEP 15 — Cross‑App Hooks
**Goal:** Ensure CRM ↔ Conversations hand‑off works.

### Prompt Block
- From Conversations **Contact mini‑pane**, add a `View Contact` button → navigates to `ContactDetail`.
- From `ContactDetail`, **Start Conversation** navigates to existing thread if any, else opens `Conversations` with a prefill (UI only).

### Deliverables
- Navigation hooks added in both directions

### Acceptance Checks
- Hand‑off works both ways without crashes.

---

## STEP 16 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow specs & privacy surfaces.

### Prompt Block
- Verify: VIP toggle, consent badges/filters, tags, LTV pill, import/export sheet, dedupe center, segments builder, redaction toggle, cross‑app hooks.
- Run offline toggles and queued edits; check Sync Center button.
- Create `mobile/docs/KNOWN_GAPS_CRM.md` listing deferred items (real storage, server dedupe, directory picker for agents, segment scheduling, etc.).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.

---

## Paste‑Ready Micro Prompts
- "Define CRM TypeScript models per STEP 1 (Contact, Segment, DedupeCandidate)."
- "Build TagChip, ConsentBadge, VipBadge, ValuePill, RedactionToggle primitives."
- "Create ContactRow with consent/VIP badges, tags, LTV, channel dots, last interaction snippet."
- "Scaffold CRMList with filters (VIP/open/channel/tag/consent), sort, search, deep link to a contactId."
- "Add RowActionsSheet (VIP, tag, consent, start conversation, merge, delete)."
- "Create ContactDetail with tabs (Profile/Activity/Notes) and redaction toggle."
- "Implement ImportExportSheet for CSV mapping stubs and export stub."
- "Build DedupeCenter with Review/merge picker UI (UI only)."
- "Create SegmentsScreen with rule builder and live preview; apply segment to CRMList."
- "Surface consent history and deletion request stub; link to Security & Privacy Center shell."
- "Queue offline edits (VIP/tag/consent) and add Sync Center button in header."
- "Add A–Z index jump and performance tweaks; debounce search."
- "Add accessibility labels and hitSlop; verify RTL."
- "Instrument analytics events for view/filter/segment/merge/tag/vip/delete_request."
- "Run final audit and create KNOWN_GAPS_CRM.md."

---

## Done‑Definition (CRM v0)
- List renders 200+ contacts smoothly with filters/sort/search.
- Contact detail with tabs works; redaction toggle masks PII visually.
- Import/export & dedupe surfaces exist (UI stubs) and don’t crash.
- Segments builder creates/apply segments; counts update live.
- Offline banner and queued edits visible; Sync Center opens.
- Accessibility labels present; contrast ok; RTL holds.
- Analytics stubs fire for key CRM interactions.

