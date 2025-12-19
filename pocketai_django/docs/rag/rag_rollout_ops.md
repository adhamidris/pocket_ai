# Phase 6 – Rollout & Operations

This guide captures the operational tooling that landed in Phase 6. Use it as the
runbook for turning on the new deterministic alias/entity stack, running
backfills, and keeping observability tight as usage scales.

## Feature Flags

Every `BusinessProfile.metadata` now carries a `features` block with three
feature gates:

- `alias_lookup`: deterministic identifier pathway
- `entity_chunking`: per-entity JSON chunks + alias extraction
- `hybrid_search`: ANN + lexical fusion with reranking

### Managing cohorts

```
python manage.py manage_feature_flags --business-id <uuid> --enable alias_lookup --enable entity_chunking
python manage.py manage_feature_flags --cohort beta --disable hybrid_search --dry-run
python manage.py manage_feature_flags --all --list
```

The command validates features, prints before/after state per business, and will
only edit *all* tenants when `--all` is passed explicitly. Dry runs never hit the
database but still show the future state.

New tenants inherit all three flags as `True`; storing the state in metadata as
part of `BusinessProfile.save` keeps it versioned alongside the tenant record.

## RAG Warmup & Embedding Cache

- Django now warms RAG embeddings via `apps.rag.apps.RagConfig.ready()`
  whenever `RAG_USE_MCP_ORCHESTRATOR` is `True`, so workers download or
  initialize the embedding backend before serving traffic.
- Run `python manage.py warm_embeddings` during deploy/build steps to populate
  the FastEmbed cache (or trigger the remote provider) and bake the resulting
  `~/.cache/fastembed/` directory (keyed by `EMBED_MODEL`) into your container
  image or shared volume.
- This warmup path is provider-agnostic; when `EMBED_PROVIDER=openai`, the
  command simply instantiates the OpenAI client and exits, while local
  FastEmbed backends reuse the cached weights at runtime.
- FastEmbed is pinned to `0.5.1` in `requirements.txt` to keep pooling behavior
  stable for `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` and
  avoid warning noise from upstream changes.
- MCP now short-circuits duplicate `search_knowledge` calls when a prior
  search already produced read-required snippets, prompting the model to issue
  `read_knowledge` instead of re-running the same query.
- Prompt guidance now instructs the agent to pack bilingual (Arabic/English)
  variants into the first `search_knowledge` call and to go straight to
  `read_knowledge` whenever snippets set `read_required`, so redundant searches
  are avoided unless the visitor provides new constraints.
- Multi-product/store sales queries now prioritize `table_aggregate` before
  `read_knowledge`, so the model fetches deterministic row totals in one call
  instead of reading many spreadsheet pages.
- A new `list_tables` tool lets the model enumerate active spreadsheet uploads (names + sheet hints) per tenant, so it can grab the correct `document_id` once and reuse it across every aggregation instead of running another `search_knowledge`.
- The `table_aggregate` tool accepts a `columns` array so the agent can request
  only the stores/customers the visitor named, keeping payloads and latency
  low while reusing cached rows for follow-up questions in the same turn.
- `table_aggregate` now supports a `match_values` array so the LLM can batch multiple products/stores in one call instead of issuing sequential aggregations for each item.
- Table scans are cached per upload for the rest of the turn, so once the model looks at a sheet it can reuse the hydrated rows for subsequent `table_aggregate` calls without hitting the ORM again.

### Embedding Model Changes (Multilingual)

- Default deployments now use a multilingual `EMBED_MODEL` so Arabic/mixed-language tenants work out of the box.
- Keep `EMBED_DIM=384` unless you intentionally migrate the `VectorField` dimension in Postgres.
- When changing `EMBED_MODEL`, re-embed existing chunks so vector search stays consistent:
  - Whole instance: `python manage.py reembed_missing_chunks --all --force --batch-size 32`
  - Single tenant: `python manage.py reembed_missing_chunks --all --business <uuid|slug> --batch-size 32`
  - Single upload: `python manage.py reembed_missing_chunks --all --upload <uuid> --batch-size 32`

## Backfill Workflow

`python manage.py backfill_knowledge_aliases` replays ingestion so entity chunks
and alias indexes match the modern schema.

Common invocations:

```
# Plan a cohort without touching production rows
python manage.py backfill_knowledge_aliases --business-id <uuid> --dry-run

# Process 20 uploads per run, pausing 2 seconds between batches
python manage.py backfill_knowledge_aliases --business-id <uuid> --limit 20 --batch-size 5 --sleep 2

# Resume a paused run by skipping uploads until the marker is seen
python manage.py backfill_knowledge_aliases --business-id <uuid> --resume-after <upload_id>
```

The command logs entity/alias counts, highlights truncated JSON or pending
embeddings, and throttles between batches so live traffic stays responsive.
Provide cohorts (business IDs or upload IDs) to avoid sweeping the entire table
accidentally.

## Observability & Dashboards

Retrieval logging now stamps every request with:

- `diagnostics.request_id`
- the active feature state (`feature_flags`)
- stage decisions (`alias_exact`, `hybrid`, `fallback`, `alias_disabled`)
- candidate counts, cache hit/miss stats, and vector distance stats

These diagnostics are persisted inside `KnowledgeDriftSample.metadata`, so they
drill straight into dashboards and alerting.

Use `apps.knowledge.knowledge_ops.KnowledgeOpsDashboard.snapshot(business)` to
power admin views. Each snapshot includes:

- **Alias Coverage:** alias/entity counts, identifier hit rate, cache hit rate
- **Search Stages:** alias vs. hybrid vs. fallback counts, not-found spike count
- **Ingestion Health:** queued/running/deferred jobs, truncation detections,
  pending embedding backlog
- **Latency:** p50/p95 across the latest retrieval samples

Because snapshots are pure queries, you can expose them over an admin API, send
them to Grafana, or run them in scheduled reports.

### MCP / Tool-RAG Logs (Chat Portal)

In MCP mode, additional structured logs are emitted for per-turn and per-tool visibility:

- `mcp.trace stage=turn.summary` — turn duration, tool count, tool error codes, throttle hits, and budget usage.
- `mcp.trace stage=search.performance` — latency breakdown for `search_knowledge` (warns when it exceeds `MCP_SLO_SEARCH_WARN_MS`).
- `mcp.trace stage=read_knowledge.performance` — unified retrieval latency + routing flags (engine, dataset/table/text, truncation).
- `mcp.trace stage=dataset.query` / `mcp.trace stage=table.aggregate` — tabular query timing, row counts, truncation; headers include `conversation=` + `document_id=` for easy grep.

Common quick checks:

- Slow turns: `rg \"stage=turn\\.summary\" var/logs/rag.log | rg \"slo=slow\" | tail`
- Slow retrieval: `rg \"stage=read_knowledge\\.performance\" var/logs/rag.log | rg \"slo=slow\" | tail`
- Dataset query engine fallback: `rg \"stage=dataset\\.duckdb_fallback\" var/logs/rag.log | tail`

### Ingestion Job Logs

Ingestion now emits structured job markers in addition to the existing plain logs:

- `rag.trace stage=ingest.job_start` — upload/job metadata at start.
- `rag.trace stage=ingest.job_done` — duration, output sizing, dataset-mode flags, and SLO warnings (`INGEST_SLO_WARN_MS`).

## Phase 1 Recall Knobs (Config-Only)

- Defaults now favor recall: `RAG_ALIAS_FTS_THRESHOLD=0.25` and `RAG_VECTOR_DISTANCE_CEILING=0.5`.
- Per-business overrides live under `BusinessProfile.metadata['rag_overrides']`:
  - `alias_fts_threshold` (float)
  - `vector_distance_ceiling` (float)
  - `max_snippets_per_search` (int)
  - `alias_chunks_per_upload` / `ann_chunks_per_upload` (int)
- Phase 2 lexical controls:
  - `alias_filler_tokens` (list) to extend the generic filler set (no vertical words baked in)
  - `fts_token_min_length` (int, default 4) for significant token gating
  - `fts_condense_max_tokens` (int, default 5) for the condensed FTS query
  - `lexical_threshold_short|medium|long` (floats) to tune trigram thresholds by query length
- Phase 3 table controls:
  - Table search now falls back automatically when chunk hits are empty/weak and tables exist.
  - `table_column_hints` (list) to augment the semantic column hints (defaults: name/title/plan/brand/company/product/clinic/doctor/provider/program/category).
  - Diagnostics log `tables_available`, `tabular_columns_hint`, and `table_reason` when table search runs.
- Phase 4 alias/name separation:
  - Alias short-circuit now only triggers for identifier-like queries; natural-name queries flow to hybrid/table instead.
  - Alias fuzzy search only runs when identifier-like tokens are present.
  - Ingestion already limits aliases to identifier-shaped values; keep free-text names in attributes/content.
- Retrieval diagnostics log the effective values (`alias_fts_threshold`, `vector_distance_ceiling`, `snippet_limit`, per-upload caps) so you can audit changes post-run.
- Example override:
  ```json
  {
    "rag_overrides": {
      "alias_fts_threshold": 0.24,
      "vector_distance_ceiling": 0.55,
      "max_snippets_per_search": 5,
      "ann_chunks_per_upload": 4,
      "fts_token_min_length": 3,
      "fts_condense_max_tokens": 6,
      "lexical_threshold_long": 0.12
    }
  }
  ```

## Alerts & Runbooks

QualityMonitor now emits alerts for:

- Alias hit rate below `RAG_DRIFT_ALIAS_HIT_THRESHOLD`
- Not-found spikes above `RAG_DRIFT_NOT_FOUND_THRESHOLD`
- Ingestion truncation rate above `RAG_DRIFT_TRUNCATION_THRESHOLD`

Recommended runbook flow:

1. Check feature flags for the tenant (`manage_feature_flags --list`).
2. Pull a snapshot via `KnowledgeOpsDashboard` to inspect alias coverage and
   ingestion backlog.
3. Use `backfill_knowledge_aliases --dry-run` to confirm affected uploads.
4. If aliases are missing, re-run the backfill in throttled batches.
5. Verify new retrieval logs show `alias_exact` hits and improved cache metrics.

## Change Management Checklist

Before rolling the modern stack to another cohort:

1. Run the Phase 5 evaluation suite (`python manage.py run_rag_eval`).
2. Flip feature flags for the pilot group via the management command, using
   `--dry-run` first.
3. Schedule backfill windows per tenant (limit + resume options let you pause).
4. Monitor `KnowledgeOpsDashboard` output for alias coverage and latency swings.
5. Communicate the schedule + expected impact to customer success/on-call.

Use metadata versioning to keep legacy tenants on the old behavior by pinning
individual flags to `False` until they opt in.

## Capacity & Scaling Notes

- Watch `KnowledgeOpsDashboard.ingestion.pending_embeddings` to size embedding
  workers; IVFFLAT probe counts and vector distance stats are logged per search.
- Alias counts can explode on certain JSON feeds; prune synonyms in ingestion or
  raise the per-entity alias cap before enabling `alias_lookup` for those tenants.
- `backfill_knowledge_aliases` exposes throttling knobs so you can drip through
  large uploads without starving production. Pair it with the ingestion backlog
  warning already emitted by the ingestion service.

## Support Feedback Loop & Training

Conversation metadata now stores the last retrieval diagnostics and feature
flags, and each `KnowledgeFeedbackCase.metadata` captures those details. Support
can open the admin record, copy the stored request ID, and replay the query in
the evaluation harness with the same feature context.

Share this document (and the refreshed `docs/ops/manual_qa_playbook.md`) during
handoff sessions. Include:

- Where to inspect feature flags and snapshots
- How to trigger and monitor backfill jobs
- How to triage “trip not found” issues using the stored diagnostics
- Escalation steps: workers to restart, caches to invalidate

Recording a short demo of the workflow (flag flip → backfill → dashboard check)
helps onboard new on-call engineers quickly.
