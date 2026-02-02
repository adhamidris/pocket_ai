## Pocket AI – Business Flow (2025-09-10)

### 1) Product Vision (Phase 1 → Platform)
- On‑the‑go AI agents for businesses: fast setup, real outcomes, customizable to each business.
- Agents handle frontline conversations across social/comm channels; humans intervene when needed.
- UI‑first architecture so the backend can be tailored later without UI rework.

### 2) Core User Journey (B2C initial focus)
1. Register → Setup Wizard
   - Company info: name, country, industry, niches, business type, website
   - Agent: name, title, role
   - Knowledge: uploads (T&C, policies, docs, URLs, mission/vision)
2. Create an Agent (if not created in wizard)
   - Role/persona, tone, allowed actions (capabilities), knowledge sources selection
3. Get Unique Agent Link
   - Paste into WhatsApp Business auto‑replies, Instagram bio/links, Facebook page, etc.
4. Customers interact with the Agent
   - Agent responds using knowledge + rules; triage to human queues when needed
5. Dashboard Tailored to Setup
   - KPIs/alerts, queues, insights reflect the business’s industry, channels, and setup progress

### 3) Channels & Entry Points (Phase 1)
- Unique link → hosted chat portal (themeable per business)
- Copy/paste guidance for:
  - WhatsApp Business quick replies and off‑hours auto‑responses
  - Instagram bio and story link stickers
  - Facebook page link / Messenger greeting
  - Email signatures, website CTA

### 4) Dashboard (UI‑first) – Tailored Blocks
- Above‑the‑fold KPIs: Live conversations, First Response Time (FRT), Resolution Rate, CSAT/NPS
- Alerts: Urgent backlog, SLA at risk, Volume spikes
- Queues: Waiting, Unassigned, VIP, Escalated
- Quick Actions: Review urgent, Publish widget, Connect channels, Train from uploads
- Insights: Top intents, Repeat contacts, Peak times; Industry‑aware tiles (e.g., Orders/Returns for Retail)
- Setup Progress: compact checklist (connect channels, publish widget, train knowledge)

Componentization (UI‑only):
- <KpiTile />, <AlertCard />, <QueueStrip />, <QuickAction />, <InsightsSection />, <SetupProgress />
- “Personalizer” UI utility chooses which tiles to show based on stored setup (no backend needed yet)

### 5) Agent Personalization & Governance (UI surface)
- Capabilities configuration (allowed actions): refunds, appointment booking, info updates, etc.
- Guardrails: escalation conditions, PII masking, compliance copy blocks (from uploads)
- Tone/persona presets with preview and live playground (UI stub; backend later)

### 6) Knowledge & Data
- Sources: T&C, policies, mission/vision, URLs; later: FAQs, CRM notes
- UI affordances:
  - “Use in training” toggles per source
  - Quick retrain button + progress indicator (UI placeholder)

### 7) HITL (Human‑in‑the‑loop)
- Conversations screen supports: claim, assign, prioritize, quick replies, escalate
- Status chips and neutral UX (we’ve implemented text‑readable chips)

### 8) Theming & Branding
- Themeable chat portal (unique link): logo, colors, avatar style, bubble style
- Live theme preview + copyable embed CTA (UI stub)

### 9) Voice & Assistant UX (Phase 2 surface in UI)
- Voice toggle per channel; input mode switcher in chat portal
- “Ask your dashboard” embedded assistant: Today’s summary, Issues, Sentiment, Trends

### 10) Analytics & Reporting (UI plan)
- Time range selector with saved views
- Cards: FRT percentiles, SLA compliance, CSAT trend, Intent share, Deflection rate
- Drill‑downs: by channel, agent, intent, VIP cohort

### 11) Roadmap (Incremental, UI‑first)
- Milestone A: Tailored Dashboard MVP
  - Personalizer utility + prop‑driven KPI/Alerts/Queues/Quick Actions
  - SetupProgress + Connect/Publish/Train stubs
- Milestone B: Channel Guidance & Hosted Portal Theming
  - Copy snippets & “Test portal”
- Milestone C: Knowledge Manage & Retrain
  - Source toggles, dedupe, retrain stub
- Milestone D: Embedded Assistant & Voice UI Surface
  - Dashboard Q&A, voice input in portal
- Milestone E: Analytics Views & Saved Filters
  - Trends, breakdowns, saved queries

### 12) Data Shapes (UI contracts to guide backend later)
- DashboardKpi { id, label, value, delta, target? }
- AlertItem { id, severity, title, count, href }
- QueueItem { id, label, count, href }
- IntentItem { name, share, deflection }
- SetupStep { id, label, done, href }

### 13) Quality & UX Principles we’ve enforced
- Neutral containers, semantic accents only
- Safe‑area aligned headers
- Chip design: tiny colored dot + neutral background
- No tinted fills that obscure text
- Scrollable modals without visible scrollbars

### 14) Next UI tasks (no backend required)
- Add Personalizer utility & types; refactor Dashboard blocks to prop‑driven
- Add channel guidance CTA block on Dashboard
- Add Theme preview & save in chat portal config screen (stub route)
- Create “Train from uploads” UI with progress placeholder


