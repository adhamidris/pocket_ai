# Agentic Read V2 Spec (Phase 0)

NOTE (2026-01-26)
-----------------
This spec documents the current "agentic read v2" contract built around `read_document(items=[...], max_chars=...)`.

The platform is now moving toward a refs-first contract:
- `search_knowledge` returns EvidenceRefs (pointers), not content-heavy snippets
- `read_knowledge` ("Reading Knowledge") materializes canonical slices from refs

See: `apps/mcp/AGENTIC_EVIDENCE_REFS_V1_SPEC.md`

Goal
----
Provide a stable agentic retrieval experience by reducing the LLM-facing read surface
to ONE reliable method while keeping "no evidence loss" as the primary constraint.

The core idea:
- The system prompt stays simple (one voice).
- The tool boundary becomes smarter.
- The LLM uses a single read shape and follows deterministic continuations.

Non-goals (Phase 0)
------------------
- No runtime behavior changes yet.
- No migrations/artifact storage changes yet.
- No new tool implementations yet.

Gates
-----
- `MCP_NEW_CONTRACT_ENABLED` (existing): master gate for the "one voice + smart tools" contract.
- `MCP_AGENTIC_READ_V2_ENABLED` (new): gate for agentic read v2. Intended to be rolled out
  gradually and independently of the broader contract gate.

When agentic v2 is enabled:
- The LLM sees only the V2 read schema.
- Legacy read knobs (excerpt/full_page/pages/page/offset/neighbor/token_budget) are hidden
  and later rejected with constraint errors (cleanup phase).

Tool Contracts
--------------

search_knowledge (agentic view)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Return only "what exists + how big" so the model can plan reads. Avoid exposing read knobs.

Response (conceptual):
```json
{
  "tool": "search_knowledge",
  "status": "ok|empty|duplicate|throttled|error",
  "results": [
    {
      "id": "uuid",
      "title": "string",
      "type": "text|table",
      "source": "string",
      "preview": "string",
      "char_estimate": 11279,
      "read_hint": { "suggested_max_chars": 12000 }
    }
  ],
  "total_found": 3,
  "budget": { "...": "..." }
}
```

read_document (agentic v2)
~~~~~~~~~~~~~~~~~~~~~~~~~~
The ONLY agentic read interface:
- The model supplies ids and optional continuation cursors.
- The tool decides how to read (page blocks vs structured tables vs chunk windows).
- The tool returns either full content segments, deterministic continuations, or an artifact pointer.

Request:
```json
{
  "items": [
    { "id": "uuid" },
    { "id": "uuid", "cursor": "opaque_string_from_previous_read" }
  ],
  "max_chars": 12000
}
```

Response:
```json
{
  "tool": "read_document",
  "status": "ok|partial|constraint_error|throttled|error",
  "contents": [
    {
      "id": "uuid",
      "title": "string",
      "type": "text|table",
      "content": "string",
      "chars": 5300,
      "cursor_used": "opaque_or_null",
      "next_cursor": "opaque_or_null",
      "complete": true
    }
  ],
  "read": [
    {
      "id": "uuid",
      "status": "full|partial|artifact|error|deferred",
      "chars": 5300,
      "next_cursor": "opaque_or_null",
      "artifact_id": "uuid_or_null",
      "prompt_view": { "optional_small_summary": true }
    }
  ],
  "deferred": [
    {
      "id": "uuid",
      "reason": "not_enough_remaining_chars|exceeds_budget|too_large_for_single_segment",
      "chars": 11279,
      "suggested_max_chars": 12000,
      "hint": "string"
    }
  ],
  "errors": [{ "id": "uuid", "error_code": "not_found|...", "hint": "string" }],
  "max_chars": 12000,
  "max_chars_allowed": 10500,
  "budget": { "...": "..." }
}
```

Cursor Strategy (Decision)
-------------------------
Use stateless, signed cursors (opaque to the LLM).

Requirements:
- The LLM cannot forge or mutate cursors.
- Cursors are scoped to the current conversation and business.
- Cursor payloads may contain offsets (page/block indices) but must not expose PII.

Recommended implementation detail (later phase):
- Use `django.core.signing` to sign a compact JSON payload with a fixed salt, e.g.
  `mcp.read_cursor.v2`.
- Payload includes: conversation_id, business_profile_id, document_id, source_kind,
  and source-specific offsets.

Cursor Kinds (Phase 3)
----------------------
The cursor is an opaque, signed token. Internally it contains:
- version + expiry: `v=2`, `exp` (unix timestamp)
- scope: `conversation_id`, `business_id`, `item_id`
- kind: one of
  - `page_blocks`: `{upload_id, page_number, block_order, char_offset, prepend_sep?}`
  - `table_rows`: `{upload_id, table_id, row_chunk_index, char_offset, prepend_sep?}`
  - `chunk_window`: `{upload_id, chunk_start, chunk_end, chunk_index, char_offset, prepend_sep?}`
  - `artifact`: `{artifact_id, char_offset}`

`prepend_sep` is set only when resuming at an element boundary so that concatenating
multiple tool calls preserves the exact `\\n\\n` joins between blocks/rows/chunks.

Artifact Strategy (Decision)
---------------------------
When the tool cannot inline a safe segment (e.g., extremely large structured table),
store the full output out-of-band and return:
- `artifact_id` (UUID)
- `prompt_view` (small index/summary safe for prompt injection)
- continuation cursors for paging/querying the artifact

Implementation preference:
- Reuse `apps.mcp.models.McpToolOutputArtifact` for local knowledge artifacts
  (remote fields remain blank). This avoids new tables/migrations in Phase 4.
- Best-effort retention controls (Phase 4):
  - `MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS`
  - `MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION`

Budgets / Interactions
----------------------
Key constraint layering:
- `MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS` is a safety backstop (prompt injection).
- `MCP_READ_DOCUMENT_MAX_CHARS_MARGIN` reserves JSON overhead so read segments fit.
- `RAG_MAX_CHAR_BUDGET_PER_TURN` / `RAG_MAX_CHAR_BUDGET_PER_MINUTE` limit throughput.

Agentic read v2 should be designed so prompt-side truncation is rare-to-zero:
- Segment outputs to fit safely under `(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS - margin)`.
- Prefer returning `next_cursor` over dumping huge payloads.

Cleanup Target (Later Phase)
----------------------------
In agentic v2 mode, deprecate and remove the LLM-facing read knobs:
- excerpt/full_page
- neighbor_window
- pages/page/offset
- token_budget

Those can remain as internal strategies selected by the tool, not model choices.
