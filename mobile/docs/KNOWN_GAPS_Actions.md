# KNOWN GAPS — MCP / Actions (UI-only demo)

This module is UI-first and mocks backend behavior. The following items are intentionally deferred for a production system:

- Real execution engine: durable job queue, retries/backoff, timeouts, idempotency keys, dedupe.
- Approvals workflow: routing, SLAs/expiry, multi-step approvals, audit reasons, override paths.
- Secrets vault integration: no secrets stored in-app; use server-side KMS/Vault, rotation policies.
- Structured effects & rollback contracts: typed effect schemas, compensating actions, partial failure rules.
- Concurrency & sequencing: per-entity locks, conflict detection, exactly-once side-effects.
- Per-tenant & per-actor limits: hierarchical rate limits/quotas, burst/bucket policies, throttling responses.
- Observability: traces, metrics, structured logs, run timelines sourced from backend events.
- Sandbox/test envs: dry-run/sim-only environments, fixture data, safe no-op connectors.
- Audit immutability: append-only audit log with signatures, tamper-evident storage.
- Catalog versioning: action spec registry, compatibility checks, breaking-change migration flow.
- Policy layering: org/global policies, per-agent capability packs, exception lists, time-based toggles.
- Safety gates: PII/PHI redaction at source, DLP checks, guardrail evaluations on the server.
- Cross-app coherence: backend-enforced business hours, guardrails, and limits (UI mirrors state only).

Notes:
- All navigation, evaluator checks, and analytics are demo-only.
- Import/Export bundles lack signing and schema version negotiation.
- Rollback is a UI placeholder; real rollback requires effect journaling and contracts.
