# Plan: Add search pagination + “show me more” stability

## Plan
1. Add a cursor to `search_knowledge` responses (`next_cursor` + `has_more`).
2. Cache a per-search session in a tenant-safe cache so paging does not re-run the DB/vector search.
3. Add optional “exclude already shown” behavior so follow-up pages don’t repeat the same top results.
4. Propagate cursor hints through duplicate-intent reuse (so the agent pages instead of re-searching).
5. Add tests + `.env` knobs for operators.

## Business POV (expected UX)
### Scenario 1: Visitor asks for a complete list (results > page size)
- Before: The assistant returns only the top N results, then re-searches and often gets the same top N again.
- After: The assistant returns the first page plus `next_cursor`, then quickly fetches the next page(s) without re-searching.
- Success metrics: fewer searches, fewer repeated results, faster completion.

### Scenario 2: Visitor says “show me more”
- Before: “More” often repeats the same snippets because ranking keeps the same items in the top limit.
- After: Paging skips previously shown snippets and returns genuinely new results.
- Success metrics: fewer user retries, higher “new info” rate per follow-up.

### Scenario 3: Performance / cost under load
- Before: Multiple re-searches hammer the backend for what is effectively “page 2”.
- After: Page 2+ is served from cache within the cursor TTL.
- Success metrics: reduced backend QPS, lower p95 latency for follow-ups.

### Scenario 4: Debugging and support
- Before: Hard to reproduce “why did it repeat?” because re-searching changes the ranking.
- After: Cursor lets support teams replay deterministic “next page” flows.
- Success metrics: fewer inconsistent reproductions, easier traceability.
