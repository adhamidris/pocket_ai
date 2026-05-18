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
- ai_orchestrator.py
  Temporary compatibility bridge for older imports. Do not add new retrieval
  logic here.
- knowledge_search.py
  Active public import surface for knowledge search (`KnowledgeSearchService`,
  `QueryNormalizer`, and search result types).
- knowledge_search_service.py
  `KnowledgeSearchService` class composition and initialization.
- query_normalizer.py
  Query normalization, alias candidate generation, and identifier-like query
  detection used by active retrieval.
- query_signals.py
  Query-token signal helpers used by alias retrieval and table/entity routing.
- search_cache.py
  Alias, query-vector, result, and per-session cache helpers mixed into
  `KnowledgeSearchService`.
- search_config.py
  Business override, tenant-lexicon readiness, retrieval limit/threshold, and
  filler-token helpers mixed into `KnowledgeSearchService`.
- search_pipeline.py
  Public search entrypoint, DB timeout wrapper, retrieval pipeline, cache
  arbitration, table/vector routing, and final result assembly.
- table_context.py
  Table-query context construction, table metadata discovery, table intent
  signals, and table-profile cache integration.
- table_snippets.py
  Direct table-row search snippets and fallback snippet construction.
- table_expansion.py
  Table/text hit routing, table match diagnostics, structural row checks, table
  parent lookup, and row expansion/merge helpers.
- ranking_features.py
  Text quality penalties, lexical/entity/alias scoring features, phrase and
  proximity boosts, and text evidence span selection.
- reranking.py
  Candidate reranking and residual table rescue arbitration.
- auto_scoring.py
  Auto-mode table/text scoring, weak-hit detection, chunk route helpers, and
  small numeric scoring utilities.
- auto_scope.py
  Auto-mode scope/category summaries, category normalization, and scoped ref
  hints for broad table/document result sets.
- auto_decision.py
  Auto-mode evidence labels, table/text arbitration, decision contracts,
  final semantic contract application, conflict detection, and no-result reason
  derivation.
- alias_retrieval.py
  Alias exact/fuzzy lookup, alias cache hydration, and alias-path diagnostics.
- candidate_retrieval.py
  Free-text chunk retrieval orchestration, chunk queryset scoping,
  vector/lexical candidate generation, query-vector caching, candidate merging,
  vector thresholds, and MMR selection.
- evidence_grouping.py
  Evidence-group collapse and conflict detection for retrieved snippets.
- content_serialization.py
  Structured export serialization, table row samples, public labels, summaries,
  and content trimming used by search/read snippets.
- snippet_builder.py
  Chunk-to-`KnowledgeSnippet` shaping for retrieved chunks.
- snippet_selection.py
  Snippet selection, result diversification, chunk-hit conversion, public
  confidence scoring, and vector/table RRF fusion.
- search_observability.py
  Retrieval quality sampling, search summary logging, snippet previews, shadow
  vector snapshots, and small timing helpers.
- content_reading.py
  Document, page-window, chunk read/load, read-state, and neighbor/entity
  expansion helpers used by MCP read tools.
- metadata_helpers.py
  Knowledge metadata flags, topic normalization, and pinned/read hints used by
  snippet construction.
- contracts.py
  Shared RAG/MCP dataclasses and constants such as `KnowledgeSnippet`,
  `StreamingTurnContext`, and knowledge read-state values.
- embeddings.py
  Embedding provider selection (local FastEmbed or OpenAI) + warmup helper.
- rag_logging.py
  Structured RAG/MCP/LLM trace logging helpers.
- quality_monitor.py
  Drift and retrieval quality sampling + evaluation run persistence.
- evaluation/
  Harness and fixtures for regression evaluation (golden sets).
- tabular_limits.py
  Shared tool rate limiting helper.
- table_semantics.py
  Column-name normalization + heuristics for total columns.
- table_lookup.py
  Placeholder interface for future row-on-demand retrieval.
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
2) `apps/rag/query_normalizer.py` — query normalization and alias candidate generation.
3) `apps/rag/query_signals.py` — query-token signal helpers.
4) `apps/rag/search_cache.py` — search cache keys, serialization, and invalidation.
5) `apps/rag/search_config.py` — business overrides, thresholds, and tenant readiness helpers.
6) `apps/rag/search_pipeline.py` — search entrypoint and retrieval result assembly.
7) `apps/rag/knowledge_search_service.py` — service composition and initialization.
8) `apps/rag/table_context.py` — table query context and table intent signals.
9) `apps/rag/table_snippets.py` — direct table-row and fallback snippet construction.
10) `apps/rag/table_expansion.py` — table hit routing, diagnostics, and row expansion/merge helpers.
11) `apps/rag/ranking_features.py` — scoring feature helpers used by reranking.
12) `apps/rag/reranking.py` — candidate reranking and residual table rescue.
13) `apps/rag/auto_scoring.py` — auto-mode table/text scoring and route helpers.
14) `apps/rag/auto_scope.py` — scope/category summaries for broad auto-mode results.
15) `apps/rag/auto_decision.py` — auto-mode decisions, final semantics, conflicts, and no-result reasons.
16) `apps/rag/alias_retrieval.py` — exact/fuzzy alias lookup and alias diagnostics.
17) `apps/rag/candidate_retrieval.py` — free-text/chunk retrieval, vector/lexical candidates, and MMR helpers.
18) `apps/rag/evidence_grouping.py` — snippet evidence grouping and conflict detection.
19) `apps/rag/content_serialization.py` — structured snippet payloads and content trimming.
20) `apps/rag/snippet_builder.py` — chunk-to-snippet shaping.
21) `apps/rag/snippet_selection.py` — snippet selection, diversification, and fusion.
22) `apps/rag/search_observability.py` — retrieval logging, quality samples, and timing.
23) `apps/rag/content_reading.py` — document/page/chunk read expansion.
24) `apps/rag/metadata_helpers.py` — knowledge metadata flags and topic hints.
25) `apps/rag/ai_orchestrator.py` — temporary compatibility bridge.
26) `apps/rag/embeddings.py` — embedding providers + warmup.
27) `apps/mcp/tools.py` — how RAG is invoked in the tool loop.
28) `apps/rag/evaluation/harness.py` — regression tests + metrics.

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
