# Phase 6 — Production Hardening + Load Testing (Portal Streaming)

Date: 2026-02-04

## Goal

Make the portal streaming stack robust and scalable for **large concurrency**:
- Many long-lived SSE connections (session stream + turn stream).
- High token rates without overwhelming Redis/CPU/network.
- Redis outages degrade gracefully (not broken UX).

## What Changed

### 1) Turn Backpressure / Coalescing (Server-Side)

Problem:
- Token streaming can emit thousands of tiny `block_delta` events, driving up:
  - Redis writes (XADD)
  - SSE framing overhead
  - CPU and bandwidth usage

Fix:
- Coalesce adjacent `block_delta` ops in `PortalTurnEventBuilder` and flush:
  - every `PORTAL_TURN_DELTA_FLUSH_INTERVAL_MS` (default 50ms), or
  - once `PORTAL_TURN_DELTA_FLUSH_MAX_OPS` is reached (default 60 ops)

Result:
- Same UI behavior (op-based rendering, ordered tool cards).
- Fewer events, lower overhead.

Settings:
- `PORTAL_TURN_COALESCE_BLOCK_DELTAS` (default true)
- `PORTAL_TURN_DELTA_FLUSH_INTERVAL_MS` (default 50)
- `PORTAL_TURN_DELTA_FLUSH_MAX_OPS` (default 60)

### 2) Redis Stream Memory Bounding

Problem:
- Per-turn Redis Streams can grow large during long responses.

Fix:
- Add approximate maxlen trimming for per-turn streams.

Settings:
- `PORTAL_TURN_EVENT_BUS_REDIS_STREAM_MAXLEN` (default 20000)
- TTL still applies: `PORTAL_TURN_EVENT_BUS_REDIS_STREAM_TTL_SECONDS`

### 3) SSE Keepalives When Waiting For First Token

Problem:
- A “block forever” `XREAD` can prevent keepalives on slow first-token turns.
- Proxies may close quiet connections.

Fix:
- Turn SSE Redis `XREAD` uses `block=keepalive_seconds*1000` (no infinite blocking).

### 4) Graceful Degraded Mode (Redis Down / No DB Event Log)

Problem:
- With Phase 5 (`PORTAL_TURN_EVENT_LOG_MODE=minimal|off`), per-token DB logs are gone.
- If Redis is down, the turn stream would otherwise have no live events.

Fix:
- If the turn is `FINALIZED` and we haven't delivered `turn_persisted`,
  the SSE endpoint will fetch the persisted `ConversationMessage` and emit
  a **synthetic** `turn_persisted` event (single final payload).

Result:
- “Degraded but not broken”: users still get the final answer, even if live token
  streaming is unavailable.

## Server Setup (SSE Scale)

The previous `gunicorn sync` setup does not scale with large open SSE counts.
For production, use an async-friendly worker class.

Recommended (WSGI + greenlets):
- `GUNICORN_WORKER_CLASS=gevent`
- `GUNICORN_WORKER_CONNECTIONS=5000` (tune per instance size)

Note:
- This preserves the existing synchronous `StreamingHttpResponse` behavior.
- Works well for many open SSE connections.

## Load Testing

Added a lightweight client-side load test script:
- `pocketai_django/testing/portal_sse_load_test.py`

Run against a staging instance to measure:
- time-to-first-event
- disconnect rate
- aggregate events/sec

Example:
```bash
python pocketai_django/testing/portal_sse_load_test.py \
  --base-url http://localhost:8000 \
  --session-token <SESSION_TOKEN> \
  --stream session \
  --clients 500 \
  --duration 60
```

## Next (Optional)

- Add a dedicated synthetic turn producer for deterministic turn-stream load tests.
- Consider per-message “checkpoint” persistence for crash-resume guarantees if needed.

