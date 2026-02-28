# Codebase Roadmap

This document maps every active runtime engine, request path, background worker, and core module in the PocketAI Django backend. It exists to give AI coding agents an accurate orientation before touching any part of the system. Keep it current — see maintenance rules in `AGENTS.md`.

---

## Apps overview

| App | Responsibility |
|---|---|
| `apps/accounts` | Tenants, users, agent profiles, feature flags, credentials, action controls |
| `apps/api` | HTTP API layer — chat portal, voice calls, agent runs, OAuth, MCP connections |
| `apps/conversations` | Conversation models, turn processing, portal session, agent runs, automations, memory, compaction |
| `apps/knowledge` | Knowledge uploads, ingestion pipeline, chunking, document parsing, dataset cards, privacy |
| `apps/rag` | RAG search engine — retrieval strategies, embeddings, query classification, Azure search, table retrieval |
| `apps/mcp` | MCP agent orchestrator, tool definitions, remote MCP connectors, prompts, sanitization |
| `apps/llm` | LLM HTTP provider, prompt builder, request logging |
| `apps/integrations` | Gmail, Outlook, Google Calendar, Google Drive, OneDrive, Slack, HubSpot |
| `apps/voice` | Twilio/Telnyx call handling, STT (Deepgram), TTS (Deepgram/ElevenLabs), call processing, post-call |
| `apps/cases` | AI-generated case management |
| `apps/customers` | Customer records linked to tenant |
| `apps/core` | Logging utilities, console logger |
| `apps/models` | Shared base models |
| `apps/services` | Shared service layer |
| `frontend` | Django-rendered frontend views |

---

## Runtime request paths

### 1. Chat conversation (main user-facing flow)

```
HTTP POST /api/...
  → apps/api/chat_portal.py              # API entry, auth, request parsing
  → apps/conversations/portal.py         # Portal session management
  → apps/conversations/portal_turn_runner.py  # Turn lifecycle, streaming setup
  → apps/mcp/orchestrator.py             # McpOrchestratorService — agent loop
      ├─ apps/llm/llm_provider.py        # LLM HTTP client (OpenAI-compatible, httpx)
      ├─ apps/llm/ai_prompt_builder.py   # System prompt construction
      ├─ apps/mcp/tools.py               # Tool implementations dispatched per tool call
      │    ├─ search_knowledge           →  apps/rag/ai_orchestrator.py (KnowledgeSearchService)
      │    ├─ read_knowledge             →  apps/mcp/tools.py (agentic read v2)
      │    ├─ send_email / read_email    →  apps/integrations/gmail.py or microsoft_graph.py
      │    ├─ calendar tools             →  apps/integrations/google_calendar_api.py
      │    ├─ drive tools                →  apps/integrations/google_drive.py
      │    └─ slack / hubspot tools      →  apps/integrations/slack_api.py / hubspot_api.py
      └─ apps/mcp/connectors.py          # Remote MCP server tool dispatch
  → apps/conversations/portal_turn_processing.py   # Persist turn, extract response blocks
  → apps/conversations/response_blocks.py           # Normalize output blocks for UI
```

### 2. Knowledge ingestion (document upload)

```
HTTP POST /api/...  (file upload)
  → apps/api/views.py                            # Auth, upload validation
  → apps/knowledge/knowledge_preflight.py        # Pre-checks (size, format, quota)
  → apps/knowledge/knowledge_ingestion.py        # Queue KnowledgeIngestionJob → DB

Background worker (long-lived process):
  management command: process_knowledge_ingestion --watch
  → apps/knowledge/knowledge_ingestion.py        # KnowledgeIngestionService.process_next_job()
      ├─ Extraction:
      │    • PDF layout text via PyMuPDF PageRenderer
      │    • PDF table candidates from pdfplumber (flag), Azure Document Intelligence (flag), geometry reconstruction, and heuristic detector
      │    • DOCX table candidates from `document.tables` (merged-cell spans + header detection) plus paragraph rendering for supplemental context
      │    • XLSX (openpyxl) / CSV / HTML
      │    • Table candidate selection + optional VLM repair + table post-processing
      │    • Large-table indexing keeps full rows by default (bounded by hard safety cap) and uses row-sharded summary chunks for primary retrieval
      ├─ apps/knowledge/table_normalization.py   # Table structure normalization
      ├─ apps/knowledge/table_scope_engine.py    # Row-level scope detection
      ├─ apps/knowledge/column_role_inference.py # Column type classification
      ├─ apps/knowledge/lexicon_learning.py      # Tenant lexicon auto-learning from ingestion artifacts
      ├─ apps/knowledge/dataset_key_index.py     # Bloom filter index for identifier lookup
      ├─ apps/rag/embeddings.py                  # Vector embedding (FastEmbed or OpenAI)
      ├─ pgvector (PostgreSQL)                   # Vector storage
      ├─ apps/rag/azure_ai_search.py             # Azure index upsert (optional, flag-controlled)
      ├─ apps/knowledge/dataset_cards.py         # Dataset card generation
      └─ apps/rag/table_profile_cache.py         # Cache invalidation on new uploads
```

### 3. Agent runs (sub-agent / parallel task execution)

```
HTTP POST /api/agent-runs/...
  → apps/api/agent_runs.py                       # API entry, creates AgentRun record

Background worker:
  management command: process_agent_runs --watch
  → apps/conversations/agent_run_processing.py   # AgentRunProcessingService.process_next_run()
  → apps/mcp/orchestrator.py                     # Same agent loop as chat
```

### 4. Scheduled automations (cron-triggered tasks)

```
Background worker:
  management command: process_agent_automations --watch
  → apps/conversations/agent_automation_processing.py  # AgentAutomationProcessingService
  → apps/conversations/automation_scheduling.py        # CronSchedule — computes next trigger
  → apps/mcp/orchestrator.py                           # Executes automation as agent turn
```

### 5. Watch mode (monitoring business channels)

```
Background worker:
  management command: (agent watcher)
  → apps/conversations/agent_watcher_processing.py     # Monitors configured channels
  → apps/mcp/orchestrator.py                           # Triggers agent turn on events
```

### 6. Voice calls (inbound/outbound)

```
Twilio webhook  →  apps/voice/views_twilio.py
Telnyx webhook  →  apps/voice/views_telnyx.py
  → apps/voice/runtime.py               # Call session orchestration
  → apps/voice/call_processing.py       # Turn-by-turn call logic
  → apps/voice/deepgram_stt.py          # Speech-to-text (Deepgram)
  → apps/mcp/orchestrator.py            # Agent processes transcribed speech
  → apps/voice/agent_run_bridge.py      # Bridges agent output to voice response
  → apps/voice/deepgram_tts.py          # Text-to-speech (Deepgram)
     or apps/voice/elevenlabs_tts.py    # TTS alternative (ElevenLabs)
  → apps/voice/post_call_processing.py  # Transcription, summary, insights
  → apps/voice/call_insights.py         # Post-call AI analysis
  → apps/voice/r2_storage.py            # Recording storage (Cloudflare R2)

Voice API surface:
  apps/api/voice_calls.py               # Initiate/manage calls
  apps/api/voice_providers.py           # Provider config API
  apps/voice/urls.py                    # Webhook URL routing
  apps/voice/mcp_tools.py              # Voice-specific MCP tools
  apps/voice/policy_engine.py          # Call policy enforcement
```

### 7. OAuth and integrations setup

```
HTTP /api/oauth/...
  → apps/api/oauth.py                          # OAuth flow entry
  → apps/api/email_oauth.py                    # Email OAuth specifically
  → apps/api/integration_oauth.py              # Other integration OAuth
  → apps/accounts/oauth_helpers.py             # Token exchange and storage
  → apps/integrations/integration_accounts.py  # Integration account persistence
  → apps/accounts/credential_secrets.py        # Encrypted credential storage
```

---

## RAG engine internals

Entry point: `apps/rag/ai_orchestrator.py` → `KnowledgeSearchService`

```
KnowledgeSearchService.search(query, business, agent)
  → apps/rag/query_classifier.py          # QueryClassifier — intent classification
  │    Intents: ENUMERATE / SPECIFIC_LOOKUP / COMPARE / AGGREGATE / EXPLORATORY
  → apps/rag/tenant_lexicon.py            # TenantLexiconService — tenant vocabulary/synonym snapshots
  → apps/rag/intent_fallback.py           # LLM fallback classifier for low-confidence table intent + clarification routing
  → apps/rag/retrieval_strategies.py      # StrategyRouter — retrieval hints per intent
  → apps/rag/query_rewriter.py            # Optional query rewriting for better vectors
  → Alias lookup (PostgreSQL trigram similarity)
  → apps/rag/embeddings.py                # Vector generation
  → Vector search:
  │    Primary:  pgvector (PostgreSQL HNSW index)
  │    Optional: apps/rag/azure_ai_search.py (flag: AZURE_SEARCH_ENABLED)
  → Lexical FTS (PostgreSQL full-text search)
  → Hybrid fusion + multi-signal reranking
  → apps/rag/table_lookup.py              # Table-specific retrieval path
  → apps/rag/table_profile_cache.py       # Table metadata (columns, row labels, ratios)
  → apps/rag/dataset_router.py            # Bloom filter — identifier → dataset mapping
  → apps/rag/table_semantics.py           # Table chunk scoring and expansion
  → apps/rag/tabular_limits.py            # Table result budgets
  → Cross-encoder reranking (optional, flag-controlled)
  → MMR deduplication
  → apps/rag/retrieval_critique.py        # Result quality assessment
  → apps/rag/rag_logging.py              # Structured retrieval logging
  → apps/rag/quality_monitor.py           # Quality signal tracking
```

Runtime contract notes (verified from code):
- `RAG_NON_QUERYABLE_TABLE_FORMATS` defaults to empty (`[]`) in `pocketai/settings.py`, so table-aware retrieval is queryable for all formats unless explicitly restricted by env config.
- `KnowledgeSearchService` reads `RAG_NON_QUERYABLE_TABLE_FORMATS` at init and applies it via `_filter_queryable_table_uploads`.
- `read_knowledge` can auto-fallback from table preview to text read for document uploads when table result is `not_found` with zero evaluated rows and no strong table signal.
- Row expansion in `KnowledgeSearchService._expand_table_rows` prefers shard-local row chunks when a matched table summary chunk includes `table_row_shard_index`.
- `KnowledgeSearchService.search` now emits `diagnostics.auto_decision_contract` with Point #3 contract keys (`table_score`, `text_score`, `margin`, `decision`, `needs_clarification`) plus additive diagnostics (`scope_summary`, `categories`, `top_categories`, `clarification_ui_mode`, `conflict_detected`, `no_result_reason`). `scope_summary` is computed from pre-clip fused candidates and includes `total_matches`, `distinct_docs`, `category_counts`, and `is_broad_scope` so breadth detection is independent of snippet/prompt clipping.
- Phase-6 runtime semantics are active in `_search_inner` finalization:
  - conflicting top table evidence for the same segment/category upgrades `status` to `needs_clarification`, sets `diagnostics.reason=conflicting_evidence`, `conflict_detected=true`, and emits a dynamic `intent_clarification_question`.
  - `status=not_found` now emits one normalized `no_result_reason`: `not_found`, `not_applicable_to_segment`, or `insufficient_evidence`.
- `apps/mcp/tools.py::_search_knowledge_handler` now preserves clarification-worthy status across batched/fused runs: if any run yields `needs_clarification`, final tool status remains `needs_clarification` even when other runs return snippets. Diagnostics include `final_status_source_index` and `final_status_source_query`.
- Agentic prompt source of truth is `apps/mcp/schemas/agentic_prompts.py` (selected via `build_model_specific_prompt` when `rag_agentic_mode=true` and `MCP_AGENTIC_READ_V2_ENABLED=true`). Do not rely on non-agentic fallback hints in `apps/mcp/prompts.py` for runtime behavior.
- `apps/mcp/tools.py::_search_hint` now reads `diagnostics.no_result_reason` and returns reason-specific operator guidance instead of a generic not-found hint.
- Scope clarification is text-only (`clarification_ui_mode=text`) and no longer uses selector-tool payload flows.
- Ambiguous auto-arbitration clarification is now evidence-aware: `intent_clarification_question` is generated from current table/text hits and diagnostics expose `auto_arbitration_table_evidence_label` / `auto_arbitration_text_evidence_label` when available.
- `_route_chunk_hits` now follows a single authoritative source path (`table_primary*` vs `text_primary`) with table-specific refinement only; legacy cross-mode fallback route branches were merged to align with auto arbitration decisions.
- CI now includes explicit blocking gates for phase-7 scenarios in `.github/workflows/deploy.yml`: broad-query clarification, specific/multi-category direct resolution, clarification follow-up resolution (`all` + ask-categories), ambiguity retry (remain in clarification until user selects scope), non-selector payload sanitization, conflict/no-result semantics, and legacy payload compatibility under additive diagnostics.

---

## Background workers (long-lived processes)

These must be running alongside the Django web server for the platform to function:

| Process | Command | What it drives |
|---|---|---|
| Knowledge ingestion | `process_knowledge_ingestion --watch` | Document upload processing queue |
| Agent run executor | `process_agent_runs --watch` | Sub-agent / parallel task execution |
| Automation scheduler | `process_agent_automations --watch` | Cron-triggered agent automations |
| Agent watcher | *(agent watcher command)* | Watch mode — monitors channels, triggers on events |
| Email health | `apps/integrations/email_health_jobs.py` | Email account credential refresh |
| Maintenance jobs | `apps/conversations/maintenance_job_processing.py` | Compaction, retention purge |

All background workers use a **PostgreSQL-backed polling queue** (2s poll interval). Jobs have lease expiry, retry with exponential backoff, and per-business concurrency limits.

---

## Key models (database)

| Model | App | Purpose |
|---|---|---|
| `BusinessProfile` | accounts | Tenant root — every resource scopes to this |
| `AgentProfile` | accounts | Per-agent config, system prompt, tool access |
| `Conversation` | conversations | Chat thread |
| `ConversationMessage` | conversations | Individual messages within a thread |
| `PortalTurn` | conversations | Single request/response cycle with metadata |
| `AgentRun` | conversations | Sub-agent execution record |
| `KnowledgeUpload` | knowledge | Uploaded document or URL |
| `KnowledgeChunk` | knowledge | Indexed chunk (text or table) with vector |
| `KnowledgeAlias` | knowledge | Named aliases for identifier-based lookup |
| `KnowledgeLexiconTerm` | knowledge | Tenant-scoped canonical entity/attribute vocabulary |
| `KnowledgeLexiconSynonym` | knowledge | Tenant-scoped synonym/alias forms for lexicon terms |
| `KnowledgeIngestionJob` | knowledge | Background ingestion queue record |
| `McpConnection` | mcp | Remote MCP server connection config |
| `EmailAccount` | integrations | Connected email account (Gmail / Outlook) |
| `VoiceCall` | voice | Call session record |

---

## External service dependencies

| Service | Used for | Config key prefix |
|---|---|---|
| PostgreSQL + pgvector | Primary DB, vector search | `DATABASE_*` |
| Redis | Caching, session state, alias cache | `REDIS_*` |
| OpenAI-compatible LLM API | LLM inference | `OPENAI_*` / `LLM_*` |
| FastEmbed (local, CPU) | Default embedding generation | `EMBED_*` |
| Azure AI Search | Optional vector search backend | `AZURE_SEARCH_*` |
| Twilio | Inbound/outbound voice calls | `TWILIO_*` |
| Telnyx | Inbound/outbound voice calls (alternative) | `TELNYX_*` |
| Deepgram | Speech-to-text, text-to-speech | `DEEPGRAM_*` |
| ElevenLabs | Text-to-speech (alternative) | `ELEVENLABS_*` |
| Cloudflare R2 | Call recording storage | `R2_*` |
| AWS S3 | File/asset storage | `AWS_*` |
| Gmail API | Email integration | `GOOGLE_*` |
| Microsoft Graph | Outlook / OneDrive integration | `MICROSOFT_*` |
| Slack API | Slack integration | `SLACK_*` |
| HubSpot API | CRM integration | `HUBSPOT_*` |
| Sentry | Error tracking | `SENTRY_*` |
| OpenTelemetry | Distributed tracing | `OTEL_*` |

---

## URL routing

```
pocketai/urls.py                  # Root router
  /admin/                         →  Django admin
  /                               →  apps/accounts/urls.py  (auth, registration, dashboard)
  /                               →  frontend/urls.py       (Django-rendered pages)
  /api/                           →  apps/api/urls.py       (REST API)
  /voice/                         →  apps/voice/urls.py     (Twilio/Telnyx webhooks)
```

---

## Feature flags

Over 900 lines of feature flag toggles live in `.env` at the project root. These control which backends, limits, thresholds, and behaviors are active at runtime. Before debugging unexpected behavior or building something that touches existing functionality, identify and read the relevant flags first. `apps/accounts/feature_flags.py` is the flag evaluation service used throughout the codebase.

---

*Last updated: 2026-02-19. Update this file whenever a new engine, path, or major module is added — see maintenance rules in `AGENTS.md`.*
