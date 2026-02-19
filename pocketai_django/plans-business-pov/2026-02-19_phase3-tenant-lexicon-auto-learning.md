# Phase 3 Plan: Tenant Lexicon Auto-Learning During Ingestion

## Technical plan
1. Add a dedicated ingestion-time lexicon learner module (`apps/knowledge/lexicon_learning.py`).
2. Extract tenant terms from:
   - ingestion entities and aliases,
   - table headers/titles/section headings,
   - document labels,
   - schema-like ingestion metadata (including integration/tool schemas when present).
3. Persist learned terms through `TenantLexiconService` into:
   - `KnowledgeLexiconTerm` (canonical terms),
   - `KnowledgeLexiconSynonym` (synonym forms).
4. Enforce practical limits to avoid ingestion latency spikes:
   - max learned entity terms per upload,
   - max learned attribute terms per upload,
   - max synonyms per term,
   - metadata scan limits.
5. Wire learning into `_persist_extraction(...)` as best-effort logic:
   - ingestion success must not depend on lexicon-learning success,
   - write learning stats to `upload.ingestion_metadata["lexicon_auto_learning"]`.
6. Add targeted tests for:
   - extraction-to-lexicon learning coverage,
   - idempotent re-ingestion behavior,
   - ingestion hook metadata recording.

## Business POV
1. Multi-industry onboarding (new tenant uploads first docs)
   - Before: routing quality depends on global heuristics and manually seeded terms.
   - After: each tenant starts building their own term map from day one, improving relevance without custom hard-coding.
   - Success metric: lower empty/weak retrieval rate in first-week uploads.

2. Spreadsheet-heavy operations tenant (finance/ops/admin)
   - Before: table header semantics are underutilized by runtime routing.
   - After: table headers become tenant attributes automatically, improving row/table intent alignment.
   - Success metric: better answer quality on column-specific questions.

3. Tool-integrated tenant (MCP/integration schemas)
   - Before: integration field names stay mostly outside tenant lexicon memory.
   - After: schema field names are learned as tenant attributes, improving task phrasing recognition.
   - Success metric: fewer clarifications for tool-field-driven prompts.

4. Re-ingestion and sync updates
   - Before: repeated ingestion can create inconsistent vocabulary if handled manually.
   - After: upsert-based lexicon writes stay stable across sync cycles.
   - Success metric: no uncontrolled growth of duplicate terms.

5. Reliability and founder operations
   - Before: lexicon enhancement and ingestion resilience are tightly coupled risk-wise.
   - After: lexicon learning is best-effort; ingestion still succeeds on learning errors.
   - Success metric: ingestion job success rate remains stable after rollout.
