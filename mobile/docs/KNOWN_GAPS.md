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
