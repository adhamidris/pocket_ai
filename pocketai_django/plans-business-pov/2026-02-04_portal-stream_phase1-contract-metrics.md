# Portal Streaming Phase 1 — Contract + Metrics

## Plan (Engineering)

### Goal
Freeze the current portal streaming protocol **as a contract**, and add lightweight **observability** so we can safely refactor the transport (Redis / workers) in later phases without breaking the UI experience.

### Deliverables
1. **Protocol spec (v1)**
   - Document the exact SSE event names and JSON payloads that the portal frontend consumes today.
   - Cover both:
     - Turn stream: `GET /api/chat/turns/<turn_id>/events/`
     - Session stream: `GET /api/chat/events/`

2. **Contract tests**
   - Add tests that parse the SSE stream and assert:
     - event names (`turnEvent`, `statusChanged`, …)
     - required payload keys and shapes
     - monotonic `seq` behavior (turn stream)
   - These tests become a “tripwire” during Phase 2+ refactors.

3. **Metrics (no user-facing change)**
   - Turn runner summary metrics:
     - time-to-first-event (backend)
     - total streamed event count per turn
     - DB time spent appending events (aggregate)
   - Turn SSE connection metrics:
     - time-to-first-event-sent (server)
     - events sent
     - keepalives sent

### Non-Goals (Phase 1)
- No transport refactor yet (Redis/WebSockets).
- No changes to frontend UI or event ordering.
- No changes to tool approval flows / sub-agent surfaces.

### Success Criteria
- Docs match the real implementation (no drift).
- Tests reliably fail if the SSE contract changes unintentionally.
- Logs/metrics allow us to quantify “time to first token” and event volume per turn.

---

## Business POV (Why This Matters)

### What improves for end-users now?
- Nothing visible yet; Phase 1 is groundwork. The goal is to enable safe scaling work without regressions.

### What improves for the business?
- **Lower launch risk:** later performance refactors won’t break the live portal UI because we have a locked contract + tests.
- **Faster iteration:** we can change backend internals confidently, measuring impact turn-by-turn.

### Scenarios
1. **High traffic launch day**
   - Risk today: streaming becomes “laggy” under load.
   - Phase 1 outcome: we can measure where the lag is (first token delay vs throughput vs DB overhead) and refactor safely.

2. **Tool-heavy conversations**
   - UX depends on ordered tool cards + approvals interleaving with text.
   - Phase 1 outcome: contract tests ensure ordered tool lifecycle events remain compatible during refactor.

3. **Mobile / flaky connections**
   - Reconnect behavior must not corrupt message ordering.
   - Phase 1 outcome: documented SSE resume semantics (`seq` / `Last-Event-ID`) are explicit and testable.

### How we’ll measure success
- P50/P95 “time to first token” decreases in Phase 2+.
- Lower DB write volume per message in Phase 3+.
- No frontend regressions (contract tests stay green).

