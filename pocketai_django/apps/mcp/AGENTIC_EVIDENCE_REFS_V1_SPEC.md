# Agentic Evidence Refs V1 Spec (Phase 0)

Goal
----
Make RAG feel "truly agentic" and efficient:

1) **Search** finds *where* the answer is (pointers only).
2) **Reading Knowledge** fetches only the needed slice (typed, canonical, bounded).
3) The assistant answers/acts (CRM/email/etc.) without duplicate/noisy evidence.

Primary product constraints
---------------------------
- **No prompt bloat**: the assistant must not dump huge lists/tables into the prompt and pretend it is complete.
- **No fake completeness**: if the requested result cannot fit safely, the assistant must ask the user to filter (or paginate only if the user explicitly requests it).
- **No end-user citations needed** (internal ops tool), but we keep provenance for debugging and action safety.
- RBAC inside a tenant is out-of-scope for this phase.

Key Definitions
---------------
EvidenceRef
  A small pointer to canonical evidence, returned by `search_knowledge`. It is cheap to transmit and deterministic to read.

Canonical evidence
  The "source of truth" slice we read and show to the LLM for reasoning/action:
  - text excerpts are anchored (page/block anchors)
  - tables are read as structured rows/columns (lossless)

Noise rule
  The model should not see the same fact twice (e.g., table-row + text-restatement) unless the second item adds new fields/context.

Tool Contracts
--------------

search_knowledge (refs-first)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Returns pointers (EvidenceRefs), not content dumps.

Request (conceptual):
```json
{
  "query": "string",
  "queries": ["optional variants"],
  "limit": 8
}
```

Response (conceptual):
```json
{
  "tool": "search_knowledge",
  "status": "ok|empty|duplicate|throttled|error",
  "refs": [
    {
      "id": "opaque_string (Phase 1: usually a UUID that read_document can read)",
      "kind": "table_group|text_anchor|entity_record",
      "document_id": "uuid",
      "label": "Issuance and Renewal Fees",
      "score": 0.91,
      "why": ["matched_row_label: issuance and renewal fees"],
      "coverage_hint": {
        "estimated_rows": 20,
        "estimated_columns": 2,
        "matched_fields": ["card_type", "fee"]
      }
    }
  ],
  "total_found": 1,
  "budget": { "...": "..." }
}
```

Notes:
- The evidence planner behind `search_knowledge` performs dedup by canonical anchor and routes by intent.
- Refs should be stable for a document version (so caching is effective).

read_knowledge ("Reading Knowledge")
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Replaces `read_document` for normal agentic flows.

This is the ONLY supported way for the LLM to materialize evidence refs.

Request (conceptual):
```json
{
  "refs": [
    { "ref": "opaque_string" },
    { "ref": "opaque_string", "cursor": "opaque_cursor_from_previous_read" }
  ],
  "max_chars": 12000,
  "mode": "auto|excerpt|table_rows"
}
```

Response (conceptual):
```json
{
  "tool": "read_knowledge",
  "status": "ok|partial|constraint_error|throttled|error",
  "evidence": [
    {
      "ref": "opaque_string",
      "kind": "table_rows|text_excerpt",
      "title": "Fees and Charges Credit Cards Eng_185",
      "complete": true,
      "truncated": false,
      "next_cursor": "opaque_or_null",
      "payload": {
        "type": "table",
        "columns": ["card_type", "fee"],
        "rows": [
          ["white", "EGP 500"],
          ["classic", "EGP 250"]
        ]
      }
    }
  ],
  "budget": { "...": "..." }
}
```

Table payload policy (locked)
-----------------------------
- `read_knowledge` may return **compact table text** only when completeness is guaranteed (no loss).
- Otherwise it returns **lossless rows/columns** and the assistant renders.

"List everything" policy (locked)
---------------------------------
When the user requests "everything":
- Attempt a single bulk read only if it fits within safe prompt/output limits.
- If it cannot fit:
  - do not pretend it is complete,
  - ask the user to filter (recommended),
  - paginate only if the user explicitly requests continuation ("continue").

Cursor Strategy
---------------
Keep the existing `next_cursor` approach, but scope it to EvidenceRefs.

The cursor must be:
- opaque to the LLM,
- scoped to business + conversation,
- deterministic (continuation resumes from an exact row/block offset),
- safe (no raw PII embedded).

Acceptance Criteria (Phase 0)
-----------------------------
1) We can represent every result in the current system as either:
   - an EvidenceRef (pointer) or
   - canonical evidence (read output).
2) We can enforce "no prompt bloat" + "no fake completeness" as a first-class rule.
3) We have at least one worked example for each evidence kind:
   - text excerpt
   - table group -> table rows
4) We can explain, in logs/debugging, why a ref was selected without requiring end-user citations.
