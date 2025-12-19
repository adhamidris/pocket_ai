Integrations App (apps/integrations)
====================================

Purpose
-------
This app synchronizes external knowledge sources (e.g., Google Sheets) into the
knowledge ingestion pipeline. It handles OAuth, resource discovery, file export,
and ingestion job creation.

Directory Map
-------------
- integration_sync.py
  Main sync service: downloads resources and queues ingestion jobs.
- google_drive.py
  Google OAuth + Drive/Sheets API helpers.
- tests/
  Integration sync tests.

Key Flows
---------
1) OAuth setup
   build_google_authorization_url -> exchange_google_authorization_code
   -> store refresh/access tokens.

2) Resource discovery
   discover_google_sheet_resources -> list spreadsheet files + sheet tabs.

3) Sync and ingestion
   IntegrationSyncService -> download/export -> store file -> queue_ingestion_job.

Configuration Touchpoints
-------------------------
Google OAuth:
- GOOGLE_OAUTH_CLIENT_ID
- GOOGLE_OAUTH_CLIENT_SECRET
- GOOGLE_OAUTH_REDIRECT_URI
- GOOGLE_OAUTH_SCOPES

Integration behavior:
- IntegrationSyncFrequency (model-based schedule)
- KnowledgeIntegrationStatus (model-based state)

Quick Start (Dev)
----------------
- Kick a one-off sync from the admin or API:
  `IntegrationSyncService().sync_integrations([...])`
- Ensure ingestion worker is running:
  `python manage.py process_knowledge_ingestion --watch`

Examples
--------
Build OAuth URL:
```python
from apps.integrations.google_drive import build_google_authorization_url
url = build_google_authorization_url(state="tenant-123")
```

Exchange OAuth code:
```python
from apps.integrations.google_drive import exchange_google_authorization_code
tokens = exchange_google_authorization_code(code)
```

Run sync for a single integration:
```python
from apps.integrations.integration_sync import IntegrationSyncService
result = IntegrationSyncService().sync_integration(integration)
```

Sync all due integrations:
```python
service = IntegrationSyncService()
results = service.sync_integrations(KnowledgeIntegration.objects.all())
```

ASCII Flow
----------
OAuth → Resource discovery → Export → Queue ingestion → Knowledge pipeline

Troubleshooting
---------------
- OAuth errors:
  - Check Google client ID/secret/redirect URI.
  - Look for `GoogleOAuthError` in logs.
- Sync skips:
  - Integration might not be due (`due_for_sync()`).
  - Another sync may hold the lock.
- No data ingested:
  - Confirm ingestion worker is running and jobs are queued.

Observability
-------------
- Sync results are returned as IntegrationSyncResult (per integration).
- Check `KnowledgeIntegration.sync_error` for last error.
- Ingestion jobs: `KnowledgeIngestionJob` records queued/running/failed status.

Related Docs
------------
- `docs/ops/manual_qa_playbook.md`
- `docs/ingestion/ingestion_normalization_plan.md`

Glossary (Quick)
----------------
- Integration: a configured external source (Google Drive, Notion, etc.).
- Resource: a single external item (spreadsheet/file) tied to an integration.

Where To Start (Reading Order)
------------------------------
1) `apps/integrations/integration_sync.py`
2) `apps/integrations/google_drive.py`
3) `apps/knowledge/knowledge_ingestion.py` (handoff target)

High-Level Architecture
-----------------------
External Source
   ↓
Integrations (oauth + export)
   ↓
Knowledge (ingest/store)
   ↓
RAG → MCP → LLM
