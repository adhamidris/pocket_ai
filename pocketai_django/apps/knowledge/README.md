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
  Compatibility facade for legacy imports; new code should import specific modules directly.
- ingestion_service.py
  KnowledgeIngestionService composition and service initialization/configuration.
- ingestion_contracts.py
  Shared ingestion payload dataclasses and ingestion exceptions.
- ingestion_ocr.py
  OCR callable setup and low-density PDF OCR reconciliation.
- ingestion_page_renderer.py
  Layout-aware PDF/DOCX/text page rendering.
- ingestion_table_detection.py
  Heuristic table block detection and table cell normalization.
- ingestion_pdfplumber.py
  Optional pdfplumber table extraction and default table settings.
- ingestion_azure_di.py
  Optional Azure Document Intelligence table extraction and retry handling.
- ingestion_geometry_tables.py
  Geometry-based PDF table reconstruction from positioned spans.
- ingestion_jobs.py
  Ingestion job queue creation, result payloads, and queue health snapshots.
- ingestion_job_processing.py
  Ingestion and embedding job execution mixin.
- ingestion_extraction.py
  Upload extraction orchestration for file, text, link, and persisted artifacts.
- ingestion_docx_tables.py
  DOCX table extraction and DOCX-specific table cleanup helpers.
- ingestion_pdf_table_geometry.py
  PDF table bbox geometry, table-region overlap annotation, and numeric signal helpers.
- ingestion_pdf_table_routing.py
  PDF table extractor routing and candidate fallback policy.
- ingestion_pdf_table_reconstruction.py
  Collapsed native-text PDF table reconstruction and structural acceptance.
- ingestion_pdf_table_promotion.py
  PDF table promotion gating, page chrome suppression, and consumed-block restoration.
- ingestion_table_selection.py
  Table candidate scoring, diagnostics, and extractor selection.
- ingestion_table_vlm_repair.py
  VLM table repair, rendering, and guardrail validation.
- ingestion_link_extraction.py
  Link scraping extraction and small HTML/encoding helpers.
- ingestion_persistence.py
  Ingestion persistence orchestration, quality reports, and post-commit indexing hooks.
- ingestion_chunks.py
  Chunk construction, quality filtering, canonical metadata, and residual reconciliation.
- ingestion_entities.py
  Evidence grouping, entity chunk payloads, and entity/alias persistence.
- ingestion_embeddings.py
  Embedding job scheduling, fallback embedding generation, and embedding metadata.
- ingestion_table_quality.py
  Table quality scoring and decorative/noisy table detection.
- ingestion_table_postprocessing.py
  Table row stitching, header propagation, duplicate suppression, and scope refresh.
- ingestion_tabular_files.py
  CSV/TSV/XLSX/XLS/JSONL extraction, dataset mode storage, and key indexes.
- ingestion_json_files.py
  JSON entity extraction and JSON-to-table/text payload conversion.
- ingestion_table_limits.py
  Table privacy rules, row/column limits, truncation metrics, and column normalization.
- ingestion_table_semantics.py
  Table column roles, OCR text normalization, scope contracts, and table chunk payloads.
- ingestion_text_utils.py
  Generic text file loading, text normalization, clamping, and summary helpers.
- ingestion_file_formats.py
  File format detection and PDF/DOCX fallback text extraction.
- ingestion_signals.py
  Shared ingestion regexes, version constants, and numeric/table signal helpers.
- ingestion_aliases.py
  Alias and identifier extraction constants.
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
Ingestion (ingestion_service via knowledge_ingestion compatibility facade)
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
1) `apps/knowledge/ingestion_service.py`
2) `apps/knowledge/knowledge_ingestion.py`
3) `apps/knowledge/ingestion_contracts.py`
4) `apps/knowledge/ingestion_ocr.py`
5) `apps/knowledge/ingestion_page_renderer.py`
6) `apps/knowledge/ingestion_table_detection.py`
7) `apps/knowledge/ingestion_pdfplumber.py`
8) `apps/knowledge/ingestion_azure_di.py`
9) `apps/knowledge/ingestion_geometry_tables.py`
10) `apps/knowledge/ingestion_jobs.py`
11) `apps/knowledge/ingestion_job_processing.py`
12) `apps/knowledge/ingestion_extraction.py`
13) `apps/knowledge/ingestion_docx_tables.py`
14) `apps/knowledge/ingestion_pdf_table_geometry.py`
15) `apps/knowledge/ingestion_pdf_table_routing.py`
16) `apps/knowledge/ingestion_pdf_table_reconstruction.py`
17) `apps/knowledge/ingestion_pdf_table_promotion.py`
18) `apps/knowledge/ingestion_table_selection.py`
19) `apps/knowledge/ingestion_table_vlm_repair.py`
20) `apps/knowledge/ingestion_link_extraction.py`
21) `apps/knowledge/ingestion_persistence.py`
22) `apps/knowledge/ingestion_chunks.py`
23) `apps/knowledge/ingestion_entities.py`
24) `apps/knowledge/ingestion_embeddings.py`
25) `apps/knowledge/ingestion_table_quality.py`
26) `apps/knowledge/ingestion_table_postprocessing.py`
27) `apps/knowledge/ingestion_tabular_files.py`
28) `apps/knowledge/ingestion_json_files.py`
29) `apps/knowledge/ingestion_table_limits.py`
30) `apps/knowledge/ingestion_table_semantics.py`
31) `apps/knowledge/ingestion_text_utils.py`
32) `apps/knowledge/ingestion_file_formats.py`
33) `apps/knowledge/ingestion_signals.py`
34) `apps/knowledge/ingestion_aliases.py`
35) `apps/knowledge/knowledge_preflight.py`
36) `apps/knowledge/dataset_cards.py`
37) `apps/knowledge/privacy.py`

High-Level Architecture
-----------------------
Knowledge (ingest/store)
   ↓
RAG (retrieve/score)
   ↓
MCP (tool loop + guards)
   ↓
LLM (final answer)
