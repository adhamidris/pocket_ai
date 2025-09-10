# Cursor Build Plan — Assistant & Voice (Dashboard Q&A, Global Command, TTS/Mic) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first only (no backend), local demo state

> Build a **productivity Assistant** that users can ask things like “What happened today?” and issue UI‑only commands (create rules, open filtered views) plus **voice** capture/playback controls. Integrate a compact **Dashboard Ask** panel and a global assistant overlay. Everything runs with mocked data and prefilled deep links—no network calls.

---

## Assumptions
- Dashboard, Analytics, Conversations, Knowledge, Automations, Channels, Settings, Security exist.
- Settings has **voice enable** toggles; Security may enable **Hide PII** (mask sources/citations).
- Theme tokens available from Settings.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Assistant/*`
- **Components:** `mobile/src/components/assistant/*`
- **Types:** `mobile/src/types/assistant.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix `asst-` (e.g., `asst-ask-input`, `asst-answer-0`, `asst-mic`, `asst-tts`, `asst-cmdpill-0`)
- Spacing 4/8/12/16/24; ≥44dp targets; RTL ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define models for prompts, answers, tool suggestions, citations, voice state, personas, and shortcuts.

### Prompt Block
Create `mobile/src/types/assistant.ts`:
```ts
export type Persona = 'ops'|'owner'|'agent'|'analyst';
export type Tone = 'concise'|'neutral'|'friendly';

export interface AskQuery { id:string; text:string; createdAt:number; persona:Persona; tone:Tone; context?:{ range?:{startIso:string; endIso:string}; filters?:Record<string,any> } }

export interface Citation { id:string; label:string; kind:'conversation'|'intent'|'metric'|'doc'|'policy'; refId?:string; url?:string; masked?:boolean }

export type AnswerChunkKind = 'paragraph'|'kpi'|'list'|'table'|'callout'|'chart'|'code';
export interface AnswerChunk { kind:AnswerChunkKind; text?:string; items?:string[]; kpi?:{ label:string; value:string; delta?:string }; chartKey?:string; code?:string; citations?:Citation[] }

export interface AskAnswer { id:string; queryId:string; createdAt:number; chunks:AnswerChunk[]; followUps:string[]; toolSuggestions:ToolSuggestion[]; safety?:{ piiMasked:boolean; disclaimer?:string } }

export type ToolKey = 'open_conversations'|'open_rule_builder'|'open_analytics'|'open_channels'|'open_agent'|'open_knowledge'|'open_billing'|'open_security'|'create_note'|'create_test'|'start_training'|'open_hours'|'open_sla'|'open_portal_preview';
export interface ToolSuggestion { key:ToolKey; label:string; params?:Record<string,any> }

export interface VoiceState { enabled:boolean; recording:boolean; durationMs:number; transcript?:string }

export interface PromptTemplate { id:string; name:string; text:string; persona?:Persona; tone?:Tone; pinned?:boolean }

export interface MemoryPin { id:string; title:string; content:string; createdAt:number; tags?:string[] }

export interface Shortcut { id:string; label:string; icon?:string; action:ToolSuggestion }
```

### Deliverables
- `types/assistant.ts`

### Acceptance Checks
- Types compile; no circular deps.

---

## STEP 2 — Primitive Components
**Goal:** Build the Assistant UI primitives used across panel/overlay.

### Prompt Block
In `mobile/src/components/assistant/`, create:
- `AskInput.tsx` — multiline input with send button, persona switcher, tone picker, and range chip; testID `asst-ask-input`.
- `AnswerCard.tsx` — renders `AnswerChunk`s (paragraph/list/kpi/callout/table placeholder/chart placeholder) with inline `CitationChip`s.
- `CitationChip.tsx` — pill that opens a bottom sheet listing sources; respects **Hide PII**.
- `ToolSuggestionRow.tsx` — pill buttons for `ToolSuggestion[]` with icons.
- `FollowUpChips.tsx` — chips for suggested follow‑ups.
- `MicButton.tsx` — press‑and‑hold record; shows duration.
- `Waveform.tsx` — simple animated bars while recording.
- `TtsButton.tsx` — play/stop for answer blocks.
- `PersonaSwitcher.tsx`, `TonePicker.tsx`.
- `ShortcutGrid.tsx` — grid of `Shortcut`s (open Conversations with filters, open Analytics trend, etc.).
- `ResultSkeleton.tsx`, `EmptyState.tsx`, `SafetyBadge.tsx`.

### Deliverables
- 12 primitives exported

### Acceptance Checks
- All render with demo props; ≥44dp; a11y labels on send/mic/tts.

---

## STEP 3 — Dashboard Ask Panel
**Goal:** Compact assistant panel embedded on Dashboard.

### Prompt Block
Create `mobile/src/screens/Assistant/DashboardAskPanel.tsx`:
- Collapsible card titled **“Ask the Assistant”** with `AskInput`, recent queries carousel, and a small `ShortcutGrid` (e.g., "Today’s summary", "Breaches", "Top intents", "VIP queue").
- On send: push a mocked `AskAnswer` with chunks: KPI row (FRT/Resolution), paragraph summary, list of incidents, tool suggestions (Open Conversations filtered at ts, Open SLA editor).
- Integrate with Dashboard screen below KPI tiles; hide if user dismisses.

### Deliverables
- `DashboardAskPanel.tsx` + integration into Dashboard

### Acceptance Checks
- Sending a query renders an answer with tool suggestions; shortcuts prefill the input and send.

---

## STEP 4 — Global Assistant Overlay
**Goal:** Full‑screen assistant summoned from anywhere.

### Prompt Block
Create `AssistantOverlay.tsx`:
- Opens from a floating FAB or gesture; full‑screen with `AskInput` at bottom, scrollable `AnswerCard` list, `FollowUpChips`, `ToolSuggestionRow`.
- Header: persona/tone selectors, **Recent**, **Pins**, **Templates** tabs.
- State persists last 10 queries across app session (local store).

Add a floating **Assistant FAB** component into the root navigator.

### Deliverables
- `AssistantOverlay.tsx` + FAB wiring

### Acceptance Checks
- FAB opens overlay over any screen; recent answers persist; follow‑ups append new answers.

---

## STEP 5 — Tool Suggestions → Deep Links
**Goal:** Make answers actionable with one‑tap deep links (UI only).

### Prompt Block
Implement a lightweight **router helper** `assistant/openToolSuggestion.ts` that takes a `ToolSuggestion` and `navigate()` to the appropriate screen with prefilled params:
- `open_conversations` → ConversationsList with `{ atTs, filters }`
- `open_rule_builder` → Automations RuleBuilder with WHEN preset
- `open_analytics` → TrendsScreen tab and grain
- `open_channels` → Channels RecipeCenter for a channel
- `open_agent` → AgentDetail for a name/id
- `open_knowledge` → FailureLog or TrainingCenter
- `open_hours`/`open_sla` → respective editors
- `open_portal_preview` → PortalPreview

Wire `ToolSuggestionRow` buttons to this helper.

### Deliverables
- Helper + wiring

### Acceptance Checks
- Tapping a suggestion navigates to the expected screen with params visible.

---

## STEP 6 — Voice (Mic & TTS) UI
**Goal:** Capture voice to text and play back answers (mocked).

### Prompt Block
- Add `MicButton` + `Waveform` to `AskInput` when voice is enabled in Settings.
- On hold start: set `VoiceState.recording=true` and animate; on release: insert a stub transcript into input and auto‑send.
- Add `TtsButton` to `AnswerCard` blocks—play animates a progress bar; stop resets.
- Add a **Voice Settings** sheet: input device, push‑to‑talk vs tap‑to‑toggle, autoplay TTS for long answers (checkboxes only).

### Deliverables
- Mic/TTS wired; Voice settings sheet

### Acceptance Checks
- Holding mic simulates capture; TTS controls appear per answer and animate.

---

## STEP 7 — Prompt Templates & Pins
**Goal:** Reusable prompts and saved answers.

### Prompt Block
Create `PromptLibrary.tsx`:
- Manage `PromptTemplate[]` with examples: "Daily ops brief (last 24h)", "Why did SLA breach?", "Which intents to automate?".
- Actions: **Use**, **Pin to Dashboard**, **Edit**, **Duplicate**.

Create `PinsScreen.tsx`:
- Saves `MemoryPin[]` from any answer block (long‑press → **Pin**).
- Lists pins with quick open; delete via swipe.

Integrate **Templates** and **Pins** tabs into `AssistantOverlay` header.

### Deliverables
- `PromptLibrary.tsx`, `PinsScreen.tsx` + overlay tabs

### Acceptance Checks
- Using a template fills and sends; pins appear and open from overlay.

---

## STEP 8 — Persona & Tone Controls
**Goal:** Adjust style and focus of answers (UI only).

### Prompt Block
- `PersonaSwitcher` options: **Ops** (alerts & queues), **Owner** (KPIs & revenue), **Agent** (how‑to & playbooks), **Analyst** (charts & cohorts).
- `TonePicker`: Concise / Neutral / Friendly.
- Affect **answer layout order** and **follow‑up suggestions** (use different canned chunks per persona).

### Deliverables
- Persona & tone influencing answer templates

### Acceptance Checks
- Switching persona changes chunk order; tone affects copy length.

---

## STEP 9 — Safety & Privacy UX
**Goal:** Respect masking and show disclaimers.

### Prompt Block
- Add `SafetyBadge` when **Hide PII** is active (from Security). Mask emails/phones in `CitationChip` and conversation IDs.
- Add a small disclaimer footer on long analytic answers: "This is a preview; verify before acting." (UI text).

### Deliverables
- Masking & disclaimers wired

### Acceptance Checks
- Toggling Hide PII flips masking on citations within answers.

---

## STEP 10 — Daily Brief & Notifications (UI‑only)
**Goal:** One‑tap daily summary and mock schedule.

### Prompt Block
Create `DailyBriefScreen.tsx`:
- Prebuilt answer: KPIs vs yesterday, breaches, top intents, staffing hint, and recommended rules to try.
- Button **Share** (stub), **Schedule** (stub cron presets) to simulate morning digests.
- Quick CTA: **Open Analytics** → TrendsScreen day grain.

Add a **“Daily Brief”** shortcut to DashboardAskPanel and AssistantOverlay shortcuts.

### Deliverables
- `DailyBriefScreen.tsx` + shortcuts

### Acceptance Checks
- Screen renders with canned content; Schedule stores local flag; Share shows toast.

---

## STEP 11 — Cross‑App Hooks
**Goal:** Make Assistant first‑class across modules.

### Prompt Block
- From **Analytics** charts → **Ask why** opens overlay prefilled with context.
- From **Conversations** list empty state → prompt suggests search queries.
- From **Automations** save → show follow‑up chip: "Simulate this rule" opening simulator.
- From **Knowledge FailureLog** → chip: "Draft reply from FAQ" sends prefilled prompt.

### Deliverables
- Navigation hooks added

### Acceptance Checks
- Each hook opens Assistant with prefilled text and persona.

---

## STEP 12 — Offline & Rate‑Limit
**Goal:** Graceful states when offline or spammy.

### Prompt Block
- Show **Offline** banner in overlay; disable send; queue request chips visually.
- Add simple rate limit: if >3 sends in 10s, show cooldown badge and disable for 5s.

### Deliverables
- Offline & cooldown visuals

### Acceptance Checks
- Cooldown triggers and resolves; queued chips clear after timeout.

---

## STEP 13 — Accessibility & Performance
**Goal:** Meet WCAG basics and stay smooth.

### Prompt Block
- Announce new answers via `accessibilityLiveRegion`.
- Provide **Skip to input** and **Skip to newest answer** buttons for screen readers.
- Virtualize answer list; memoize `AnswerCard`; throttle mic waveform.

### Deliverables
- A11y & perf work

### Acceptance Checks
- Screen reader flow works; 60fps scroll with many answers.

---

## STEP 14 — Analytics (Meta‑events)
**Goal:** Instrument assistant usage.

### Prompt Block
Using `lib/analytics.ts`:
- `track('assistant.open', { surface:'dashboard'|'overlay' })`
- `track('assistant.ask', { persona, tone })`
- `track('assistant.voice', { action:'record'|'tts' })`
- `track('assistant.tool', { key })`
- `track('assistant.template.use', { id })`, `track('assistant.pin.add')`
- `track('assistant.cooldown')`, `track('assistant.offline_send')`

### Deliverables
- Instrumentation calls

### Acceptance Checks
- Console logs events during interactions in dev.

---

## STEP 15 — Final Review & Gaps
**Goal:** Verify parity with BusinessFlow and polish.

### Prompt Block
- Walkthrough: DashboardAskPanel → Overlay → Tool suggestions deep links → Voice mic/TTS → Templates & Pins → Persona/Tone → Safety masking → Daily Brief → Cross‑app hooks → Offline/Rate‑limit → A11y/Perf → Analytics.
- Create `mobile/docs/KNOWN_GAPS_Assistant.md` listing deferred items (real LLM calls, streaming partials, RAG citations, voice SDK integration, session memory, multilingual NLU, function execution safety, hallucination detection, confidence UI, feedback thumbs).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.

---

## Paste‑Ready Micro Prompts
- "Define assistant models (AskQuery, AskAnswer, AnswerChunk, Citation, ToolSuggestion, VoiceState, PromptTemplate, MemoryPin, Shortcut)."
- "Build primitives: AskInput, AnswerCard, CitationChip, ToolSuggestionRow, FollowUpChips, MicButton, Waveform, TtsButton, PersonaSwitcher, TonePicker, ShortcutGrid, ResultSkeleton."
- "Create DashboardAskPanel and integrate into Dashboard beneath KPI tiles."
- "Create AssistantOverlay with FAB summon, Recent/Pins/Templates tabs, and persistent session state."
- "Wire ToolSuggestion buttons to navigation helper to open target screens with params."
- "Add Mic & TTS controls with mocked behavior and a Voice Settings sheet."
- "Create PromptLibrary and Pins screens; integrate with overlay tabs."
- "Implement persona/tone switches that alter answer chunk ordering and copy length."
- "Add SafetyBadge and PII masking for citations when Hide PII is on."
- "Create DailyBriefScreen with share & schedule stubs and shortcut entries."
- "Add cross‑app hooks from Analytics, Conversations, Automations, Knowledge to prefill Assistant queries."
- "Implement offline banner and send cooldown logic."
- "Add a11y live announcements and skip links; virtualize answer list and throttle waveform."
- "Instrument assistant meta‑events; produce KNOWN_GAPS_Assistant.md."

---

## Done‑Definition (Assistant & Voice v0)
- Assistant surfaces (Dashboard panel + global overlay) work in demo mode, with voice controls mocked and deep links actionable.
- Answers render with KPIs, lists, and citations; follow‑ups and templates accelerate iteration; pins save useful snippets.
- Persona/tone adjust layout and copy; masking and disclaimers appear; Daily Brief exists.
- Offline and cooldown states are handled; a11y/perf are solid; analytics events fire.

