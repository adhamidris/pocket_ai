# Portal Streaming Phase 3 — Redis Streams Event Bus (DB Source Of Truth)

## Plan (Engineering)

### Goal
Make portal streaming feel “instant” under load by moving **live event delivery** off Postgres and onto **Redis Streams**, while keeping Postgres as the **source of truth** (turn history, approvals, final persisted message, and replay fallback).

### Deliverables
1. **Publish turn events to Redis (best-effort)**
   - When `append_turn_event()` writes to Postgres, it also `XADD`s to a per-turn Redis Stream **after commit** (`transaction.on_commit`).
   - Stream key: `portal:turn:<turn_id>:events` (configurable prefix).
   - Redis Stream ID uses the existing turn `seq` (e.g. `1-0`, `2-0`, …) so `Last-Event-ID` resume semantics stay trivial.

2. **Turn SSE reads from Redis**
   - `GET /api/chat/turns/<turn_id>/events/` reads from Redis via `XREAD BLOCK`.
   - Keeps the SSE contract identical (`event: turnEvent`, `id: seq`, same JSON envelope).

3. **Replay + safety**
   - Postgres remains the truth:
     - If Redis is down/unavailable, SSE degrades to the Postgres path (existing behavior).
     - If Redis retention expired (reconnect after TTL), SSE backfills missing events from Postgres using `seq` and continues.

4. **Bound Redis memory**
   - Per-turn streams get a TTL (default: 3600s) refreshed on each event.
   - Retention window is configurable to 10–60 minutes depending on expected reconnect patterns.

5. **Operational knobs**
   - `PORTAL_TURN_EVENT_BUS=postgres|redis`
   - `PORTAL_TURN_EVENT_BUS_REDIS_STREAM_TTL_SECONDS`
   - `PORTAL_TURN_EVENT_BUS_REDIS_STREAM_PREFIX`

### Non-Goals (Phase 3)
- No changes to the frontend contract (Phase 1 contract remains fixed).
- No migration of `/api/chat/events/` yet (that’s Phase 4).
- No removal of `PortalTurnEvent` DB writes yet (that’s Phase 5).

### Success Criteria
- Lower DB read load from streaming connections (no per-turn polling/list queries).
- Fewer “lag spikes” under concurrency (Redis is optimized for fanout + blocking reads).
- Identical UI/UX (ordered blocks/tool cards/approvals preserved by the same event schema).

---

## Business POV (Why This Matters)

### What improves for end-users?
- **Faster streaming**: tokens/blocks show up with less jitter because the browser is reading from Redis instead of repeatedly querying Postgres.
- **Better reliability under traffic**: even with lots of concurrent streams, Redis handles the fanout more smoothly than DB polling.

### What improves for the business?
- **Scales to large users** without needing to “over-upgrade” Postgres just to support streaming read load.
- **Clear cost separation**:
  - Postgres: durable truth + final messages + approvals.
  - Redis: short-lived live delivery (bounded by TTL).

### Scenarios
1. **Launch day traffic spike**
   - Many users open SSE streams at once.
   - Phase 3 keeps Postgres from becoming the bottleneck for read/polling load.

2. **Mobile reconnects / flaky networks**
   - Clients reconnect with `Last-Event-ID`.
   - Redis Streams provides a fast resume path within the retention window; outside it, Postgres backfills safely.

3. **Cost control**
   - You can tune TTL to cap Redis memory (e.g. 10–60 minutes).
   - You avoid scaling Postgres primarily for live-stream read throughput.

