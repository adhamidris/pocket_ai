# Cursor Build Plan — Hosted Chat Portal (Branded Customer UI) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Customer‑facing portal preview inside the mobile app (React Native) + embeddable widget snippet handed off by Channels
**Mode:** UI‑first only (no backend), demo state & stubs, themeable

> Build the **Hosted Chat Portal** as a fully branded, accessible, and customizable chat UI. Deliver it as: (1) an in‑app **Portal Preview** screen (so you can iterate without backend), and (2) UI contracts that match the web widget snippet from **Channels & Publishing**. Follow each *Prompt Block* in order; after each step, run and verify using *Acceptance Checks*.

---

## Assumptions
- Settings & Theming provides `ThemeTokens` and a published `themeId`.
- Channels & Publishing exposes a unique link and widget snippet screen.
- Security Center may enable **Hide PII** and **Anonymize analytics** toggles that should reflect here.
- Voice is **optional**; we’ll add UI controls but keep them mocked.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Portal/*`
- **Components:** `mobile/src/components/portal/*`
- **Types:** `mobile/src/types/portal.ts`
- **Testing IDs:** prefix `portal-` (e.g., `portal-preview`, `portal-msg-<id>`, `portal-send`, `portal-quick-<idx>`, `portal-csat-submit`)
- Spacing 4/8/12/16/24; touch ≥44×44dp; RTL‑ready; screen reader labels on all actionables.

---

## STEP 1 — Types & UI Contracts
**Goal:** Define message, session, composer, and consent models the portal will use.

### Prompt Block
Create `mobile/src/types/portal.ts`:
```ts
export type Sender = 'customer'|'ai'|'agent'|'system';
export type PartKind = 'text'|'image'|'file'|'card'|'buttons'|'list'|'divider'|'notice';

export interface MsgPartText { kind:'text'; text:string; markdown?:boolean }
export interface MsgPartImage { kind:'image'; url:string; alt?:string }
export interface MsgPartFile { kind:'file'; name:string; sizeKB:number; mime:string; url?:string }
export interface MsgPartButtons { kind:'buttons'; actions:{ id:string; label:string; style?:'primary'|'secondary'|'link' }[] }
export interface MsgPartCard { kind:'card'; title:string; subtitle?:string; body?:string; mediaUrl?:string; actions?:MsgPartButtons['actions'] }
export interface MsgPartList { kind:'list'; items:{ id:string; title:string; subtitle?:string; rightLabel?:string }[] }
export interface MsgPartDivider { kind:'divider'; label?:string }
export interface MsgPartNotice { kind:'notice'; tone:'info'|'warn'|'success'|'error'; text:string }
export type MsgPart = MsgPartText|MsgPartImage|MsgPartFile|MsgPartButtons|MsgPartCard|MsgPartList|MsgPartDivider|MsgPartNotice;

export interface ChatMessage { id:string; at:number; sender:Sender; parts:MsgPart[]; meta?:{ delivered?:boolean; read?:boolean } }

export interface ComposerState { text:string; attachments:MsgPartFile[]; disabled?:boolean; placeholder?:string }

export type SessionState = 'connecting'|'connected'|'queued'|'offline'|'ended';
export interface QueueInfo { position:number; etaMins?:number }

export interface PrechatField { key:string; label:string; type:'text'|'email'|'phone'|'select'; required?:boolean; options?:string[] }
export interface PrechatForm { title:string; fields:PrechatField[] }

export type ConsentState = 'unknown'|'granted'|'denied';
export interface PortalPrefs { themeId?:string; locale:string; timezone:string; allowUploads:boolean; allowVoice:boolean; showQuickReplies:boolean; }

export interface CsatForm { rating?:1|2|3|4|5; comment?:string; }
```

### Deliverables
- `types/portal.ts`

### Acceptance Checks
- Types compile and export cleanly.
ok
---

## STEP 2 — Primitive Components
**Goal:** Build message, composer, and chrome primitives.

### Prompt Block
In `mobile/src/components/portal/`, create:
- `PortalHeader.tsx` — logo/avatar, business name, status dot (online/away/offline), theme colors.
- `PortalFooter.tsx` — brand note (logo, small text), privacy link button.
- `Bubble.tsx` — renders `ChatMessage` with variants (customer/ai/agent/system); supports markdown text, images, files, cards, lists, buttons; shows time + delivered/read ticks.
- `QuickReplyChip.tsx` — pill button for suggested replies.
- `ActionBar.tsx` — suggested action buttons (structured actions from AI).
- `Composer.tsx` — text input, send button, attach button, mic button (if voice allowed), character counter; disabled state.
- `AttachmentPreview.tsx` — row of files/images with remove buttons.
- `TypingIndicator.tsx` — animated dots; aria live.
- `SystemNotice.tsx` — for queue positions, privacy, or end‑of‑chat.
- `CsatPanel.tsx` — 1–5 stars + comment box + submit.
- `TranscriptForm.tsx` — email field + send.
- `ConsentBanner.tsx` — text with **Accept / Decline**.
- `OfflineBadge.tsx` and `RateLimitBadge.tsx`.
- `ListSkeleton.tsx`, `EmptyState.tsx`.

### Deliverables
- 15 primitives exported

### Acceptance Checks
- Each primitive renders with demo props; minimum hit area 44dp; color tokens from theme.
ok
---

## STEP 3 — Portal Preview Shell
**Goal:** A working preview with fake conversation and theming.

### Prompt Block
Create `mobile/src/screens/Portal/PortalPreview.tsx`:
- Header `Portal Preview` with overflow: **Theme**, **Pre‑chat**, **Consent**, **Voice**, **Uploads** toggles.
- **PortalHeader** + scrollable message list (virtualized) + **Composer**.
- Seed with a demo transcript: greeting (system), AI intro card, customer question, AI answer with quick replies, action card (e.g., "Check order status"), image sample, file sample.
- Mock **TypingIndicator** for AI during 1–2s after send.
- Apply theme from Settings; when **Hide PII** is on (Security), mask email/phone in bubbles.

### Deliverables
- `PortalPreview.tsx` registered as `Portal` in navigator

### Acceptance Checks
- Preview responds to toggles; sending a message appends a customer bubble and then AI reply after a timeout.
ok
---

## STEP 4 — Pre‑Chat Form & Consent
**Goal:** Collect basic identity and consent before chat starts (UI only).

### Prompt Block
Create `PrechatScreen.tsx`:
- Render `PrechatForm` with common fields (Name*, Email, Topic select) using inputs with validation.
- Below form, render `ConsentBanner` with configurable text; require **Accept** to enable **Start Chat**.
- On **Start Chat**, navigate to `PortalPreview` and show a system notice "Session started for <name>".
- Persist pre‑chat values locally for use in header subtitle (e.g., "Hi, Karim!").

### Deliverables
- `PrechatScreen.tsx`

### Acceptance Checks
- Required fields validated; Accept enables Start; values appear in preview.
ok
---

## STEP 5 — Quick Replies & Action Suggestions
**Goal:** Provide structured suggestions that drive the conversation.

### Prompt Block
- Create `QuickRepliesBar.tsx` that shows up to 6 `QuickReplyChip`s; tapping inserts text and sends.
- Create `ActionSuggestions.tsx` that shows 1–3 primary actions (e.g., "Track order", "Book appointment"). Tapping pushes a system notice and a structured `MsgPartCard`.
- Add toggles in `PortalPreview` overflow: **Show quick replies**, **Show action suggestions**.

### Deliverables
- `QuickRepliesBar.tsx`, `ActionSuggestions.tsx` + wiring

### Acceptance Checks
- Tapping chips sends messages; actions add cards; composer stays focused.
ok
---

## STEP 6 — Attachments (UI‑only)
**Goal:** Upload/attach UI with progress.

### Prompt Block
Create `AttachmentFlow.tsx`:
- Fake picker → add `MsgPartFile` items to `ComposerState.attachments`.
- Show previews via `AttachmentPreview` with remove buttons.
- On send, show uploading progress bars on corresponding bubbles; mark as completed.
- Validate: size limit (e.g., 10MB) and type restrictions (images/pdf/doc); show error notice if invalid.

### Deliverables
- `AttachmentFlow.tsx` + integration into `Composer`

### Acceptance Checks
- Adding/removing attachments works; progress animates; validation errors display.
ok
---

## STEP 7 — Voice Controls (Mocked)
**Goal:** Push‑to‑talk capture and TTS playback UI (no audio backend).

### Prompt Block
Create `VoiceBar.tsx`:
- **Mic** press‑and‑hold button; on hold start, show recording timer and animated level bars; on release, insert a transcribed stub into composer and send.
- **TTS** play controls on AI bubbles (small play/stop icon) with a progress indicator.
- Permission sheet UI (mock) if microphone is off.
- Toggle **Enable voice** in overflow; when off, hide mic/tts.

### Deliverables
- `VoiceBar.tsx` + hooks in `Composer` & `Bubble`

### Acceptance Checks
- Holding mic simulates recording; releasing sends text; TTS icons animate per bubble.
ok
---

## STEP 8 — Queueing & Handoff (HITL)
**Goal:** Visualize queue position and request human agent.

### Prompt Block
Create `HandoffBar.tsx`:
- When **Request human** is tapped, insert system notice and set `SessionState='queued'` with `QueueInfo` (position N, ETA). Show **Cancel request** while queued.
- Add **End chat** button that moves state to `ended`, disables composer, and opens **CsatPanel**.
- If **connected** to human (simulated), show agent avatar/name in header and change bubble variant for future replies.

### Deliverables
- `HandoffBar.tsx` + wiring into `PortalPreview`

### Acceptance Checks
- Request/Cancel updates state and shows queue; End chat triggers CSAT.
ok
---

## STEP 9 — CSAT & Transcript
**Goal:** Capture satisfaction and optionally send transcript.

### Prompt Block
- When session `ended`, render `CsatPanel` inline; submit emits a small success card and disables it.
- Add `TranscriptForm` to collect email for transcript; show **Send** toast.
- Add a **Restart chat** button to reset preview state.

### Deliverables
- CSAT & Transcript flow integrated

### Acceptance Checks
- Submitting CSAT stores rating locally; email validates before enabling Send.
ok
---

## STEP 10 — Theming & Accessibility
**Goal:** Full theme application + WCAG basics.

### Prompt Block
- Create `useThemeTokens(themeId)` hook to fetch tokens from Settings and apply to Portal primitives.
- Respect **avatar shape**, **bubble radius**, **colors**, **font**.
- Add **High contrast mode** toggle in overflow (UI only) that boosts contrasts.
- Add a11y labels for bubbles (e.g., "AI says: …"), mic button, send, attachments; support **RTL mirroring**.

### Deliverables
- Theme hook; applied tokens; a11y improvements

### Acceptance Checks
- Theme changes reflect instantly; screen reader announces roles; RTL flips layout.
ok
---

## STEP 11 — Rate Limits & Abuse UX (UI‑only)
**Goal:** Friendly constraints when users send too fast or too large.

### Prompt Block
- Add `RateLimitBadge` that appears if >X msgs/min; composer shows cooldown countdown.
- Large attachment warnings; profanity notice (simple keyword list) as `MsgPartNotice`.

### Deliverables
- Rate limit visuals + notices

### Acceptance Checks
- Rapid taps trigger cooldown; notices appear and disappear correctly.
ok
---

## STEP 12 — Analytics Stubs (Portal)
**Goal:** Instrument the portal preview interactions.

### Prompt Block
Using `lib/analytics.ts` add:
- `track('portal.open')`, `track('portal.prechat.submit')`, `track('portal.consent', { state })`
- `track('portal.message.send', { sender })`, `track('portal.quick_reply')`, `track('portal.action_click')`
- `track('portal.attachment.add')`, `track('portal.attachment.fail')`
- `track('portal.voice.record')`, `track('portal.voice.tts')`
- `track('portal.handoff.request')`, `track('portal.queue.cancel')`, `track('portal.end')`
- `track('portal.csat.submit', { rating })`, `track('portal.transcript.send')`

### Deliverables
- Instrumentation calls wired into preview

### Acceptance Checks
- Console logs expected events during interactions.
ok
---

## STEP 13 — Cross‑App Hooks
**Goal:** Make the portal tie into Channels/Settings/Security.

### Prompt Block
- From **ChannelsHome** → **Test in Portal** opens `PortalPreview`.
- From **WidgetSnippetScreen** → **Open Portal Preview** (passes current link as context).
- From **Settings → Branding/Theming** → **Preview in Portal**.
- From **Security → Privacy Modes** → toggle **Hide PII** and see effect here.

### Deliverables
- Navigation hooks between modules

### Acceptance Checks
- Deep links carry context (themeId, link) and reflect immediately on preview.
ok
---

## STEP 14 — Offline & Error States
**Goal:** Show graceful degradation.

### Prompt Block
- Add **OfflineBadge** when global offline; disable send; show retry banner for failed sends (UI only).
- Add **Reconnecting…** system notice when toggling offline→online.

### Deliverables
- Offline & error visuals integrated

### Acceptance Checks
- Toggling offline shows badges and disables composer; returning online clears.
ok
---

## STEP 15 — Performance & Virtualization
**Goal:** Keep long chats smooth.

### Prompt Block
- Use `FlashList`/`VirtualizedList`; keyExtractor by `id`.
- Memoize `Bubble` and heavy components; window size tuned.
- Lazy‑load image previews and throttle layout.

### Deliverables
- Perf tweaks in list + memoization

### Acceptance Checks
- 60fps scroll with 200+ demo messages; no layout thrash.
ok
---

## STEP 16 — Definitions & Copy Bank
**Goal:** Consistent copy and fallback texts.

### Prompt Block
Create `mobile/src/content/portalCopy.ts` with strings for system notices, consent text, queue messages, and error hints. Localize in a simple object keyed by locale.

### Deliverables
- `portalCopy.ts` and usage

### Acceptance Checks
- Changing locale in Settings swaps copy strings.
ok
---

## STEP 17 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow for Portal.

### Prompt Block
- Verify: Pre‑chat + Consent, Themed Header/Footer, Message list with bubbles/cards/lists/files, Quick replies + Action suggestions, Composer + Attachments, Voice mocks, Queue/Handoff, CSAT + Transcript, Rate‑limit & notices, Offline, A11y/RTL, Perf, Analytics, Cross‑app hooks.
- Create `mobile/docs/KNOWN_GAPS_Portal.md` listing deferred items (real transport, recording upload, streaming partials, emoji picker, file virus scan, voice diarization, true TTS, real queue depth, push channel capture, web widget parity tests).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; KNOWN_GAPS doc created.

---

## Paste‑Ready Micro Prompts
- "Define portal types for messages, parts, composer, session, prechat, consent, prefs, CSAT."
- "Build portal primitives: Header, Footer, Bubble (text/image/file/card/buttons/list), QuickReplyChip, ActionBar, Composer, AttachmentPreview, TypingIndicator, SystemNotice, CsatPanel, TranscriptForm, ConsentBanner, Offline/RateLimit badges."
- "Create PortalPreview screen with demo transcript, theme application, and toggles for quick replies, actions, voice, uploads."
- "Implement PrechatScreen with validation and consent accept gating Start Chat."
- "Add QuickRepliesBar and ActionSuggestions and wire into send flow."
- "Implement AttachmentFlow with previews, size/type validation, and progress."
- "Add VoiceBar with push‑to‑talk mock and TTS play controls on AI bubbles."
- "Add HandoffBar with Request Human, queue state, End chat → CSAT."
- "Integrate CSAT + Transcript email form and Restart chat."
- "Apply theme tokens and high contrast toggle; add a11y labels and RTL support."
- "Add RateLimitBadge and cooldown UX; profanity notice via SystemNotice."
- "Instrument portal analytics events for open, prechat, consent, send, quick reply, action, attachments, voice, handoff, end, csat, transcript."
- "Wire cross‑app deep links from Channels, Settings, and Security."
- "Implement offline & reconnect notices and disable composer appropriately."
- "Virtualize messages and memoize bubbles for long transcripts."
- "Create portalCopy.ts and swap strings by locale."
- "Run final audit and produce KNOWN_GAPS_Portal.md."

---

## Done‑Definition (Hosted Chat Portal v0)
- The in‑app Portal Preview functions end‑to‑end in demo mode.
- Pre‑chat + consent gate the session; theme tokens apply everywhere; bubbles render all part kinds.
- Quick replies/actions accelerate flows; attachments and voice controls show realistic UI.
- Handoff/queue and CSAT/transcript flows work; offline and rate‑limit behaviors are visible.
- Accessibility and RTL supported; performance is smooth on large transcripts.
- Analytics events fire; cross‑app deep links from Channels/Settings/Security work.

