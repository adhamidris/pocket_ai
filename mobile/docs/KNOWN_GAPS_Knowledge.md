# Knowledge Module — Known Gaps (UI-only build)

This app currently implements a UI-only Knowledge experience. The following items are intentionally deferred and/or stubbed:

- Real training pipeline: no backend jobs, embeddings, or vector store; `TrainingCenter` simulates progress.
- URL crawler: no fetch, robots handling, sitemaps, or content extraction; `AddUrlSource` estimates size only.
- File processing: uploads are mock-picked with fake metadata; no OCR, chunking, or parsing.
- Coverage & drift: coverage metrics and drift warnings are randomized; no live measurement or detectors.
- Failure log: items are synthetic; attach/resolve flows are UI stubs with alerts only.
- Test harness: expected vs actual is canned; no LLM or retrieval calls.
- Source priority & scope: ordering and enable toggles affect only local state; no persistence.
- Redaction rules: regex preview is local; no runtime inference-time masking applied.
- Versions: timeline, diff chips, and restore are placeholders; no snapshot store or true diffs.
- Offline & sync: offline banner and queue clocks are visual only; no persistent queue or retries.
- Analytics: events log to console in dev; no pipeline export.
- Cross-app hooks: navigation is wired, but no data linking or filtering beyond local params.
- Multi-tenant scopes, RBAC: not implemented; scopes are local enums only.
- Long-running job toasts and retries: not implemented; success/failure are simulated.
- Pagination/virtualization at scale: lists are optimized for demo sizes; no pagination API.

These gaps will be addressed in backend integration and subsequent phases.
