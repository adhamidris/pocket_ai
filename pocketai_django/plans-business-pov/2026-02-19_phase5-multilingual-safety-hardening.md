# Phase 5 Plan — Multilingual & Safety Hardening

## Plan
1. Strengthen lexicon normalization to handle Arabic script variants:
   - remove Arabic diacritics and tatweel
   - normalize Arabic/Persian digits
   - normalize common Arabic glyph variants (Alef forms, Yeh/Kaf variants).
2. Reuse canonical normalization in runtime tokenization for classifier and rewriter so Arabic queries and lexicon terms match consistently.
3. Harden tenant isolation in lexicon snapshot reads:
   - require persisted tenant IDs for lexicon operations
   - enforce synonym query joins on both synonym tenant and canonical-term tenant
   - skip and log any inconsistent synonym rows as safety defense.
4. Harden classifier cache isolation by including tenant identity in context signatures to prevent cross-tenant cache reuse.
5. Add multilingual + isolation tests:
   - Arabic normalization parity tests
   - Arabic query classification tests
   - cross-tenant classifier cache isolation tests.

## Business POV
1. Scenario: Arabic tenant asks with diacritics, while ingested text has plain letters  
Expected UX: retrieval/classification still matches correctly; fewer false “not found” experiences.

2. Scenario: two tenants use similar vocabulary (“orders”, “status”), but operate independently  
Expected UX: no tenant can inherit cached routing decisions from another tenant session.

3. Scenario: data inconsistency/corruption in lexicon synonym rows  
Expected UX: system drops inconsistent rows safely and continues operating instead of leaking terms across tenants.

4. Success indicators
- Better Arabic intent/routing consistency across equivalent spellings.
- Zero observed cross-tenant lexicon/cache bleed in diagnostics/tests.
- Stable latency (normalization is local string processing only).

## Implementation Status
Completed on 2026-02-19 with:
1. Arabic-aware canonical normalization in `apps/rag/tenant_lexicon.py`:
   - Unicode NFKC normalization
   - Arabic/Persian digit normalization
   - diacritics and tatweel stripping
   - common Arabic glyph canonicalization
2. Runtime classifier/rewriter normalization alignment:
   - classifier tokenization and phrase matching now uses canonical lexicon normalization
   - rewriter tokenization and document-mention checks now use canonical lexicon normalization
   - Arabic follow-up indicators/pronouns added for conversational rewrite detection
3. Tenant isolation hardening:
   - lexicon service now requires persisted tenant IDs
   - snapshot synonym query enforces both synonym tenant and canonical-term tenant matching
   - classifier context signature now includes `tenant_id` to partition cache entries by tenant
4. Test coverage added for Phase 5:
   - Arabic normalization/tokenization behavior
   - Arabic intent classification and Arabic rewrite detection
   - classifier cache partitioning by tenant ID
   - corrupted cross-tenant synonym row exclusion from snapshots

Verification command used:
`cd pocketai_django && .venv/bin/python manage.py test apps.rag.tests.test_tenant_lexicon apps.rag.tests.test_query_classifier apps.rag.tests.test_query_rewriter --noinput`
