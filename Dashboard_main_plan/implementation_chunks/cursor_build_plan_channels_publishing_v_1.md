# Cursor Build Plan — Channels & Publishing (Unique Link, Recipes, QR, Widget) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first (no backend), prop‑driven components

> Build the **Channels & Publishing** area: generate/preview a unique chat link, copy/regenerate, create QR codes, provide copy‑ready publishing recipes for WhatsApp/Instagram/Facebook, and a web widget snippet with framework notes. Follow each *Prompt Block* in order; after each step run and verify with *Acceptance Checks*.

---

## Assumptions
- Navigation exists; Dashboard/Conversations/CRM/Agents/Knowledge built per prior plans.
- UI primitives: `Box`, `Text`, `ui/tokens`; analytics `track()`; Offline/Sync Center stubs exist.
- Theming preview for the hosted portal exists under Settings (or we’ll deep link to a placeholder).

## Paths & Conventions
- **Screens:** `mobile/src/screens/Channels/*`
- **Components:** `mobile/src/components/channels/*`
- **Types:** `mobile/src/types/channels.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix with `chn-` (e.g., `chn-home`, `chn-copy-link`, `chn-qr`, `chn-widget-copy`)
- Spacing 4/8/12/16/24; touch ≥ 44×44dp; RTL ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define minimal models for channels, link, verification and recipes.

### Prompt Block
Create `mobile/src/types/channels.ts`:
```ts
export type SocialChannel = 'whatsapp'|'instagram'|'facebook'|'web';
export type VerifyState = 'unverified'|'verifying'|'verified'|'failed';

export interface PublishLink {
  id: string;                 // unique agent link id
  url: string;                // full URL
  shortUrl?: string;          // optional short form
  themeId?: string;           // link to theming preset
  createdAt: number;
  lastRotatedAt?: number;
  verify: { state: VerifyState; lastCheckedAt?: number; message?: string };
}

export interface ChannelStatus {
  channel: SocialChannel;
  connected: boolean;         // UI only (whether user completed recipe)
  lastVerifiedAt?: number;
  notes?: string;
}

export interface RecipeItem { id: string; channel: SocialChannel; title: string; steps: string[]; ctaLabel: string; }

export interface WidgetSnippet { framework: 'html'|'react'|'next'|'shopify'; code: string; }
```

### Deliverables
- `types/channels.ts`

### Acceptance Checks
- Types compile and export; no circular imports.
ok
---

## STEP 2 — Primitive Components
**Goal:** Create reusable pieces for link card, status badges, recipe cards, and QR.

### Prompt Block
In `mobile/src/components/channels/`, create:
- `LinkCard.tsx` (props: `{ link: PublishLink; onCopy:()=>void; onRotate:()=>void; onOpen:()=>void }`) — shows URL, short URL, verify badge, copy + open + rotate buttons.
- `VerifyBadge.tsx` (props: `{ state: VerifyState; message?: string }`)
- `ChannelCard.tsx` (props: `{ status: ChannelStatus; onOpenRecipe:()=>void }`) — shows channel icon, connected state, last verified.
- `RecipeCard.tsx` (props: `{ recipe: RecipeItem; onCopyTemplate:()=>void }`) — shows title & CTA; contains collapsible steps.
- `QrBlock.tsx` (props: `{ url: string; onSave:()=>void }`) — draws a simple QR placeholder (box grid) with Save button (stub).
- `WidgetSnippetBlock.tsx` (props: `{ snippet: WidgetSnippet; onCopy:()=>void }`) — code box + copy button.
- `ListSkeleton.tsx`, `EmptyState.tsx`
Provide `testID`s and use tokens.

### Deliverables
- 8 primitives exported

### Acceptance Checks
- Each renders with demo props; copy/rotate/save handlers `console.log` without crashing.
ok
---

## STEP 3 — Channels Home Screen
**Goal:** Hub for unique link, QR, channel cards, and recipes entry points.

### Prompt Block
Create `mobile/src/screens/Channels/ChannelsHome.tsx`:
- Header `Channels & Publishing` with overflow menu: **Theming**, **Widget Snippet**, **UTM Builder**, **Verification Logs**.
- **LinkCard** at top with the current `PublishLink` (demo in state); buttons: Copy, Open (use Linking), Rotate (confirm modal → updates id/url/shortUrl and `lastRotatedAt`).
- **QR section** with `QrBlock` rendering current link; Save triggers a toast (stub file creation).
- **Channel grid/list** of `ChannelCard` for WhatsApp, Instagram, Facebook, Web; tap → open `RecipeCenter` for that channel.
- **Status row** showing verification state and last check.
- Pull‑to‑refresh reshuffles demo states; `track('channels.view')` on mount.
Register as `Channels` in navigator.

### Deliverables
- `ChannelsHome.tsx`
- Navigator update

### Acceptance Checks
- Home renders; copy/open/rotate/QR save stubs work; channel cards navigate to recipes.
ok
---

## STEP 4 — Recipe Center (Per Channel)
**Goal:** Copy‑ready templates and steps.

### Prompt Block
Create `mobile/src/screens/Channels/RecipeCenter.tsx`:
- Accept param `{ channel: SocialChannel }`.
- For **whatsapp**: template auto‑reply text including the link; steps to configure WhatsApp Business autoresponder.
- For **instagram**: template for Bio + Link‑in‑bio; steps to edit profile and add link.
- For **facebook**: template for Page "About" and Pinned Post; steps to publish and pin.
- For **web**: mini CTA text and link button snippet.
- Render a list of `RecipeCard`s with `onCopyTemplate` buttons that place the template text into clipboard (stub `Clipboard` if needed).
- Add a **Mark as connected** toggle updating `ChannelStatus.connected` locally.

### Deliverables
- `RecipeCenter.tsx`

### Acceptance Checks
- Changing channel param switches templates; copy buttons log; connected toggle updates parent state when returning.
ok
---

## STEP 5 — Widget Snippet Screen
**Goal:** Provide embeddable widget snippets for different frameworks.

### Prompt Block
Create `mobile/src/screens/Channels/WidgetSnippetScreen.tsx`:
- Tabs for `HTML`, `React`, `Next.js`, `Shopify`.
- Each tab renders a `WidgetSnippetBlock` with demo `code` that includes the current link URL.
- **Copy** button logs and shows a toast.
- Info note: SPA hydration & defer script tips (UI text only).
Link from Channels overflow menu.

### Deliverables
- `WidgetSnippetScreen.tsx`

### Acceptance Checks
- Tabs switch; copy works; link URL appears inside code blocks.
ok
---

## STEP 6 — UTM Builder (Optional but Useful)
**Goal:** Build UTM parameters for the link and update QR.

### Prompt Block
Create `UtmBuilder.tsx`:
- Fields: `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`.
- Live preview of `link.url + ?utm_…` and a **Copy** button.
- Toggle **Shorten** (UI‑only) to display a fake `shortUrl` variant.
- Update `QrBlock` preview with the UTM’d URL.
Link from overflow menu.

### Deliverables
- `UtmBuilder.tsx`

### Acceptance Checks
- Editing fields updates preview; Copy logs and shows toast; QR preview uses new URL.
ok
---

## STEP 7 — Verification & Logs (UI‑Only)
**Goal:** Show verification attempts and status.

### Prompt Block
Create `VerificationLogs.tsx`:
- List of verify attempts (time, result, message).
- Button **Run verification** (simulated): sets `verify.state = 'verifying'` then flips to `'verified'` or `'failed'` after timeout; logs entry.
- Surface last checked time on ChannelsHome.
Link from overflow menu.

### Deliverables
- `VerificationLogs.tsx`

### Acceptance Checks
- Running a verification updates state and the log list; ChannelsHome badge updates.
ok
---

## STEP 8 — Theming Deep Link
**Goal:** Hook to theming preview for the hosted portal.

### Prompt Block
- Add a **Open Theming** action in ChannelsHome header that navigates to your theming screen (or a placeholder screen `ThemePreview.tsx` in Settings) with the current `themeId`.
- On returning, show any changed theme name/color chips in the LinkCard (UI only).

### Deliverables
- Navigation to theming; ThemePreview placeholder if missing

### Acceptance Checks
- Tapping Theming opens the preview; returning updates chips.
ok
---

## STEP 9 — Short Link & Rotation UX
**Goal:** Manage short link toggle and rotation confirm.

### Prompt Block
- In `LinkCard`, add a **Short link** toggle; when enabled, show and copy `shortUrl`; when disabled, hide.
- **Rotate Link**: show confirm modal explaining that old links will stop responding (UI text only) and update `id`, `url`, `shortUrl`, `lastRotatedAt` in local state.
- Show a subtle **broadcast reminder** banner to update bios/auto‑replies after rotation (dismissible).

### Deliverables
- LinkCard updated; rotation flow + reminder banner

### Acceptance Checks
- Toggle shows short link; rotation updates fields and shows reminder.
ok
---

## STEP 10 — Cross‑App Hooks
**Goal:** Make setup smoother from Dashboard.

### Prompt Block
- Ensure Dashboard **SetupProgress** step for "Publish link/widget" deep‑links to ChannelsHome.
- From **ChannelsHome**, add a button **Test in Portal** → opens an in‑app WebView (placeholder) to `link.url`.
- From **Knowledge** coverage view, offer **Publish new FAQs** → navigates to WidgetSnippetScreen.

### Deliverables
- Deep link wiring between modules

### Acceptance Checks
- Navigation works both ways; WebView opens without crash.
ok
---

## STEP 11 — Offline & Sync Stubs
**Goal:** Prepare link/channel edits for offline‑first.

### Prompt Block
- Reuse global **OfflineBanner** at top of Channels screens.
- Queue local mutations (rotate link, mark channel connected, update verify state); show small clock icons and clear after timeout.
- Add **Sync Center** button in Channels header.

### Deliverables
- Queued visuals; Sync Center access

### Acceptance Checks
- Offline toggle shows banner; edits show queued state then clear.
ok
---

## STEP 12 — Performance & Accessibility
**Goal:** Smooth scrolling and WCAG basics.

### Prompt Block
- Memoize `ChannelCard` and `RecipeCard`; stable keys.
- Debounce text inputs in UTM builder.
- Add `accessibilityLabel` for copy/rotate/open/save; ensure touch targets ≥44dp; contrast checked for verify badges.

### Deliverables
- Perf & a11y tweaks

### Acceptance Checks
- Scrolling stays smooth; screen readers can activate core controls.
ok
---

## STEP 13 — Analytics Stubs
**Goal:** Instrument key publishing events.

### Prompt Block
Using `lib/analytics.ts` instrument:
- `track('channels.view')` on ChannelsHome mount
- `track('channels.copy_link')`, `track('channels.rotate_link')`, `track('channels.qr_save')`
- `track('channels.recipe_copy', { channel })`, `track('channels.mark_connected', { channel, value })`
- `track('channels.widget_copy', { framework })`
- `track('channels.utm_copy')`
- `track('channels.verify', { result })`

### Deliverables
- Analytics calls added

### Acceptance Checks
- Console logs events during interactions in dev.
ok
---

## STEP 14 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow and smooth setup.

### Prompt Block
- Verify: LinkCard (copy/open/short/rotate), QR save, Channel cards + RecipeCenter, Widget snippets, UTM builder, Verification logs, Theming deep link, cross‑app hooks, offline/queue, perf/a11y, analytics.
- Create `mobile/docs/KNOWN_GAPS_Channels.md` listing deferred items (real QR PNG export, real shortener, channel verification APIs, WebView portal auth, widget package, multi‑domain whitelists, rate limit on rotation, etc.).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.
ok
---

## Paste‑Ready Micro Prompts
- "Define Channels TypeScript models for link, verify, channel status, recipes, widget snippets."
- "Build LinkCard, VerifyBadge, ChannelCard, RecipeCard, QrBlock, WidgetSnippetBlock primitives."
- "Create ChannelsHome with LinkCard, QR, channel grid, status row; register in navigator."
- "Implement RecipeCenter for WhatsApp/Instagram/Facebook/Web with copyable templates and connected toggle."
- "Create WidgetSnippetScreen with tabs and copy‑to‑clipboard for code blocks."
- "Add UTM Builder screen with live preview and QR update."
- "Create VerificationLogs with run verification action and result list."
- "Wire Theming deep link from Channels header and reflect updates in LinkCard."
- "Implement short link toggle and rotate flow with reminder banner."
- "Add cross‑app deep links (Dashboard SetupProgress ↔ Channels, Knowledge ↔ Widget)."
- "Queue link/channel edits when offline and add Sync Center access."
- "Memoize heavy cards, debounce inputs, add a11y labels and ≥44dp hit areas."
- "Instrument analytics events for copy/rotate/qr/recipe/widget/utm/verify."
- "Run final audit and create KNOWN_GAPS_Channels.md."

---

## Done‑Definition (Channels v0)
- ChannelsHome presents the link, QR, channel cards, and status clearly.
- Recipes are copy‑ready; marking channels connected updates local state.
- Widget snippets include the live link; UTM builder modifies preview and QR.
- Verification logs and manual verify work visually; theming deep link opens.
- Offline banner and queued edits visible; Sync Center accessible.
- Accessibility labels present; controls are reachable; perf is smooth.
- Analytics stubs fire for key interactions.
