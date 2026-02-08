## Plan

1. Add a PDF-only table-overlap annotation step in `KnowledgeIngestionService`.
   - Build per-page table regions from extracted `TablePayload.bbox`.
   - Compute block-to-table overlap ratio using block bbox area overlap.
   - Mark overlapping narrative blocks with `metadata.suppress_text_chunk=true` when overlap exceeds a threshold.

2. Apply annotation after table extraction/repair/post-processing/limits in `_extract_from_file`.
   - Keep the suppression decision aligned with the final tables that will be persisted.
   - Record lightweight diagnostics under extraction metadata for observability.

3. Update `_build_text_segments_from_blocks` to skip blocks explicitly marked for suppression.
   - Preserve existing decorative/type filters.
   - Do not alter behavior for documents that are not PDF or have no table overlap annotation.

4. Add regression tests.
   - Verify overlapping blocks are marked and filtered from text segments.
   - Verify non-overlapping blocks remain chunked.

5. Run targeted tests.
   - `apps.knowledge.tests.test_knowledge_ingestion_phase_one`
   - `apps.knowledge.tests.test_table_vlm_repair`

## Business POV

### Scenario 1: Table-heavy PDF (e.g., tariffs/pricing sheet)
Expected UX improvement:
- Answers cite cleaner row/table evidence instead of noisy duplicated text fragments.
- Retrieval relevance improves for exact fee/rate questions.
Risk:
- If threshold is too aggressive, some nearby explanatory text may be dropped.
Success metric:
- Lower proportion of duplicated table-as-text chunks and higher exact-answer hit rate on table queries.

### Scenario 2: Mixed PDF (narrative + small tables)
Expected UX improvement:
- Narrative sections remain available while table internals are represented via structured table chunks.
Risk:
- Accidental suppression of paragraphs near table borders if bbox quality is poor.
Success metric:
- No drop in answer quality for narrative-only questions on mixed documents.

### Scenario 3: Non-table PDF/manual text/docs
Expected UX improvement:
- No behavior change (feature is PDF+table-overlap gated).
Risk:
- None expected if gating is correct.
Success metric:
- Chunk counts and retrieval behavior remain consistent for non-table content.

### Scenario 4: Operational debugging and trust
Expected UX improvement:
- Clear metadata on how many blocks were suppressed per page.
- Faster diagnosis when a tenant reports missing or noisy evidence.
Risk:
- Additional metadata could be misread as errors if not labeled clearly.
Success metric:
- Reduced QA iteration time for ingestion/retrieval investigations.
