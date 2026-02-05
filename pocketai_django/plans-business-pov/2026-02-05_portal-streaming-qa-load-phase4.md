# Phase 4 — QA + Load (Portal Streaming Engine)

Date: 2026-02-05

## Scope

Validate the Phase 1–3 streaming changes end-to-end:
- Caret (`|`) attaches correctly during streaming (leaf blocks only, no jumping to containers).
- No “force-drain dump” bursts at tool boundaries / finalization.
- Rich blocks remain styled during streaming (code blocks + markdown tables).
- SSE endpoints behave under moderate concurrent load (no regressions).

## Manual QA (local, deterministic)

Environment:
- `PORTAL_TURN_EXECUTION_MODE=worker`
- `PORTAL_TURN_EVENT_BUS=redis`
- `PORTAL_SESSION_EVENT_BUS=redis`

Steps executed:
1) Start server: `./.venv/bin/python manage.py runserver 127.0.0.1:8000 --noreload`
2) Open portal: `http://127.0.0.1:8000/phase4-load-corp/phase4-agent/`
3) Send a prompt that produces long markdown lists and code blocks, then observe the caret mid-stream.
4) Send a prompt that produces a markdown table and confirm styling (wrapper + table classes) render correctly.
5) Cancel a long streaming list mid-stream via the Stop button and confirm partial output remains without a “dump”.

Findings:
- Caret class `.portal-stream-active` appears on **leaf blocks only** during streaming:
  - Observed on `LI[data-block-type="list_item"]` (parent `UL[data-block-type="list"]`).
  - Observed on `PRE[data-block-type="code_block"]` / inner code target.
  - Not observed on container blocks (`UL/OL` list containers).
- Table styling is applied during streaming:
  - `table.className === "w-full border-collapse text-sm"`
  - table wrapper present with `rounded-xl border ...` classes.
- Cancel/resume behavior:
  - Cancelling a long list mid-stream leaves a partially typed final list item (expected).
  - Turn status in DB transitions to `cancelled`.
  - No “end-of-turn dump” observed after cancellation.

## SSE Load Test (local)

Tool: `testing/portal_sse_load_test.py`

### Session stream (ok baseline)
Command:
`./.venv/bin/python testing/portal_sse_load_test.py --base-url http://127.0.0.1:8000 --session-token <token> --clients 50 --duration 10 --stream session`

Result (50 clients / 10s):
- ok: 50, failed: 0
- first_event_ms: mean=29 p50=22 p95=76 p99=89

### Turn stream (ok baseline)
Command:
`./.venv/bin/python testing/portal_sse_load_test.py --base-url http://127.0.0.1:8000 --session-token <token> --turn-id <uuid> --clients 20 --duration 10 --stream turn`

Result (20 clients / 10s):
- ok: 20, failed: 0
- first_event_ms: mean=46 p50=42 p95=84 p99=89

### Note: Postgres connection pressure
When stacking multiple load runs back-to-back (without waiting for prior SSE generators to unwind), the local Postgres
`max_connections` limit can be reached and produce `500` errors on `/api/chat/events/`.

This is expected locally because:
- DB connections are persistent (`DB_CONN_MAX_AGE=600` in root `.env`).
- Each long-lived SSE request can hold a DB connection even when the live bus is Redis.

Production implication: if we want thousands of SSE clients (gunicorn gevent + `GUNICORN_WORKER_CONNECTIONS`), we should
ensure SSE streaming does **not** consume one Postgres connection per client (e.g., explicit DB-connection release inside
SSE generators, and/or a pooler like PgBouncer).

