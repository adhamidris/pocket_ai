# Cursor Build Plan — Dashboard (Ultra‑Chunked)
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first (no backend), prop‑driven components with skeletons and deep links

> Use this file with Cursor’s GPT Agent. Work **step by step**. Paste each *Prompt Block* verbatim, complete the step, run the app, validate with the *Acceptance Checks*, then move to the next block. Avoid changing files outside the step’s deliverables.

---

## Assumptions
- Repo uses: React Native + TypeScript + React Navigation (Stack/Tabs), project structure like `mobile/src/*`.
- Existing onboarding captures: company/country/industry/niches/business type/website/agent/Uploads (Mission/Vision/T&C/URLs).
- We’ll avoid adding libraries in v0. Basic charts are placeholder tiles; instrumentation is stubbed.

## Conventions
- **Paths:** `mobile/src/screens/Dashboard/*`, `mobile/src/components/dashboard/*`, `mobile/src/types/dashboard.ts`.
- **Testing IDs:** `testID` per component (e.g., `kpi-tile-live`, `alert-urgent`).
- **Styling:** central spacing scale `4/8/12/16/24` and touch targets ≥ `44x44`.

---

## STEP 0 — Primitives & Tokens (Design System Lite)
**Goal:** Add spacing/typography/color primitives used across dashboard.

### Prompt Block
Create `mobile/src/ui/tokens.ts` exporting spacing, radius, fonts, and semantic colors. Create `mobile/src/ui/Box.tsx` and `mobile/src/ui/Text.tsx` lightweight wrappers. Do not introduce a UI library.

### Deliverables
- `ui/tokens.ts` with `{space, radius, colors, font}`
- `ui/Box.tsx`, `ui/Text.tsx` (props: padding/margin shorthand, row/col, align, justify)

### Acceptance Checks
- Build passes. You can import `Box`/`Text` without TypeScript errors.

---

## STEP 1 — Types & Data Contracts (UI Props)
**Goal:** Define the prop shapes we’ll use (to mirror backend later).

### Prompt Block
Create `mobile/src/types/dashboard.ts` with:
```ts
export type KpiKind = 'liveConversations'|'frtP50'|'frtP90'|'resolutionRate'|'csat'|'deflection'|'vipQueue';
export interface DashboardKpi { kind: KpiKind; value: number; unit?: '%"|"s"|"count"; delta?: number; period?: '24h'|'7d'; target?: number; }
export type AlertKind = 'urgentBacklog'|'slaRisk'|'unassigned'|'volumeSpike';
export interface AlertItem { id: string; kind: AlertKind; count: number; buckets?: {label:string;count:number}[]; deeplink: string; }
export interface SetupStep { id: string; title: string; status: 'done'|'todo'; deeplink: string; }
export interface IntentItem { name: string; sharePct: number; deflectionPct?: number; trendDelta?: number; }
export interface PeakHour { hour: number; value: number; }
export type IndustryPackId = 'neutral'|'retail'|'services'|'saas';
export interface DashboardConfig { pack?: IndustryPackId; overrides?: Record<string, any>; }
```
Fix any minor TS quoting issues.

### Deliverables
- `types/dashboard.ts`

### Acceptance Checks
- Type exports compile; no unused import errors.

---

## STEP 2 — Component Shells (No Logic)
**Goal:** Create bare components with props and skeletons.

### Prompt Block
Under `mobile/src/components/dashboard/` create:
- `KpiTile.tsx` (props: `item: DashboardKpi`, `loading?: boolean`)
- `AlertCard.tsx` (`alert: AlertItem`, `loading?: boolean`)
- `QuickActions.tsx` (`actions: {label:string; deeplink:string; testID:string;}[]`, `onPress:(link:string)=>void`)
- `SetupProgressCard.tsx` (`steps: SetupStep[]`)
- `InsightsTopIntents.tsx` (`items: IntentItem[]`, `loading?: boolean`)
- `InsightsPeakTimes.tsx` (`hours: PeakHour[]`, placeholder grid)
- `VolumeByChannelMini.tsx` (`items: {channel:string; value:number;}[]`)
- `IndustryTiles.tsx` (props: `pack: IndustryPackId`, supports 3 modes: retail/services/saas with placeholder tiles)
- `ErrorBanner.tsx` (`message:string`)
- `Skeleton.tsx` (rect and text variants)
All components use `Box`/`Text` and `tokens`. Include `testID` props.

### Deliverables
- 9 components created with default exports

### Acceptance Checks
- Import each component into a dummy screen without runtime errors.

---

## STEP 3 — Dashboard Screen Scaffolding
**Goal:** Mount the components with demo data & sections.

### Prompt Block
Create `mobile/src/screens/Dashboard/Dashboard.tsx`:
- SafeAreaView + ScrollView
- **Sections in order:** KPIs (grid), Alerts (h-stack), QuickActions, SetupProgress (show only if any `todo`), Insights (TopIntents, PeakTimes, VolumeByChannel), IndustryTiles (based on pack), spacer bottom.
- Provide temporary demo data for props (hardcoded arrays) and `loading` toggles.
- Wire `QuickActions.onPress` to `navigation.navigate('Conversations', { filter: ... })` (placeholder).
Register screen in your navigator (Tab or Stack) as `Dashboard`.

### Deliverables
- `screens/Dashboard/Dashboard.tsx`
- Navigator update

### Acceptance Checks
- App runs and shows sections with demo data; scrolling is smooth; no UI library leaks.

---

## STEP 4 — Personalizer (UI‑only Resolver)
**Goal:** Control what tiles/alerts/actions appear using stored setup flags.

### Prompt Block
Create `mobile/src/screens/Dashboard/personalizer.ts` exporting:
- `getFlags()` — stub reading AsyncStorage (or in‑memory mock) for: `hasWebsite, hasUploads, channelsConnected, slaDefined, businessHoursSet, csatEnabled, industryPack`.
- `selectKpis(flags)` — prefers CSAT if `csatEnabled` else `deflection`; always include `liveConversations`, `frtP50`.
- `selectAlerts(flags)` — prefers `slaRisk` if `slaDefined` else `urgentBacklog` + `unassigned`.
- `selectQuickActions(flags)` — include deep links for Urgent / Waiting>30 / Unassigned; if !channelsConnected add `connectChannels` action.
- `selectIndustryPack(flags)` — return `flags.industryPack || 'neutral'`.
Update `Dashboard.tsx` to call selectors on mount; swap demo arrays with selected lists.

### Deliverables
- `screens/Dashboard/personalizer.ts`
- `Dashboard.tsx` updated to use it

### Acceptance Checks
- Toggling flags in code changes visible KPIs/alerts/actions without restart.

---

## STEP 5 — Deep Link Contracts
**Goal:** Standardize navigation params for filtered views.

### Prompt Block
Add/extend navigation types: `Conversations` screen accepts params `{ filter?: 'urgent'|'waiting30'|'unassigned'|'slaRisk'|'vip' }`.
- Create `mobile/src/navigation/types.ts` if absent.
- Update `QuickActions` and `AlertCard` taps to navigate with the correct `filter`.
- Add `testID`s to touchables to enable E2E later.

### Deliverables
- Navigation types & screen updates

### Acceptance Checks
- Tapping an alert/quick action navigates to Conversations (even if that screen is placeholder). No crashes.

---

## STEP 6 — Skeletons, Empty & Error States
**Goal:** Visual polish for loading, zero data, and error banners.

### Prompt Block
- Add `loading` prop to KPI/Insights containers and render `Skeleton` variants when true.
- Add `EmptyState` helper in `components/dashboard/EmptyState.tsx` (icon + message).
- Add `ErrorBanner` at top of Dashboard that can be toggled via local state.
- Provide a temporary “Simulate Loading/Error” dev toggle (e.g., button in header in dev builds only).

### Deliverables
- `EmptyState.tsx`, updated components with skeleton logic

### Acceptance Checks
- You can simulate loading and error; UI remains stable and accessible.

---

## STEP 7 — Accessibility & Touch Ergonomics
**Goal:** Meet WCAG AA basics and mobile reach.

### Prompt Block
- Ensure all interactive elements have `accessibilityLabel` and role.
- Verify touch targets ≥44dp and add `hitSlop` where needed.
- Add `testID`s per component root.
- Verify color contrast using semantic tokens (no low‑contrast text on tinted surfaces).

### Deliverables
- Small styling/prop tweaks across dashboard components

### Acceptance Checks
- VoiceOver/TalkBack can read labels; manual contrast check passes.

---

## STEP 8 — Offline/Sync UI Stubs
**Goal:** Prepare for offline‑first without backend.

### Prompt Block
- Add `OfflineBanner` component shown when `netInfo.isConnected === false`.
- Create `SyncCenterSheet.tsx` showing: last sync time, queued actions count, retry button (stub handlers).
- Add a button (three dots in header) to open Sync Center.

### Deliverables
- `OfflineBanner.tsx`, `SyncCenterSheet.tsx`, header action wiring

### Acceptance Checks
- Toggling a local `offline` state shows the banner; sheet opens and is scrollable.

---

## STEP 9 — Day‑1 Analytics Stubs
**Goal:** Standardize event calls (no external SDK yet).

### Prompt Block
Create `mobile/src/lib/analytics.ts`:
```ts
export const track = (event: string, props?: Record<string, any>) => {
  if (__DEV__) console.log('[track]', event, props||{});
};
```
Instrument in Dashboard:
- On mount: `track('dashboard.view')`
- KPI impressions: track kpi kinds visible
- On alert click: `track('alert.clicked', { kind })`
- On quick action: `track('quick_action.used', { kind })`

### Deliverables
- `lib/analytics.ts`, calls added

### Acceptance Checks
- Console shows events when interacting with dashboard.

---

## STEP 10 — Industry Tiles (Retail/Services/SaaS)
**Goal:** Gate domain tiles by selected pack.

### Prompt Block
Update `IndustryTiles.tsx` to render:
- **retail:** `Orders Today`, `Pending`, `Returns` (placeholder counts)
- **services:** `Upcoming Bookings`, `No‑Shows`, `Callback Queue`
- **saas:** `Trials Expiring`, `At‑Risk Accounts`
Expose a prop `onTilePress(title: string)` to navigate to contextual filtered views.

### Deliverables
- `IndustryTiles.tsx` logic + `Dashboard.tsx` wiring

### Acceptance Checks
- Changing `industryPack` flag swaps tiles immediately.

---

## STEP 11 — Dashboard Q&A (Ask the Dashboard)
**Goal:** NLQ panel stub (no LLM call yet).

### Prompt Block
Create `AskDashboard.tsx` with a text input + submit button; on submit, render a canned summary card (fake analytics summary). Place below Insights.

### Deliverables
- `AskDashboard.tsx` added into Dashboard screen

### Acceptance Checks
- Typing a question and submitting shows a summary card; no crashes.

---

## STEP 12 — Performance Pass
**Goal:** Ensure smoothness.

### Prompt Block
- Wrap expensive lists with `React.memo` where relevant.
- Use `FlatList` for KPI grid and alerts if counts > 6.
- Debounce expensive state changes; ensure 60fps during scroll (manual test).

### Deliverables
- Minor memoization & list updates

### Acceptance Checks
- JS frame drops minimized; scroll smooth.

---

## STEP 13 — Final Review & Gaps Audit
**Goal:** Confirm nothing was dropped.

### Prompt Block
- Run through acceptance checks of Steps 0–12.
- Verify deep links navigate with expected params.
- Toggle all flags in `personalizer.ts` to ensure KPIs/alerts/actions swap.
- Confirm accessibility labels exist and contrast is adequate.
- Document known gaps at bottom of this file.

### Deliverables
- Small fixes; add a `KNOWN_GAPS.md` under `mobile/docs/` listing anything deferred.

### Acceptance Checks
- All checks green; `KNOWN_GAPS.md` created.

---

## Optional Add‑Ons (after v0)
- **Charts:** swap placeholders with mini charts (Recharts for web or Skia for native).
- **State:** move flags to Zustand/Redux if needed.
- **E2E:** add Detox tests for dashboard flows using `testID`s.

---

## Paste‑Ready Mini Prompts (for each section)
Use these micro‑prompts inside Cursor when a step is too broad:
- "Create a lightweight Box and Text wrapper using RN View/Text with padding/margin props. No external libs."
- "Define dashboard TypeScript prop contracts exactly as in STEP 1."
- "Scaffold KpiTile/AlertCard/QuickActions/etc. with props + Skeletons; export default."
- "Create Dashboard.tsx with section order and demo data; register in navigator."
- "Implement personalizer selectors reading mock flags; swap demo arrays accordingly."
- "Add Conversations filter param typing and wire deep links from Alerts/QuickActions."
- "Add loading skeletons and ErrorBanner with a dev-only toggle."
- "Add OfflineBanner + SyncCenterSheet (UI only) and a header button to open it."
- "Instrument analytics with a simple local track() util; log interactions."
- "Render IndustryTiles based on pack; expose onTilePress for navigation."
- "Add AskDashboard panel stub that echoes a canned summary on submit."
- "Memoize heavy lists and ensure smooth scroll; use FlatList where helpful."
- "Run final audit; create KNOWN_GAPS.md and list follow-ups."

---

## Done‑Definition (for Dashboard v0)
- Above‑the‑fold renders under 1.5s with skeletons.
- Alerts & Quick Actions deep-link correctly.
- Personalizer flags switch content without reload.
- Loading/empty/error/ offline states visible and stable.
- Accessibility labels present; touch targets meet ≥44dp.
- Analytics stubs fire on key interactions.

