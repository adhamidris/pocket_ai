# Knowledge Ingestion Worker

Uploads are ingested asynchronously via `KnowledgeIngestionJob` rows. For production you should run the ingestion worker as a long-lived process so uploads become searchable automatically (no manual queue draining).

## Run (long-lived)

```bash
python manage.py knowledge_ingestion_worker
```

This is an alias for:

```bash
python manage.py process_knowledge_ingestion --watch
```

## Queue Health Telemetry

When running in watch mode, the worker emits `rag.trace stage=ingest.queue_health` periodically (configurable via `INGEST_WORKER_HEALTH_INTERVAL_SECONDS`), and escalates to warning-level logs when thresholds are crossed:

- `INGEST_QUEUE_WARN_BACKLOG`
- `INGEST_QUEUE_WARN_OLDEST_SECONDS`
- `INGEST_QUEUE_WARN_FAILED_LAST_HOUR`

## Notes

- The worker uses row locks (`select_for_update` + `skip_locked` when supported) so multiple worker processes can run safely.
- Duplicate ingest jobs for the same upload are cancelled at claim-time (QUEUED/DEFERRED duplicates).
