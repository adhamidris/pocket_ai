# KNOWN GAPS — Conversations (UI-only)

- Data is locally mocked; no backend/API wiring yet.
- Deep links support `{ filter }` only; channel/intent/tag deep links are placeholders.
- FilterBar dropdowns for Channel/Intent/Tag are non-functional stubs.
- Assignment/Tag/Priority/Resolve/Escalate actions are UI-only; no persistence.
- Offline state is simulated; no NetInfo integration and no real action queue.
- Sync Center button in thread header is a stub; opens no sheet yet from thread.
- Intent detection/low-confidence flags are mocked; no real model signals.
- Accessibility reviewed for main controls; full audit pending across all list/thread elements.
- Performance tuned with FlatList and debounced search; further profiling on devices pending.
- i18n: New strings are EN-only; AR translations deferred per plan.
