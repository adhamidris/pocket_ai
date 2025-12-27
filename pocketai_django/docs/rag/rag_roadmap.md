# RAG Roadmap (Structure-First)

This roadmap reflects the latest research: table structure recognition is the prerequisite for reliable retrieval on complex PDFs. P0/P1 are complete.

## P0 — Baseline + Safety Rails (Done)

Objective: establish ground-truth metrics, evaluation harness, and safe rollout controls.

Steps:
- Build an eval set (10-20 queries, including fees-credit-cards).
- Log top-k snippets, answers, latency, and cost.
- Add eval logging, shadow ingestion, and shadow retrieval.

Success metrics:
- Baseline MRR/NDCG/Hit@3 captured.
- Baseline latency and cost captured.
- Reproducible failure on Fees and Charges Credit Cards Eng_185.pdf documented.

Validation/rollback:
- No-op phase; disable logging/shadow modes to revert.

Effort: 3-5 days
Dependencies: none

## P1 — Chunk Quality + Retrieval Hygiene (Done)

Objective: eliminate low-signal chunks and reduce alias noise.

Steps:
- Add chunk quality scoring (length, unique token ratio, heading-only).
- Suppress or down-rank low-quality chunks.
- Dedupe near-identical chunks.
- Tighten alias extraction to ignore card-number/date patterns.
- Add text-chunk penalties to reranker.

Success metrics:
- <5% chunks under 20 tokens.
- Duplicate rate reduced.
- Top-3 for fees-credit-cards includes fee content (not titles).
- MRR improves vs baseline.

Validation/rollback:
- Disable quality/dedupe penalties to revert.

Effort: 1-2 weeks
Dependencies: P0

## P2 — Table Structure Recognition (TSR) + Extraction Hybrid (Updated)

Objective: produce reliable cell-level tables (rows/columns/headers) for complex PDFs.

Steps:
- Pick a primary TSR path:
  - Open-source: UniTable or PP-StructureV3 (HTML/JSON output), or
  - Managed: Azure Document Intelligence (Markdown/JSON), or
  - Hybrid: PyMuPDF for text + TSR for tables, with per-table selection.
- Add confidence scoring for table structure (TEDS/GriTS or proxy quality signals).
- Persist table cell provenance (page anchor + cell coords) and structure metadata.
- Add low-confidence fallback: crop table images and repair with a VLM or DI.
- Keep a config switch to select extractor at runtime (pre-launch, default on).

Success metrics:
- Table structure accuracy improves on eval set (TEDS/GriTS or field-level F1).
- Fee tables produce multi-column headers and row cells (no single-column collapse).
- Extraction coverage: table detection + structure recovery for all table pages.

Validation/rollback:
- Shadow extraction to compare outputs; fallback to previous extractor via config switch.

Effort: 3-6 weeks
Dependencies: P0

## P3 — Schema-Aware Chunking + Parent/Child Index (Updated)

Objective: preserve table semantics and keep context intact during retrieval.

Steps:
- Emit parent table chunks (full table in Markdown/HTML).
- Emit child chunks as header+row key/value entries.
- Store section headings and page anchors in child metadata.
- For small tables, also store compact table summaries.

Success metrics:
- Table queries retrieve correct row-level chunks with parent context.
- Fees-credit-cards top-3 includes fee rows and correct values.
- Reduced hallucination on table questions.

Validation/rollback:
- Toggle back to legacy chunking if retrieval regressions appear.

Effort: 2-3 weeks
Dependencies: P2

## P4 — Table-Intent Routing + Reranking (Updated)

Objective: route fee/charges/rates queries to structured tables reliably.

Steps:
- Add table-intent detection (query classifier or keyword+embedding rules).
- Build a per-tenant table-header token frequency model (IDF-like); treat high-frequency tokens as generic.
- Route and rerank using specificity: require at least one low-frequency token match for table-first routing.
- Prefer structured table chunks when intent matches.
- Rerank with header/column match signals.
- Render snippets with row context and header labels.

Success metrics:
- Fees-credit-cards top-1/top-3 contain relevant fee rows.
- Not-found accuracy improves on unrelated queries.
- Lower incidence of title-only or artifact chunks.

Validation/rollback:
- Disable table-intent routing and revert to baseline ranking.

Effort: 1-2 weeks
Dependencies: P3

## P5 — Multi-Tenant Scale + Hardening

Objective: secure, performant retrieval at scale with strict tenant isolation.

Steps:
- Partition vector tables by business_profile_id where feasible.
- Add per-partition ANN indexes; ensure metadata pre-filters run before vector search.
- Use tenant-scoped caching and auditing.
- If using third-party DI/VLM, enforce tenant data boundaries and retention controls.

Success metrics:
- Stable latency under load at target tenant scale.
- No cross-tenant leakage in retrieval or caches.
- Cost per page/query within budget targets.

Validation/rollback:
- Revert to monolithic indexes or simpler routing if indexes regress.

Effort: 3-6 weeks
Dependencies: P3, P4

## P6 — Prompt Contract + Response QA

Objective: align prompts with table-aware retrieval and enforce source-anchored answers.

Steps:
- Define a single response contract (truthfulness, table formatting, not-found behavior).
- Unify system prompts across orchestrators (MCP + legacy) to prevent conflicts.
- Add table-intent response templates (header + row values + units/currency).
- Enforce “no-source ⇒ no-answer” in regulated flows.
- Add prompt regression tests in the eval harness.

Success metrics:
- Faithfulness improves, hallucination rate drops.
- Consistent table answers across agents.
- Not-found accuracy improves on unrelated queries.

Validation/rollback:
- Revert to prior prompt bundles if regression is observed.

Effort: 1-2 weeks
Dependencies: P4
