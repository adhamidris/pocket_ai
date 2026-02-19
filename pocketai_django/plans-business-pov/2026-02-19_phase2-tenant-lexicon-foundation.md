# Phase 2 — Tenant Lexicon Foundation (Business POV)

## Plan
1. Add tenant-scoped lexicon tables for canonical terms and synonyms.
2. Store per-term metadata needed for future routing quality: term type, language tag, confidence, source, active state.
3. Add a reusable service layer for upsert + snapshot access with cache invalidation.
4. Add admin visibility to support founder/operator inspection and manual corrections.
5. Add tests for tenant isolation, idempotency, and cache behavior.

## Business POV
### Scenario 1: New tenant in a niche industry
- Before: intent logic depends on global defaults and misses tenant-specific vocabulary.
- After: tenant terms/synonyms can be stored as first-class data and prepared for runtime routing.
- Success signals: fewer “not found”/misrouted early queries after onboarding docs.

### Scenario 2: Multi-tenant language differences
- Before: language distinctions are implicit and hard to inspect.
- After: terms and synonyms are language-tagged (`en`, `ar`, etc.) per tenant.
- Success signals: cleaner multilingual routing roadmap and less manual prompt patching.

### Scenario 3: Founder operations/debugging
- Before: no single place to inspect tenant vocabulary decisions.
- After: superuser can inspect/edit lexicon records in admin, with source + confidence.
- Success signals: faster root-cause analysis when a tenant reports weak retrieval/routing.

### Trade-offs
- Additional schema and cache invalidation complexity.
- Requires follow-up Phase 3/4 integration to influence live routing decisions.
