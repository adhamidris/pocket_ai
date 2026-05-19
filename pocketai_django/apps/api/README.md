API App (apps/api)
=================

Purpose
-------
This app exposes HTTP endpoints for the web portal and admin dashboard:
chat streaming, registration, knowledge documents, integrations, and CRM v1.

Directory Map
-------------
- urls.py
  Public API routes.
- chat_portal.py
  Streaming chat endpoints + orchestration dispatch.
- portal_chat/
  Extracted portal chat helpers used by the legacy chat_portal.py entrypoint.
  - debug_tools.py: portal tool/debug payload serialization.
  - planning.py: low-intent and planner-decision helpers.
  - activity_snapshots.py: agent run, automation, and agent request portal snapshots.
  - serializers.py: portal session, message, turn, approval, and block serializers.
  - tracing.py: portal structured trace logger.
  - request_context.py: portal request parsing, auth resolution, JSON errors, and UI language metadata.
  - email_drafts.py: pending email draft metadata helpers.
  - streaming.py: SSE cursor parsing and Postgres LISTEN connection helpers.
  - status_events.py: portal status event queue helpers.
  - session_endpoints.py: portal handle, session bootstrap, messages, CSAT, and feedback endpoints.
  - tool_approvals.py: portal MCP tool approval endpoint.
  - activity_actions.py: portal agent-run, automation, checkpoint, approval, and request action endpoints.
  - tool_history.py: portal tool approval and tool event history endpoint.
  - email_endpoints.py: portal email draft send/discard endpoints.
  - session_stream.py: legacy session-level portal SSE stream endpoint.
  - conversation_endpoints.py: portal conversation collection, message list, turn creation, and deprecated session endpoints.
  - turn_stream.py: event-sourced portal turn SSE stream and cancel endpoints.
- registration/
  Registration wizard endpoints used by the legacy views.py entrypoint.
  - endpoints.py: registration session start, business profile update, and default agent configuration.
- agents/
  Agent dashboard endpoints used by the legacy views.py entrypoint.
  - endpoints.py: agent list/detail, capability graph, directory, and knowledge access endpoints.
- knowledge_documents/
  Knowledge document endpoints used by the legacy views.py entrypoint.
  - endpoints.py: document list/status/detail/download/scrape/CSV-preview endpoints and serializers.
- integrations/
  Integration endpoints used by the legacy views.py entrypoint.
  - endpoints.py: Google Drive OAuth, resource selection, sync, and integration collection endpoints.
- mcp/
  MCP connection API helpers used by the legacy mcp_connections.py entrypoint.
  - shared.py: request parsing, business resolution, and MCP server URL validation.
  - marketplace.py: curated MCP marketplace catalog, setup-field validation, and native account payloads.
  - serializers.py: MCP connection, tool setting, auth header, and active test-job serializers.
  - audit.py: MCP audit event writer.
  - controls.py: MCP tool-control and approval-mode helpers.
- views.py
  REST-style endpoints for agents, registrations, documents, integrations,
  and CRM resources.
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
- RAG_WARM_EMBEDDINGS_ON_STARTUP
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
  - Verify the MCP provider loads and turn events are being appended.
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
- GET  `/api/chat/events/` — portal status + background run events
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
- GET  `/api/agents/<agent_id>/capabilities/` — resolved capability graph

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

CRM V1
- GET/POST `/api/crm/contacts/` — list/create contacts
- GET/PATCH/DELETE `/api/crm/contacts/<contact_id>/` — contact detail/update/delete
- POST `/api/crm/contacts/<contact_id>/merge/` — merge contacts
- GET/POST `/api/crm/companies/` — list/create companies
- GET/PATCH/DELETE `/api/crm/companies/<company_id>/` — company detail/update/delete
- POST `/api/crm/companies/<company_id>/merge/` — merge companies
- GET/POST `/api/crm/field-definitions/` — list/create custom field definitions
- POST `/api/crm/imports/sources/` — upload an import source file and get a suggested mapping
- GET/POST `/api/crm/imports/templates/` — list/create reusable import mappings
- GET/POST `/api/crm/imports/jobs/` — list/queue import jobs
- GET `/api/crm/imports/jobs/<job_id>/` — import job detail
- GET `/api/crm/duplicates/` — duplicate suggestions

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

# Configure default assistant
curl -s -X PUT http://localhost:8000/api/register/businesses/<business_id>/agent/ \
  -H "Content-Type: application/json" \
  -d '{"agentName":"Ava","agentTone":"friendly"}'

# Finalize uploads
curl -s -X PUT http://localhost:8000/api/register/businesses/<business_id>/uploads/ \
  -H "Content-Type: application/json" \
  -d '{"selected":["file","link"],"links":{"policy":["https://example.com/policy"]},"skip":false}'
```

Agents
```bash
curl -s http://localhost:8000/api/agents/
curl -s http://localhost:8000/api/agents/<agent_id>/
curl -s http://localhost:8000/api/agents/<agent_id>/capabilities/
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

CRM V1
```bash
curl -s "http://localhost:8000/api/crm/contacts/?business_id=<uuid>"
curl -s "http://localhost:8000/api/crm/companies/?business_id=<uuid>"
curl -s "http://localhost:8000/api/crm/field-definitions/?business_id=<uuid>"
curl -s "http://localhost:8000/api/crm/imports/jobs/?business_id=<uuid>"
curl -s "http://localhost:8000/api/crm/duplicates/?business_id=<uuid>"
```

High-Level Architecture
-----------------------
API (chat + admin)
   ↓
Conversations + MCP + RAG
   ↓
LLM response
