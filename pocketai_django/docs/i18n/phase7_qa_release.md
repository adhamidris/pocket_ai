# Phase 7: Web i18n QA + Release Checklist (EN/AR)

## Release Gate

- Feature flag: `WEB_I18N_ARABIC_ENABLED`
- Default: `true` (Arabic enabled)
- Rollback mode: set `WEB_I18N_ARABIC_ENABLED=false` to run English-only web language options.

## Automated Coverage (Added in Phase 7)

- Language switch endpoint sets cookie and applies Arabic RTL rendering.
- `?lang=ar|en` direct links switch language instantly and persist across refresh.
- Authenticated language changes persist into business metadata preferences.
- Root HTML language direction (`dir`) is asserted for LTR/RTL.
- Prompt language policy prioritizes selected UI language over message-script detection.

## Manual RTL Visual QA Matrix

Run in desktop and mobile breakpoints:

- Public pages: `/`, `/login/`, `/register/`, `/privacy/`, `/terms/`
- App shell/pages: `/dashboard/`, `/dashboard/customers/`, `/dashboard/agents/`, `/dashboard/leads/`, `/dashboard/cases/`
- Platform pages: `/dashboard/knowledge/`, `/dashboard/knowledge/visualizer/`, `/dashboard/integrations/`, `/dashboard/mcp/`, `/dashboard/controls/`, `/dashboard/voice/`
- OAuth popups: provider callback popup windows

Checkpoints:

- Sidebar and nav placement, dropdown alignment, and button groups in RTL
- Table headers/cells, pagination controls, and badge placement in RTL
- Form field labels, helper/error text alignment, and modal footer action ordering
- Icon direction for directional glyphs (chevrons/arrows) in RTL
- Toasts and transient notices positioning/alignment

## Native Arabic Linguistic Review

- Reviewer confirms MSA-first tone and terminology consistency.
- Review includes primary flows: onboarding, dashboard navigation, chat portal prompts, case/lead language.
- Track corrections in translation catalog (`locale/ar/LC_MESSAGES/django.po`) before production cut.
