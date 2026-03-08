Knowledge App (apps/knowledge)
==============================

Purpose
-------
This app owns ingestion, storage, and metadata for tenant knowledge. It is the
"library" layer: upload handling, parsing, normalization, privacy masking, and
dataset metadata/index generation. RAG and MCP *read* from this layer but do
not own its lifecycle.

Directory Map
-------------
- knowledge_ingestion.py
  Main ingestion pipeline (files/urls/text), chunking, embeddings, dataset mode.
- knowledge_preflight.py
  Fast preflight scan to classify uploads + surface limits.
- documents.py
  Document listing/detail/preview helpers for UI + APIs.
- dataset_cards.py
  Build + refresh dataset card chunks (RAG discovery snippets).
- dataset_key_index.py
  Bloom filters for dataset identifier routing (generated on ingest).
- table_normalization.py
  Sheet normalization policy (drop empty columns, null tokens, etc).
- knowledge_access.py
  Visibility rules for customer-facing retrieval.
- privacy.py
  PII masking helpers for previews/logs.
- knowledge_ops.py
  Ops dashboard snapshot (alias coverage, ingestion health, latency).
- tests/
  Ingestion + normalization unit tests.

Key Flows
---------
1) Upload preflight
   Upload -> ensure_upload_preflight() -> ingestion_metadata["preflight"].

2) Ingestion pipeline
   process_knowledge_ingestion -> KnowledgeIngestionService
   -> parse file/url/text -> normalize tables -> build chunks + embeddings
   -> dataset mode + key indexes + dataset cards.

3) Dataset metadata lifecycle
   Dataset card + key index written during ingestion.
   RAG reads them for routing/discovery.

Configuration Touchpoints
-------------------------
Ingestion:
- INGEST_* settings (timeouts, normalization policy)
- DATASET_* settings (dataset mode, limits)
- EMBED_* settings (embedding model/provider)

Privacy:
- Legacy PII masking / verified lookup knobs were removed (this deployment is knowledge-RAG only).
- DATASET_KEY_INDEX_ALLOW_SENSITIVE

Examples
--------
Preflight (manual trigger):
```python
from apps.knowledge.knowledge_preflight import ensure_upload_preflight
ensure_upload_preflight(upload, trigger="manual")
```

Dataset card refresh:
```python
from apps.knowledge.dataset_cards import refresh_dataset_card_chunk
refresh_dataset_card_chunk(upload=upload)
```

Key index helper:
```python
from apps.knowledge.dataset_key_index import normalize_identifier_value
normalize_identifier_value("INV-123 456")
```

Quick Start (Dev)
----------------
- Ingest pending uploads (watch):
  `python manage.py process_knowledge_ingestion --watch`
- Force a one-off ingestion pass:
  `python manage.py process_knowledge_ingestion`

Dataset Mode Notes
------------------
- Large sheets are stored as compressed files (csv_gz) with metadata only in DB.
- Dataset cards are generated so RAG can discover the dataset without loading rows.
- Key indexes (Bloom filters) speed up identifier routing.

ASCII Flow
----------
Upload
  ↓
Preflight (knowledge_preflight)
  ↓
Ingestion (knowledge_ingestion)
  ↓
Chunks + Embeddings + Dataset Metadata

Troubleshooting
---------------
- Ingestion failed:
  - Check `var/logs/rag.log` for `ingest.*` errors.
- Preflight missing:
  - Ensure dashboard triggers `ensure_upload_preflight`.
- Dataset not discovered:
  - Confirm dataset card chunk exists and is not filtered by visibility.

Observability
-------------
- Ops snapshot: `KnowledgeOpsDashboard.snapshot(business)`
- Drift metrics stored in `KnowledgeDriftSample` (ingestion + retrieval).

Related Docs
------------
- `docs/ingestion/ingestion_normalization_plan.md`
- `docs/rag/rag_rollout_ops.md`
- `docs/ops/manual_qa_playbook.md`

Data Ownership
--------------
- Knowledge app owns ingestion, storage metadata, and privacy masking rules.
- RAG and MCP must treat this layer as read-only except via ingestion jobs.

Glossary (Quick)
----------------
- Dataset card: compact snippet describing a dataset (used for discovery).
- Key index: Bloom filter for fast identifier routing.
- Preflight: lightweight scan to determine file limits before ingest.

Where To Start (Reading Order)
------------------------------
1) `apps/knowledge/knowledge_ingestion.py`
2) `apps/knowledge/knowledge_preflight.py`
3) `apps/knowledge/dataset_cards.py`
4) `apps/knowledge/privacy.py`

High-Level Architecture
-----------------------
Knowledge (ingest/store)
   ↓
RAG (retrieve/score)
   ↓
MCP (tool loop + guards)
   ↓
LLM (final answer)
