# Cursor Build Plan — Conversations (Ultra‑Chunked)
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first, prop‑driven, deep‑links from Dashboard

> Follow these steps sequentially in Cursor. Paste each *Prompt Block* verbatim, complete it, run the app, verify using *Acceptance Checks*, then move on. Keep changes scoped to each step’s deliverables.

---

## Assumptions
- You already shipped **Dashboard v0** and navigation exists.
- UI primitives/tokens available: `Box`, `Text`, `ui/tokens` (from Dashboard plan Step 0).
- Deep link filters from Dashboard: `'urgent' | 'waiting30' | 'unassigned' | 'slaRisk' | 'vip'` (+ optional: channel/intent/tag).

## Paths & Conventions
- **Screens:** `mobile/src/screens/Conversations/*`
- **Components:** `mobile/src/components/conversations/*`
- **Types:** `mobile/src/types/conversations.ts`
- **Navigation Types:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix with `conv-` (e.g., `conv-list`, `conv-row-<id>`, `conv-filter-urgent`, `conv-thread`)
- **Touch targets:** ≥ 44×44dp. Spacing scale: 4/8/12/16/24.

---

## STEP 1 — Conversation Types & Contracts
**Goal:** Define minimal TypeScript models used across list/thread.

### Prompt Block
Create `mobile/src/types/conversations.ts`:
```ts
export type Channel = 'whatsapp'|'instagram'|'facebook'|'web'|'email';
export type Priority = 'low'|'normal'|'high'|'vip';
export type SLAState = 'ok'|'risk'|'breach';
export type FilterKey = 'urgent'|'waiting30'|'unassigned'|'slaRisk'|'vip'|'channel'|'intent'|'tag';

export interface ConversationSummary {
  id: string;
  customerName: string;
  lastMessageSnippet: string;
  lastUpdatedTs: number; // epoch ms
  channel: Channel;
  tags: string[];
  assignedTo?: string; // agent id or name
  priority: Priority;
  waitingMinutes: number; // since last customer msg
  sla: SLAState;
}

export interface Message {
  id: string;
  sender: 'customer'|'ai'|'agent';
  text: string;
  ts: number; // epoch ms
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[];
}
```

### Deliverables
- `types/conversations.ts`

### Acceptance Checks
- Type exports compile; no circular deps.
ok
---

## STEP 2 — Component Shells (List‑level)
**Goal:** Create reusable, logic‑free shells with props + skeletons.

### Prompt Block
In `mobile/src/components/conversations/`, create:
- `ConversationListItem.tsx` (props: `item: ConversationSummary`, `onPress: (id:string)=>void`)
  - Row shows: name, snippet, time‑ago, **channel dot**, **SLA badge**, **waiting chip**, **tags chips**, optional **assignee** and **priority icon**.
- `FilterBar.tsx` (`active: Partial<Record<FilterKey,string>>`, `onChange:(key:FilterKey, value?:string)=>void`)
  - Render chips for: Urgent, Waiting>30m, Unassigned, SLA Risk, VIP; dropdowns for Channel, Intent, Tag (placeholders).
- `SortBar.tsx` (`sort: 'recent'|'oldest'|'priority'`, `onChange:(s:string)=>void`)
- `ListSkeleton.tsx` (N rows placeholder)
- `EmptyState.tsx` (icon+message)
- `SlaBadge.tsx` (props: `state:SLAState`)
- `ChannelDot.tsx` (props: `channel:Channel`)

Use `Box`/`Text` and tokens. Include `testID`s.

### Deliverables
- 7 components exported

### Acceptance Checks
- Import into a dummy screen; no runtime errors; visual shells render.
ok
---

## STEP 3 — Conversations Screen (List)
**Goal:** Scaffold `ConversationsList` with filters, sort, search, deep link support.

### Prompt Block
Create `mobile/src/screens/Conversations/ConversationsList.tsx`:
- SafeAreaView + header title `Conversations`.
- Local demo array of ~25 `ConversationSummary` items (varied channels/tags/waitingMinutes/sla/priority).
- **Incoming deep link:** read `route.params.filter` (optional) to pre‑activate filter chips.
- UI layout: `FilterBar` (top) → `SortBar` → `Search` input → `FlatList` of `ConversationListItem`.
- Implement simple client‑side filters for active chips and search by name/snippet.
- Implement sort by recent/oldest/priority.
- On row press → navigate to `ConversationThread` with `{ id }`.
- Empty & skeleton states: show `ListSkeleton` while `loading` (local toggle); show `EmptyState` if 0 results.
- Add pull‑to‑refresh (refreshes demo data order).
Register screen in navigator as `Conversations`.

### Deliverables
- `ConversationsList.tsx`
- Navigator updated

### Acceptance Checks
- Navigating from Dashboard deep link lands with the right active filter.
- Filters/sort/search update the list quickly; pull‑to‑refresh works.
ok
---

## STEP 4 — Row Interactions (Quick Actions)
**Goal:** Long‑press and swipe actions (assign/tag/resolve/priority).

### Prompt Block
Update `ConversationListItem.tsx`:
- Long‑press → open `RowActionsSheet` (new component) with actions: Assign, Tag, Set Priority, Mark Resolved, Escalate.
- Optional: left/right swipe reveals **Assign** and **Resolve** buttons (platform‑friendly; can be a simple conditional render).
Create `RowActionsSheet.tsx` with props: `id`, callbacks for each action (no backend; just `console.log`).

### Deliverables
- `RowActionsSheet.tsx`
- Updated `ConversationListItem.tsx`

### Acceptance Checks
- Long‑press shows sheet; actions invoke callbacks; UI stays stable.
ok
---

## STEP 5 — Navigation Contracts
**Goal:** Strongly type params for list/thread screens.

### Prompt Block
Update `mobile/src/navigation/types.ts`:
```ts
export type ConversationsStackParamList = {
  Conversations: { filter?: 'urgent'|'waiting30'|'unassigned'|'slaRisk'|'vip' } | undefined;
  ConversationThread: { id: string };
};
```
Ensure `ConversationsList` and `ConversationThread` use these types. Fix imports.

### Deliverables
- Navigation type updates

### Acceptance Checks
- Type checking passes; navigation calls are typed.
ok
---

## STEP 6 — Thread Components (Message UI)
**Goal:** Message bubble primitives with markers.

### Prompt Block
In `mobile/src/components/conversations/`, add:
- `MessageBubble.tsx` (props: `msg: Message`) → left/right alignment for customer vs agent/ai; timestamp; max width ~80%.
- `MessageMetaRow.tsx` → shows sender chip (AI/Human/Customer), time‑ago, and status (placeholder).
- `Composer.tsx` → input + send button (no backend); height grows to 4 lines; has `accessibilityLabel`.
- `ThreadSkeleton.tsx` (placeholder bubbles)

### Deliverables
- 4 components exported

### Acceptance Checks
- Render in a dummy screen without errors.
ok
---

## STEP 7 — Conversation Thread Screen
**Goal:** Assemble full thread with header context.

### Prompt Block
Create `mobile/src/screens/Conversations/ConversationThread.tsx`:
- Fetch demo `ConversationDetail` by `id` (local in‑memory objects).
- Header: customer name, channel dot, SLA badge, priority icon, assignee (if any).
- Body: `FlatList` of `MessageBubble` with inverted scroll (newest at bottom), initial scroll to end.
- Composer at bottom; on send → append a local agent message; `track('message.sent')` (use existing `track` util).
- Add **Context Drawer** (right sheet) with tabs: **Contact**, **History**, **Notes** (placeholders).
- Add **Top bar actions**: Assign, Tag, Priority, Resolve, Escalate (reuse `RowActionsSheet` from list or simple menu).

### Deliverables
- `ConversationThread.tsx`
- Demo data source for details

### Acceptance Checks
- Navigating from list opens the right thread; sending a message appends to end smoothly.
ok
---

## STEP 8 — HITL & Risk Gates (UI‑only)
**Goal:** Surface human‑in‑the‑loop affordances.

### Prompt Block
- Add a `LowConfidenceBanner` component (yellow) at top of thread that appears based on a `lowConfidence` boolean from demo data.
- Add a **Restricted Action Gate**: if `escalate` is tapped and `requiresApproval` flag is true, show confirm modal (UI‑only; no backend).
- In list view, annotate rows with a small ⚠ marker when `lowConfidence` is true.

### Deliverables
- `LowConfidenceBanner.tsx`, confirm modal logic in thread

### Acceptance Checks
- Gates appear based on flags; interactions remain non‑blocking.
ok
---

## STEP 9 — Offline & Queue Stubs
**Goal:** Prepare for offline‑first.

### Prompt Block
- Reuse global `OfflineBanner` (from Dashboard plan) at top of Conversation screens.
- Queue outgoing `send` actions locally (append to thread and mark `queued` visually—e.g., clock icon). Clear `queued` after a timeout to mimic retry.
- Add `SyncCenter` button in thread header (opens the global sheet).

### Deliverables
- Queued message visual, banner reuse, header button

### Acceptance Checks
- Toggling local `offline` state shows banner; queued message shows a clock; then clears.
ok
---

## STEP 10 — Performance & Virtualization
**Goal:** Smooth lists and avoid jank.

### Prompt Block
- Use `FlatList` with `getItemLayout` for message list; set `initialNumToRender` and `maxToRenderPerBatch` reasonably.
- Memoize `ConversationListItem` and `MessageBubble` (`React.memo`); provide stable `keyExtractor`s.
- Debounce search/filter changes in list screen (≥250ms).

### Deliverables
- Performance tweaks in list & thread

### Acceptance Checks
- Scroll stays 60fps on test devices; search doesn’t stutter.
ok
---

## STEP 11 — Accessibility & Ergonomics
**Goal:** WCAG AA basics + mobile reach.

### Prompt Block
- Provide `accessibilityLabel` for all touchables, including chips and list rows.
- Ensure hit areas meet ≥44dp; add `hitSlop` for small icons.
- Verify contrast with semantic tokens; avoid low‑contrast snippets.
- Support RTL bubble alignment; test with an RTL flag.

### Deliverables
- A11y tweaks across components

### Acceptance Checks
- VoiceOver/TalkBack reads sender and message correctly; chips are focusable.
ok
---

## STEP 12 — Analytics Stubs
**Goal:** Instrument key actions.

### Prompt Block
Using `lib/analytics.ts`:
- On list mount: `track('conversations.view')`
- On filter change: `track('conversations.filter', { key, value })`
- On row open: `track('conversation.open', { id })`
- On message sent: `track('message.sent', { id, len })`
- On escalate/resolve: `track('conversation.action', { action })`

### Deliverables
- Calls added in list/thread

### Acceptance Checks
- Console shows events on interactions in dev.
ok
---

## STEP 13 — Final Review & Gaps
**Goal:** Ensure parity with BusinessFlow specs.

### Prompt Block
- Verify deep links from Dashboard arrive with correct filter.
- Confirm FilterBar includes Urgent/Waiting30/Unassigned/SLA Risk/VIP and stubs for Channel/Intent/Tag.
- Ensure RowActions available: Assign, Tag, Priority, Resolve, Escalate (UI‑only).
- Test offline banners, queued send, and Sync Center button.
- Create `mobile/docs/KNOWN_GAPS_Conversations.md` listing any deferred items (real data, smart intent chips, assignee directory, etc.).

### Deliverables
- Fixes + KNOWN_GAPS file

### Acceptance Checks
- All checks pass; doc created.
ok
---

## Paste‑Ready Micro Prompts
- "Create conversation TypeScript models per STEP 1."
- "Build ConversationListItem with channel dot, SLA badge, waiting chip, tags; long‑press opens actions sheet."
- "Implement FilterBar with five chips + three dropdown placeholders; wire onChange callback."
- "Scaffold ConversationsList with demo data, deep link filter, FlatList, search, sort."
- "Add RowActionsSheet with Assign/Tag/Priority/Resolve/Escalate callbacks."
- "Strongly type navigation params for Conversations/ConversationThread."
- "Create MessageBubble/Composer components and build ConversationThread with inverted FlatList."
- "Add LowConfidenceBanner and restricted action confirm for Escalate."
- "Queue outgoing messages when offline and show a queued indicator; add Sync Center button."
- "Memoize list rows and message bubbles; debounce search."
- "Add accessibility labels and hitSlop; verify RTL alignment."
- "Instrument analytics events for views, filters, opens, sends, actions."
- "Run final audit and create KNOWN_GAPS_Conversations.md."

---

## Done‑Definition (Conversations v0)
- Deep link filters from Dashboard work.
- Filters/sort/search operate smoothly; list stays 60fps.
- Thread view renders reliably; sending appends locally.
- Offline banner and queued sends visible; Sync Center opens.
- A11y labels present; chips focusable; contrast ok.
- Analytics stubs fire for key interactions.

