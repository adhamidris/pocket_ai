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
- ingestion/azure_di_client.py
  Azure Document Intelligence request, polling, retry, and failure metadata helpers.
- ingestion/azure_di_scope.py
  Azure DI table scope reconciliation and row applicability annotation.
- tables/geometry_tools/reconstructor.py
  Geometry-based PDF table reconstruction from positioned spans.
- tables/geometry_tools/rows.py
  Logical row-fragment merge helpers for geometry table reconstruction.
- ingestion_jobs.py
  Ingestion job queue creation, result payloads, and queue health snapshots.
- ingestion_job_processing.py
  Ingestion and embedding job execution mixin.
- ingestion_extraction.py
  Upload extraction orchestration for file, text, link, and persisted artifacts.
- tables/docx_tables/extraction.py
  DOCX table extraction and DOCX-specific table cleanup helpers.
- tables/docx_tables/schema.py
  DOCX helper-cell, header-label, and grouped-column schema helpers.
- tables/docx_tables/sparse_series.py
  DOCX sparse period-series and helper-column collapse helpers.
- tables/docx_tables/headers.py
  DOCX header-band and section-row detection helpers.
- tables/pdf/geometry.py
  PDF table bbox geometry, table-region overlap annotation, and numeric signal helpers.
- tables/pdf/routing.py
  PDF table extractor routing and candidate fallback policy.
- tables/pdf/reconstruction.py
  Collapsed native-text PDF table reconstruction and structural acceptance.
- tables/pdf/sparse_matrix.py
  Sparse-cell PDF matrix reconstruction helpers.
- tables/pdf/span_matrix.py
  Span-band PDF matrix reconstruction helpers.
- tables/canonical/reconstruction.py
  Canonical table reconstruction coordinator and shared geometry/text helpers.
- tables/canonical/pseudo_tables.py
  Pseudo-table reconstruction from aligned page blocks.
- tables/canonical/attachments.py
  Orphan/residual block attachment into table cells.
- tables/pdf/promotion.py
  PDF table promotion support and recurrence signature helpers.
- tables/pdf/page_chrome.py
  Repeated PDF page header/footer detection and suppression.
- tables/pdf/promotion_gate.py
  PDF table candidate classification and promotion gate policy.
- tables/pdf/promotion_restore.py
  Restoration of page blocks consumed by suppressed table candidates.
- ingestion_table_selection.py
  Table candidate scoring, diagnostics, and extractor selection.
- tables/vlm/repair.py
  VLM table repair orchestration and repair candidate selection.
- tables/vlm/guardrails.py
  VLM table repair guardrail metrics and acceptance policy.
- tables/vlm/payload.py
  VLM table image rendering, model call parsing, and payload reconstruction.
- ingestion_link_extraction.py
  Link scraping extraction and small HTML/encoding helpers.
- ingestion_persistence.py
  Ingestion persistence orchestration and structured artifact storage.
- ingestion/lexicon_auto_learning.py
  Tenant lexicon auto-learning during ingestion persistence.
- ingestion/search_indexing.py
  Azure AI Search post-commit indexing and embedding update hooks.
- ingestion/quality_report.py
  Ingestion quality report construction.
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
- tables/postprocess/aggregator.py
  Table row stitching, header propagation, duplicate suppression, and scope refresh.
- tables/postprocess/signals.py
  Table row value/signal scoring and fragment compatibility gates.
- tables/postprocess/rows.py
  Table row cell rewrite, contextual column, and parent-label carry-down helpers.
- tables/postprocess/stitching.py
  Row continuation stitching, scope value stitching, and scope refresh helpers.
- tables/tabular/aggregator.py
  Compatibility-style aggregator for tabular file ingestion mixins.
- tables/tabular/delimited.py
  CSV/TSV extraction for normal indexed-table ingestion.
- tables/tabular/dataset_delimited.py
  Large CSV/TSV dataset-mode storage and key indexes.
- tables/tabular/spreadsheet_xls.py
  XLS extraction for normal indexed-table ingestion.
- tables/tabular/spreadsheet_common.py
  XLSX extraction, normalized-sheet table building, sheet role classification, and spreadsheet row heuristics.
- tables/tabular/dataset_common.py
  Dataset-mode storage directories, preview table payloads, and key-column selection.
- tables/tabular/dataset_xlsx.py
  XLSX dataset-mode storage, preview table construction, and key indexes.
- tables/tabular/dataset_xls.py
  XLS dataset-mode storage and preview table construction.
- tables/tabular/dataset_jsonl.py
  JSONL dataset-mode storage and preview table construction.
- tables/entity_rows.py
  Table title derivation, embedded header promotion, and row attribute/model helpers.
- ingestion_json_files.py
  JSON entity extraction and JSON-to-table/text payload conversion.
- ingestion_table_limits.py
  Table privacy rules, row/column limits, truncation metrics, and column normalization.
- tables/semantic/aggregator.py
  Table column role enrichment and scope contract helpers.
- tables/semantic/text_normalization.py
  OCR/text normalization helpers for table cell and row rendering.
- tables/semantic/chunk_payloads.py
  Parent, row, summary, and preview chunk payload rendering for persisted tables.
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
9) `apps/knowledge/ingestion/azure_di_client.py`
10) `apps/knowledge/ingestion/azure_di_scope.py`
11) `apps/knowledge/tables/geometry_tools/reconstructor.py`
12) `apps/knowledge/tables/geometry_tools/rows.py`
13) `apps/knowledge/ingestion_jobs.py`
14) `apps/knowledge/ingestion_job_processing.py`
15) `apps/knowledge/ingestion_extraction.py`
16) `apps/knowledge/tables/docx_tables/extraction.py`
17) `apps/knowledge/tables/docx_tables/schema.py`
18) `apps/knowledge/tables/docx_tables/sparse_series.py`
19) `apps/knowledge/tables/docx_tables/headers.py`
20) `apps/knowledge/tables/pdf/geometry.py`
21) `apps/knowledge/tables/pdf/routing.py`
22) `apps/knowledge/tables/pdf/reconstruction.py`
23) `apps/knowledge/tables/pdf/sparse_matrix.py`
24) `apps/knowledge/tables/pdf/span_matrix.py`
25) `apps/knowledge/tables/canonical/reconstruction.py`
26) `apps/knowledge/tables/canonical/pseudo_tables.py`
27) `apps/knowledge/tables/canonical/attachments.py`
28) `apps/knowledge/tables/pdf/promotion.py`
29) `apps/knowledge/tables/pdf/page_chrome.py`
30) `apps/knowledge/tables/pdf/promotion_gate.py`
31) `apps/knowledge/tables/pdf/promotion_restore.py`
31) `apps/knowledge/ingestion_table_selection.py`
32) `apps/knowledge/tables/vlm/repair.py`
33) `apps/knowledge/tables/vlm/guardrails.py`
31) `apps/knowledge/tables/vlm/payload.py`
32) `apps/knowledge/ingestion_link_extraction.py`
33) `apps/knowledge/ingestion_persistence.py`
34) `apps/knowledge/ingestion/chunks.py`
35) `apps/knowledge/ingestion/chunk_text_segments.py`
29) `apps/knowledge/ingestion/chunk_quality.py`
30) `apps/knowledge/ingestion/chunk_canonical.py`
26) `apps/knowledge/ingestion/chunk_residuals.py`
27) `apps/knowledge/ingestion/entities.py`
28) `apps/knowledge/ingestion/entity_aliases.py`
29) `apps/knowledge/ingestion/entity_evidence.py`
30) `apps/knowledge/ingestion/entity_payloads.py`
31) `apps/knowledge/ingestion/entity_persistence.py`
32) `apps/knowledge/ingestion/embeddings.py`
33) `apps/knowledge/tables/quality.py`
34) `apps/knowledge/tables/postprocess/aggregator.py`
35) `apps/knowledge/tables/postprocess/signals.py`
36) `apps/knowledge/tables/postprocess/rows.py`
37) `apps/knowledge/tables/postprocess/stitching.py`
38) `apps/knowledge/tables/tabular/aggregator.py`
39) `apps/knowledge/tables/tabular/delimited.py`
40) `apps/knowledge/tables/tabular/dataset_delimited.py`
41) `apps/knowledge/tables/tabular/spreadsheet_xls.py`
42) `apps/knowledge/tables/tabular/dataset_common.py`
43) `apps/knowledge/tables/tabular/spreadsheet_common.py`
44) `apps/knowledge/tables/tabular/dataset_xlsx.py`
45) `apps/knowledge/tables/tabular/dataset_xls.py`
46) `apps/knowledge/tables/tabular/dataset_jsonl.py`
47) `apps/knowledge/tables/entity_rows.py`
48) `apps/knowledge/ingestion/json_files.py`
49) `apps/knowledge/tables/limits.py`
50) `apps/knowledge/tables/semantic/aggregator.py`
51) `apps/knowledge/tables/semantic/text_normalization.py`
52) `apps/knowledge/tables/semantic/chunk_payloads.py`
53) `apps/knowledge/ingestion/text_utils.py`
54) `apps/knowledge/ingestion/file_formats.py`
55) `apps/knowledge/ingestion/signals.py`
56) `apps/knowledge/ingestion/aliases.py`
57) `apps/knowledge/preflight/service.py`
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
