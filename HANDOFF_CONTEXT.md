# Session Handoff: Table/Text Chunk Consolidation Fix

## Situation

File: `pocketai_django/apps/mcp/tools.py` on branch `9to5`

The file currently has **broken uncommitted changes** from two layers:
1. **Original good changes** (246-line diff) — table consolidation that was mostly working
2. **Codex's "fixes" on top** — which regressed things (fake score 1.0, reverted consolidation, single-row reads, caused LLM hallucination)

## What needs to happen

1. **Copy current file as backup**: `cp pocketai_django/apps/mcp/tools.py /tmp/tools_codex_backup.py`
2. **git restore**: `git restore pocketai_django/apps/mcp/tools.py`
3. **Re-apply ONLY the original good 246-line diff** (details below)
4. **Apply the per-table threshold fix** (details below)

## The original good diff to re-apply

These were the original uncommitted changes before Codex touched them. Apply them to the git-restored file:

### Change 1: Table row counting + promotion flag (insert after line ~4013, after `planned_snippets` dedup loop)

```python
    table_row_ref_counts: Counter[str] = Counter()
    total_table_row_candidates = 0
    for snippet in planned_snippets:
        if not bool(snippet.get("is_table_chunk")):
            continue
        diagnostics = (
            snippet.get("source_diagnostics")
            if isinstance(snippet.get("source_diagnostics"), Mapping)
            else {}
        )
        table_id = diagnostics.get("table_id")
        row_index = diagnostics.get("row_index")
        if row_index is None:
            row_index = diagnostics.get("table_row_index")
        if not table_id or row_index is None:
            continue
        total_table_row_candidates += 1
        table_row_ref_counts[str(table_id)] += 1

    promote_table_context = bool(
        total_table_row_candidates >= 3
        or any(count >= 2 for count in table_row_ref_counts.values())
    )
    promoted_table_ids: set[str] = set()
```

### Change 2: In the ref-building loop, add promote_table_ref logic

After the `kind = "text_anchor"` line (~line 4084 in committed), add:
```python
        promote_table_ref = bool(
            content_type == "table"
            and table_id
            and row_index is not None
            and promote_table_context
            and table_row_ref_counts.get(str(table_id), 0) >= 2  # <-- THIS IS THE FIX
        )
```
Note: The original diff did NOT have the last condition. **Add it** — this is the per-table threshold fix that prevents over-promotion of single-row tables.

Then after `kind = "table_row" if ... else "table_chunk"`:
```python
            if promote_table_ref:
                kind = "table_chunk"
```

### Change 3: Ref ID replacement for promoted tables

Replace `if not chunk_id: continue` with:
```python
        ref_id = chunk_id
        if promote_table_ref and table_id:
            canonical_table_id = str(table_id).strip()
            try:
                canonical_table_id = str(uuid.UUID(canonical_table_id))
            except (TypeError, ValueError):
                canonical_table_id = ""
            if canonical_table_id:
                if canonical_table_id in promoted_table_ids:
                    continue
                promoted_table_ids.add(canonical_table_id)
                ref_id = canonical_table_id

        if not ref_id:
            continue
```

### Change 4: Why list for promoted tables

Replace `why.append("kind:table")` with:
```python
            if promote_table_ref:
                why.append("kind:table_context")
            else:
                why.append("kind:table")
```

### Change 5: Use ref_id instead of chunk_id in ref_item

Change `"id": chunk_id` to `"id": ref_id` in the ref_item dict.

### Change 6: Coverage hint adjustment for promoted refs

After `ref_item["why"] = why[:3]`, add:
```python
        if promote_table_ref and isinstance(coverage_hint, dict):
            matched_row_index = coverage_hint.get("row_index")
            if matched_row_index is not None:
                coverage_hint["matched_row_index"] = matched_row_index
            coverage_hint.pop("row_index", None)
```

### Change 7: Move search rate limit to after cache check (~line 4767→5738 area)

Remove these lines from their original location (before query processing):
```python
    limited = _enforce_search_rate_limit()
    if limited is not None:
        return limited
    context.reserve_search()
```

And insert them later, gated on `non_cached_queries > 0`:
```python
    if non_cached_queries > 0:
        limited = _enforce_search_rate_limit()
        if limited is not None:
            return limited
        context.reserve_search()
```

### Change 8: Column dedup function (insert around line 7112)

```python
        def _dedupe_column_labels(raw_columns: Sequence[str]) -> list[str]:
            deduped: list[str] = []
            seen: dict[str, int] = {}
            for index, raw_column in enumerate(raw_columns):
                label = str(raw_column or "").strip() or f"column_{index + 1}"
                key = label.lower()
                count = seen.get(key, 0) + 1
                seen[key] = count
                deduped.append(label if count == 1 else f"{label}_{count}")
            return deduped
```

Then after columns are built: `columns = _dedupe_column_labels(columns[:200])`

### Change 9: Read-side table grouping (insert around line 7426)

Pre-query for row table ref counts:
```python
    row_table_ref_counts: Counter[str] = Counter()
    # ... (query KnowledgeUploadTableRow to count rows per table_id)
    expanded_row_tables_seen: set[str] = set()
```

In the row_record handling block, add grouping:
```python
                grouped_table_ref_count = row_table_ref_counts.get(str(table_id), 0)
                expand_to_table_context = bool(
                    table_id
                    and grouped_table_ref_count >= 2
                    and not cursor_payload
                )
                if expand_to_table_context and table_id in expanded_row_tables_seen:
                    read.append({
                        "id": item_id,
                        "status": "covered",
                        "chars": 0,
                        "hint": "Covered by an earlier table context read for this table.",
                    })
                    continue

                effective_start_row = 0 if expand_to_table_context else row_index
                effective_max_rows: int | None = None if expand_to_table_context else 1
```

And after the read: `if expand_to_table_context: expanded_row_tables_seen.add(table_id)`

## Key bug fix (not in original diff)

In the `promote_table_ref` condition, add per-table check:
```python
and table_row_ref_counts.get(str(table_id), 0) >= 2
```
This prevents promoting tables that only have 1 row in results. The original code promoted ALL tables when the global `promote_table_context` was True (which was almost always, since 3+ total rows is common).

## What Codex broke (do NOT re-apply)

- Set all scores to `1.0` — fake, harmful
- Reverted table consolidation — reads return 1 row instead of full table
- Caused LLM to hallucinate entire credit card fee tables from nothing

## Background context

- The broader goal is consolidating same-document chunks into single search slots (10 slots per search)
- Table consolidation (by table_id) is implemented and needs the threshold fix
- Text chunk consolidation (by upload_id) is the next phase — not yet implemented
- The Codex branch `final6-codex-rag-ingestion-update` has a different refactor (unified read_knowledge tool) in `apps/services/mcp/` path — separate from this work
