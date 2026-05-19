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
- ingestion/chunks.py
  Chunk construction orchestration.
- ingestion/chunk_text_segments.py
  Text segment construction, flat-text fallback selection, and page-block fragmentation scoring.
- ingestion/chunk_quality.py
  Chunk quality metrics, dedupe fingerprints, and table payload classifiers.
- ingestion/chunk_canonical.py
  Canonical chunk metadata helpers.
- ingestion/chunk_residuals.py
  Table residual annotations, canonical payload projection, and residual reconciliation.
- ingestion/entities.py
  Table row entity extraction and spreadsheet entity gating.
- ingestion/entity_aliases.py
  Identifier alias extraction, normalization, and alias metadata helpers.
- ingestion/entity_evidence.py
  Evidence key/group metadata assignment.
- ingestion/entity_payloads.py
  Entity chunk payload rendering.
- ingestion/entity_persistence.py
  Knowledge entity and alias persistence.
- ingestion_embeddings.py
  Embedding job scheduling, fallback embedding generation, and embedding metadata.
- ingestion_table_quality.py
  Table quality scoring and decorative/noisy table detection.
- ingestion_table_postprocessing.py
  Table row stitching, header propagation, duplicate suppression, and scope refresh.
- tables/postprocess_signals.py
  Table row value/signal scoring and fragment compatibility gates.
- tables/postprocess_rows.py
  Table row cell rewrite, contextual column, and parent-label carry-down helpers.
- tables/postprocess_stitching.py
  Row continuation stitching, scope value stitching, and scope refresh helpers.
- tables/tabular_files.py
  Compatibility-style aggregator for tabular file ingestion mixins.
- tables/delimited_files.py
  CSV/TSV extraction for normal indexed-table ingestion.
- tables/dataset_delimited.py
  Large CSV/TSV dataset-mode storage and key indexes.
- tables/spreadsheet_xls.py
  XLS extraction for normal indexed-table ingestion.
- tables/spreadsheet_common.py
  XLSX extraction, normalized-sheet table building, sheet role classification, and spreadsheet row heuristics.
- tables/dataset_common.py
  Dataset-mode storage directories, preview table payloads, and key-column selection.
- tables/dataset_xlsx.py
  XLSX dataset-mode storage, preview table construction, and key indexes.
- tables/dataset_xls.py
  XLS dataset-mode storage and preview table construction.
- tables/dataset_jsonl.py
  JSONL dataset-mode storage and preview table construction.
- tables/entity_rows.py
  Table title derivation, embedded header promotion, and row attribute/model helpers.
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
22) `apps/knowledge/ingestion/chunks.py`
23) `apps/knowledge/ingestion/chunk_text_segments.py`
24) `apps/knowledge/ingestion/chunk_quality.py`
25) `apps/knowledge/ingestion/chunk_canonical.py`
26) `apps/knowledge/ingestion/chunk_residuals.py`
27) `apps/knowledge/ingestion/entities.py`
28) `apps/knowledge/ingestion/entity_aliases.py`
29) `apps/knowledge/ingestion/entity_evidence.py`
30) `apps/knowledge/ingestion/entity_payloads.py`
31) `apps/knowledge/ingestion/entity_persistence.py`
32) `apps/knowledge/ingestion/embeddings.py`
33) `apps/knowledge/tables/quality.py`
34) `apps/knowledge/tables/postprocessing.py`
35) `apps/knowledge/tables/postprocess_signals.py`
36) `apps/knowledge/tables/postprocess_rows.py`
37) `apps/knowledge/tables/postprocess_stitching.py`
38) `apps/knowledge/tables/tabular_files.py`
39) `apps/knowledge/tables/delimited_files.py`
40) `apps/knowledge/tables/dataset_delimited.py`
41) `apps/knowledge/tables/spreadsheet_xls.py`
42) `apps/knowledge/tables/dataset_common.py`
43) `apps/knowledge/tables/spreadsheet_common.py`
44) `apps/knowledge/tables/dataset_xlsx.py`
45) `apps/knowledge/tables/dataset_xls.py`
46) `apps/knowledge/tables/dataset_jsonl.py`
47) `apps/knowledge/tables/entity_rows.py`
48) `apps/knowledge/ingestion/json_files.py`
49) `apps/knowledge/tables/limits.py`
50) `apps/knowledge/tables/semantics.py`
51) `apps/knowledge/ingestion/text_utils.py`
52) `apps/knowledge/ingestion/file_formats.py`
53) `apps/knowledge/ingestion/signals.py`
54) `apps/knowledge/ingestion/aliases.py`
55) `apps/knowledge/preflight/service.py`
48) `apps/knowledge/datasets/cards.py`
49) `apps/knowledge/privacy_tools/hashing.py`

High-Level Architecture
-----------------------
Knowledge (ingest/store)
   ↓
RAG (retrieve/score)
   ↓
MCP (tool loop + guards)
   ↓
LLM (final answer)
