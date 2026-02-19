# Phase 6 Plan — Validation + Direct Cutover

## Plan
1. Add multilingual intent regression tests (Arabic + English + mixed-language phrasing).
2. Add multi-industry routing tests (retail/operations/healthcare phrasing).
3. Add tenant-isolation validation tests for runtime classification context.
4. Validate knowledge-search table context wiring for:
   - tenant lexicon counts
   - Arabic aggregate intent behavior
   - tenant ID propagation into classifier context.
5. Enforce a dedicated CI gate for the RAG validation suite before full test run.

## Business POV
1. Scenario: pre-production tenants from different industries ask similar questions  
Expected UX: intent classification remains accurate and not tuned to one industry’s vocabulary.

2. Scenario: Arabic-speaking users ask aggregate/list/compare questions  
Expected UX: intents are detected reliably and consistently, not treated as generic exploratory queries.

3. Scenario: two tenants run similar queries at the same time  
Expected UX: lexicon/routing context stays isolated, no cross-tenant bleed.

4. Scenario: aggressive direct cutover without feature flags  
Expected UX: deployment is still protected by deterministic tests and CI gates.

## Implementation Notes
- Scope is testing/gating only.
- No rollback feature flag added (as requested).
- If failures appear after cutover, fix-forward or commit-level revert remains operational path.

## Validation Suite (CI Gate)
`python manage.py test apps.rag.tests.test_query_classifier apps.rag.tests.test_query_rewriter apps.rag.tests.test_tenant_lexicon apps.rag.tests.test_intent_fallback apps.rag.tests.test_knowledge_search_service apps.knowledge.tests.test_lexicon_learning_phase_three --noinput`
