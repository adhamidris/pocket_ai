# Cursor Build Plan — Analytics (Dashboards, Trends, Cohorts, Attribution) — Ultra‑Chunked
**Date:** 2025‑09‑10 • **Scope:** Mobile app (React Native + TypeScript) • **Mode:** UI‑first (no backend), prop‑driven with chart placeholders (no external libs)

> Build the **Analytics** area for operational KPIs (FRT, Resolution, CSAT, Deflection, Volume, SLA), breakdowns by channel/intent/agent, cohorts/repeat contacts, funnels, attribution (UTM), saved reports, exports, and scheduling stubs. Work step‑by‑step. Paste each *Prompt Block* into Cursor, complete it, run, verify with *Acceptance Checks*, then proceed.

---

## Assumptions
- Prior modules exist: Dashboard, Conversations, CRM, Agents, Knowledge, Channels, Automations.
- UI primitives available: `Box`, `Text`, `ui/tokens`; analytics `track()` util exists.
- Use simple chart placeholders (bars/lines/heatmaps) built from `Box` components; no third‑party charts.

## Paths & Conventions
- **Screens:** `mobile/src/screens/Analytics/*`
- **Components:** `mobile/src/components/analytics/*`
- **Types:** `mobile/src/types/analytics.ts`
- **Navigation:** `mobile/src/navigation/types.ts`
- **Testing IDs:** prefix with `an-` (e.g., `an-home`, `an-trend-frt`, `an-heatmap-hours`, `an-save-report`)
- Spacing 4/8/12/16/24; touch ≥44×44dp; RTL‑ready.

---

## STEP 1 — Types & Data Contracts
**Goal:** Define metric keys, series, breakdowns, filters, and saved report model.

### Prompt Block
Create `mobile/src/types/analytics.ts`:
```ts
export type MetricKey = 'frtP50'|'frtP90'|'resolutionRate'|'csat'|'deflection'|'volume'|'slaBreaches'|'repeatContactRate'|'aht'|'backlog'|'vipQueue';
export type Dimension = 'time'|'channel'|'intent'|'agent'|'priority'|'segment'|'source';
export type Timegrain = 'hour'|'day'|'week'|'month';
export interface TimeRange { startIso: string; endIso: string; compareStartIso?: string; compareEndIso?: string; grain: Timegrain }

export interface SeriesPoint { ts: number; value: number }
export interface MetricSeries { key: MetricKey; points: SeriesPoint[]; summary?: {current:number; previous?:number; delta?:number} }

export interface BreakdownRow { name: string; value: number; sharePct?: number; extra?: Record<string, number|undefined> } // e.g., frtP50, csat per row

export interface HeatmapPoint { x: number; y: number; value: number } // e.g., hour vs weekday

export interface SavedReport {
  id: string;
  name: string;
  metrics: MetricKey[];
  dims: Dimension[];
  range: TimeRange;
  filters?: Record<string, any>;
  createdAt: number;
  schedule?: { cron?: string; recipients?: string[] };
}

export interface AttributionRow { source: string; medium?: string; campaign?: string; volume: number; conversions?: number; resolutionRate?: number }
```

### Deliverables
- `types/analytics.ts`

### Acceptance Checks
- Types compile; no circular deps.

---

## STEP 2 — Chart & Tile Primitives (No external libs)
**Goal:** Create minimal visual components for KPIs and charts.

### Prompt Block
In `mobile/src/components/analytics/`, create:
- `NumberTile.tsx` (`label:string; value:number|string; delta?:number; helpText?:string`)
- `LineChartMini.tsx` (`series:SeriesPoint[]; testID?:string`) — draw simple polyline using `View` segments (approx), or vertical bars; supports RTL.
- `BarChartMini.tsx` (`rows:{label:string; value:number}[]; maxBars?:number`)
- `StackedBarChart.tsx` (`stacks:{label:string; parts:{name:string; value:number}[]}[]`)
- `HeatmapGrid.tsx` (`points:HeatmapPoint[]; xLabels:string[]; yLabels:string[]`)
- `BreakdownTable.tsx` (`rows:BreakdownRow[]; columns:string[]`) — simple table with sticky header.
- `DateRangePicker.tsx` (`range:TimeRange; onChange:(r:TimeRange)=>void`) — presets: 24h/7d/30d/90d and compare toggle.
- `FilterBar.tsx` (`filters:Record<string,any>; onChange:(key:string,val:any)=>void`) — dropdowns for Channel/Intent/Agent/Segment/Priority.
- `ExportBar.tsx` (buttons: **Export CSV**, **Export PDF**, **Save Report**, **Schedule** — stubs).

Provide `testID`s and a11y labels.

### Deliverables
- 9 primitives exported

### Acceptance Checks
- Each renders with demo props; touch targets ≥44dp; tables scroll.

---

## STEP 3 — Analytics Home (Overview)
**Goal:** KPI tiles with compare, quick filters, and top insights.

### Prompt Block
Create `mobile/src/screens/Analytics/AnalyticsHome.tsx`:
- Header `Analytics` with overflow: **Definitions**, **Saved Reports**.
- Top row: `DateRangePicker` + `FilterBar` (Channel/Segment/Priority), Compare toggle.
- KPI tiles (use `NumberTile` + `LineChartMini`): **FRT P50**, **Resolution %**, **CSAT**, **Deflection %**, **Volume**, **SLA Breaches** (choose 4–6 tiles; make scrollable row if many).
- **Top Insights** list (static heuristics): e.g., "IG DM FRT improved 18% vs last week", "VIP queue breached twice yesterday".
- `ExportBar` at bottom.
- `track('analytics.view')` on mount.
Register as `Analytics` in navigator.

### Deliverables
- `AnalyticsHome.tsx`
- Navigator update

### Acceptance Checks
- Presets switch range; compare shows deltas on tiles; filters change demo series.

---

## STEP 4 — Trends Screen
**Goal:** Multi‑metric time series with compare bands.

### Prompt Block
Create `TrendsScreen.tsx`:
- Tabs for metrics: **FRT (P50/P90)**, **Resolution %**, **CSAT**, **Volume**, **Deflection %**.
- Each tab renders a large `LineChartMini` plus mini summary cards (current, previous, delta).
- Controls: **Grain** (hour/day/week/month) and **Compare Period** toggle.
- Link: **Open Conversations filtered** for a selected time bucket (tap handler returns bucket timestamp via onPress event).

### Deliverables
- `TrendsScreen.tsx`

### Acceptance Checks
- Switching tabs/grain updates chart; tapping a bucket navigates to Conversations with `{ atTs: bucketTs }` (UI‑only).

---

## STEP 5 — Channel Breakdown
**Goal:** Compare metrics across channels.

### Prompt Block
Create `ChannelBreakdown.tsx`:
- `StackedBarChart` for **Volume by Channel**.
- `BreakdownTable` rows per channel with columns: Volume, FRT P50, Resolution %, CSAT, Deflection %.
- Filter: include/exclude channels.
- CTA: **Open Recipe** (navigates to Channels → RecipeCenter for that channel).

### Deliverables
- `ChannelBreakdown.tsx`

### Acceptance Checks
- Table updates when filters change; CTA navigates.

---

## STEP 6 — Intent Analytics
**Goal:** Identify top intents and their operational impact.

### Prompt Block
Create `IntentAnalytics.tsx`:
- Table of top intents (name, share %, FRT P50, Resolution %, Deflection %, Repeat contact %).
- Inline bar for share; small delta arrows vs previous period.
- CTA per row: **Open conversations** filtered by intent; **Create automation…** (navigates to RuleBuilder with WHEN preset).

### Deliverables
- `IntentAnalytics.tsx`

### Acceptance Checks
- Tapping actions navigates properly; table scrolls smoothly.

---

## STEP 7 — Agent Performance
**Goal:** Compare human/AI agents on core metrics.

### Prompt Block
Create `AgentPerformance.tsx`:
- Grid of agent cards with: FRT P50, AHT, Resolution %, CSAT (humans); Deflection %, Low‑confidence % (AI).
- Sort by metric; filter by kind (AI/Human) and skill.
- CTA: **Open Agent** (to AgentDetail) and **Assigned Conversations** (to Conversations filtered).

### Deliverables
- `AgentPerformance.tsx`

### Acceptance Checks
- Sorting/filtering work; CTAs navigate.

---

## STEP 8 — Cohorts & Repeat Contacts
**Goal:** Visualize repeat contact rate and improvements over time.

### Prompt Block
Create `CohortsRepeat.tsx`:
- Cohort heatmap (rows: week of first contact; cols: weeks since; cell: repeat contact % within 48h/7d).
- Small cards: **Repeat contact % (48h)** and **Reduction vs last period**.
- CTA: **See failure log for cohort** → Knowledge FailureLog filtered by time.

### Deliverables
- `CohortsRepeat.tsx`

### Acceptance Checks
- Heatmap renders; CTA navigates.

---

## STEP 9 — Peak Hours & Staffing Hints
**Goal:** Hourly heatmap + hint cards.

### Prompt Block
Create `PeakHours.tsx`:
- `HeatmapGrid` for **Volume by hour (x: 0–23, y: weekday)**.
- Hint cards (static heuristics): "Add 1 agent 6–9pm UTC+X", "Enable outside‑hours auto‑responder".
- CTA: **Open Business Hours** (Automations → BusinessHoursScreen).

### Deliverables
- `PeakHours.tsx`

### Acceptance Checks
- Heatmap renders; CTA navigates.

---

## STEP 10 — Funnels
**Goal:** Simple funnels: Setup, Deflection, Resolution.

### Prompt Block
Create `Funnels.tsx`:
- **Setup funnel**: Signed up → Connected channel → Published link → Trained knowledge → First resolution.
- **Deflection funnel**: New conversations → Auto‑answer → No agent needed.
- **Resolution funnel**: New conversations → Assigned → Resolved (w/ reopened %).
- Bars with step counts and drop‑offs; inline notes.

### Deliverables
- `Funnels.tsx`

### Acceptance Checks
- Funnels render with demo counts; steps show drop‑off labels.

---

## STEP 11 — Attribution (UTM & Source)
**Goal:** Attribute volume/outcomes to sources/campaigns.

### Prompt Block
Create `Attribution.tsx`:
- Table of `AttributionRow`s (Source, Medium, Campaign, Volume, Conversions*, Resolution %). `*`Conversions = resolved conversations (UI‑only definition note).
- Filter by campaign; CTA: **Open UTM Builder** (Channels → UtmBuilder) and **Open Conversations** filtered by source.

### Deliverables
- `Attribution.tsx`

### Acceptance Checks
- Filters work; CTAs navigate.

---

## STEP 12 — Saved Reports & Scheduling (UI‑Only)
**Goal:** Save current view and schedule email shares.

### Prompt Block
Create `SavedReports.tsx`:
- List of `SavedReport`s; **Save Current** button captures: metrics on screen, filters, time range.
- Schedule modal: set simple cron presets (daily/weekly/monthly) and recipients (comma‑sep emails).
- CTA: **Export now** (reuse ExportBar stubs).
Link from Analytics overflow.

### Deliverables
- `SavedReports.tsx`

### Acceptance Checks
- Saving current view appends to list; scheduling UI stores data locally.

---

## STEP 13 — Export (CSV/PDF) Stubs
**Goal:** Provide export actions.

### Prompt Block
Create `ExportScreen.tsx`:
- Options: select metrics, time range, grain, breakdowns; generate **CSV**/**PDF** (UI stubs); show toast.
- Keep code minimal: just log out selections and present a success message.
Link from `ExportBar`.

### Deliverables
- `ExportScreen.tsx`

### Acceptance Checks
- Opening from ExportBar works; selections persist until dismiss.

---

## STEP 14 — Definitions & Methodology
**Goal:** Make every metric auditable.

### Prompt Block
Create `Definitions.tsx`:
- List metrics with plain‑language formulas, e.g., **FRT P50** = 50th percentile of (first agent or AI reply latency) for time bucket; **Resolution %** = resolved / total; **Deflection %** = resolved by AI with no agent / total; **Repeat contact %** = users contacting again within 48h.
- Notes on data caveats and timezone.
Link from Analytics overflow and inline `?` tooltips.

### Deliverables
- `Definitions.tsx`

### Acceptance Checks
- Links open; tooltips show brief definitions.

---

## STEP 15 — Cross‑App Hooks
**Goal:** Make analytics actionable.

### Prompt Block
- From tiles/charts/tables add CTAs:
  - **Open Conversations filtered** (intent/channel/agent/time bucket)
  - **Create Automation** (RuleBuilder with WHEN from selected row)
  - **Open Agent** (AgentDetail)
  - **Open Knowledge FailureLog** (for intents/cohorts with high repeat)
  - **Open Channels Recipe** (for weak channel performance)

### Deliverables
- Navigation hooks added in each screen

### Acceptance Checks
- Tapping CTAs navigates to the correct module with expected params.

---

## STEP 16 — Offline & Cache Stubs
**Goal:** Keep analytics usable offline.

### Prompt Block
- Reuse global **OfflineBanner**.
- Cache last viewed series per screen in local state; show **Cached** badge when offline.
- Add **Refresh** button that simulates re‑pulling demo data.

### Deliverables
- Cached badge; refresh button

### Acceptance Checks
- Offline shows banner; cached badge appears; refresh re‑seeds data.

---

## STEP 17 — Performance & Accessibility
**Goal:** Smooth charts & readable tables.

### Prompt Block
- Use `React.memo` on chart components; avoid re‑calc on every render.
- Virtualize long tables; stable keys.
- Add `accessibilityLabel`/roles to chart containers; provide textual summaries below each chart for screen readers.
- Ensure contrast and ≥44dp touch targets.

### Deliverables
- Perf & a11y tweaks

### Acceptance Checks
- Scroll/interaction runs 60fps; screen readers can consume summaries.

---

## STEP 18 — Analytics Event Instrumentation
**Goal:** Instrument UI interactions (meta‑analytics).

### Prompt Block
Using `lib/analytics.ts` add events:
- `track('analytics.view')`, `track('analytics.trend_tab', { key })`
- `track('analytics.filter', { key, value })`
- `track('analytics.bucket_open', { ts })`
- `track('analytics.report_save', { id })`, `track('analytics.schedule', { id })`
- `track('analytics.export', { type })`
- `track('analytics.cta', { target })`

### Deliverables
- Instrumentation calls

### Acceptance Checks
- Console logs events on interactions in dev.

---

## STEP 19 — Final Review & Gaps
**Goal:** Verify parity with BusinessFlow specs and coverage.

### Prompt Block
- Walk through: Home tiles/compare → Trends → Channel → Intent → Agent → Cohorts → Peak Hours → Funnels → Attribution → Saved Reports → Exports → Definitions → Cross‑app hooks → Offline → Perf/A11y → Events.
- Create `mobile/docs/KNOWN_GAPS_Analytics.md` listing deferred items (real time series APIs, percentile calc on device, timezone server sync, compare logic, PDF export engine, forecast models).

### Deliverables
- Fixes; KNOWN_GAPS doc

### Acceptance Checks
- All checks pass; document created.

---

## Paste‑Ready Micro Prompts
- "Define analytics models for metrics, series, breakdowns, heatmaps, saved reports."
- "Build NumberTile, LineChartMini, BarChartMini, StackedBarChart, HeatmapGrid, BreakdownTable, DateRangePicker, FilterBar, ExportBar primitives."
- "Create AnalyticsHome with date/filter bar, KPI tiles with compare, insights, and ExportBar."
- "Create TrendsScreen with tabs, grain selector, compare, and bucket tap→Conversations."
- "Build ChannelBreakdown with stacked bars and table; CTA to Channels recipes."
- "Build IntentAnalytics table with CTAs: open conversations and create automation."
- "Build AgentPerformance grid with AI/Human variants and navigation CTAs."
- "Create CohortsRepeat heatmap and link to Knowledge FailureLog."
- "Create PeakHours heatmap with staffing hint cards and link to Business Hours."
- "Create Funnels for setup/deflection/resolution."
- "Create Attribution table linked to UTM Builder and Conversations."
- "Implement SavedReports with schedule modal and Export reuse."
- "Implement ExportScreen stubs for CSV/PDF."
- "Create Definitions screen and inline tooltips for formulas."
- "Add cross‑app CTAs across Analytics screens."
- "Add OfflineBanner reuse, cached badge, refresh button."
- "Memoize charts/tables; add a11y summaries for charts."
- "Instrument analytics UI events; create KNOWN_GAPS_Analytics.md."

---

## Done‑Definition (Analytics v0)
- AnalyticsHome shows KPI tiles with compare and responds to filters/time range.
- Trends, Channel, Intent, Agent, Cohorts, Peak Hours, Funnels, Attribution screens all render demo data with CTAs.
- Saved Reports and Export stubs work; Definitions present; cross‑app hooks navigate correctly.
- Offline shows cached state; performance is smooth; accessibility labels and textual chart summaries exist.
- Analytics UI events are instrumented.
