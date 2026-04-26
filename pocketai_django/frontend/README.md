Frontend App (frontend/)
========================

Purpose
-------
This Django app serves the public website, dashboard pages, and the authenticated
chat workspace UI (templates + static assets).

Directory Map
-------------
- views.py
  Server-rendered pages (landing, dashboard, chat workspace).
- urls.py
  Frontend routes.
- context_processors.py
  Shared navigation/CTA data for templates.
- templates/frontend/
  HTML templates (base, dashboard, chat portal, legal).
- static/
  CSS + JS assets (including chat portal bundle).

Key Flows
---------
1) Landing + dashboard
   frontend/views.py -> templates -> CSS/JS assets.

2) Dashboard chat workspace
   /dashboard/chat/ -> templates/frontend/chat/portal.html
   -> loads `frontend/static/js/chat-portal.js` and hits API endpoints.

3) Knowledge dashboard
   dashboard/knowledge/* -> upload + integrations UI + ingestion triggers.

Quick Start (Dev)
----------------
- Run Django and open:
  http://localhost:8000/
- Dashboard chat:
  http://localhost:8000/dashboard/chat/

Examples
--------
Chat bootstrap from frontend (server-side call):
```python
payload = _call_portal_bootstrap_api(request, business_slug, agent_slug, existing_session_token, metadata)
```

Chat page (client-side flow):
- Template: `frontend/templates/frontend/chat/portal.html`
- JS bundle: `frontend/static/js/chat-portal.js`
- API endpoints: `/api/chat/conversations/` + `/api/chat/turns/`

Troubleshooting
---------------
- Chat workspace not loading:
  - Ensure the authenticated user owns a business + agent and API returns 200.
- Static assets missing:
  - Check `STATIC_URL` and `STATICFILES_DIRS`.

Observability
-------------
- Check `portal.trace` entries in `var/logs/rag.log`.

Related Docs
------------
- `docs/architecture/llm_conversation_backend_flow.md`
- `docs/ops/manual_qa_playbook.md`

Where To Start (Reading Order)
------------------------------
1) `frontend/views.py`
2) `frontend/templates/frontend/chat/portal.html`
3) `frontend/static/js/chat-portal.js`

High-Level Architecture
-----------------------
Frontend templates
   ↓
API (apps/api)
   ↓
MCP + RAG + LLM
