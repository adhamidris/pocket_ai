API App (apps/api)
=================

Purpose
-------
This app exposes HTTP endpoints for the web portal and admin dashboard:
chat streaming, registration, knowledge documents, integrations, cases,
customers.

Directory Map
-------------
- urls.py
  Public API routes.
- chat_portal.py
  Streaming chat endpoints + orchestration dispatch.
- views.py
  REST-style endpoints for agents, registrations, documents, integrations,
  cases, and customers.
- tests/
  API tests for chat, knowledge, integrations, and portal runtime.

Key Flows
---------
1) Chat turns (event-sourced)
   /api/chat/turns/ -> create turn, then stream from /api/chat/turns/<turn_id>/events/.

2) Portal bootstrap
   /api/chat/portal/sessions/ -> ChatPortalService.bootstrap_session().

3) Knowledge documents
   /api/knowledge/documents/* -> document list/detail/scrape/preview/download.

4) Integrations
   /api/integrations/google/* -> OAuth + resource discovery + sync.

Configuration Touchpoints
-------------------------
- RAG_USE_MCP_ORCHESTRATOR (legacy path is deprecated; MCP is the active runtime)
- MCP_MAX_TOOL_ITERATIONS / MCP_*_CALLS_PER_MINUTE

Quick Start (Dev)
----------------
- Chat session bootstrap:
  POST /api/chat/portal/sessions/
- Create turn:
  POST /api/chat/turns/
- Stream turn events:
  GET /api/chat/turns/<turn_id>/events/
- List documents:
  GET /api/knowledge/documents/

Examples (Curl)
---------------
Bootstrap portal session:
```bash
curl -s -X POST http://localhost:8000/api/chat/portal/sessions/ \
  -H "Content-Type: application/json" \
  -d '{"business_slug":"aug-pharma","agent_slug":"ahmed","metadata":{}}'
```

Create a turn:
```bash
curl -s -X POST http://localhost:8000/api/chat/turns/ \
  -H "Content-Type: application/json" \
  -d '{"session_token":"<token>","body":"check invoice 9125779195"}'
```

Stream turn events:
```bash
curl -N "http://localhost:8000/api/chat/turns/<turn_id>/events/?session_token=<token>"
```

ASCII Flow
----------
HTTP client
   ↓
apps/api (chat_portal + views)
   ↓
conversations portal + mcp/rag
   ↓
LLM response -> API response

Troubleshooting
---------------
- 404 on portal:
  - Check business/agent slug and `resolve_portal_handle`.
- Chat stalls:
  - Verify MCP provider loads and `RAG_USE_MCP_ORCHESTRATOR` is set.
- Knowledge list empty:
  - Ensure ingestion ran and uploads are ACTIVE.

Observability
-------------
- Main logs: `var/logs/rag.log` (look for `portal.trace` and `mcp.trace`).
- Chat load tests: `var/logs/mcp_load_test_latest.json`.

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/ops/manual_qa_playbook.md`

Glossary (Quick)
----------------
- Portal: public chat UI backed by Conversation + MCP.
- Streaming: server-sent message fragments for live responses.

Where To Start (Reading Order)
------------------------------
1) `apps/api/urls.py`
2) `apps/api/chat_portal.py`
3) `apps/api/views.py`

Endpoints (by section)
----------------------
Chat + Portal
- POST `/api/chat/portal/sessions/` — bootstrap a portal session
- GET  `/api/chat/portal/resolve/<business>/<agent>/` — validate handle
- POST `/api/chat/turns/` — create a portal turn
- GET  `/api/chat/turns/<turn_id>/events/` — stream turn events (SSE)
- POST `/api/chat/turns/<turn_id>/cancel/` — cancel an active turn
- POST `/api/chat/messages/` — persist non-stream messages
- GET  `/api/chat/events/` — portal status + sub-agent events
- POST `/api/chat/csat/` — submit CSAT rating
- POST `/api/chat/feedback/` — submit feedback (e.g., incorrect answer)

Registration
- POST `/api/register/sessions/` — start registration
- POST `/api/register/sessions/<session_id>/business/` — save business profile
- POST `/api/register/businesses/<business_id>/agent/` — configure agent
- POST `/api/register/businesses/<business_id>/uploads/` — finalize uploads

Agents
- GET  `/api/agents/` — list agents
- GET  `/api/agents/<agent_id>/` — agent detail
- GET/POST `/api/agents/<agent_id>/actions/` — action settings

Knowledge Documents
- GET  `/api/knowledge/documents/` — list uploads
- GET  `/api/knowledge/documents/<document_id>/` — document detail
- GET  `/api/knowledge/documents/<document_id>/download/` — download
- POST `/api/knowledge/documents/scrape/` — scrape URL
- POST `/api/knowledge/documents/preview-csv/` — preview CSV

Integrations (Google Drive)
- GET  `/api/integrations/` — list integrations
- GET  `/api/integrations/<integration_id>/sheets/` — list sheets
- GET  `/api/integrations/google/start/` — OAuth start
- GET  `/api/integrations/google/callback/` — OAuth callback
- GET  `/api/integrations/google/resources/` — list drive resources
- POST `/api/integrations/google/resources/save/` — save selected resources
- POST `/api/integrations/google/sync/` — sync now

MCP Connector (BETA)
- GET/POST `/api/mcp/connections/` — list/create MCP connections
- GET/PUT/DELETE `/api/mcp/connections/<connection_id>/` — connection detail/update/delete
- POST `/api/mcp/connections/<connection_id>/test/` — test connection and cache tool schemas
- GET/POST `/api/mcp/connections/<connection_id>/agents/` — view/update per-agent opt-outs

Cases + Customers
- GET  `/api/cases/` — list cases
- GET  `/api/cases/<case_id>/` — case detail
- GET  `/api/cases/<case_id>/history/` — case history
- GET  `/api/cases/<case_id>/messages/` — case messages
- GET  `/api/cases/<case_id>/notes/` — case notes
- GET  `/api/customers/<customer_id>/` — customer detail

Examples (By Endpoint)
----------------------
Chat + Portal
```bash
# Resolve handle
curl -s http://localhost:8000/api/chat/portal/resolve/acme/agent-1/

# Bootstrap session
curl -s -X POST http://localhost:8000/api/chat/portal/sessions/ \
  -H "Content-Type: application/json" \
  -d '{"business_slug":"acme","agent_slug":"agent-1","metadata":{}}'

# Create a turn
curl -s -X POST http://localhost:8000/api/chat/turns/ \
  -H "Content-Type: application/json" \
  -d '{"session_token":"<token>","body":"check invoice 9125779195"}'

# Stream turn events (replace <turn_id>)
curl -N "http://localhost:8000/api/chat/turns/<turn_id>/events/?session_token=<token>"

# Send chat message (store only)
curl -s -X POST http://localhost:8000/api/chat/messages/ \
  -H "Content-Type: application/json" \
  -d '{"session_token":"<token>","body":"hello","metadata":{}}'

# Fetch chat messages
curl -s "http://localhost:8000/api/chat/messages/?session_token=<token>&limit=50"

# Events heartbeat
curl -N "http://localhost:8000/api/chat/events/?session_token=<token>"

# Submit CSAT
curl -s -X POST http://localhost:8000/api/chat/csat/ \
  -H "Content-Type: application/json" \
  -d '{"session_token":"<token>","score":5,"comment":"great"}'

# Submit feedback
curl -s -X POST http://localhost:8000/api/chat/feedback/ \
  -H "Content-Type: application/json" \
  -d '{"session_token":"<token>","feedback_type":"not_found_incorrect","message_id":"<uuid>"}'
```

Registration
```bash
# Start registration
curl -s -X POST http://localhost:8000/api/register/sessions/ \
  -H "Content-Type: application/json" \
  -d '{"firstName":"Ada","email":"ada@example.com","password":"pass1234","confirmPassword":"pass1234"}'

# Save business profile
curl -s -X PUT http://localhost:8000/api/register/sessions/<session_id>/business/ \
  -H "Content-Type: application/json" \
  -d '{"businessName":"Acme","industry":"Retail","industryKey":"retail","country":"US","website":"https://acme.com"}'

# Configure agent
curl -s -X PUT http://localhost:8000/api/register/businesses/<business_id>/agent/ \
  -H "Content-Type: application/json" \
  -d '{"agentName":"Ava","agentTitle":"Support Lead","agentTone":"friendly","agentTraits":["helpful"]}'

# Finalize uploads
curl -s -X PUT http://localhost:8000/api/register/businesses/<business_id>/uploads/ \
  -H "Content-Type: application/json" \
  -d '{"selected":["file","link"],"links":{"policy":["https://example.com/policy"]},"skip":false}'
```

Agents
```bash
curl -s http://localhost:8000/api/agents/
curl -s http://localhost:8000/api/agents/<agent_id>/
curl -s http://localhost:8000/api/agents/<agent_id>/actions/
curl -s -X PUT http://localhost:8000/api/agents/<agent_id>/actions/ \
  -H "Content-Type: application/json" \
  -d '{"action":"create_case","enabled":true}'
```

Knowledge Documents
```bash
curl -s "http://localhost:8000/api/knowledge/documents/?business_id=<uuid>&limit=50"
curl -s "http://localhost:8000/api/knowledge/documents/<doc_id>/?business_id=<uuid>"
curl -s "http://localhost:8000/api/knowledge/documents/<doc_id>/download/?business_id=<uuid>"

curl -s -X POST http://localhost:8000/api/knowledge/documents/scrape/ \
  -H "Content-Type: application/json" \
  -d '{"business_id":"<uuid>","url":"https://example.com/policy.pdf"}'

curl -s -X POST http://localhost:8000/api/knowledge/documents/preview-csv/ \
  -H "Content-Type: application/json" \
  -d '{"business_id":"<uuid>","filename":"sample.csv","content":"a,b\\n1,2"}'
```

Integrations (Google Drive)
```bash
curl -s http://localhost:8000/api/integrations/
curl -s http://localhost:8000/api/integrations/<integration_id>/sheets/
curl -s http://localhost:8000/api/integrations/google/start/
curl -s "http://localhost:8000/api/integrations/google/callback/?code=<code>&state=<state>"
curl -s http://localhost:8000/api/integrations/google/resources/
curl -s -X POST http://localhost:8000/api/integrations/google/resources/save/ \
  -H "Content-Type: application/json" \
  -d '{"integration_id":"<uuid>","resources":["<sheet_id>"]}'
curl -s -X POST http://localhost:8000/api/integrations/google/sync/ \
  -H "Content-Type: application/json" \
  -d '{"integration_id":"<uuid>","resource_ids":["<sheet_id>"]}'
```

Cases + Customers
```bash
curl -s "http://localhost:8000/api/cases/?business_id=<uuid>"
curl -s "http://localhost:8000/api/cases/<case_id>/?business_id=<uuid>"
curl -s "http://localhost:8000/api/cases/<case_id>/history/?business_id=<uuid>"
curl -s "http://localhost:8000/api/cases/<case_id>/messages/?business_id=<uuid>"
curl -s "http://localhost:8000/api/cases/<case_id>/notes/?business_id=<uuid>"
curl -s "http://localhost:8000/api/customers/<customer_id>/?business_id=<uuid>"
```

High-Level Architecture
-----------------------
API (chat + admin)
   ↓
Conversations + MCP + RAG
   ↓
LLM response
