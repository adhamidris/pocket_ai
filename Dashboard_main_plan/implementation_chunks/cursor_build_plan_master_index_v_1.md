# Cursor Build Plan — MASTER INDEX (UI‑First) 
**Date:** 2025‑09‑10 • **Scope:** React Native Mobile App • **Mode:** UI/UX only (no backend). All modules are prop‑driven with local demo state.

> Use this index as your single source of truth. It links every ultra‑chunked plan, gives the **recommended build order**, lists **dependencies**, and tracks **Done‑Definitions** and **Known Gaps**.

---

## How to use this index
1) Open the target plan from the list below. 2) Copy each *Prompt Block* into Cursor step‑by‑step. 3) After each step, run the *Acceptance Checks*. 4) Return here to tick progress and jump to the next module.

---

## Recommended Build Order (Phased)
1. **Dashboard** — overview, KPIs, quick actions  
   _Doc:_ **Cursor_BuildPlan_Dashboard_v1.md**
2. **Conversations** — lists, filters, detail, actions  
   _Doc:_ **Cursor_BuildPlan_Conversations_v1.md**
3. **CRM** — contacts, profiles, timelines  
   _Doc:_ **Cursor_BuildPlan_CRM_v1.md**
4. **Agents** — AI/Human agents, routing, presence  
   _Doc:_ **Cursor_BuildPlan_Agents_v1.md**
5. **Channels & Publishing** — unique link, widget snippet, recipes  
   _Doc:_ **Cursor_BuildPlan_Channels_Publishing_v1.md**
6. **Knowledge** — sources, training, FailureLog, redaction  
   _Doc:_ **Cursor_BuildPlan_Knowledge_v1.md**
7. **Automations & SLAs** — rules, hours/holidays, SLA editor, simulator  
   _Doc:_ **Cursor_BuildPlan_Automations_SLAs_v1.md**
8. **Analytics** — KPIs, trends, cohorts, funnels, attribution  
   _Doc:_ **Cursor_BuildPlan_Analytics_v1.md**
9. **Settings & Theming** — branding, locale, notifications, publish theme  
   _Doc:_ **Cursor_BuildPlan_Settings_Theming_v1.md**
10. **Security & Privacy Center** — retention, consent, audit, access  
    _Doc:_ **Cursor_BuildPlan_Security_Privacy_v1.md**
11. **Billing & Plans** — plans, checkout, invoices, entitlements  
    _Doc:_ **Cursor_BuildPlan_Billing_Plans_v1.md**
12. **Hosted Chat Portal** — branded customer UI + preview  
    _Doc:_ **Cursor_BuildPlan_Hosted_Chat_Portal_v1.md**
13. **Assistant & Voice** — dashboard Q&A, overlay, mic/TTS  
    _Doc:_ **Cursor_BuildPlan_Assistant_Voice_v1.md**
14. **MCP / Action Pack** — agent actions, allowlists, approvals  
    _Doc:_ **Cursor_BuildPlan_MCP_ActionPack_v1.md**
15. **Help, Docs & Onboarding** — help center, tours, checklists  
    _Doc:_ **Cursor_BuildPlan_Help_Docs_Onboarding_v1.md**
16. **Cross‑Module QA & Release Candidate** — fixtures, audits, RC  
    _Doc:_ **Cursor_BuildPlan_QA_ReleaseCandidate_v1.md**

> **Source Vision:** _BusinessFlow10092025.md_ (master vision) + merged OPUS notes. Keep it handy for context.

---

## Dependency Map (Quick View)
- **Conversations** ← depends on: Dashboard (deep‑links), Agents (assignment variants)
- **CRM** ← depends on: Conversations (contact linking)
- **Agents** ← no hard deps; informs Conversations & Automations
- **Channels** ← depends on Settings (theme publish); powers Portal
- **Knowledge** ← standalone; informs Automations (intents) & Analytics (deflection)
- **Automations & SLAs** ← depends on Conversations/Agents; hooks into Analytics
- **Analytics** ← reads signals from Conversations/Automations/Knowledge/Channels
- **Settings & Theming** ← informs Portal and Channels
- **Security & Privacy** ← affects CRM, Portal, Analytics (PII masks)
- **Billing & Plans** ← gates premium surfaces across Knowledge/Automations/Analytics/Channels
- **Hosted Portal** ← depends on Settings (theme), Channels (link), Security (PII)
- **Assistant & Voice** ← cross‑links to Analytics/Conversations/Automations/Knowledge
- **MCP / Actions** ← ties to Assistant and Automations for "Then: Trigger action"
- **Help & Onboarding** ← overlays every module
- **QA & RC** ← validates everything end‑to‑end

---

## Per‑Module Shortcuts
Each plan contains **Paste‑Ready Micro Prompts**. Use these one‑liners to keep Cursor focused.

- **Dashboard:** "Create Dashboard shell with KPI tiles, alerts, quick actions, and setup progress."
- **Conversations:** "Build list with filters (Active/Waiting/Urgent/VIP), detail pane, reply composer, and assignment."
- **CRM:** "Create Contacts list/detail with timeline and masks honoring Privacy Modes."
- **Agents:** "Agent grid with presence, skill tags, and performance micro‑cards."
- **Channels:** "Widget snippet, unique link, and channel recipes for WhatsApp/IG/FB/Web."
- **Knowledge:** "Sources manager, TrainingCenter, FailureLog, and RedactionRules."
- **Automations:** "RuleBuilder, BusinessHours/Holidays, SLA editor, autoresponder, and Simulator."
- **Analytics:** "Types → chart primitives → Overview → Trends → Breakdowns → Cohorts/Funnels/Attribution → Saved Reports."
- **Settings/Theming:** "Brand editor with live preview, theme publish, locale/timezone, notifications."
- **Security/Privacy:** "Retention editor, Consent center, Audit log, Access controls, Residency, Exports/DSR."
- **Billing/Plans:** "Plan matrix + checkout, payment methods, invoices, usage & caps, dunning, entitlements gating."
- **Portal:** "Pre‑chat + consent, themed bubbles, attachments/voice mocks, handoff queue, CSAT/transcript."
- **Assistant/Voice:** "Dashboard Ask + global overlay, tool suggestions deep‑links, templates, pins, mic/TTS."
- **MCP/Actions:** "Catalog, Action detail + simulate, Allowlist builder, Approvals inbox, Packs, Monitor."
- **Help/Onboarding:** "Help Center, quickstart checklist, tours/coachmarks, command palette, micro‑surveys."
- **QA & RC:** "Fixtures, ComponentGallery, DeepLinkAudit, A11y/RTL/Theming, Offline, Perf, Telemetry, RC checklist."

---

## Progress Tracker (tick as you complete)
- [ ] Dashboard
- [ ] Conversations
- [ ] CRM
- [ ] Agents
- [ ] Channels & Publishing
- [ ] Knowledge
- [ ] Automations & SLAs
- [ ] Analytics
- [ ] Settings & Theming
- [ ] Security & Privacy Center
- [ ] Billing & Plans
- [ ] Hosted Chat Portal
- [ ] Assistant & Voice
- [ ] MCP / Action Pack
- [ ] Help, Docs & Onboarding
- [ ] QA & Release Candidate

---

## Done‑Definitions (quick copy)
Every plan ends with a **Done‑Definition v0**. Before moving on, ensure the module meets its v0 definition.
- Find the **Done‑Definition** at the bottom of each module plan.

---

## Known Gaps (backend contracts to wire later)
When you finish UI, open the module’s KNOWN_GAPS doc:
- `mobile/docs/KNOWN_GAPS_Analytics.md`
- `mobile/docs/KNOWN_GAPS_Settings.md`
- `mobile/docs/KNOWN_GAPS_Security.md`
- `mobile/docs/KNOWN_GAPS_Billing.md`
- `mobile/docs/KNOWN_GAPS_Portal.md`
- `mobile/docs/KNOWN_GAPS_Assistant.md`
- `mobile/docs/KNOWN_GAPS_Actions.md`
- `mobile/docs/KNOWN_GAPS_Help.md`
- `mobile/docs/KNOWN_GAPS_RC.md`

---

## Today’s Sprint Suggestions
1) Finish **Channels & Publishing** → then immediately preview in **Hosted Portal** (fast win).  
2) Ship **Automations & SLAs** basic flow → unlocks real value in **Analytics** and **Assistant**.  
3) Add **Security & Privacy** masks early → avoids rework across CRM/Portal.

---

## Notes
- Timezone: Africa/Cairo — use absolute dates in UI copy where relevant.
- This index is living. As you refine modules, update the build order or dependencies here.
