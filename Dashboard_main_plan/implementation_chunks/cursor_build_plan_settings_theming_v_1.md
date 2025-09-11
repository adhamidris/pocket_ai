# Cursor Build Plan — Settings & Theming (Branding, Profile, Locale, Notifications) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first, prop‑driven (no backend)

> Build **Settings & Theming** for the hosted chat portal and app: business profile, branding (logo, colors, bubbles), theme preview & publish, locale/timezone, notifications, and small developer shells (API Keys/Webhooks link). Work step‑by‑step; after each *Prompt Block*, run and verify the *Acceptance Checks*.

---

## Assumptions
- Dashboard/Conversations/CRM/Agents/Knowledge/Channels/Automations/Analytics exist; `track()` util available; Offline/Sync Center stubs exist.
- Channels module expects a `themeId` from Settings.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Settings/*`
- **Components:** `mobile/src/components/settings/*`
- **Types:** `mobile/src/types/settings.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix `set-` (e.g., `set-home`, `set-logo-upload`, `set-color-primary`, `set-theme-publish`)
- Spacing 4/8/12/16/24; touch ≥ 44×44dp; RTL ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define models for business profile, theme tokens, and notifications.

### Prompt Block
Create `mobile/src/types/settings.ts`:
```ts
export interface BusinessProfile { id:string; name:string; website?:string; country?:string; industry?:string; logoUrl?:string; faviconUrl?:string; description?:string }

export interface ThemeTokens {
  id:string; name:string; updatedAt:number;
  primary:string; secondary:string; bg:string; surface:string; text:string; textMuted:string;
  bubbleAgent:string; bubbleCustomer:string; bubbleAI:string; bubbleRadius:number;
  fontFamily?:string; logoUrl?:string; avatarShape:'circle'|'rounded'|'square';
}

export interface ThemePreset { id:string; name:string; tokens: Partial<ThemeTokens> }

export interface LocaleSettings { timezone:string; locale:string; dateFormat:'auto'|'DMY'|'MDY'|'YMD'; timeFormat:'12h'|'24h'; rtl:boolean }

export interface NotificationPrefs { email:boolean; push:boolean; alerts:{ sla:boolean; backlog:boolean; volumeSpike:boolean; billing:boolean } }

export interface PublishState { themeId:string; lastPublishedAt?:number; linkedChannels: ('whatsapp'|'instagram'|'facebook'|'web')[] }
```

### Deliverables
- `types/settings.ts`

### Acceptance Checks
- Types compile; no circular deps.
ok
---

## STEP 2 — Primitive Components
**Goal:** Build reusable controls for branding & prefs.

### Prompt Block
In `mobile/src/components/settings/`, create:
- `LogoUploader.tsx` (props: `{ url?:string; onPick:(url:string)=>void }`) — fake picker; preview and replace.
- `ColorSwatch.tsx` (`label:string; value:string; onChange:(hex:string)=>void`)
- `ContrastBadge.tsx` (`fg:string; bg:string`) — computes WCAG-ish approximation and shows Pass/Fail.
- `BubblePreview.tsx` (`tokens:ThemeTokens`) — renders agent/AI/customer bubbles with radius/colors.
- `FontPicker.tsx` (`value?:string; onChange:(v:string)=>void`) — limited list.
- `AvatarShapePicker.tsx` (`value:'circle'|'rounded'|'square'; onChange:(v:any)=>void`)
- `TimezonePicker.tsx` (`value:string; onChange:(v:string)=>void`)
- `LocalePicker.tsx` (`value:string; onChange:(v:string)=>void`)
- `ToggleRow.tsx` (`label:string; value:boolean; onChange:(v:boolean)=>void`)
- `ListSkeleton.tsx`, `EmptyState.tsx`

### Deliverables
- 11 primitives exported

### Acceptance Checks
- All primitives render with demo props; touch ≥44dp; `ContrastBadge` shows pass/fail toggling colors.
ok
---

## STEP 3 — Settings Home
**Goal:** Entry hub for all settings and theming.

### Prompt Block
Create `mobile/src/screens/Settings/SettingsHome.tsx`:
- Header `Settings` with overflow: **Account**, **Developer (API & Webhooks)**, **About** (stubs).
- Sections with rows → screens: **Business Profile**, **Branding & Theming**, **Locale & Timezone**, **Notifications**, **Publish Theme**.
- Show current theme name + verify badge (if applied to Channels), current locale/timezone, and notification summary.
- `track('settings.view')` on mount.
Register as `Settings` in navigator.

### Deliverables
- `SettingsHome.tsx`
- Navigator update

### Acceptance Checks
- Rows navigate; summaries update when returning from sub‑screens.
ok
---

## STEP 4 — Business Profile Screen
**Goal:** Basic business metadata with logo & website.

### Prompt Block
Create `BusinessProfileScreen.tsx`:
- Fields: **Name**, **Website**, **Country**, **Industry**, **Description**.
- **LogoUploader** and optional **favicon** uploader.
- Button **Save** → updates local profile.
- CTA: **Publish link** → navigates to ChannelsHome.

### Deliverables
- `BusinessProfileScreen.tsx`

### Acceptance Checks
- Saving updates the SettingsHome summary; CTA navigates to Channels.
ok
---

## STEP 5 — Branding & Theming
**Goal:** Full theme editor with live preview.

### Prompt Block
Create `BrandingThemeScreen.tsx`:
- **Preset row** with a few `ThemePreset`s (Neutral, Bold, Soft). Clicking applies preset tokens.
- **Colors**: `ColorSwatch` controls for primary/secondary/bg/surface/text/textMuted with `ContrastBadge` vs bg.
- **Bubbles**: color pickers for agent/customer/AI + `AvatarShapePicker` + radius slider.
- **Typography**: `FontPicker` + size note (uses OS scale).
- **LogoUploader** within theme context.
- Right side (or top) **ThemePreview** using `BubblePreview` and sample header/footer showing colors.
- Buttons: **Save Draft**, **Reset**, **Export JSON** (copy to console).

### Deliverables
- `BrandingThemeScreen.tsx`

### Acceptance Checks
- Presets apply; contrast badges respond; preview updates live; save draft updates local theme.
ok
---

## STEP 6 — Theme Publish & Linkage
**Goal:** Publish theme and link to Channels widget/link.

### Prompt Block
Create `PublishThemeScreen.tsx`:
- Shows current theme name/id and **Publish** button. On publish → update `PublishState.lastPublishedAt` and set `themeId` in Channels link (local).
- **Linked channels** checklist (WhatsApp/Instagram/Facebook/Web) toggleable (UI only).
- Banner hint: "Rotation of link may require updating bios" (links to Channels rotation flow).
- Button: **Open Channels**.

### Deliverables
- `PublishThemeScreen.tsx`

### Acceptance Checks
- Publishing updates state and SettingsHome summary; channel toggles persist locally.
ok
---

## STEP 7 — Locale & Timezone
**Goal:** Regional settings with RTL toggle.

### Prompt Block
Create `LocaleTimezoneScreen.tsx`:
- **TimezonePicker**, **LocalePicker**, **Date format**, **12/24h**, **RTL toggle**.
- Preview row showing a formatted example date/time and a mirrored UI snippet when RTL is on.
- Save → updates app‑wide i18n context (UI‑only) and shows a toast.

### Deliverables
- `LocaleTimezoneScreen.tsx`

### Acceptance Checks
- Preview reflects changes; SettingsHome summary updates; basic RTL mirror appears.
ok
---

## STEP 8 — Notifications
**Goal:** Alert preferences for ops & billing.

### Prompt Block
Create `NotificationsScreen.tsx`:
- Toggles: **Email**, **Push**; group of alert toggles (SLA, Backlog, Volume Spike, Billing).
- Test buttons: **Send test email/push** (stubs) and show a toast.
- Save updates local `NotificationPrefs`.

### Deliverables
- `NotificationsScreen.tsx`

### Acceptance Checks
- Toggles persist; test buttons log/notify.
ok
---

## STEP 9 — Developer Shell Links
**Goal:** Shallow links into API/Webhooks shells (from §30).

### Prompt Block
Create `DeveloperShell.tsx` as a simple hub with buttons for **API Keys** and **Webhooks** (if your app already has these shells, navigate there; otherwise create placeholders that match §30).
Link from Settings overflow → Developer.

### Deliverables
- `DeveloperShell.tsx`

### Acceptance Checks
- Navigation works; placeholder screens exist if shells not built yet.
ok
---

## STEP 10 — About & Legal (UI‑only)
**Goal:** Show app version, terms, privacy, and licenses.

### Prompt Block
Create `AboutLegal.tsx`:
- Show version/build (static), links to **Terms**, **Privacy Policy** (open WebView placeholders), **Open‑source licenses** (static list or long text scroll).
- Button **Open Security & Privacy Center** (navigates to §31 shell).

### Deliverables
- `AboutLegal.tsx`

### Acceptance Checks
- Links open; navigation to security center works.
ok
---

## STEP 11 — Cross‑App Hooks
**Goal:** Wire Settings with other modules.

### Prompt Block
- From **ChannelsHome** overflow → **Theming** opens `BrandingThemeScreen`.
- After **Publish**, prompt to **Update Widget** → navigates to `WidgetSnippetScreen` (Channels).
- From **Dashboard SetupProgress**, when widget not themed, deep‑link to `PublishThemeScreen`.

### Deliverables
- Hooked navigations between Settings and Channels/Dashboard

### Acceptance Checks
- Links navigate and return correctly; state reflects changes.
ok
---

## STEP 12 — Offline & Queue Stubs
**Goal:** Prepare settings edits for offline.

### Prompt Block
- Reuse **OfflineBanner** on Settings screens.
- Queue local mutations: save theme draft, publish theme, update locale, toggle notifications; show small clock near the changed field until timeout clears.
- Add **Sync Center** button in Settings header.

### Deliverables
- Queued visuals; header button

### Acceptance Checks
- Offline banner shows; queued icons appear then clear.
ok
---

## STEP 13 — Performance & Accessibility
**Goal:** Keep theme editor responsive; meet WCAG basics.

### Prompt Block
- Memoize heavy previews (`BubblePreview`) and debounce color/text inputs.
- Add `accessibilityLabel` for every control (e.g., "Primary color picker"). Ensure touch ≥44dp and contrast; provide text alternatives for color swatches.

### Deliverables
- Perf & a11y tweaks

### Acceptance Checks
- Editor feels snappy; screen readers can adjust controls; contrast passes via `ContrastBadge`.
ok
---

## STEP 14 — Analytics Stubs
**Goal:** Instrument settings events.

### Prompt Block
Using `lib/analytics.ts` add:
- `track('settings.view')`
- `track('settings.profile.save')`
- `track('settings.theme.save')`, `track('settings.theme.publish')`
- `track('settings.locale.save')`
- `track('settings.notifications.save')`, `track('settings.notifications.test', { kind })`
- `track('settings.developer.open')`, `track('settings.about.open')`

### Deliverables
- Instrumentation calls

### Acceptance Checks
- Console logs events during interactions in dev.
ok
---

## STEP 15 — Final Review & Gaps
**Goal:** Confirm parity with BusinessFlow for Settings & Theming.

### Prompt Block
- Verify: BusinessProfile, BrandingTheme editor + preview, PublishTheme linkage, Locale & Timezone, Notifications, Developer/About shells, cross‑app hooks, offline queue visuals, perf/a11y, analytics.
- Create `mobile/docs/KNOWN_GAPS_Settings.md` listing deferred items (theme validation across screens, brand asset CDN, multi‑theme support, per‑channel overrides, enterprise SSO brand, i18n resources, email templates branding, export/import theme JSON).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; doc created.
ok
---

## Paste‑Ready Micro Prompts
- "Define settings models (BusinessProfile, ThemeTokens, LocaleSettings, NotificationPrefs, PublishState)."
- "Build LogoUploader, ColorSwatch, ContrastBadge, BubblePreview, FontPicker, AvatarShapePicker, TimezonePicker, LocalePicker, ToggleRow primitives."
- "Create SettingsHome and register in navigator with rows to sub‑screens."
- "Create BusinessProfileScreen with logo, website, and save behavior."
- "Create BrandingThemeScreen with presets, colors, bubbles, typography, and live ThemePreview."
- "Create PublishThemeScreen that publishes theme and links to Channels link/widget."
- "Create LocaleTimezoneScreen with timezone/locale/date/time/RTL and live preview."
- "Create NotificationsScreen with email/push and alert toggles and test actions."
- "Create DeveloperShell hub and AboutLegal with links to docs and security center."
- "Wire cross‑app hooks: Channels↔Settings↔Dashboard."
- "Queue offline edits and add Sync Center access."
- "Memoize previews, debounce inputs, add a11y labels and ≥44dp hit areas."
- "Instrument analytics events for saving/publishing."
- "Run final audit and create KNOWN_GAPS_Settings.md."

---

## Done‑Definition (Settings & Theming v0)
- SettingsHome routes work and show summaries.
- BrandingTheme editor updates preview, validates contrast, and saves drafts; Publish links theme to Channels.
- Locale/timezone apply across app (UI‑only); notifications toggles persist and test toasts appear.
- Developer/About stubs exist; cross‑app hooks navigate correctly.
- Offline visuals present; accessibility labels and hit areas meet guidelines; analytics events fire.

