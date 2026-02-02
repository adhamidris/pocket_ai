# Agentic Read V2 Spec (Current)

NOTE (2026-02-02)
-----------------
This spec documents the current **refs-first** agentic read contract used in MCP.
The LLM calls **`read_knowledge`**, not `read_document`, when agentic v2 is enabled.
`read_document` remains implemented for non-agentic/legacy flows and is **deprecated** in agentic mode.

See also: `apps/mcp/AGENTIC_EVIDENCE_REFS_V1_SPEC.md`

Goal
----
Provide a stable, deterministic retrieval experience by reducing the LLM-facing
read surface to **one** method and enforcing predictable continuation via cursors.

The core idea:
- The system prompt stays simple (one voice).
- The tool boundary becomes smarter.
- The LLM uses a single read shape and follows deterministic continuations.

Non-goals
---------
- Exposing page/offset/neighbor knobs to the LLM.
- Allowing agentic LLMs to query tabular datasets directly (backend-only).

Gates
-----
- `MCP_NEW_CONTRACT_ENABLED` — master gate for the “one voice + smart tools” contract.
- `MCP_AGENTIC_READ_V2_ENABLED` — gate for agentic read v2 (refs-first + cursor continuation).

When agentic v2 is enabled:
- The LLM is instructed to call `read_knowledge(refs=[...], max_chars=...)`.
- Legacy read knobs (excerpt/full_page/pages/page/offset/neighbor) are hidden and rejected.

Tool Contracts
--------------

search_knowledge (agentic view)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Returns **EvidenceRefs** (pointers), not full snippets. Refs can include optional
previews and read hints so the model can plan `max_chars` for `read_knowledge`.

Response (conceptual):
```json
{
  "tool": "search_knowledge",
  "status": "ok|empty|duplicate|throttled|error",
  "refs": [
    {
      "id": "chunk-uuid",
      "document_id": "upload-uuid",
      "kind": "text_anchor|table_row|table_chunk",
      "type": "text|table",
      "label": "Human title",
      "char_estimate": 11279,
      "read_hint": { "suggested_max_chars": 12000 }
    }
  ],
  "total_found": 3,
  "read_budget_hint": { "total_suggested_max_chars": 12000, "max_chars_allowed": 12000 },
  "next_cursor": "opaque",
  "has_more": true
}
```

read_knowledge (agentic v2)
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The **only** agentic read interface:
- The model supplies refs (IDs) and optional continuation cursors.
- The tool chooses the right read strategy (page blocks, table rows, chunk windows).
- The tool returns evidence plus deterministic continuation cursors when needed.

Request:
```json
{
  "refs": [
    { "id": "chunk-uuid" },
    { "id": "chunk-uuid", "cursor": "opaque_string_from_previous_read" }
  ],
  "max_chars": 12000
}
```

Response (conceptual):
```json
{
  "tool": "read_knowledge",
  "status": "ok|truncated|constraint_error|throttled|error|already_read",
  "evidence": [
    {
      "id": "chunk-uuid",
      "type": "text|table",
      "payload": { "text": "..." },
      "chars": 5300,
      "cursor_used": "opaque_or_null",
      "next_cursor": "opaque_or_null",
      "complete": true,
      "truncated": false
    }
  ],
  "read": [
    { "id": "chunk-uuid", "status": "full|truncated|artifact|error|deferred", "chars": 5300 }
  ],
  "deferred": [
    {
      "id": "chunk-uuid",
      "reason": "empty|exceeds_budget|too_large_for_single_segment",
      "hint": "string"
    }
  ],
  "errors": [{ "id": "chunk-uuid", "error_code": "not_found|...", "hint": "string" }],
  "max_chars": 12000,
  "max_chars_allowed": 10500,
  "total_chars": 5300,
  "budget": { "...": "..." }
}
```

For the precise payload shape and cursor semantics, see:
`apps/mcp/tools.py::_agentic_read_v2_handler`.

Cursor Strategy
---------------
Use stateless, signed cursors (opaque to the LLM).

Requirements:
- The LLM cannot forge or mutate cursors.
- Cursors are scoped to the current conversation and business.
- Cursor payloads may contain offsets (page/block indices) but must not expose PII.

Implementation detail:
- Cursors are signed with an HMAC and include expiration (`exp`).
- Payload includes: conversation_id, business_profile_id, item_id, and offsets.

Cursor Kinds
------------
The cursor is an opaque, signed token. Internally it contains:
- version + expiry: `v=2`, `exp` (unix timestamp)
- scope: `conversation_id`, `business_id`, `item_id`
- kind: one of
  - `page_blocks`: `{upload_id, page_number, block_order, char_offset, prepend_sep?}`
  - `table_rows`: `{upload_id, table_id, row_chunk_index, char_offset, prepend_sep?}`
  - `chunk_window`: `{upload_id, chunk_start, chunk_end, chunk_index, char_offset, prepend_sep?}`
  - `artifact`: `{artifact_id, char_offset}`

`prepend_sep` is set only when resuming at an element boundary so that concatenating
multiple tool calls preserves the exact `\n\n` joins between blocks/rows/chunks.

Artifact Strategy
-----------------
When the tool cannot inline a safe segment (very large payload), it stores the
full output out-of-band and returns:
- `artifact_id` (UUID)
- `prompt_view` (small, safe preview)
- continuation cursors for paging/querying the artifact

Implementation preference:
- Reuse `apps.mcp.models.McpToolOutputArtifact` for local knowledge artifacts.
- Best-effort retention controls:
  - `MCP_READ_DOCUMENT_ARTIFACT_RETENTION_DAYS`
  - `MCP_READ_DOCUMENT_ARTIFACT_MAX_PER_CONVERSATION`

Budgets / Interactions
----------------------
Key constraint layering:
- `MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS` is a safety backstop.
- `MCP_READ_DOCUMENT_MAX_CHARS_MARGIN` reserves JSON overhead so read segments fit.
- `RAG_MAX_CHAR_BUDGET_PER_TURN` / `RAG_MAX_CHAR_BUDGET_PER_MINUTE` limit throughput.

Agentic read v2 should be designed so prompt-side truncation is rare-to-zero:
- Segment outputs to fit safely under `(MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS - margin)`.
- Prefer returning `next_cursor` over dumping huge payloads.

Cleanup Target (Later Phase)
----------------------------
In agentic v2 mode, keep legacy read knobs **internal** to the tool:
- excerpt/full_page
- neighbor_window
- pages/page/offset
- token_budget

These remain backend strategies, not model choices.
