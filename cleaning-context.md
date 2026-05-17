Sup codex, we recently were on another clean-up session that i want you to continue. but please confirm first that you are aware of context.

context dumped;
just to confirm, we are now putting every single micro-service in it's actual file so that everything is ordered like modern codebases, correct? or even similar?
1:03 AM

Yes, broadly correct, with one wording adjustment: we are not creating “micro-services” in the deployment sense. We are splitting a large runtime module into focused internal modules/components.

That is the right modern-codebase direction:

query_normalizer.py: query parsing/normalization concern
search_cache.py: cache keys, serialization, invalidation concern
table_context.py: table intent/context/metadata concern
contracts.py: shared dataclasses and constants
knowledge_search.py: stable public import/API surface
ai_orchestrator.py: still the remaining search implementation container, shrinking over time
The important principle is separation by responsibility, not just “one file per small thing.” We move a chunk only when it has a coherent purpose and tests can prove behavior stayed stable.

So yes: we’re turning the old giant legacy-style file into a more normal modular service layer, while keeping production runtime behavior intact.





1:03 AM
and that is the path to production , right? will i be requiring any additional changes later? just asking.
1:04 AM

Yes, this is the right path toward production-readiness, but it is not the whole production checklist by itself.

What we’re doing now is codebase hygiene and runtime consolidation:

fewer legacy paths
clearer active modules
safer imports
less agent confusion
easier testing/debugging
lower risk when changing retrieval/MCP behavior
You will still likely need additional production work later, mainly:

Behavior acceptance: decide/fix the two known broader RAG failures:
topic switch locking to primary document
JSON vs JSONL format detection expectation
Full regression pass: run broader backend/RAG/API tests, not only focused suites.
Observability review: make sure logs/metrics around MCP tool calls, retrieval, latency, and failures are useful in production.
Performance review: table-context cache, vector search, and reranking should be checked with realistic tenant data.
Security/privacy review: especially document visibility, internal uploads, tool permissions, and MCP external tool approval.
Deployment config: env vars, cache backend, DB indexes, embedding backend, migrations, workers, and storage settings.
So: this cleanup is absolutely part of the production path. It reduces the chance that production fixes are made in the wrong place. But before going live, we still need a proper production validation pass after the cleanup stabilizes.





1:04 AM
Okay let's continue our core task. 
1:05 AM

I’ll continue with the next coherent extraction from the remaining search container. I’m going to inspect the ranking/fusion/snippet-building area first and pick the smallest boundary that reduces ai_orchestrator.py without changing behavior.


Explored 1 file, 2 searches, ran 1 command
Oh hey before you continue, do you also make sure we are deleting or cleaning up after the separations so we don't have duplicates or dead codes or legacy anymore, correct?
1:06 AM

Yes. The cleanup rule is:

Extract active code into the correct module.
Delete the original duplicate block from the old file.
Update imports/tests/docs to the new location.
Search for stale references.
Run focused validation.
Only leave a bridge when it is intentional and temporary.
Example from the last chunk:

QueryNormalizer was moved to query_normalizer.py, then removed from ai_orchestrator.py.
Cache helpers were moved to search_cache.py, then removed from ai_orchestrator.py.
Table context helpers were moved to table_context.py, then removed from ai_orchestrator.py.
Tests that patched old internal locations were updated.
README was updated so future agents don’t reintroduce the old layout.
The only remaining direct apps.rag.ai_orchestrator import is the intentional public bridge in knowledge_search.py, because KnowledgeSearchService itself still lives in ai_orchestrator.py for now. As we keep extracting, that bridge should eventually disappear too when the service gets moved/renamed cleanly.





1:06 AM
Ok go ahead continue.
1:06 AM


Worked for 6m 6s
Continued the cleanup and kept duplicate/dead code removed after each split.

What changed:

Added evidence_grouping.py for snippet evidence grouping/conflict collapse.
Added ranking_features.py for text quality penalties, lexical/entity/alias scoring, phrase/proximity boosts, and text evidence spans.
Removed those original helper blocks from ai_orchestrator.py.
Updated README.md with the new module map.
ai_orchestrator.py is now down to about 8004 lines.
Validation passed sequentially:

.venv/bin/python manage.py check
apps.rag.tests.test_knowledge_search_service → 63 OK
apps.rag.tests.test_table_row_expansion → 6 OK
apps.rag.tests.test_agentic_rag_regression_evals → 7 OK
apps.api.tests.test_chat_portal apps.api.tests.test_chat_portal_files → 7 OK
Stale-reference check still shows only the intentional bridge:
apps/rag/knowledge_search.py imports KnowledgeSearchService from apps.rag.ai_orchestrator.

Next best target: move the main _rerank_candidates method itself into a dedicated reranking module, now that its feature helpers have been separated and validated.


README.md
Document · MD
Open

Edited 4 files
+563
-248
Undo
Review
apps/rag/ai_orchestrator.py
apps/rag/evidence_grouping.py
apps/rag/ranking_features.py

Show 1 more file




1:12 AM
Okay continue
1:13 AM


Worked for 8m 17s
Continued cleanup is done and validated.

I finished the reranking.py extraction verification, then did one more safe cleanup pass:

Added metadata_helpers.py for pinned flags, topic normalization, and legacy PDF table-entity checks.
Removed those helpers from ai_orchestrator.py.
Deleted the unused TOPIC_KEYWORD_MAP instead of moving dead code.
Updated apps/rag/README.md with the current module map.
Cleaned generated __pycache__ after tests.
Current ai_orchestrator.py is down to 7,562 lines. The extracted active modules now include query_normalizer.py, search_cache.py, table_context.py, ranking_features.py, reranking.py, evidence_grouping.py, and metadata_helpers.py.

Validation passed:

.venv/bin/python manage.py check
.venv/bin/python manage.py test apps.rag.tests.test_knowledge_search_service --noinput
.venv/bin/python manage.py test apps.rag.tests.test_table_row_expansion --noinput
.venv/bin/python manage.py test apps.rag.tests.test_agentic_rag_regression_evals --noinput
.venv/bin/python manage.py test apps.api.tests.test_chat_portal apps.api.tests.test_chat_portal_files --noinput
Stale reference sweep is clean: the only remaining apps.rag.ai_orchestrator import is the intentional bridge in apps/rag/knowledge_search.py.

Next best cleanup target: split the large content-loading/read path from ai_orchestrator.py into a dedicated active module, likely around load_contents, load_page_window, load_chunk_contents, and structured table rendering.


README.md
Document · MD
Open

Edited 4 files
+466
-112
Undo
Review
pocketai_django/apps/rag/ai_orchestrator.py
pocketai_django/apps/rag/reranking.py
pocketai_django/apps/rag/README.md

Show 1 more file




1:21 AM
Okay go ahead continue. 
1:22 AM



Worked for 5m 32s
Continued the cleanup with another safe extraction.

What changed:

Added content_serialization.py for structured export serialization, table row samples, labels, summaries, and content trimming.
Removed that helper block from ai_orchestrator.py, so it is not duplicated.
Updated apps/rag/README.md with the new module.
Cleaned generated __pycache__.
ai_orchestrator.py is now down to 7,166 lines.

Validation passed:

.venv/bin/python manage.py check
.venv/bin/python manage.py test apps.rag.tests.test_knowledge_search_service --noinput
.venv/bin/python manage.py test apps.rag.tests.test_table_row_expansion apps.rag.tests.test_rag_scalable_fixes --noinput
.venv/bin/python manage.py test apps.api.tests.test_chat_portal apps.api.tests.test_chat_portal_files --noinput
Stale reference sweep is clean. The only remaining direct import from apps.rag.ai_orchestrator is still the intentional bridge in apps/rag/knowledge_search.py.

Next target: extract the actual read/load methods (load_contents, load_page_window, load_chunk_contents) into a dedicated read service/mixin. That’s larger than today’s helper extraction, but it’s the correct next production-safe step.


README.md
Document · MD
Open

Edited 3 files
+407
-403
Undo
Review
apps/rag/content_serialization.py
apps/rag/ai_orchestrator.py
apps/rag/README.md

confirm understanding of the task please.


----

Yes, I’m aware of the context.

We’re continuing the modular cleanup of `apps/rag/ai_orchestrator.py`: extracting coherent active responsibilities into focused modules, deleting the original duplicated code from `ai_orchestrator.py`, updating imports/docs/tests, and checking for stale references after each move.

Current state from the dump:

- Already extracted: `query_normalizer.py`, `search_cache.py`, `table_context.py`, `ranking_features.py`, `reranking.py`, `evidence_grouping.py`, `metadata_helpers.py`, `content_serialization.py`
- `ai_orchestrator.py` is still the shrinking container for remaining search/read orchestration logic
- The only intentional remaining direct import from `apps.rag.ai_orchestrator` is the public bridge in `apps/rag/knowledge_search.py`
- Next intended target: extract the read/load path around `load_contents`, `load_page_window`, `load_chunk_contents`, and related structured table rendering into a dedicated read service/mixin
- Cleanup rule remains: move active code, delete the old duplicate, update references, run focused validation, and avoid leaving dead legacy paths behind

I’ll continue from there by inspecting the current codebase state first, since the actual workspace is what matters.

----

NOT only this file this whole session began as a moving-toward production cleaning up and separation and correction of messy AI slop style of nesting things and bloating files.. we were taking the whole thing progressively.

so when we are done with a phase i'd expect you mention what's next or at least ask if you can go take a codebase exploration round to see what needs organizing, cleaning, separation, etc.

we do not remove active runtime features or break the app.. we are just organzing AI Slop that built this platform

now understood?

----

Yes, understood now.

The real task is broader than `ai_orchestrator.py`: this is an ongoing production-readiness cleanup of the whole codebase, focused on undoing messy AI-generated structure: oversized files, misplaced responsibilities, duplicated logic, dead/legacy paths, awkward nesting, unclear module boundaries, and brittle imports.

The working rules are:

- Preserve active runtime behavior and product features.
- Do not delete working functionality just because it looks messy.
- Separate coherent responsibilities into appropriate files/modules.
- Remove duplicates only after the new location is wired and validated.
- Keep public APIs stable unless there is a deliberate migration.
- Update tests/docs/imports as we go.
- Run focused validation after each phase.
- When a phase is done, identify the next likely cleanup target or ask to do a broader codebase exploration pass.

So after finishing the current RAG/search cleanup phase, the natural next move is either:

1. Continue extracting the remaining read/load/search orchestration pieces from RAG, or
2. Do a fresh codebase exploration round to find the next worst “AI slop” areas across the platform.

I’ve got the mission now: production-oriented organization, separation, and correction across the whole platform, without breaking the app.

----

Continue what your agent colleage has kept last on his last round on the session dumped.

then we'll see what's next from there.

----

Continued the cleanup phase from the dumped session.

What changed:

- Added [content_reading.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/content_reading.py) for the active document/page/chunk read path:
  `load_contents`, `load_page_window`, `load_chunk_contents`, and their page/table helpers.
- Removed those methods from [ai_orchestrator.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/ai_orchestrator.py), so there is no duplicate read path left behind.
- Wired `ContentReadingMixin` into `KnowledgeSearchService`.
- Removed a true duplicate `_effective_neighbor_window` definition from `ai_orchestrator.py`.
- Updated [README.md](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/README.md) with the new module map.
- Fixed an MCP agentic search response contract issue in [tools.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/mcp/tools.py): refs were computing labels but not returning `label`, which broke noisy-corpus read tests.

Current size: `ai_orchestrator.py` is down to `6,480` lines. New `content_reading.py` is `670` lines.

Validation passed:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test apps.rag.tests.test_knowledge_search_service --noinput
.venv/bin/python manage.py test apps.rag.tests.test_table_row_expansion apps.rag.tests.test_rag_scalable_fixes apps.rag.tests.test_agentic_rag_regression_evals --noinput
.venv/bin/python manage.py test apps.mcp.tests.test_agentic_read_section_span --noinput
.venv/bin/python manage.py test apps.mcp.tests.test_agentic_read_regression_evals apps.api.tests.test_chat_portal apps.api.tests.test_chat_portal_files --noinput
.venv/bin/python manage.py test apps.rag.tests.test_knowledge_search_service apps.mcp.tests.test_agentic_read_section_span --noinput
```

Stale active import sweep is still clean: the only non-test direct `ai_orchestrator` import is the intentional bridge in `apps/rag/knowledge_search.py`.

Next sensible target from here: continue shrinking RAG by extracting the remaining chunk/table search helpers around table scoring, weak-hit detection, and auto-mode arbitration, or do a broader codebase exploration pass for the next largest AI-slop area outside RAG.

---

i'd like to finalize the rag first, but i got a quick question, what's the difference between rag/ and knowledge/

is it normal that both are separated?

---

Yes, it is normal that `rag/` and `knowledge/` are separated. In fact, that separation is usually healthier for production.

The difference is roughly:

`apps/knowledge/` is the **storage and ingestion domain**.
It should own things like uploaded knowledge files, extracted chunks, tables, pages, metadata, ingestion jobs, document status, visibility, database models, and persistence rules.

`apps/rag/` is the **retrieval and reasoning support domain**.
It should own things like query normalization, embedding search, lexical/vector ranking, reranking, table-aware retrieval logic, result caching, snippet shaping, and evaluation of retrieval quality.

So the flow is:

```text
knowledge = what exists in the system
rag       = how we search and select from it
mcp/llm   = how the assistant uses selected evidence
```

Example:

- A PDF upload, its rows, chunks, page blocks, ingestion issues -> `knowledge`
- “Given this user query, which chunks are best evidence?” -> `rag`
- “Call search/read tools and answer the user” -> `mcp` / `llm`

The main thing to watch is boundary creep. `rag/` may read `knowledge` models, but it should not start owning ingestion/storage behavior. `knowledge/` may prepare searchable artifacts, but it should not start doing query-specific ranking/orchestration.

So yes: separate apps are correct. The cleanup goal is to make that boundary clearer, not merge them.

---

is that common on production grade rags? so rag is just about retrieval and reasoning while knowledge holds the ingestion? i thought it was all called RAG

---

Yes, that split is common in production-grade systems.

“RAG” as a product/architecture term often refers to the whole pipeline:

```text
ingest documents -> parse/chunk/embed/store -> retrieve evidence -> answer with LLM
```

But in a production codebase, teams usually split that into domains:

```text
knowledge / corpus / documents / ingestion
  Owns uploads, parsing, extraction, chunking, tables, metadata, visibility, indexes, storage.

rag / retrieval / search
  Owns query understanding, hybrid search, reranking, evidence selection, read expansion, evals.

mcp / agents / chat / llm
  Owns tool orchestration, conversation flow, final answer generation.
```

So you’re both right:

- Conceptually, ingestion + retrieval + generation are all part of “RAG.”
- Code-architecture-wise, it is cleaner not to put all of that under one giant `rag/` bucket.

The reason is production maintenance. Ingestion and retrieval change for different reasons:

- Ingestion changes when file formats, OCR, table extraction, chunking, storage, background jobs, or upload lifecycle changes.
- Retrieval changes when query behavior, ranking, reranking, caching, semantic search, table search, or eval quality changes.

Keeping `knowledge/` and `rag/` separate makes the codebase easier to reason about and safer to modify. So yes: `knowledge` holding ingestion/storage and `rag` holding retrieval/evidence logic is a normal, healthy production boundary.

---

okay now what was on our list? go for it.

---

Continued the RAG cleanup and kept it behavior-safe.

This round added:

- [content_reading.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/content_reading.py): document/page/chunk read expansion.
- [auto_scoring.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/auto_scoring.py): auto-mode scoring, weak-hit detection, table/text chunk routing helpers.
- [auto_scope.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/auto_scope.py): broad-result scope/category summaries and category normalization.

Also cleaned up:

- Removed the moved code from [ai_orchestrator.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/ai_orchestrator.py), no duplicate runtime copies left.
- Removed one dead duplicate `_effective_neighbor_window`.
- Updated [README.md](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/README.md).
- Fixed MCP refs to include the computed `label` field in [tools.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/mcp/tools.py), which restored the section-span contract tests.

`ai_orchestrator.py` is now down to `5,751` lines.

Validation passed:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test apps.rag.tests.test_knowledge_search_service apps.rag.tests.test_table_row_expansion apps.rag.tests.test_rag_scalable_fixes apps.rag.tests.test_agentic_rag_regression_evals --noinput
.venv/bin/python manage.py test apps.mcp.tests.test_agentic_read_section_span apps.mcp.tests.test_agentic_read_regression_evals apps.api.tests.test_chat_portal apps.api.tests.test_chat_portal_files --noinput
```

Stale active import sweep is still clean: only the intentional bridge remains in `apps/rag/knowledge_search.py`.

Next RAG target: extract the remaining auto decision/conflict helpers, then the larger direct table-snippet construction block.

---

ok go next.

---

Continued the RAG finalization pass.

This round moved two more coherent chunks out of [ai_orchestrator.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/ai_orchestrator.py):

- [auto_decision.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/auto_decision.py): auto evidence labels, table/text arbitration, decision contracts, conflict detection, and no-result reasons.
- [table_snippets.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/table_snippets.py): direct table-row snippets and fallback snippet construction.

Also updated [README.md](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/README.md) and removed the duplicate runtime copies from `ai_orchestrator.py`.

Current `ai_orchestrator.py`: `4,583` lines.

Validation passed:

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test apps.rag.tests.test_knowledge_search_service apps.rag.tests.test_table_row_expansion apps.rag.tests.test_rag_scalable_fixes apps.rag.tests.test_agentic_rag_regression_evals --noinput
.venv/bin/python manage.py test apps.mcp.tests.test_agentic_read_section_span apps.mcp.tests.test_agentic_read_regression_evals apps.api.tests.test_chat_portal apps.api.tests.test_chat_portal_files --noinput
```

Stale active import sweep is still clean: only the intentional `knowledge_search.py -> ai_orchestrator.py` bridge remains.

Next target: `_chunk_to_snippet` and adjacent snippet-construction helpers, then the remaining route/table expansion helpers near the bottom.

---

I can see you added around 593 lines of code with no equal removals, did you duplicate or followed our strict rules move then clean up old if needed?

---

Yes, I followed the strict move-then-clean rule.

What you’re seeing is likely because the new files are still **untracked**, so plain `git diff --stat` shows the removals from `ai_orchestrator.py` but not the added file contents unless your UI separately counts untracked files.

Verification I just ran:

- `ai_orchestrator.py`: `16` added, `2599` removed
- New extracted modules:
  - `content_reading.py`
  - `auto_scoring.py`
  - `auto_scope.py`
  - `auto_decision.py`
  - `table_snippets.py`

I also searched for the moved method names. They now exist only in the new modules, not duplicated in `ai_orchestrator.py`, for example:

- `_table_search_snippets` -> `table_snippets.py`
- `_fallback_snippets` -> `table_snippets.py`
- `_derive_auto_decision_contract` -> `auto_decision.py`
- `_detect_conflicting_evidence` -> `auto_decision.py`
- `_score_auto_mode_candidates` -> `auto_scoring.py`
- `load_contents/load_page_window/load_chunk_contents` -> `content_reading.py`

So no, I did not intentionally duplicate active runtime code. The only non-move functional change was the small MCP fix adding `label` to returned refs, because tests exposed that the label was computed but not included in the payload.

---

Okay what's next?

---

Next best RAG target: extract `_chunk_to_snippet` and its adjacent snippet construction helpers from `ai_orchestrator.py`.

Why this one:

- It is still core “shape a retrieved chunk into a `KnowledgeSnippet`” logic.
- It sits naturally near the modules we just made: `content_serialization.py`, `table_snippets.py`, `metadata_helpers.py`.
- It should shrink `ai_orchestrator.py` further without touching search behavior.
- It is used by multiple search paths, so we’ll validate with the same RAG/MCP suites.

After that, the next likely chunks are:

1. Route/table expansion helpers near the bottom: `_route_chunk_hits`, `_table_parent_hits`, `_merge_expanded_table_hits`, `_expand_table_rows`.
2. Search candidate plumbing: `_chunk_hits`, `_vector_candidates`, `_lexical_candidates`, `_fts_candidates`, `_trigram_candidates`.
3. Eventually move `KnowledgeSearchService` itself out of `ai_orchestrator.py` so the `knowledge_search.py -> ai_orchestrator.py` bridge can disappear.

So the immediate next move is: `chunk_to_snippet` extraction, then validation.

---

Okay go for it.

---

Done. I continued the RAG cleanup and kept the strict move-then-remove rule.

Moved into [table_expansion.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/table_expansion.py):
- table/text hit routing
- table parent lookup
- table row hit/context helpers
- expanded table row merge logic
- table row expansion query/load logic

Removed the original copies from [ai_orchestrator.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/ai_orchestrator.py). Current size is down to `3,901` lines. The moved methods now only exist in `table_expansion.py`; `_chunk_to_snippet` only exists in `snippet_builder.py`.

Updated [README.md](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/README.md) with the new module map. Cleaned generated `__pycache__`.

Validation passed:
- `.venv/bin/python manage.py check`
- RAG focused suite: `102 OK`
- MCP/API read/search suite: `15 OK`

Stale import check is still clean: only [knowledge_search.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/knowledge_search.py:11) imports from `ai_orchestrator.py`, which is the intentional bridge until `KnowledgeSearchService` itself gets moved.

Next best target: split the remaining candidate retrieval/search plumbing from `ai_orchestrator.py`, likely around `_search_chunks`, `_chunk_hits`, alias hits, vector candidates, FTS/trigram candidates, and candidate merge/scope helpers.

---

Okay go for it.

---

Done. I kept the strict cleanup rule: moved active code into focused modules, removed the original copies from `ai_orchestrator.py`, then checked for duplicate definitions.

This round added:
- [table_expansion.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/table_expansion.py) for table/text routing and row expansion/merge logic.
- [candidate_retrieval.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/candidate_retrieval.py) for `_chunk_hits`, `search_free_text`, vector/lexical retrieval, query-vector caching, candidate merge, vector thresholds, and MMR.

Updated [README.md](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/README.md) with the new module map.

`ai_orchestrator.py` is now down to `2,895` lines. The moved methods now only exist in their new modules. The only remaining direct `ai_orchestrator` import is still the intentional bridge in `apps/rag/knowledge_search.py`.

Validation passed:
- `.venv/bin/python manage.py check`
- RAG focused suite: `102 OK`
- MCP/API search/read suite: `15 OK`
- `git diff --check`
- cleaned generated `__pycache__`

Next best RAG target: split alias retrieval into `alias_retrieval.py`, covering `search_by_alias`, `_alias_exact_hits`, `_alias_fuzzy_hits`, and `_normalize_alias_input`.

---

Ok go for it.

---

Done. I moved alias retrieval into [alias_retrieval.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/alias_retrieval.py) and removed the old copies from [ai_orchestrator.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/ai_orchestrator.py).

Moved:
- `search_by_alias`
- `_normalize_alias_input`
- `_alias_exact_hits`
- `_alias_fuzzy_hits`

Updated [README.md](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/README.md) with the new module. `ai_orchestrator.py` is now down to `2,603` lines.

Validation passed:
- `.venv/bin/python manage.py check`
- RAG focused suite: `102 OK`
- MCP/API search/read suite: `15 OK`
- `git diff --check`
- duplicate method scan clean
- generated `__pycache__` cleaned

Only remaining direct `ai_orchestrator` import is still the intentional bridge in [knowledge_search.py](/Users/adham2/Desktop/pocket_ai/pocketai_django/apps/rag/knowledge_search.py:11).

Next best RAG target: split snippet diversification and `_search_chunks` into something like `snippet_selection.py`, then the remaining orchestrator gets closer to only high-level flow, logging, and service setup.

---

