RAG App (apps/rag)
==================

Purpose
-------
This app owns retrieval logic: embedding generation, query analysis, ranking,
and evaluation for knowledge search. It is the "librarian" layer that selects
the right evidence; ingestion/storage live in apps/knowledge.

When MCP is enabled, RAG is used by the tool-based orchestrator (apps/mcp).
The legacy orchestrator in this app remains as a fallback.

Directory Map
-------------
- ai_orchestrator.py
  Legacy ledger-based orchestration + retrieval pipelines.
- embeddings.py
  Embedding provider selection (local FastEmbed or OpenAI) + warmup helper.
- rag_logging.py
  Structured RAG/MCP/LLM trace logging helpers.
- quality_monitor.py
  Drift and retrieval quality sampling + evaluation run persistence.
- evaluation/
  Harness and fixtures for regression evaluation (golden sets).
- dataset_router.py
  Identifier-to-dataset routing using dataset key indexes (read-only).
- tabular_limits.py
  Shared policy limits for dataset/table tool calls.
- table_semantics.py
  Column-name normalization + heuristics for total columns.
- table_lookup.py
  Placeholder interface for future row-on-demand retrieval.
- legacy_backup/
  Historical orchestrator snapshots (kept for reference).
- management/commands/warm_embeddings.py
  Warms embedding backend and FastEmbed cache.
- tests/
  Retrieval, caching, and orchestration tests for the RAG layer.

Key Flows
---------
1) MCP retrieval (primary path)
   apps/mcp/tools.py -> KnowledgeSearchService (ai_orchestrator.py)
   -> vector + lexical + alias blending -> snippets returned to MCP.

2) Legacy retrieval (fallback)
   AiOrchestratorService (ai_orchestrator.py) builds a prompt directly using
   PromptBuilder and returns an answer without the tool loop.

3) Embedding lifecycle
   apps/knowledge/knowledge_ingestion.py calls build_embedding_service()
   (embeddings.py) to generate vectors during ingestion.

Quick Start (Dev)
----------------
- Warm embeddings cache:
  `python manage.py warm_embeddings`
- Run RAG eval harness (example):
  `python manage.py run_rag_eval --set=fees-credit-cards --export=var/logs/rag_eval_fees.json`

ASCII Flow (MCP Path)
---------------------
User msg
   ↓
MCP planner (apps/mcp/orchestrator.py)
   ↓
search_knowledge tool (apps/mcp/tools.py)
   ↓
KnowledgeSearchService (apps/rag/ai_orchestrator.py)
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
1) `apps/rag/ai_orchestrator.py` — main retrieval flow and scoring.
2) `apps/rag/embeddings.py` — embedding providers + warmup.
3) `apps/mcp/tools.py` — how RAG is invoked in the tool loop.
4) `apps/rag/evaluation/harness.py` — regression tests + metrics.

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
- RAG_USE_MCP_ORCHESTRATOR controls whether RagConfig warms embeddings.
- EMBED_PROVIDER / EMBED_MODEL control embedding backend selection.
- RAG_FTS_ENABLED + lexical thresholds tune hybrid search behavior.
- RAG_EVAL_* thresholds used by evaluation harness.

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
