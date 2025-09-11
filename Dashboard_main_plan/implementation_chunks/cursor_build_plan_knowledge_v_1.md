# Cursor Build Plan — Knowledge (Sources, Training, Coverage & Fix Loop) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first (no backend), prop‑driven components with skeletons

> Build the **Knowledge** area that manages sources (Uploads, URLs, Notes), training runs, coverage, failure log, test harness, redaction rules, and drift warnings. Follow each *Prompt Block* in order. After each step: run, verify with the *Acceptance Checks*, commit, then move forward.

---

## Assumptions
- **Dashboard**, **Conversations**, **CRM**, **Agents** v0 are present with navigation.
- UI primitives exist: `Box`, `Text`, `ui/tokens`; analytics util `track()` is available.
- Security & Privacy Center shell exists (§31 in BusinessFlow doc) for linking.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Knowledge/*`
- **Components:** `mobile/src/components/knowledge/*`
- **Types:** `mobile/src/types/knowledge.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix with `kn-` (e.g., `kn-home`, `kn-source-row-<id>`, `kn-train-btn`)
- Spacing: 4/8/12/16/24; touch ≥ 44×44dp; RTL ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define models for sources, training, coverage, failures, tests, redaction, drift.

### Prompt Block
Create `mobile/src/types/knowledge.ts`:
```ts
export type SourceKind = 'upload'|'url'|'note';
export type SourceScope = 'global'|`agent:${string}`;
export type SourceStatus = 'idle'|'training'|'trained'|'error';

export interface BaseSource { id:string; kind:SourceKind; title:string; enabled:boolean; scope: SourceScope; status: SourceStatus; lastTrainedTs?: number; estSizeKB?: number; }
export interface UrlSource extends BaseSource { kind:'url'; url:string; crawlDepth:number; allowPaths?: string[]; blockPaths?: string[]; respectRobots?: boolean; }
export interface UploadSource extends BaseSource { kind:'upload'; filename:string; mime:string; sizeKB:number; pages?: number; hash?: string; }
export interface NoteSource extends BaseSource { kind:'note'; content:string; tags?: string[]; }
export type KnowledgeSource = UrlSource | UploadSource | NoteSource;

export interface TrainingJob { id:string; sourceId:string; progress:number; state:'queued'|'running'|'completed'|'failed'; startedAt:number; finishedAt?:number; message?:string }

export interface CoverageStat { topics:number; faqs:number; gaps:number; coveragePct:number }
export interface FailureItem { id:string; question:string; matchedIntent?:string; confidence:number; occurredAt:number; channel?:string; conversationId?:string; suggestedSourceKind?: SourceKind }

export interface TestCase { id:string; question:string; expectedAnswer?:string; tags?:string[]; lastRunAt?:number; lastPass?: boolean }

export interface RedactionRule { id:string; pattern:string; enabled:boolean; sample?:string; description?:string }
export interface DriftWarning { sourceId:string; kind:'stale_url'|'upload_changed'|'domain_unreachable'; detail?:string; detectedAt:number }
```

### Deliverables
- `types/knowledge.ts`

### Acceptance Checks
- Types compile; no circular dependencies.
ok
---

## STEP 2 — Primitive Components
**Goal:** Create reusable knowledge UI primitives.

### Prompt Block
In `mobile/src/components/knowledge/`, create:
- `SourceCard.tsx` (props: `{ src: KnowledgeSource; onPress: (id:string)=>void }`) — shows kind icon, title, scope chip, status badge, last trained time.
- `StatusBadge.tsx` (props: `status: SourceStatus`)
- `ScopePicker.tsx` (props: `{ value: SourceScope; onChange:(v:SourceScope)=>void }`)
- `TrainProgressBar.tsx` (props: `{ job?: TrainingJob }`)
- `CoverageTile.tsx` (props: `CoverageStat`)
- `FailureRow.tsx` (props: `FailureItem`, onPress to open details or linked conversation)
- `RedactionRuleRow.tsx` (props: `RedactionRule`, toggle)
- `ListSkeleton.tsx`, `EmptyState.tsx`
Ensure each uses `Box`/`Text` and tokens; include `testID`s.

### Deliverables
- 9 primitives exported

### Acceptance Checks
- All render in isolation with demo props; touch targets ≥44dp.
ok
---

## STEP 3 — Knowledge Home Screen
**Goal:** Central hub listing sources, coverage, drift, and training CTA.

### Prompt Block
Create `mobile/src/screens/Knowledge/KnowledgeHome.tsx`:
- Header `Knowledge` with overflow menu (links to Privacy Center and Redaction Rules).
- **Coverage row**: `CoverageTile` + tiny trend deltas (stub).
- **Sources section**: group by kind — Uploads, URLs, Notes. Render `SourceCard` items.
- **Actions**: buttons `Add URL`, `Add Upload`, `New Note`, `Train Now` (UI only).
- **Drift & Warnings**: list `DriftWarning` items if any.
- Pull‑to‑refresh to reshuffle demo data.
Register in navigator as `Knowledge`.

### Deliverables
- `KnowledgeHome.tsx`
- Navigator update

### Acceptance Checks
- Screen renders with demo sources; buttons visible; pull‑to‑refresh works.
ok
---

## STEP 4 — Add URL Source Flow
**Goal:** Create a guided flow (sheet or screen) to add URL sources.

### Prompt Block
Create `AddUrlSource.tsx`:
- Fields: **URL**, **Crawl depth** (0–2), **Respect robots.txt** (toggle), **Allow paths**/**Block paths** (comma‑sep), **Title**, **ScopePicker**.
- Validate URL format; show est. size (fake calculation).
- `Save` → adds a demo `UrlSource` to local list; toast success.
Link from `KnowledgeHome` → `AddUrlSource`.

### Deliverables
- `AddUrlSource.tsx` + wiring

### Acceptance Checks
- Invalid URLs show inline error; saving appends to sources list.
ok
---

## STEP 5 — Add Upload Source Flow (UI‑only)
**Goal:** File picker stub + preview + chunking hint.

### Prompt Block
Create `AddUploadSource.tsx`:
- Fake picker button → simulate file metadata (name/mime/size/pages).
- Options: **Split by headings** (toggle), **Auto** (default).
- Fields: **Title**, **ScopePicker**.
- `Save` → adds `UploadSource` to list; toast.
Wire from `KnowledgeHome`.

### Deliverables
- `AddUploadSource.tsx` + wiring

### Acceptance Checks
- File metadata preview appears; saving creates a new upload source locally.
ok
---

## STEP 6 — Note Source Editor
**Goal:** Simple internal knowledge notes.

### Prompt Block
Create `NoteEditor.tsx`:
- Fields: **Title**, **Tags**, **Content** (multi‑line), **ScopePicker**, **Enabled** toggle.
- `Save` → adds/updates `NoteSource`.
Link from `KnowledgeHome` as `New Note` and on `SourceCard` press for notes.

### Deliverables
- `NoteEditor.tsx` + routes

### Acceptance Checks
- Creating and editing notes works; tags render as chips elsewhere.
ok
---

## STEP 7 — Training Queue & Status
**Goal:** Queue training across selected/changed sources with progress.

### Prompt Block
Create `TrainingCenter.tsx`:
- `Train Now` button → creates demo `TrainingJob` per enabled source; jobs show in a list with `TrainProgressBar` until completion.
- `lastTrainedTs` updates on each source upon job completion.
- Error injection toggle (dev‑only) to simulate a failure.
Hook `Train Now` from `KnowledgeHome` to open `TrainingCenter`.

### Deliverables
- `TrainingCenter.tsx` + linkage

### Acceptance Checks
- Jobs progress visually; sources reflect updated status/last trained times.
ok
---

## STEP 8 — Coverage & Health
**Goal:** Visualize coverage and show drift warnings.

### Prompt Block
Create `CoverageHealth.tsx`:
- Show `CoverageTile` + mini bars: topics/faqs/gaps.
- **Drift warnings** list with kinds (stale_url/upload_changed/domain_unreachable) and quick actions (open URL, re‑train, remove).
Link from `KnowledgeHome` coverage row.

### Deliverables
- `CoverageHealth.tsx`

### Acceptance Checks
- Coverage numbers render; tapping a warning opens the correct action sheet.
ok
---

## STEP 9 — Failure Log & Fix Loop
**Goal:** Close the loop on low‑confidence answers and gaps.

### Prompt Block
Create `FailureLog.tsx`:
- List `FailureItem` rows; filters: **Confidence < x%**, **Channel**, **Intent tag**, **Time**.
- Row actions: **Open conversation** (navigates to `ConversationThread`), **Attach to source** (choose existing note or URL/upload), **Create Note** (opens `NoteEditor` prefilled with question), **Mark as resolved** (UI toggle).
Link from `KnowledgeHome` via button `Failures`.

### Deliverables
- `FailureLog.tsx` + navigation hooks

### Acceptance Checks
- Clicking a failure can jump to thread or create/edit a note; UI updates list.
ok
---

## STEP 10 — Test Harness
**Goal:** Verify answers pre‑deployment.

### Prompt Block
Create `TestHarness.tsx`:
- Input: **Question**; on submit → show **Actual** (canned response) vs **Expected** (editable); mark **Pass/Fail**.
- Maintain a local list of `TestCase`s; can **Run All** (simulated) to update `lastPass`.
- Tag tests by topic; filter by tag.
Add entry from Knowledge header.

### Deliverables
- `TestHarness.tsx`

### Acceptance Checks
- Creating/running tests works; pass/fail badges visible.
ok
---

## STEP 11 — Source Priority & Scope
**Goal:** Control precedence and agent scoping.

### Prompt Block
Create `SourcePriority.tsx`:
- Draggable (or up/down buttons) list of sources to set priority order; show scope chips; toggle **Enabled** per source.
- Info note: "Higher sources override when conflicts occur" (UI only).
Link from Knowledge overflow menu.

### Deliverables
- `SourcePriority.tsx`

### Acceptance Checks
- Reordering changes local order; toggling enabled hides source from home list.
ok
---

## STEP 12 — Redaction & Content Controls
**Goal:** PII protection and content toggles.

### Prompt Block
Create `RedactionRules.tsx`:
- List of `RedactionRuleRow` items; add/edit rule modal (pattern preview with sample masking, UI only).
- Global toggle: **Apply redaction in answers**.
- Button: **Open Security & Privacy Center** (navigates to §31 shell).
Link from Knowledge header overflow.

### Deliverables
- `RedactionRules.tsx` + navigation

### Acceptance Checks
- Toggling rules flips UI state; link navigates correctly.
ok
---

## STEP 13 — Versioning & Change Log (UI‑only)
**Goal:** Show training snapshots and basic diffs.

### Prompt Block
Create `VersionsScreen.tsx`:
- Timeline of training jobs with status; selecting a run shows included sources and counts.
- **Restore** button (UI only) with confirm modal; **Diff** placeholder showing changed sources and “+/- topics/faqs”.
Entry via Knowledge overflow.

### Deliverables
- `VersionsScreen.tsx`

### Acceptance Checks
- Selecting versions updates the panel; restore shows a toast; no crashes.
ok
---

## STEP 14 — Offline & Queue Stubs
**Goal:** Prepare knowledge edits for offline.

### Prompt Block
- Reuse global **OfflineBanner**.
- Queue local mutations: source add/edit, enable toggle, redaction changes; show small clock near changed field while queued; clear after timeout.
- Add **Sync Center** button in Knowledge header.

### Deliverables
- Queued state visuals; header button

### Acceptance Checks
- Offline state shows banner; queued icons appear then clear.
ok
---

## STEP 15 — Performance & Accessibility
**Goal:** Smooth lists and WCAG basics.

### Prompt Block
- Virtualize long source lists; provide stable keys; memoize `SourceCard`.
- Debounce search/filters (≥250ms) where present.
- Add `accessibilityLabel` for every interactive control (e.g., "Train now", "Add URL").
- Ensure contrast for status badges; touch targets ≥44dp; add `hitSlop` to small icons.

### Deliverables
- Perf & a11y tweaks across Knowledge screens

### Acceptance Checks
- 60fps on scroll; screen readers can operate controls; contrast ok.
ok
---

## STEP 16 — Analytics Stubs
**Goal:** Instrument key knowledge events.

### Prompt Block
Using `lib/analytics.ts` instrument:
- `track('knowledge.view')` on home mount
- `track('knowledge.source_add', { kind })`
- `track('knowledge.train', { count })`
- `track('knowledge.failure_action', { action })` for open/attach/createNote/resolve
- `track('knowledge.test_case', { action:'create'|'run_all'|'run_one', pass })`
- `track('knowledge.redaction', { action:'toggle'|'add'|'edit'|'remove' })`
- `track('knowledge.version', { action:'restore'|'diff_view' })`

### Deliverables
- Analytics calls added

### Acceptance Checks
- Console logs events in dev during interactions.
ok
---

## STEP 17 — Cross‑App Hooks
**Goal:** Tighten loops with Conversations & Dashboard.

### Prompt Block
- From **FailureLog** row → **Open conversation** (to `ConversationThread` by `conversationId`).
- From **KnowledgeHome** overflow → **Open Dashboard Q&A** (navigate to Dashboard section for Ask panel).
- From **AgentDetail** (AI) → link **KnowledgeLinks** to KnowledgeHome filtered for those sources.

### Deliverables
- Navigation hooks added

### Acceptance Checks
- Navigation works as expected without crashes.
ok
---

## STEP 18 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow specs for Knowledge.

### Prompt Block
- Verify: sources list, add flows (URL/Upload/Note), training center with progress, coverage health & drift, failure log with fix loop, test harness, priority ordering & scope, redaction rules, versions, offline & sync, a11y/perf, analytics, cross‑app.
- Create `mobile/docs/KNOWN_GAPS_Knowledge.md` listing deferred items (real training jobs, crawler, embeddings store, diff visualizer, long‑running job toasts, multi‑tenant scopes, etc.).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.
ok
---

## Paste‑Ready Micro Prompts
- "Define Knowledge TypeScript models (sources, training, coverage, failures, tests, redaction, drift)."
- "Build SourceCard, StatusBadge, ScopePicker, TrainProgressBar, CoverageTile, FailureRow, RedactionRuleRow primitives."
- "Create KnowledgeHome screen with coverage row, grouped sources, actions, and drift warnings; register in navigator."
- "Implement AddUrlSource flow with URL validation, crawl depth, robots toggle, allow/block paths, title, scope."
- "Implement AddUploadSource flow with fake picker, metadata preview, split-by-headings toggle, title, scope."
- "Create NoteEditor for new/edit notes with tags and enabled toggle."
- "Create TrainingCenter to queue demo jobs, show progress, and update lastTrainedTs."
- "Build CoverageHealth view with mini bars and drift warnings quick actions."
- "Create FailureLog with filters and actions (open conversation, attach source, create note, mark resolved)."
- "Create TestHarness for Actual vs Expected with run-all and pass/fail badges."
- "Create SourcePriority ordering UI with scope chips and enabled toggles."
- "Create RedactionRules screen with rule list, modal editor, global apply toggle, and link to Privacy Center."
- "Create VersionsScreen timeline with restore/diff placeholders."
- "Add OfflineBanner reuse and queue edits; add Sync Center button in header."
- "Add performance memoization/virtualization and accessibility labels; ensure ≥44dp touch targets."
- "Instrument analytics events for view, source_add, train, failure_action, test_case, redaction, version."
- "Run final audit and create KNOWN_GAPS_Knowledge.md."

---

## Done‑Definition (Knowledge v0)
- KnowledgeHome shows coverage, sources grouped by kind, drift warnings, and actions.
- Add flows (URL/Upload/Note) work locally; TrainingCenter queues jobs and updates status.
- CoverageHealth and FailureLog enable the fix loop; TestHarness saves & runs test cases.
- SourcePriority ordering, RedactionRules, and Versions screens exist and are navigable.
- Offline banner and queued edits visible; Sync Center opens.
- Accessibility labels present; lists scroll smoothly; contrast holds.
- Analytics stubs fire for key knowledge interactions.

