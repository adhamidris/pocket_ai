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

Use `apps.services.knowledge_ops.KnowledgeOpsDashboard.snapshot(business)` to
power admin views. Each snapshot includes:

- **Alias Coverage:** alias/entity counts, identifier hit rate, cache hit rate
- **Search Stages:** alias vs. hybrid vs. fallback counts, not-found spike count
- **Ingestion Health:** queued/running/deferred jobs, truncation detections,
  pending embedding backlog
- **Latency:** p50/p95 across the latest retrieval samples

Because snapshots are pure queries, you can expose them over an admin API, send
them to Grafana, or run them in scheduled reports.

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

Share this document (and the refreshed `docs/manual_qa_playbook.md`) during
handoff sessions. Include:

- Where to inspect feature flags and snapshots
- How to trigger and monitor backfill jobs
- How to triage “trip not found” issues using the stored diagnostics
- Escalation steps: workers to restart, caches to invalidate

Recording a short demo of the workflow (flag flip → backfill → dashboard check)
helps onboard new on-call engineers quickly.
