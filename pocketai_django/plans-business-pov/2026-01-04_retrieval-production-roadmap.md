# Retrieval & Production Readiness Roadmap — Business POV

## Plan

### P0 — Stop “slow + vague” retrieval (stability first)
1. Make retrieval bounded and predictable:
   - Disable parallel fanout by default; keep fanout sequential and capped.
   - Add hard query timeouts for retrieval DB work (fail fast, never hang minutes).
2. Align prompt + policy to avoid tool thrash:
   - Ensure the “one search per visitor message” rule matches the prompt guidance.
   - Prevent tool misuse (e.g., `read_knowledge` must use tool-provided IDs only).
3. Fix “entity drift” (wrong document/table) for noisy queries:
   - Make lexical retrieval robust to extra/generic words (avoid strict “all tokens must match” failure modes).
   - Preserve and prioritize “anchor tokens” from the visitor message (e.g., product name, plan name, SKU).
   - Avoid dataset-only tooling for PDF uploads; rely on document table chunks instead.
3. Add missing observability:
   - Break down retrieval timing so “where did 300s go?” is attributable (table context, DB waits, rerank, etc.).
4. Make caching production-like:
   - Replace dev-only `LocMemCache` assumptions with Redis-backed shared caching (per-tenant + per-collection keys).

**Acceptance criteria**
- p95 retrieval time < 8s for simple queries; no multi-minute hangs.
- One coherent retrieval attempt per visitor message (no repeated “Searching…” loops).
- “Tell me about X” returns evidence that explicitly mentions X (no Swype/other-product drift for named-entity queries).
- Tool traces clearly show where time is spent (no “unexplained” latency buckets).

### P1 — Improve evidence quality (answerable snippets)
1. Move expensive table context off the hot path:
   - Precompute per-tenant/per-upload table profiles during ingestion (columns, row labels, stats).
2. Make table evidence answer-ready:
   - Auto-expand the specific rows/cells needed for pricing/fees/rates questions.
   - Normalize numeric/percent/currency values during ingestion to avoid OCR artifacts.
3. Collection-aware retrieval:
   - Default retrieval scope to the agent’s assigned collections to improve precision.

**Acceptance criteria**
- Fee/price questions consistently return concrete, “professional” answers grounded in tables (not vague category lists).
- Retrieval uses collection scoping to reduce wrong-document hits.

### P2 — Production-grade document search backend (low maintenance at scale)
1. Index tenant knowledge into Azure AI Search (hybrid text + vector):
   - Include tenant + collection filters in the index schema.
2. Use Azure AI Search as the primary document retrieval engine:
   - Keep Postgres as the system of record; use it for tables/datasets + core app entities.
3. Lifecycle correctness:
   - Overwrite updates the index; delete removes from active retrieval (and invalidates caches).

**Acceptance criteria**
- Retrieval performance remains stable as tenants grow to 2,000–10,000 uploads.
- Operational burden is low (managed search; predictable scaling).

### P3 — Production operations: safety, verification, audit, and active‑passive deployment
1. Privacy by default:
   - Detect sensitive columns/fields and enforce platform-level refusal for portal visitors unless verified.
2. Verified lookup (OTP) when tenant enables it:
   - Email/SMS via Azure Communication Services; WhatsApp via Twilio/Meta.
3. Audit logging:
   - Retain access logs for disputes without storing raw document content/PII (store IDs + hashes + metadata).
4. Azure deployment target (active‑passive):
   - Primary: UAE North; DR: Saudi Central; tenant subdomains (`tenant.yourapp.com`) behind a global entrypoint.

**Acceptance criteria**
- No sensitive data leakage for portal visitors.
- Verification is only triggered when needed and configured by the tenant.
- Audit logs support investigations without violating deletion/purge semantics.

---

## Business POV

### Scenario 1: End user asks “credit card fees” (PDF with tables)
- Before: long “Searching…” spinner, repeated tool attempts, vague answer without numbers.
- After: one retrieval pass returns table evidence + targeted row/cell expansion; agent answers with a clear breakdown and asks one relevant follow-up (e.g., card type).
- Success signals: p95 retrieval < 8s; fewer “I’ll search…” duplicate lines; measurable increase in “answer contains concrete fees” rate.

### Scenario 1b: End user asks “tell me about X” (named product)
- Before: retrieval drifts to a “similar-looking” table (wrong product) when the query contains extra generic words (features/benefits/requirements).
- After: retrieval anchors on X; evidence packets include the X row/table first; the answer is product-specific and clearly cites the correct fees/features.
- Success signals: “entity mentioned in evidence” rate > 99%; near-zero wrong-product answers for named-entity queries.

### Scenario 2: Tenant uploads 5,000 documents and publishes the portal link
- Before: retrieval gets slower as the tenant grows; fanout multiplies load; intermittent timeouts and vague answers.
- After: Azure AI Search handles large-scale retrieval; collection scoping keeps the search space small; latency remains consistent.
- Success signals: stable p95 retrieval across tenant size; reduced infra incidents; lower support load from tenants complaining about accuracy/latency.

### Scenario 3: End user asks for customer-specific information (PII risk)
- Before: risk of accidental disclosure if a tenant forgets to configure identifiers; inconsistent model behavior.
- After: platform refuses by default and requests verification only when tenant enabled it and the intent requires it.
- Success signals: zero PII leakage incidents in logs; clear user messaging; tenants see predictable “verification required” behavior.

### Scenario 4: Tenant overwrites pricing policy document
- Before: old answers keep appearing due to stale caches/indexes; confusion for end users.
- After: overwrite triggers re-index; retrieval uses latest content; cache invalidation is shared (Redis) and deterministic.
- Success signals: “stale answer” complaints drop; internal checks show citations only from the latest upload version.

### Scenario 5: Tenant deletes a document (purge from active retrieval)
- Before: document might still appear via cached results or delayed index removal.
- After: delete removes it from search index and invalidates all related caches; audit logs remain as metadata-only evidence of access.
- Success signals: deleted docs never appear in subsequent retrieval; audit logs remain usable for disputes without storing raw content.
