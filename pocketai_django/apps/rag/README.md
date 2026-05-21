RAG App (apps/rag)
==================

Purpose
-------
This app owns retrieval logic: embedding generation, query analysis, ranking,
and evaluation for knowledge search. It is the "librarian" layer that selects
the right evidence; ingestion/storage live in apps/knowledge.

The active chat/runtime path uses MCP (`apps/mcp`). Removed legacy orchestrator
snapshots/classes should not be treated as runtime code.

Directory Map
-------------
- knowledge_search.py
  Active public import surface for knowledge search (`KnowledgeSearchService`,
  `QueryNormalizer`, and search result types).
- knowledge_search_service.py
  `KnowledgeSearchService` class composition and initialization.
- query/
  Query classification, normalization, rewriting, signals, and analytics.
- search/
  Search entrypoint/pipeline, DB timeout wrapper, retrieval cache, tenant
  search configuration, and retrieval observability helpers.
- tables/
  Table-query context, profile discovery, query-token helpers, support
  utilities, snippets, expansion, lookup, profile cache, semantics, and tool
  limits.
- retrieval/
  Candidate retrieval, retrieval strategies, critiques, alias retrieval,
  ranking features, and reranking.
- decision/
  Auto-mode scoring, scope/category summaries, table/text arbitration, final
  semantic contracts, conflict detection, and intent fallback.
- content/
  Document reading and content serialization helpers used by search/read
  snippets.
- snippets/
  Chunk-to-`KnowledgeSnippet` shaping, snippet selection, evidence-group
  collapse, metadata helpers, and MCP/read payload serialization.
- contracts.py
  Shared RAG/MCP dataclasses and constants such as `KnowledgeSnippet`,
  `StreamingTurnContext`, and knowledge read-state values.
- embeddings.py
  Embedding provider selection (local FastEmbed or OpenAI) + warmup helper.
- lexicon/
  Tenant lexicon service, lexicon tokenization, and plural/singular text
  utilities.
- observability/
  Structured RAG/MCP/LLM trace logging helpers plus drift/retrieval quality
  sampling and evaluation run persistence.
- integrations/
  External retrieval integrations such as Azure AI Search.
- evaluation/
  Harness and fixtures for regression evaluation (golden sets).
- management/commands/warm_embeddings.py
  Warms embedding backend and FastEmbed cache.
- tests/
  Retrieval, caching, and orchestration tests for the RAG layer.

Key Flows
---------
1) MCP retrieval (primary path)
   apps/mcp/tools.py -> KnowledgeSearchService (apps/rag/knowledge_search.py)
   -> vector + lexical + alias blending -> refs/previews returned to MCP.

2) Embedding lifecycle
   apps/knowledge/knowledge_ingestion.py calls build_embedding_service()
   (embeddings.py) to generate vectors during ingestion.

Quick Start (Dev)
----------------
- Warm embeddings cache:
  `.venv/bin/python manage.py warm_embeddings`
- Run RAG eval harness (example):
  `.venv/bin/python manage.py run_rag_eval --set=fees-credit-cards --export=var/logs/rag_eval_fees.json`
- (Optional) Azure AI Search index:
  - Ensure index: `.venv/bin/python manage.py azure_search_ensure_index`
  - Backfill a tenant: `.venv/bin/python manage.py azure_search_backfill --business-id <uuid>`

ASCII Flow (MCP Path)
---------------------
User msg
   ↓
MCP planner (apps/mcp/orchestrator.py)
   ↓
search_knowledge tool (apps/mcp/tools.py)
   ↓
KnowledgeSearchService (apps/rag/knowledge_search.py)
   ↓
Hybrid ranking (vector + lexical + alias)
   ↓
Snippets → read_knowledge tool → evidence packets
   ↓
Final response

Troubleshooting
---------------
- No snippets returned:
  - Check `RAG_FTS_ENABLED` and `EMBED_PROVIDER` are set correctly.
  - Inspect `var/logs/rag.log` for `stage=search.summary` diagnostics.
- Slow queries:
  - Look at `stage=search.summary` timings (vector_ms, lexical_ms, rerank_ms).
  - Verify Postgres FTS indexes exist if `RAG_FTS_ENABLED=true`.
- Unexpected retrieval:
  - Compare `top_score` breakdown in `search.summary`.
  - Run `run_rag_eval` to reproduce with fixtures.

Related Docs
------------
- `docs/rag/rag_rollout_ops.md`
- `docs/ops/manual_qa_playbook.md`
- `docs/architecture/llm_conversation_backend_flow.md`

Glossary (Quick)
----------------
- ANN: Approximate nearest neighbor search over embeddings.
- MMR: Maximal marginal relevance (diversity-aware ranking).
- FTS: Full-text search (Postgres ranking).
- Alias hits: Exact/near-exact identifier matches derived from ingestion.

Where To Start (Reading Order)
------------------------------
1) `apps/rag/knowledge_search.py` — public active search import surface.
2) `apps/rag/query/normalizer.py` — query normalization and alias candidate generation.
3) `apps/rag/query/signals.py` — query-token signal helpers.
4) `apps/rag/search/cache.py` — search cache keys, serialization, and invalidation.
5) `apps/rag/search/config.py` — business overrides, thresholds, and tenant readiness helpers.
6) `apps/rag/search/pipeline.py` — search entrypoint and retrieval result assembly.
7) `apps/rag/knowledge_search_service.py` — service composition and initialization.
8) `apps/rag/tables/context.py` — table query context and table intent signals.
9) `apps/rag/tables/snippets.py` — direct table-row and fallback snippet construction.
10) `apps/rag/tables/expansion.py` — table hit routing, diagnostics, and row expansion/merge helpers.
11) `apps/rag/retrieval/ranking_features.py` — scoring feature helpers used by reranking.
12) `apps/rag/retrieval/reranking.py` — candidate reranking and residual table rescue.
13) `apps/rag/decision/auto_scoring.py` — auto-mode table/text scoring and route helpers.
14) `apps/rag/decision/auto_scope.py` — scope/category summaries for broad auto-mode results.
15) `apps/rag/decision/auto_decision.py` — auto-mode decisions, final semantics, conflicts, and no-result reasons.
16) `apps/rag/retrieval/alias.py` — exact/fuzzy alias lookup and alias diagnostics.
17) `apps/rag/retrieval/candidates.py` — free-text/chunk retrieval orchestration.
18) `apps/rag/snippets/evidence_grouping.py` — snippet evidence grouping and conflict detection.
19) `apps/rag/content/serialization.py` — structured snippet payloads and content trimming.
20) `apps/rag/snippets/builder.py` — chunk-to-snippet shaping.
21) `apps/rag/snippets/selection.py` — snippet selection, diversification, and fusion.
22) `apps/rag/search/observability.py` — retrieval logging, quality samples, and timing.
23) `apps/rag/content/reading.py` — document/page/chunk read expansion.
24) `apps/rag/snippets/metadata.py` — knowledge metadata flags and topic hints.
25) `apps/rag/embeddings.py` — embedding providers + warmup.
26) `apps/mcp/knowledge_search/handler.py` — how RAG is invoked in the tool loop.
27) `apps/rag/evaluation/harness.py` — regression tests + metrics.

High-Level Architecture
-----------------------
Knowledge (ingest/store)
   ↓
RAG (retrieve/score)
   ↓
MCP (tool loop + guards)
   ↓
LLM (final answer)

Configuration Touchpoints
-------------------------
- RAG_WARM_EMBEDDINGS_ON_STARTUP controls whether RagConfig warms embeddings.
- EMBED_PROVIDER / EMBED_MODEL control embedding backend selection.
- RAG_FTS_ENABLED + lexical thresholds tune hybrid search behavior.
- RAG_EVAL_* thresholds used by evaluation harness.
- RAG_SEARCH_BACKEND selects retrieval backend:
  - `postgres` (default): Postgres FTS + vector candidates.
  - `azure`: Azure AI Search provides candidates; Postgres remains the source of truth for chunk content + table artifacts.
- Azure AI Search env vars (when `RAG_SEARCH_BACKEND=azure`):
  - `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_ADMIN_KEY`, `AZURE_SEARCH_INDEX_NAME`
  - Optional: `AZURE_SEARCH_QUERY_KEY`, `AZURE_SEARCH_SEMANTIC_ENABLED`, `AZURE_SEARCH_SEMANTIC_CONFIG`

Boundaries
----------
- RAG reads from apps/knowledge for visibility rules and dataset metadata.
- apps/knowledge owns ingestion/storage/metadata; RAG should not implement it.
- If a module only affects retrieval behavior (ranking, routing, limits),
  keep it in this app.

Operational Notes
-----------------
- Warm embeddings at deploy time: `python manage.py warm_embeddings`.
- Evaluation harness: apps/rag/evaluation/harness.py (golden set runs).
