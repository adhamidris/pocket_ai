# Portal Streaming Phase 2 — Turn Worker Pool (DB-Leased)

## Plan (Engineering)

### Goal
Move portal turn execution out of the web server process (daemon threads) into a **dedicated worker pool** that can scale horizontally, while keeping the **exact same SSE/UI contract** (ordered blocks, tool cards, sub-agent updates, approvals).

### Deliverables
1. **Execution mode flag**
   - `PORTAL_TURN_EXECUTION_MODE=thread|worker` (default: `thread`)
   - `thread`: legacy `run_turn_background()` daemon threads (dev-only).
   - `worker`: enqueue turns in Postgres and process via workers.

2. **DB-leased queue**
   - `PortalTurnProcessingService`:
     - claims turns with `SELECT ... FOR UPDATE SKIP LOCKED`
     - uses `lease_expires_at` to avoid double-processing
     - increments `attempt_count` on claim
     - runs in correct `tenant_context(business_id)` for RLS enforcement

3. **Worker runner**
   - `manage.py process_portal_turns --watch`
   - Designed to run multiple replicas safely (leases + `skip_locked`).

4. **Operational wiring (local/staging)**
   - Docker compose adds `turn_worker` service.
   - Web sets `PORTAL_TURN_EXECUTION_MODE` and worker lease env vars.

5. **Safety for long approval waits**
   - While waiting on tool approvals, refresh the portal turn lease periodically so a second worker does not pick up the same turn.

### Non-Goals (Phase 2)
- No change to portal SSE payloads (Phase 1 contract remains the source of truth).
- No websocket/redis transport migration yet.
- No full “turn resume” state machine (hard crash mid-stream can still require user retry).

### Success Criteria
- Portal turns no longer depend on the web process lifetime.
- Running N worker replicas increases throughput without duplicate executions.
- UX remains identical: ordered streaming blocks, tool lifecycle events, approvals, and agentic structure.

---

## Business POV (Why This Matters)

### What improves for end-users?
- **More consistent streaming under load:** web servers stop doing heavy LLM work; they just serve HTTP/SSE.
- **Fewer “stalls” during traffic spikes:** worker pool scales independently.

### What improves for the business?
- **Lower production risk & cost control:** scaling web servers for HTTP is cheaper than scaling them to also run long-lived agent turns.
- **Predictable scaling:** you can add worker replicas as user count grows without touching the UI contract.

### Scenarios
1. **Launch with large users**
   - Add more `turn_worker` replicas to increase concurrent turn throughput.
   - Keep web replicas focused on request handling + streaming.

2. **Tool-heavy conversations**
   - Tool approvals can block for minutes; lease refresh prevents accidental double-processing.
   - UI stays fully ordered because the event log remains the stream source.

3. **Cost management**
   - Separate worker autoscaling from web autoscaling.
   - Reduce “overprovision web” strategy just to survive long agent runs.

