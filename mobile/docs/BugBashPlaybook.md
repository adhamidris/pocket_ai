## Bug Bash Playbook — Mobile RC

Date: TBA • Scope: React Native app (UI-only) • Devices: Small (SE/compact), Medium (iPhone 13/Pixel 6), Large (tablet/landscape)

### Roles
- **Facilitator**: Kicks off, assigns scenarios, keeps time, resolves blockers, closes session.
- **Scribe**: Owns the bug board, triages duplicates, ensures reports meet template, exports summary.
- **Fix captain**: On-call to hotfix trivial issues on the spot or tag owners for follow-ups.

### Ground Rules
- Use seeded demo profiles via QA FixtureSwitcher (Baseline/High Volume/Low Data/Edge Cases).
- Run each scenario on 3 device classes and 2 themes (light/dark); include RTL and high-contrast checks.
- Toggle privacy modes (Hide PII, Anonymize analytics) during relevant scenarios.
- Use I18nAudit/AccessibilityAudit/ThemeAudit for focused checks where noted.

## Scenarios Matrix (by module × device)

| Module | Scenario | Small | Medium | Large |
|---|---|---:|---:|---:|
| Dashboard | KPIs load, Alerts open deep-links, Quick Actions navigate | ✅ | ✅ | ✅ |
| Conversations | Filter, search, open thread; empty/loaded/error states | ✅ | ✅ | ✅ |
| CRM | VIP/Consent filters, A–Z jump, PII masking toggle | ✅ | ✅ | ✅ |
| Agents | List, detail status changes, policies | ✅ | ✅ | ✅ |
| Knowledge | Failure Log, Training Center, add sources | ✅ | ✅ | ✅ |
| Channels | Widget Snippet, Recipe Center, Portal Preview | ✅ | ✅ | ✅ |
| Automations | SlaEditor, RuleBuilder (prefilled), Simulator | ✅ | ✅ | ✅ |
| Analytics | Range/filters, Trends, anonymized badge | ✅ | ✅ | ✅ |
| Settings | Theme publish, locale/timezone, notifications | ✅ | ✅ | ✅ |
| Security/Privacy | Privacy Center, Data Residency, Audit Log | ✅ | ✅ | ✅ |
| Billing | PlanMatrix, Checkout (cancel), Invoices | ✅ | ✅ | ✅ |
| Assistant/Voice | Overlay open, ask/answer, tool chips | ✅ | ✅ | ✅ |
| Hosted Portal | Prechat, consent, queue, CSAT, transcript | ✅ | ✅ | ✅ |

Notes:
- For each ✅, record issues found per device class; verify back stack sanity and no dead-ends.

## Severity & Priority Rubric

### Severity (S)
- **S1 Blocker**: App crash, hard navigation dead-end, P0 feature unusable, security/privacy risk.
- **S2 Major**: Critical flow degraded (bad data, broken deep-link), severe a11y/RTL issues, wrong gating.
- **S3 Minor**: UI misalignment, contrast below threshold, copy issues, non-blocking visual bugs.
- **S4 Polish**: Nits, micro-animations, spacing, minor iconography.

### Priority (P)
- **P0**: Must fix before RC cut.
- **P1**: Fix in RC if low risk, else first patch.
- **P2**: Backlog for minor release.
- **P3**: Consider for future polish.

## Reporting Template

Copy/paste per issue (one issue per report):

```
Title: [Module] concise summary
Severity/Priority: S?/P?
Device: [Small/Medium/Large]  OS: [iOS/Android]  App build: [hash/date]
Theme: [Light/Dark/High‑contrast]  Locale/RTL: [en/ar + RTL on/off]
Fixture profile: [Baseline/HighVolume/LowData/EdgeCases]
Privacy modes: [Hide PII on/off, Anonymize analytics on/off]

Steps to Reproduce:
1. …
2. …

Expected:
- …

Actual:
- …

Artifacts: screenshots/screen recordings (annotated), console track() logs (if telemetry-related)
Related routes/params: [e.g., Analytics → Trends]
Notes: any hypotheses or quick fixes
```

## Triage & SLA
- S1/P0: acknowledge in 15m; fix/decision within 24h.
- S2/P1: acknowledge in 2h; assign within 24h; fix in RC if low risk.
- S3/P2: acknowledge in 24h; schedule next minor.
- S4/P3: batch for polish; reassess post‑RC.

Escalation: Facilitator → Fix captain → Module owner. Update status on board; Scribe keeps the single source of truth.

## Known Tricky Areas (focus checks)
- Lists under RTL (A–Z jump, virtualization, overscroll indicators).
- High‑contrast charts and small label readability.
- Offline banners and queued edits (retry icons, Sync Center counts).
- i18n pseudo‑locale clipping and long strings in buttons/chips.
- Portal consent & queue states; assistant cooldown and tool chips.
- Deep‑link back stack (no duplicate screens, correct params persisted).
- Entitlement gating: UpsellInline shown on Free; unlock after upgrade; re‑lock on downgrade.
- Telemetry double‑fires: ensure one track() per action; no PII when Hide PII is on.

## Session Flow (Suggested)
1) Kickoff (5m): roles, scope, boards, builds.
2) Round 1 (30m): Dashboard/Conversations/CRM/Channels.
3) Round 2 (30m): Knowledge/Automations/Analytics.
4) Round 3 (30m): Settings/Security/Billing/Portal/Assistant.
5) Wrap (15m): Triage, assign P0/P1, schedule fixes.

## Checklists & Helpers
- Use QA screens: ComponentGallery, DeepLinkAudit, AccessibilityAudit, I18nAudit, ThemeAudit, OfflineAudit, PerfAudit, StateCoverageAudit, TelemetryAudit, EntitlementAudit, SecPrivacyAudit, ScriptedWalks.
- Record device screenshots for each module (Small/Medium/Large) in the board.


