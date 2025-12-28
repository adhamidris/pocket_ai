# P5 Partitioning Runbook (64 partitions)

This file tracks the exact steps, commands, and checks executed during the P5 rollout.

## Phase 1: KnowledgeEntity chunk_id refactor

- Planned:
  - Add `chunk_id` UUID field to `KnowledgeEntity`.
  - Backfill from legacy `chunk` FK.
  - Remove legacy `chunk` FK.
  - Update code paths to resolve chunks by ID.
- Applied:
  - Updated models + ingestion + alias lookup to use `chunk_id`.
  - Added migration `accounts.0034_knowledge_entity_chunk_id`.
  - Fixed ingestion syntax error around `KnowledgeIngestionError` handling (indentation).
  - Command: `python manage.py migrate` (after fix).

## Phase 2: Partitioned chunk tables (64 hash partitions)

- Planned:
  - Create partitioned tables for chunk + shadow chunk.
  - Apply per-partition indexes (HNSW, GIN/trgm, business filters).
  - Add unique constraint including `business_profile_id`.
  - Recreate RLS policies on new tables.
  - Update refresh procedure for partitioned indexes.
- Applied:
  - Added migration `accounts.0035_partition_knowledge_chunks` with 64 hash partitions.
  - Fixed FK table name to `accounts_business_profile`.
  - Ensured legacy table drop occurs before new unique constraint creation.
  - Added partition creation inside index loop to guarantee partitions exist.
  - Command: `python manage.py migrate accounts 0035_partition_knowledge_chunks`.

## Phase 3: Cutover + validation

- Planned:
  - Run migrations.
  - Re-ingest via eval harness.
  - Validate pruning with `EXPLAIN`.
  - Run `ANALYZE` on parent tables.
- Applied:
  - Eval run: `RAG_PDF_TABLE_EXTRACTOR=azure:layout python manage.py run_rag_eval --set fees-credit-cards --baseline --output var/logs/rag_eval_p5.json`
  - Result: `[fees-credit-cards] status=fail top1=1.00 top3=1.00 mrr=1.00 src_acc=1.00 beh_acc=1.00 not_found_acc=1.00`
  - Partition pruning check:
    - `EXPLAIN SELECT * FROM accounts_knowledge_upload_chunk WHERE business_profile_id = '75bb3104-37ad-40fd-bc79-18d5992afc66' LIMIT 5;`
  - `ANALYZE accounts_knowledge_upload_chunk;`
  - `ANALYZE accounts_knowledge_upload_shadow_chunk;`

## Phase 4: Validation + maintenance

- Applied:
  - Updated refresh procedure for partitioned HNSW indexes in `accounts.0036_refresh_chunk_embedding_index_partitioned`.
  - Command: `python manage.py migrate accounts 0036_refresh_chunk_embedding_index_partitioned`.
