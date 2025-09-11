# KNOWN GAPS — Assistant & Voice (UI-only demo)

This mobile implementation is UI-first and intentionally mocks backend behavior. Deferred items below are out of scope for this demo and should be addressed for production:

- Real LLM calls: Replace local stubs with API client, error handling, retries, tracing.
- Streaming partials: Token streaming for answers with incremental rendering and abort controls.
- RAG citations: Grounding with datastore connectors; verifiable, linkable citations per chunk.
- Voice SDK integration: Mic permissions, wake word, ASR/TTS streaming, edge fallback.
- Session memory: Cross-screen assistant memory store with TTL, scoping, and clear controls.
- Multilingual NLU: Locale-aware intent detection, translation pipeline, RTL adjustments.
- Function execution safety: Tool registry, parameter schemas, dry-runs, guardrails, auth checks.
- Hallucination detection: Consistency checks, self-critique prompts, low-confidence flags.
- Confidence UI: Calibrated scores, uncertainty phrasing, “needs verification” badges.
- Feedback (thumbs): Per-chunk rating, freeform feedback, optional supervision queue.

Notes:
- All network calls, navigation side-effects, and analytics are mocked.
- Privacy/PII masking is UI-only; actual redaction must occur server-side.
- Rate-limit and offline states simulate visuals only.
