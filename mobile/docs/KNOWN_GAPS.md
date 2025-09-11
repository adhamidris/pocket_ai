# KNOWN GAPS — Dashboard (UI-only)

- Personalizer flags are read from AsyncStorage with a stub; no settings UI yet.
- Industry pack selection is not exposed in Settings; defaults to `neutral`.
- KPI/Alerts values are demo data; no backend wiring.
- Deep links navigate to `Conversations` with basic `{ filter }` params; target screen may not yet consume filters fully.
- Offline banner and Sync Center are UI stubs; no actual NetInfo hook or queue implementation.
- Ask the Dashboard returns a canned summary; no NLQ/LLM integration.
- Accessibility: labels added for main touch targets; fuller audit pending.
- Performance: Components memoized; further profiling/virtualization pending for larger datasets.
- Analytics `track()` logs to console only; no external telemetry.
- i18n: New component strings are EN-only for now; AR translation deferred.
- Theming tokens are static; components primarily read ThemeProvider at screen usage.

## Help

Implemented (UI-only): Help Center tabs, Quickstart checklist, Tours engine, contextual help icons & Command Palette, empty-state guides, What’s New, micro-surveys, Feedback screen, Assistant hooks, localization/a11y/offline stubs, analytics events.

Deferred:
- Remote CMS for docs, sync/versioning
- Deep search & indexing across content
- Multimedia tutorials (video/GIF/interactive)
- Survey/feedback backend & dashboards
- Versioned tour configs & experiments
- Role-based checklists & progress sync
- Accessibility polish (full SR coverage, keyboard nav)
- Offline robustness (queues, retries, cache invalidation)
- Telemetry pipeline & privacy filtering
- Security & privacy hardening for help data
