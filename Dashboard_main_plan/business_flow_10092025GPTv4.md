# BusinessFlow — AI Agent Platform (B2C-first)
**Date:** 2025-09-10 • **Owner:** You • **Version:** v1.0 (living doc)

> Purpose: Capture the product vision, business flow, UI-first plan, data contracts, and a component-level blueprint for a modern, future-proof mobile app + hosted chat portal. This is the reference for design, engineering, and go-to-market.

---

## 1) One‑liner & North Star
**One‑liner:** A self-serve, hyper‑customizable AI agent platform that lets any business spin up branded assistants across social channels in minutes—backed by a calm, actionable mobile dashboard.

**North Star (12–18 mo):** Time‑to‑value < 10 minutes (from sign‑up → published agent link), ≥ 60% automated resolution for eligible intents, and P50 First‑Response‑Time < 60s during business hours.

**Primary Jobs‑to‑Be‑Done:**
1. *Set up an agent fast* (no engineer needed) and brand it.
2. *Connect channels* (WhatsApp auto‑reply link, IG bio, Facebook, web widget).
3. *Continuously improve answers* (upload/sync knowledge; see what’s failing; fix).
4. *Run operations on the go* (mobile dashboard: KPIs, alerts, queues, actions).
5. *Stay in control* (HITL, audit, SLAs, business hours, routing, allowed actions).

---

## 2) Personas
- **Owner / Admin** (small business, ops lead): wants fast setup, oversight, and minimal noise.
- **Agent (Human)**: handles escalations, needs clean queues and customer context.
- **Customer (End‑user)**: interacts with branded chat link; expects quick, correct help.

---

## 3) High‑level System & Channels
- **Hosted Chat Portal (Unique Link):** per‑agent URL, themeable (logo/colors/avatars/fonts), optional web widget snippet.
- **Mobile App (Ops brain):** Dashboard, Conversations, CRM, Agents, Knowledge, Channels, Automations, Analytics, Settings.
- **No‑code Personalization:** UI “Personalizer” adapts KPIs/alerts/tiles based on setup (industry, niches, business type, website, uploaded sources, connected channels, SLA/business hours).
- **MCP / Actionables:** Allowlist of safe operations agents can perform (e.g., create ticket, schedule callback, collect info), extensible later to 3P systems.
- **Voice:** Optional STT/TTS for chat portal & dashboard Q&A.

---

## 4) Onboarding / Setup Wizard (v1 fields)
**Captured today:** Business name, country, industry (+niches), business type, website, agent name/title, uploaded: Mission, Vision, T&C, URLs.

**Extend (UI‑ready):**
- Business hours & time zone; holiday calendar.
- SLA targets (FRT P50/P90, Resolution time per priority).
- Channels to publish (WhatsApp auto‑reply recipe, IG bio, Facebook page, Web widget).
- Branding theme (logo, primary/secondary color, bubble style, accent tone, avatar shape, font).
- Allowed agent actions (collect data, schedule call, escalate to human, send link pack, etc.).
- Sensitive terms / redaction rules (emails, phones, order IDs).

**Setup Checklist (always visible until complete):** Connect channels → Publish widget/link → Train from uploads → Define SLAs & Hours → Review Privacy/Consent → Invite human agents.

---

## 5) Information Architecture (Mobile App)
1. **Dashboard** (default)
2. **Conversations** (filters, assignment, actions)
3. **CRM** (contacts, segments, VIP)
4. **Agents** (AI & human; routing, skills)
5. **Knowledge** (sources, training, coverage)
6. **Channels** (links, widget, QR, recipes)
7. **Automations** (rules, business hours, SLAs)
8. **Analytics** (KPIs, trends, exports)
9. **Settings** (brand, security, consent, roles)

---

## 6) Dashboard Blueprint (calm, high‑signal)
**Above‑the‑fold KPIs (show 4 based on Personalizer):**
- Live Conversations (current / 24h trend)
- First Response Time (P50 / P90 vs target)
- Resolution Rate (7d trend)
- CSAT/NPS (latest & 7d trend)
- *(Fallbacks if CSAT off: Deflection Rate; VIP Queue size)*

**Alerts (2–3)**
- Urgent Backlog (age buckets 0–15m / 15–60m / >60m)
- SLA at Risk (waiting items nearing breach; honors business hours)
- Unassigned Conversations
- *(Optional)* Volume Spike vs baseline

**Quick Actions (3)**
- Review Urgent → Conversations[urgent]
- Waiting > 30m → Conversations[waiting>30]
- Unassigned → Conversations[unassigned]
- *(Swappable)* Publish Web Widget, Connect Channels, Train from Uploads

**Setup Progress (compact)**
- Show 3 most relevant incomplete steps only; each a deep link.

**Insights (below fold)**
- Top Intents (24h) with share & deflection rate; tap to Intent drill‑in.
- Repeat Contacts (≤48h) spotlighting broken experiences.
- Peak Times heatmap (hour vs day) to tune staffing/autoresponders.
- Volume by Channel (stacked mini‑chart).

**Industry Add‑ons (auto‑gated by setup):**
- *Retail/E‑comm:* Orders Today / Pending / Returns tiles; deep link to tagged conversations.
- *Services:* Upcoming Bookings / Missed Calls / Callback Queue.
- *SaaS:* Trials Expiring / At‑risk Accounts / Tiered FRT.

**Dashboard Q&A (Ask the Dashboard):**
- Natural‑language panel: “What happened today?” → returns summaries, anomalies, suggested actions.

**Micro‑interactions:**
- Pull‑to‑refresh; skeleton loaders; link‑style CTAs; semantic color for state only.

---

## 7) Conversations
**List & Filters:** Active, Waiting, Unassigned, SLA Risk, VIP, Channel, Intent, Tag.
**Row:** Customer name, last message snippet, age, SLA badge, channel dot, tags.
**Thread View:** messages, AI/human markers, suggested replies/macros, side pane with Contact profile, past interactions, consent/PII flags, notes.
**Actions:** assign, tag, change priority, escalate to human, schedule callback, send link pack, mark resolved. *(Gate risky actions via HITL.)*
**HITL Controls:** auto‑escalate on low confidence, sensitive keywords, or restricted intents.

---

## 8) CRM (Lightweight)
Contacts, attributes (tags, VIP toggle), segments (dynamic rules), dedup, consent state, import.
RFM‑style signals (if enabled later): recency/frequency proxies from conversations.

---

## 9) Agents (AI & Human)
Roster of AI agents (roles) and human agents; routing rules (skills, schedule), overflow rules, capacity hints; performance micro‑cards (FRT, AHT, CSAT).

---

## 10) Knowledge
**Sources:** Uploads (PDF/Doc), URLs, manual notes; toggles per source.
**Training:** “Train now” with progress & coverage; last trained time; drift warnings.
**Quality:** Test harness (ask sample questions), failure log (unanswered/low‑conf), suggestions to enrich sources.

---

## 11) Channels & Publishing
- **Unique Link:** Copy, regenerate, QR code; preview theme.
- **Recipes:**
  - WhatsApp Business: auto‑reply template with your link.
  - Instagram: bio + link‑in‑bio format.
  - Facebook Page: About + pinned post template.
  - Web Widget: script snippet + SPA/Next/Shopify notes.
- **Status:** connected/unconnected, last verified.

---

## 12) Theming (Chat Portal)
Logo, brand colors, bubble style (rounded/square), avatar shape, font (system + popular safe picks), background (solid/gradient/image), welcome text; live preview; accessibility contrast checks; RTL support.

---

## 13) Automations & SLAs
**Rules:** IF <intent/tag/channel/time> THEN <auto‑reply/route/tag/escalate/set priority>.
**Business Hours:** per weekday + holidays.
**SLAs:** FRT/Resolution per priority & channel; pause outside hours (optional).
**Allowed Actions (MCP allowlist):** which commands AI may execute.

---

## 14) Analytics
**Overview:** Volume, FRT (P50/P90), AHT, Resolution Rate, CSAT, Deflection, Repeat Rate, Volume by Channel, Top Intents, Staffing hints.
**Trends:** 7/14/30‑day, with anomalies.
**Exports:** CSV/JSON; scheduled email reports.

---

## 15) Governance, Security, Privacy
- **HITL:** thresholds, escalation rules, human approval steps.
- **Audit Log:** config changes, agent actions, redactions.
- **Privacy:** PII redaction patterns; consent capture; data retention windows.
- **Roles & Permissions:** Owner, Admin, Agent, Viewer; per‑feature scopes.
- **Compliance prep:** logging, data residency options (future), DSR workflows.

---

## 16) Voice (Optional v1.5+)
Enable STT/TTS in portal and dashboard Q&A; transcription storage policy; mic permissions UX.

---

## 17) “Personalizer” (UI‑only logic)
At app load, read setup (industry, niches, business type, website present, sources uploaded, channels connected, SLA/hours set). Compute flags:
- `hasWebsite`, `hasUploads`, `channelsConnected`, `slaDefined`, `businessHoursSet`, `csatEnabled`.
Select:
- Which **KPIs** (prefer CSAT if `csatEnabled` else Deflection).
- Which **Alerts** (prefer SLA at Risk if `slaDefined`).
- Which **Quick Actions** (prefer Unassigned if `channelsConnected`).
- Which **Industry Tiles** (based on industry/niches).

---

## 18) UI Component Inventory (Mobile)
**Primitives:** Button, IconButton, Input, TextArea, Select, Checkbox, Radio, Switch, Chip, Badge, Tag, Tooltip, Toast, Modal, Sheet/Bottom‑Sheet, Popover, Tabs, Accordion, Skeleton, EmptyState, ErrorBanner, Divider.
**Data & Visualization:** KPI Tile, Sparkline Tile, Donut, Line chart, Heatmap, Intent Cloud (chip cluster), Progress Bar, Trend Delta.
**Lists & Cards:** ConversationListItem, AlertCard, QueueItem, CustomerMiniCard, AgentCard, KnowledgeSourceItem, ChannelCard, SetupStepItem.
**Layout:** AppHeader (safe‑area), FilterBar (chips + sort), StickyFooter (bulk actions), FloatingAction (contextual), PullToRefresh.
**Accessibility/i18n:** Dynamic fonts, high‑contrast mode, screen reader labels, RTL.

---

## 19) Data Contracts (UI props — to guide backend later)
```ts
export type KpiKind = "liveConversations"|"frtP50"|"frtP90"|"resolutionRate"|"csat"|"deflection"|"vipQueue";
export interface DashboardKpi { kind: KpiKind; value: number; unit?: "%"|"s"|"count"; delta?: number; period?: "24h"|"7d"; target?: number; }
export interface AlertItem { id: string; kind: "urgentBacklog"|"slaRisk"|"unassigned"|"volumeSpike"; count: number; buckets?: {label:string;count:number}[]; deeplink: string; }
export interface QueueItem { id: string; title: string; subtitle?: string; count: number; deeplink: string; }
export interface IntentItem { name: string; sharePct: number; deflectionPct?: number; trendDelta?: number; }
export interface SetupStep { id: string; title: string; status: "done"|"todo"; deeplink: string; }
```

---

## 20) Failure Modes & Safeguards
- No channels connected → emphasize “Connect Channels” quick action; hide channel‑specific tiles.
- CSAT disabled → swap in Deflection Rate KPI.
- No uploads → show Knowledge CTA and sample Q&A test harness.
- Low confidence answers → auto‑escalate and log for training.

---

## 21) Roadmap (Phased & Arranged)
**Goal:** Ship a calm, useful dashboard fast, then layer platform strength (offline, API, privacy, billing, growth) without breaking UX.

### v0 — MVP Beta (Weeks 0–2)
**Focus:** Core flows + Personalizer + shippable defaults
- Setup Wizard (current fields), Unique Link, Theme Preview
- Dashboard (prop‑driven KPI/Alerts/Quick Actions, SetupProgress, Insights stubs)
- Personalizer utility (flags → choose tiles/alerts/quick actions)
- Conversations list + filters + deep link targets (Urgent/Waiting/Unassigned/SLA risk)
- Channels: Publish recipes (WhatsApp/IG/FB) + QR generator (basic)
- Knowledge uploads (manual) + “Train now” stub + coverage placeholder
- Basic Analytics (activation: time‑to‑published link)
- **Ergonomics & A11y/Perf baselines**: spacing/tap sizes, skeletons <1s, list virtualization plan
- **Day‑1 metrics scaffolding** (event taxonomy defined; minimal events wired)

**Exit criteria:** Above‑fold in <1.5s; deep links work; Personalizer switches content without restart.

### Sprint 1 — Merge Additions (Weeks 2–4)
Implements §35:
- Gesture map + spacing tokens audit
- Offline indicators + local queue + Sync Center sheet (UI + minimal logic)
- API Keys & Webhooks shells (read‑only metrics + test hook)
- Security & Privacy Center shell (retention sliders, consent templates, audit viewer stub)
- Billing & Usage page (plan card + usage bars + invoice placeholder)
- Referral card on Dashboard
- A11y/Perf audit; virtualize Conversations; debounced search; instrument Day‑1 metrics

**Exit criteria:** Offline queue surfaces; API/Webhooks screens navigable; Security/Billing shells visible.

### v1 — B2C Launch (Month 2–3)
**Focus:** Operational maturity
- Business Hours & SLAs; SLA‑at‑Risk alert honoring hours
- Dashboard Q&A (Ask the Dashboard)
- Web Widget snippet (copyable) with SPA/Shopify notes
- CSAT capture toggle; CSAT KPI when enabled
- **Industry Packs**: Pack picker + Domain Tiles gallery + Threshold templates + Canned replies library starter
- Security & Privacy Center (functional: retention applies to sample entity; audit viewer lists recent events)
- API Keys/Webhooks (key rotation UX; delivery log list)
- Billing & Usage (invoice list, plan upgrades; soft limits warnings)
- Growth: referral milestones

**Exit criteria:** SLA timers/alerts correct; widget installs; packs toggle content; basic billing & audit usable.

### v1.1 — Platform Polish (Month 3)
**Focus:** Reliability & device natives
- Offline‑first: conflict prompts, smarter retries; Sync Center details
- Platform adaptations: iOS haptics, Android back‑stack rules, PWA install prompt
- Accessibility & Performance tuning to targets in §33; success‑metrics view (activation & ops KPIs)

**Exit criteria:** Crash‑free ≥99.5%; ANR <0.3%; WCAG AA checks; conflict UX verified.

### v1.5 — Intelligence & Voice (Month 4–5)
- Voice (STT/TTS) in chat portal & Dashboard Q&A
- Knowledge failure log + suggestions; coverage %
- VIP Queue, Segments, Repeat‑contact insights
- Expanded canned replies & rules templates by pack

**Exit criteria:** Voice toggle functional; failure log driving improvements; VIP/segments used in routing.

### v2 — Developer Surfaces & Advanced Ops (Month 6+)
- Plugins/Marketplace beta (install/uninstall, scopes/consent, uninstall data policy)
- SDKs (TS/JS, Kotlin, Swift) with examples; API usage/latency metrics
- Advanced analytics (forecasts, staffing hints); channel verifications
- Governance expansion (role scopes, IP allowlist, session/device mgmt)

**Exit criteria:** First external plugin; SDK sample app; forecast charts live.

### v3+ — Integrations & Enterprise (Month 9+)
- Deep integrations (Shopify/Woo/HubSpot/Zendesk)
- Marketplace GA; data residency options; enterprise roles & SSO
- Performance hardening at scale; multi‑region tenancy options

**Exit criteria:** First integration GA; enterprise tenants onboarded.

---

## 22) Acceptance Criteria (Design‑led)
- Above‑fold loads < 1s with skeletons; actionable links resolve to filtered screens.
- Zero hard‑tinted surfaces; semantic color communicates state only.
- Every alert/quick action is a working deep link.
- Personalizer switches KPIs/alerts/tiles based on flags without app restart.

---

## 23) Open Notes (for future refinement)
- Define exact CSAT/NPS capture flow in the portal (opt‑in).
- Finalize default SLA targets by industry template.
- Confirm privacy copy and consent screens per region.

---

## 24) “Perfect” Dashboard Default (opinionated v1)
- **KPIs:** Live Conversations, FRT P50/P90, Resolution Rate, CSAT.
- **Alerts:** Urgent Backlog, SLA at Risk, Unassigned.
- **Quick Actions:** Review Urgent, Waiting > 30m, Unassigned.
- **Setup (3 steps):** Connect Channels, Publish Widget/Link, Train from Uploads.
- **Insights:** Top Intents, Repeat Contacts, Peak Times, Volume by Channel.
- **Add‑ons:** Auto‑gated by industry.

---

## 25) Next Tickets (shippable UI)
1. Implement Personalizer utility (flags + selections).
2. Convert Dashboard tiles to prop‑driven with skeleton states.
3. Wire deep links for all Alerts/Quick Actions to Conversations filters.
4. Build SetupProgress compact card (max 3 visible steps).
5. Channels “Recipes” sheet: copy‑ready templates + QR generator.

> Keep this doc updated as decisions solidify; treat it as the single source for scope & hand‑offs.



---

## 26) Industry Packs (Optional presets + domain modules)
**Why:** Speed up setup with domain‑smart defaults while keeping everything overridable.

**Layers:**
1. **Universal layer (always on):** Core screens, generic KPIs (FRT/Resolution/CSAT), queues (Urgent/Unassigned), setup checklist, Personalizer.
2. **Industry pack (optional):** Opinionated presets + domain modules.
3. **Your customization (final say):** Your toggles/edits override all defaults.

**Precedence:** Your overrides > inferred preferences (setup flags) > pack defaults > universal fallback.

**What a pack includes:**
- **KPI preset:** which 4 KPIs show above the fold + targets.
- **Alerts preset:** which 2–3 alerts are on + breach thresholds.
- **Quick actions:** the 3 most useful buttons for the domain.
- **Domain tiles:** extra blocks (e.g., Orders/Returns; Bookings/Callbacks; Trials/At‑Risk).
- **Intent taxonomy & tags:** suggested intents for auto‑tagging/analytics.
- **Automations & rules:** ready‑to‑toggle IF/THEN rules common in the domain.
- **Canned replies/macros:** domain‑specific templates.
- **Channel recipes:** which channels to prioritize and how to publish.
- **Knowledge templates:** stubs for policies/FAQs typical to the domain.
- **Sensible thresholds:** e.g., IG DM FRT ≤ 10 min for retail vs next‑biz‑day for B2B billing.

**Concrete packs (examples)—all items are editable:**

### Retail / E‑commerce
- **KPIs:** Live Conversations, FRT P50, Resolution %, **Deflection** (CSAT optional)
- **Alerts:** Spike in “Where is my order?”, Unassigned, SLA at Risk (≤10m)
- **Quick actions:** Send tracking link, Create return, Waiting > 30m
- **Domain tiles:** **Orders Today / Pending / Returns**
- **Intents:** order status, return/exchange, delivery delay, product availability
- **Rules:** IF intent = “order status” → auto‑send tracking steps; IF “return” → generate return flow link
- **Channel focus:** WhatsApp + Instagram recipes pre‑promoted

### Services (clinics, salons, field services)
- **KPIs:** Bookings Today, FRT P50, No‑show rate, Resolution %
- **Alerts:** Missed calls, Callback queue, SLA at Risk (≤15m)
- **Quick actions:** Schedule callback, Confirm appointment, Waiting > 30m
- **Domain tiles:** **Upcoming Bookings / No‑Shows / Callback Queue**
- **Intents:** booking request, reschedule, pricing, location/hours
- **Rules:** IF “reschedule” → propose next 3 slots; IF missed call → create callback task

### SaaS (subscriptions/support)
- **KPIs:** Trials expiring (7d), FRT P50/P90, Resolution %, CSAT
- **Alerts:** Spike in billing/login, Unassigned, VIP SLA risk
- **Quick actions:** Send onboarding checklist, Escalate to Success, Waiting > 30m
- **Domain tiles:** **Trials Expiring / At‑Risk Accounts**
- **Intents:** onboarding help, billing, login, feature request
- **Rules:** IF trial expiring < 7d & no reply → nudge with checklist

**How it works with the Personalizer (UI‑only):**
- When an industry is selected (or inferred by niches), the Personalizer loads `pack = getPack(industry)` to obtain an **availability map** (what domain tiles exist) and **defaults** (KPIs/alerts/actions/thresholds).
- Merge order:
```ts
const base = universalDefaults();
const pack = getPack(industry);           // retail/services/saas (or neutral)
const inferred = inferFromSetupFlags();   // csatEnabled, channelsConnected, slaDefined…

const proposal = merge(base, pack.defaults, inferred.suggestions);
const finalConfig = applyUserOverrides(proposal); // user settings always win
```

**Data‑contract note (optional additions):**
```ts
export type IndustryPackId = 'neutral'|'retail'|'services'|'saas';
export interface DashboardConfig { pack?: IndustryPackId; overrides: Record<string, any>; }
```

**Benefits:**
- Faster time‑to‑value, less noise, and still fully in your control.

---

## 27) Next Tickets — Add‑on for Industry Packs
1. **Pack picker** in Onboarding → Industry step (optional, default Neutral B2C).
2. **Pack preview** in Dashboard settings (shows proposed changes; accept/reject per item).
3. **Domain tiles gallery** (toggle tiles on/off regardless of pack).
4. **Threshold templates** (pack‑level SLA defaults; user editable).
5. **Canned replies library** seeded per pack.



---

## 28) Mobile‑First & Platform Adaptations
**Ergonomics & layout**
- Touch targets ≥ 44×44dp; spacing scale 8/12/16/24; 4‑pt baseline grid.
- Safe‑area aware headers/footers; bottom navigation within thumb‑reach (60–72dp height).
- Floating Action (contextual) + Sticky Footer for bulk actions.
- Dynamic type: respect OS font scaling; never truncate critical stats.
- Breakpoints: **S** ≤360dp, **M** 361–400dp, **L** 401–480dp, **XL** (foldables/tablets). Reflow KPI tiles 2→3→4 cols accordingly.

**Gesture map**
- Pull‑to‑refresh on Dashboard & Conversations list.
- Edge‑swipe back (iOS) / back button (Android) → pop screen or dismiss modal/sheet.
- Long‑press list rows → quick actions (assign/tag/priority).
- Horizontal swipe on rows (optional) → reveal “Assign/Resolve”.
- Tap KPI/Alert/Quick Action → deep‑link to pre‑filtered views (already standard).

**Platform adaptations**
- iOS: haptics on critical actions (impact medium); native share sheets.
- Android: predictable back‑stack behavior (modals dismiss, then pop); system intents for share/phone.
- Web/PWA: install prompt, service worker for cache/offline, push opt‑in.

---

## 29) Offline‑First UX
**Data classes & caching**
- *Static:* theme, industry pack, permissions → cache long‑lived.
- *Semi‑static:* knowledge metadata, settings → cache with background refresh.
- *Dynamic:* conversations, alerts, KPIs → cache short TTL; subscribe to realtime when online.

**Queue & optimistic UI**
- Local action queue (assign, tag, resolve, note). Show **Queued** state + toast; auto‑retry with exp backoff.
- Offline banner with “View sync details”.

**Conflict resolution**
- Low‑risk fields → last‑write‑wins (with server time authority).
- Mergeable fields (tags/notes) → union + ordered by timestamp.
- Destructive ops (delete/close) → prompt if stale (>N sec) or if server diverged.

**Diagnostics**
- “Sync Center” sheet: last sync time, queued actions, failures with retry.

---

## 30) API & Plugins (Shell)
**API Keys**: create/regenerate/restrict scopes; show last used; rotate reminders.
**Usage & rate limits**: per key metrics (requests, errors, p95 latency).
**Webhooks**: signed deliveries, retry policy; test delivery button.
**SDKs**: TS/JS, Kotlin, Swift code snippets for common tasks.
**Plugins/Marketplace (future)**: cards with scopes & consent; per‑tenant enable/disable; clear uninstall data policy.

---

## 31) Security & Privacy Center
- **Retention controls**: sliders (30/90/180/365 custom) per data class; preview items slated for purge.
- **Consent & disclosures**: portal banner templates; region presets; cookie/storage notes.
- **Encryption status**: in‑transit/at‑rest badges; key mgmt notes.
- **Redaction**: PII patterns (email/phone/orderID) + on/off by channel.
- **Roles & access**: matrix view; IP allowlist; session/device management.
- **Audit**: event viewer (config changes, escalations, redactions) with export.

---

## 32) Billing & Growth
- **Plans & limits**: messages/month, MAU, AI minutes, agents, channels.
- **Usage bars** with projection to limit; overage rules; grace period.
- **Invoices & payments**: history, download PDF, tax/VAT IDs, payment method.
- **Referrals**: unique link, milestones, rewards; lightweight achievements (finish setup, connect channel, first 100 resolved).
- **In‑product nudges**: contextual upgrade CTAs on hitting limits (never block critical support workflows mid‑conversation).

---

## 33) Accessibility & Performance Budgets
**Accessibility (WCAG 2.1 AA targets)**
- Color contrast ≥ 4.5:1; visible focus; labels for all controls; readable charts (with text alternatives).
- Screen reader order mirrors visual; RTL ready; large text modes.

**Performance**
- Above‑the‑fold skeleton in < 1s; KPI tiles render < 1.5s on cold start.
- 60fps target on scroll; virtualize long lists; debounce search (≥250ms).
- Image rules: serve <= 1.5× display size; lazy‑load below fold.
- Caching TTLs: KPIs 30–60s, alerts 15–30s, conversations list 5–15s.
- Observability: crash‑free rate ≥ 99.5%; ANR < 0.3%; memory/battery budgets documented per screen.

---

## 34) Success Metrics (Day‑1 instrumentation)
**Activation**: time‑to‑published link; setup completion (%).
**Ops**: FRT P50/P90 vs SLA; Resolution %; Deflection %; Repeat contact % (48h); SLA breach rate.
**Quality**: CSAT/NPS; low‑confidence answer rate; HITL escalation rate.
**Adoption**: DAU/WAU/MAU by role; channel coverage; knowledge training cadence.
**Growth**: conversion by plan; referral contribution; retention & NRR (later).
**Reliability**: crash‑free, uptime, webhook delivery success.

**Event taxonomy (starter)**
- `onboarding.completed_step` (id), `channels.connected` (channel), `widget.published`
- `dashboard.kpi_viewed` (kpi), `alert.clicked` (kind), `quick_action.used` (kind)
- `conversation.assigned` (agent), `conversation.escalated` (reason), `conversation.resolved`
- `knowledge.trained` (sources), `policy.changed` (area), `billing.limit_hit` (metric)

---

## 35) Next Tickets — Merge Additions (2‑week sprint)
1. Design **gesture map** & spacing tokens; audit current screens for compliance.
2. Implement **offline indicators** + local queue; add Sync Center sheet.
3. Ship **API Keys** & **Webhooks** shells (read‑only metrics + test hook).
4. Add **Security & Privacy Center** shell (retention sliders + consent templates + audit viewer stub).
5. Add **Billing & Usage** page (plan card + usage bars + invoice list placeholder).
6. Enable **referral card** on Dashboard (copy link, track milestone 1).
7. Run **a11y/perf audit**; virtualize Conversations list; add debounced search.
8. Instrument **Day‑1 metrics** per taxonomy above; wire to internal analytics.

