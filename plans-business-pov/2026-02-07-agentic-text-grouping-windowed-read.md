# Plan

1. Add text-chunk grouping controls in search shaping
2. Emit document-anchor refs with chunk manifests
3. Persist grouped manifests in per-turn tool context
4. Read grouped anchors via bounded matched windows
5. Mark redundant same-document chunk reads as covered
6. Add targeted tests for grouping and read behavior
7. Run focused MCP test modules

# Business POV

## Why this matters
When search returns many adjacent chunks from the same PDF, users see slot waste and lower-quality answers. Grouping related chunks into one coherent document context gives the model better evidence and improves first-response clarity.

## Practical scenarios

### 1) Fee table explanations split across adjacent PDF chunks
- **Today:** Results show chunk 4, 5, and 6 as separate refs; the model spends evidence slots and often answers with fragmented context.
- **After fix:** Those refs collapse into one `document_anchor` with a chunk manifest and windowed reads around matched chunks.
- **Success signal:** Better first-answer completeness with fewer repeated follow-up searches.

### 2) Mixed table rows and text chunks in one query
- **Today:** Table consolidation helps, but text evidence from the same document is still noisy and repetitive.
- **After fix:** Table grouping remains intact while text grouping runs independently for non-table snippets.
- **Success signal:** Cleaner evidence plans and lower prompt clutter.

### 3) Redundant read calls after a grouped document read
- **Today:** A model may read a doc-level ref and then still attempt chunk-level reads from the same upload.
- **After fix:** Subsequent chunk reads from that upload are marked `covered` after access checks.
- **Success signal:** Reduced token waste and fewer no-op tool calls.

### 4) Large PDF safety
- **Today:** Naive full-document reads can waste budget before reaching relevant sections.
- **After fix:** Reads are bounded to matched chunk windows (plus small neighbor context) and capped by configured limits.
- **Success signal:** Stable latency and predictable char usage on large documents.

## Risks / regressions to watch
- Over-aggressive grouping can hide distinct sections if thresholds are too low.
- Chunk-window bounds need safe caps to avoid oversized reads.
- Manifest cache misses should degrade safely without broad, irrelevant document scans.
