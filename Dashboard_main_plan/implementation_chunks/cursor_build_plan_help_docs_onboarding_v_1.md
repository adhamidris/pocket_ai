# Cursor Build Plan — Help, Docs & Onboarding Tours (Coachmarks, Checklists, Help Center) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript)
**Mode:** UI‑first only (no backend), local demo content & flags

> Build the **in‑product education system**: Help Center, contextual help, command palette, quickstart checklist, tours/coachmarks, empty‑state guides, release notes, micro‑surveys, and feedback. Everything is UI‑only with local state. Follow each *Prompt Block* in order and validate with *Acceptance Checks*.

---

## Assumptions
- Settings, Dashboard, Conversations, Knowledge, Channels, Automations, Analytics, Portal, Assistant, Security, Billing, Actions modules exist.
- Theme tokens available; analytics `track()` util exists; Offline/Sync stubs exist.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Help/*`
- **Components:** `mobile/src/components/help/*`
- **Types:** `mobile/src/types/help.ts`
- **Content:** `mobile/src/content/help/*`
- **Testing IDs:** prefix `help-` (e.g., `help-button`, `help-checklist-item-0`, `help-tour-step-1`)
- Spacing 4/8/12/16/24; hit area ≥44dp; RTL‑ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define models for articles, tours, checklists, nudges, release notes, feedback, and surveys.

### Prompt Block
Create `mobile/src/types/help.ts`:
```ts
export type ArticleKind = 'guide'|'howto'|'faq'|'troubleshoot'|'release';
export interface Article { id:string; kind:ArticleKind; title:string; bodyMd:string; tags:string[]; updatedAt:number; relatedIds?:string[] }

export interface ChecklistStep { id:string; title:string; done:boolean; cta?:{ label:string; route:string; params?:Record<string,any> } }
export interface Checklist { id:string; title:string; steps:ChecklistStep[]; progress:number }

export interface TourStep { id:string; anchorTestId:string; title:string; body:string; placement:'top'|'bottom'|'left'|'right'|'auto'; }
export interface Tour { id:string; title:string; steps:TourStep[]; completed:boolean; eligible:boolean; surface:'dashboard'|'conversations'|'knowledge'|'channels'|'automations'|'analytics'|'settings'|'portal'|'actions' }

export interface Nudge { id:string; text:string; route?:string; testId?:string; dismissible:boolean; priority:'low'|'med'|'high' }

export interface ReleaseNote { id:string; version:string; dateIso:string; highlights:{ title:string; body:string; route?:string }[] }

export interface FeedbackItem { id:string; text:string; contact?:string; context?:string; rating?:1|2|3|4|5; createdAt:number }

export interface Survey { id:string; name:string; trigger:'time'|'screen'|'action'; question:string; scale?:'nps'|'csat'|'likert'; options?:string[]; dismissible:boolean; cooldownDays:number }

export interface HelpState { seenVersions:string[]; completedTours:string[]; dismissedNudges:string[]; checklists:Checklist[]; }
```

### Deliverables
- `types/help.ts`

### Acceptance Checks
- Types compile; exported from index barrel if used.

---

## STEP 2 — Primitive Components
**Goal:** Build reusable education UI primitives.

### Prompt Block
In `mobile/src/components/help/`, create:
- `HelpButton.tsx` — floating button with `?` icon; opens Help Center; testID `help-button`.
- `Coachmark.tsx` — anchored tooltip with arrow and **Next/Skip** controls.
- `Spotlight.tsx` — overlays and highlights a target by `testID`.
- `ChecklistCard.tsx` — list of `ChecklistStep`s with progress, checkboxes, and CTA buttons.
- `EmptyStateGuide.tsx` — illustration + steps + CTA; accepts `title`, `lines[]`, `cta`.
- `CommandPalette.tsx` — search input + results (routes/actions); opens via global shortcut/FAB.
- `SearchBar.tsx` + `SearchResults.tsx` for articles/how‑tos.
- `ArticleViewer.tsx` — renders markdown body (UI‑only) with related links.
- `ReleaseNotes.tsx` — version list and details; **What’s new** banner when unseen version exists.
- `FeedbackForm.tsx` — multiline, rating stars (1–5), contact field, **Send**.
- `SurveyModal.tsx` — NPS/CSAT/Likert variants; throttle by `cooldownDays` (UI‑only).
- `ListSkeleton.tsx`, `EmptyState.tsx`.

### Deliverables
- 13 primitives exported

### Acceptance Checks
- All primitives render with demo props; meet ≥44dp; provide `accessibilityLabel`s.

---

## STEP 3 — Help Center Screen
**Goal:** Centralized searchable Help.

### Prompt Block
Create `mobile/src/screens/Help/HelpCenter.tsx`:
- Header `Help & Docs` with tabs: **Search**, **Quickstart**, **Tours**, **What’s New**.
- **Search**: `SearchBar` + `SearchResults` over local `Article[]`; filter by tag (e.g., Channels, Knowledge, Automations).
- **Quickstart**: `ChecklistCard` for the core onboarding checklist (Connect channel → Publish link → Train FAQs → Set hours → Test portal → Create rule).
- **Tours**: list of available `Tour`s with **Start/Resume** buttons and completion badges.
- **What’s New**: `ReleaseNotes` summary.
- Footer links: **Feedback**, **Contact**, **Privacy** (stub routes).
- `track('help.center.view')` on mount.
Register `HelpCenter` in navigator; open from `HelpButton` and Settings/About.

### Deliverables
- `HelpCenter.tsx`

### Acceptance Checks
- Search returns demo articles; starting a tour navigates to the correct surface.

---

## STEP 4 — Onboarding Checklist (Global)
**Goal:** A persistent quickstart checklist that shows on Dashboard until complete.

### Prompt Block
Create `mobile/src/screens/Help/OnboardingChecklist.tsx`:
- Card shown on Dashboard top or under KPIs until progress = 100%.
- Steps map to deep links: **Connect channel** (ChannelsHome), **Publish link** (WidgetSnippetScreen), **Train FAQs** (Knowledge → TrainingCenter), **Set hours** (Automations → BusinessHours), **Test portal** (PortalPreview), **Create rule** (RuleBuilder).
- Each completion toggles a `done` flag locally; progress bar updates.
- Dismiss button hides card (can reopen via Help Center).

### Deliverables
- `OnboardingChecklist.tsx` + Dashboard integration

### Acceptance Checks
- Tapping CTAs navigates; returning marks step done; progress updates.

---

## STEP 5 — Tours Engine
**Goal:** Lightweight coachmark/spotlight engine that can run on any screen.

### Prompt Block
Create `mobile/src/screens/Help/TourRunner.tsx`:
- Accepts a `Tour` object; for each `TourStep`, finds `anchorTestId`, positions `Coachmark` + `Spotlight`.
- Controls: **Next**, **Back**, **Skip**, **Done**; persists completion in `HelpState.completedTours`.
- Provide default tours: **Dashboard**, **Conversations**, **Knowledge**, **Channels**, **Automations**, **Analytics**, **Portal**.
- Add a **Start tour** button in each module’s overflow menu.

### Deliverables
- `TourRunner.tsx` + default tours definitions (in `mobile/src/content/help/tours.ts`)

### Acceptance Checks
- Tours overlay anchors to elements by `testID`; completion persists and reflects in Help Center.

---

## STEP 6 — Contextual Help & Command Palette
**Goal:** One‑tap help for the current screen + global actions.

### Prompt Block
- Add a small **`?`** icon to key headers (Dashboard, Conversations, Knowledge, Channels, Automations, Analytics, Settings). Tapping opens Help Center pre‑filtered to that module’s tag.
- Integrate **CommandPalette** (long‑press or keyboard shortcut): search routes/actions (e.g., “Open Rule Builder”, “Open Portal Preview”), and top help articles.

### Deliverables
- Header help icons; Command palette overlay

### Acceptance Checks
- Palette opens from anywhere; selecting an item navigates; contextual help filters correctly.

---

## STEP 7 — Empty States & Embedded Guides
**Goal:** Make empty screens teach users.

### Prompt Block
Replace generic blanks with `EmptyStateGuide` in:
- **Conversations** (no data): “Publish your link to start receiving messages” → CTA to Channels.
- **Knowledge** (no sources): “Add your first source” → CTA to AddSource.
- **Automations** (no rules): “Create your first rule” → CTA to RuleBuilder.
- **Analytics** (not enough data): explain volume threshold and suggest timeframe.
- **Portal** (theme not published): “Publish a theme to see branding”.

### Deliverables
- Empty state guides across modules

### Acceptance Checks
- Guides render when lists are empty; CTAs navigate.

---

## STEP 8 — Release Notes & What’s New
**Goal:** Show versions and highlight changes.

### Prompt Block
Create `mobile/src/screens/Help/WhatsNew.tsx`:
- Reads local `ReleaseNote[]`; shows current version banner if unseen; **Dismiss** stores version in `HelpState.seenVersions`.
- Integrate with Settings → About and Help Center tab.

### Deliverables
- `WhatsNew.tsx` + banner hook

### Acceptance Checks
- Banner appears once per version; “View details” opens highlights; dismiss persists.

---

## STEP 9 — Micro‑Surveys
**Goal:** Lightweight CSAT/NPS/Likert surveys triggered by time/screen/action.

### Prompt Block
Create `SurveyManager.tsx`:
- Watches navigation events and timers; when trigger conditions match and cooldown elapsed, shows `SurveyModal` with the configured question.
- Store results locally and show a small thank‑you toast.
- Preconfigure surveys: **Day‑7 NPS**, **After first rule save CSAT**, **After portal test Likert**.

### Deliverables
- `SurveyManager.tsx` + integration into root app

### Acceptance Checks
- Surveys appear under the right conditions; cooldown prevents spam; results persist.

---

## STEP 10 — Feedback & Contact
**Goal:** Collect qualitative feedback quickly.

### Prompt Block
Create `FeedbackScreen.tsx`:
- `FeedbackForm` (rating + comment + contact). **Send** stores locally and appends to a mock “inbox” list.
- Add **Send feedback** entries from Help Center footer and Settings → About.

### Deliverables
- `FeedbackScreen.tsx`

### Acceptance Checks
- Submissions appear in local list; toasts show success.

---

## STEP 11 — Assistant‑Powered Help (UI‑only)
**Goal:** Bridge Help with the in‑app Assistant.

### Prompt Block
- From **ArticleViewer**, add **Ask Assistant about this** → opens Assistant overlay prefilled with the article title.
- From **OnboardingChecklist**, add **Generate step‑by‑step** → opens Assistant with a prebuilt prompt.
- From **TourRunner**, after completion → show chip **Practice with Assistant**.

### Deliverables
- Cross‑app hooks to Assistant

### Acceptance Checks
- Hooks open Assistant overlay with prefilled text and persona `agent`.

---

## STEP 12 — Localization, Accessibility, Offline
**Goal:** Make help accessible and resilient.

### Prompt Block
- Put all strings in `mobile/src/content/help/strings.ts` (keys by locale); wire to `ArticleViewer`, `Coachmark`, `ChecklistCard`.
- Ensure **screen reader** labels/roles for coachmarks and spotlights; offer **Skip tour** keyboard shortcut.
- Offline: cache last 10 articles and the quickstart checklist; show **Cached** badge when offline.

### Deliverables
- Localized strings; a11y improvements; offline cache stubs

### Acceptance Checks
- Changing locale swaps copy; screen readers announce step counts; offline badge appears.

---

## STEP 13 — Analytics (Meta‑Events)
**Goal:** Measure help effectiveness.

### Prompt Block
Using `lib/analytics.ts` instrument:
- `track('help.center.view')`, `track('help.search', { q })`, `track('help.article.view', { id })`
- `track('help.checklist.open')`, `track('help.checklist.step_complete', { id })`
- `track('help.tour.start', { id })`, `track('help.tour.complete', { id })`, `track('help.tour.skip', { id })`
- `track('help.whatsnew.view', { version })`
- `track('help.survey.submit', { id, score })`, `track('help.feedback.submit')`
- `track('help.command_palette.open')`, `track('help.command_palette.run', { cmd })`

### Deliverables
- Instrumentation calls

### Acceptance Checks
- Console logs events during interactions in dev.

---

## STEP 14 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow and polish.

### Prompt Block
- Verify: Help Center tabs, Quickstart checklist card on Dashboard, Tours engine across modules, Contextual help icons and Command palette, Empty‑state guides, Release notes banner, Micro‑surveys, Feedback screen, Assistant hooks, Localization/a11y/offline, Analytics.
- Create `mobile/docs/KNOWN_GAPS_Help.md` listing deferred items (remote CMS for docs, deep search, multimedia tutorials, survey/feedback backend, versioned tour configs, experimentation framework, role‑based checklists).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.

---

## Paste‑Ready Micro Prompts
- "Define help models (Article, Checklist, Tour, Nudge, ReleaseNote, Survey, Feedback)."
- "Build HelpButton, Coachmark, Spotlight, ChecklistCard, EmptyStateGuide, CommandPalette, SearchBar/Results, ArticleViewer, ReleaseNotes, FeedbackForm, SurveyModal primitives."
- "Create HelpCenter with tabs Search/Quickstart/Tours/What’s New and register in navigator."
- "Add OnboardingChecklist card to Dashboard with deep links and progress."
- "Create TourRunner and default tours; anchor by testIDs and persist completion."
- "Add contextual `?` header icons and a global CommandPalette overlay."
- "Replace empty screens with guided EmptyStateGuide CTAs across modules."
- "Create WhatsNew screen and one‑time banner; integrate with Settings → About."
- "Add SurveyManager with NPS/CSAT/Likert triggers and cooldowns."
- "Create FeedbackScreen with rating/comment/contact; wire from Help footer/Settings."
- "Hook Help to Assistant (Ask about this, Generate steps, Practice)."
- "Localize strings, add screen reader labels/roles, cache articles/checklists for offline."
- "Instrument help analytics; produce KNOWN_GAPS_Help.md."

---

## Done‑Definition (Help, Docs & Onboarding v0)
- Help Center is searchable and houses Quickstart, Tours, and What’s New.
- The Dashboard shows an actionable onboarding checklist until complete.
- Tours/coachmarks guide users through each module with accessible overlays.
- Contextual help and a command palette reduce friction.
- Empty states guide users to first success; release notes, micro‑surveys, and feedback capture learning loops.
- Assistant hooks, localization, offline caching, and analytics are implemented in UI‑only mode.

