# Portal Streaming Phase 4 — Move `/api/chat/events/` Off DB Polling

## Plan (Engineering)

### Goal
Eliminate constant Postgres polling for the portal **session** stream (`/api/chat/events/`) by switching to a Redis Streams event bus:
- Send a **DB snapshot once** on connect (runs, requests).
- Stream **incremental updates from Redis** as they happen.

This keeps Tasks/Inbox/Voice updates live while reducing DB load under many concurrent SSE connections.

### Deliverables
1. **Session event bus toggle**
   - `PORTAL_SESSION_EVENT_BUS=postgres|redis` (default: `postgres`)
   - `redis`: SSE blocks on `XREAD` instead of polling DB tables
   - `postgres`: keep legacy behavior (safe fallback)

2. **Redis Streams keys**
   - Per-conversation stream: `portal:session:conversation:<conversation_id>:events`
   - Per-agent request stream: `portal:session:agent:<agent_profile_id>:requests`
   - TTL enforced on each stream key (default: 3600s) so memory is bounded.

3. **Publishers (best-effort, on-commit)**
   - `AgentRunEvent` create → publish `agentRunEvent` to the conversation stream.
   - `ConversationMessage` create (source in `agent_run|voice_call`) → publish `conversationMessage` to the conversation stream.
   - `AgentRequest` save → publish `agentRequestEvent` to both involved agents' request streams.
   - Voice transcript updates publish `voiceCallTranscript` to the conversation stream (and keep cache fallback).

4. **SSE endpoint reads from Redis**
   - `/api/chat/events/` reads from Redis Streams when enabled.
   - Preserves existing event names + payload schemas (`statusChanged`, `agentRunEvent`, `agentRequestEvent`, `conversationMessage`, `voiceCallTranscript`, `heartbeat`).
   - If Redis is unavailable at runtime, the endpoint degrades to the legacy Postgres polling loop.

### Non-Goals (Phase 4)
- No migration of turn streaming (already in Phase 3).
- No removal of DB event tables (Phase 5).
- No major frontend changes.

### Success Criteria
- Under high concurrency, DB read load drops significantly (no per-connection polling loops).
- Tasks/Inbox/Voice remain live with equal or better responsiveness.
- Redis outages cause degraded behavior (polling fallback), not a broken portal.

---

## Business POV (Why This Matters)

### What improves for end-users?
- **Less “laggy” Tasks/Inbox updates** when many users are connected.
- **More consistent live UI** for sub-agent runs and voice transcripts.

### What improves for the business?
- **Scales to large user counts** without needing to scale Postgres mainly for polling traffic.
- **Predictable costs**: Redis handles short-lived fanout; Postgres remains durable truth.

### Scenarios
1. **Hundreds/thousands of open portals**
   - Old: each connection polls the DB constantly → DB becomes the bottleneck.
   - New: connections block on Redis → DB stays reserved for actual writes and snapshots.

2. **Mobile reconnects**
   - Snapshot-on-connect ensures UI rebuilds correctly.
   - Incremental updates resume smoothly while connected.

3. **Redis downtime**
   - The portal falls back to DB polling so the product still works (degraded, not broken).

