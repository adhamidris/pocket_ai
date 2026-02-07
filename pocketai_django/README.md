# PocketAI Django

This is the Django backend + web portal for PocketAI (Chat Portal, RAG, ingestion, admin).

Status highlights:
- MCP orchestrator only (legacy orchestration is deprecated).
- Agentic read v2 is enabled via `MCP_AGENTIC_READ_V2_ENABLED=true` in the root `.env`.
- Voice stack is **dev-only** right now (single Phase 1+ runtime path).
- Platform is **beta**.

## Quickstart

```sh
cd pocketai_django
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 127.0.0.1:3000
```

## Local dev processes (Portal + Ingestion + Sub-agents + Voice Calls)

Run these in separate terminals (with `cd pocketai_django` and `.venv` activated):

```sh
# Web / Portal
.venv/bin/python manage.py runserver 127.0.0.1:3000

# Knowledge ingestion worker (required for uploads)
.venv/bin/python manage.py process_knowledge_ingestion --watch

# Knowledge integrations sync (Google Drive, etc.)
.venv/bin/python manage.py sync_knowledge_integrations --watch --sleep 300

# Sub-agents background worker (Tasks panel)
.venv/bin/python manage.py process_agent_runs --watch

# Voice calls: queue worker
.venv/bin/python manage.py voice_call_worker --watch

# Voice calls: Twilio Media Streams WebSocket server
.venv/bin/python manage.py voice_ws_server --port 8081

# Voice calls: post-call processing (transcript/summary/recording ingest)
.venv/bin/python manage.py voice_post_call_worker --watch
```

### ngrok (voice dev-only, one session, two tunnels)

If you’re testing Twilio webhooks + the voice WS server locally, run both tunnels in a single ngrok session:

1) Add this to your ngrok config file (usually `~/.config/ngrok/ngrok.yml`):

```yaml
version: "2"
tunnels:
  web:
    proto: http
    addr: 3000
  voice_ws:
    proto: http
    addr: 8081
```

2) Start ngrok:

```sh
ngrok start --all
```

## Docs

- `AGENTS.md` — working agreement for AI coding agents (read first)
- `docs/product/` — business & SaaS docs (start with `docs/product/saas_brief.md` and `docs/product/technical.md`)
- `docs/architecture/chat_portal_content_blocks.md` — chat portal content blocks contract
- `docs/architecture/` — RAG/LLM flow docs
- `docs/ops/` — rollout/runbooks/load testing
- `docs/ops/mcp_grouped_retrieval_observability.md` — grouped retrieval telemetry (logs, dashboard queries, alerts)
- `docs/ops/datadog_mcp_grouped_retrieval_dashboard.json` — importable Datadog dashboard
- `docs/prompts/` — prompt catalogs

## Notes

- Environment variables are loaded from the repository root `.env` via `pocketai/env.py`.
- MCP chat provider uses `MCP_PROVIDER`; non‑MCP flows (voice/post‑call) use `LLM_PROVIDER` if set (otherwise auto‑pick based on available keys).
- Do not commit virtualenvs or runtime artifacts (`.venv/`, `var/`).
