# 2026-01-26 — Agentic RAG Refactor: Evidence Refs + "Reading Knowledge"

## Plan (Phased)

### Phase 0 — Align on the “Agentic RAG Contract” (1–2 days)
- Define the new contract in product terms: **Search finds where the answer is; Read fetches only what’s needed; Answer acts**.
- Define “canonical evidence units” we will support in v1:
  - `text_anchor` (small excerpt around a stable anchor)
  - `table_group` / `table_rows` (structured rows, not markdown blobs)
  - (optional later) `entity_record` (for JSON-like sources)
- Define the “noise rule”: the model should **not** see the same fact twice (table + text) unless the second source adds new fields/context.
- Decide the minimal UI/debug needs for operators (internal only): “why did we pick this evidence?” (no end-user citations required).
- Confirm scope decisions for this refactor:
  - RBAC inside a tenant is out-of-scope for now (single “business context”).
  - “List everything” must be LLM-generated (no table viewers / attachments as the primary experience).
  - When “everything” is too large to print safely in one answer, the assistant must ask to narrow or paginate.

Deliverable: a short spec (tool inputs/outputs + examples) and acceptance criteria.
Spec location: `apps/mcp/AGENTIC_EVIDENCE_REFS_V1_SPEC.md`

---

### Phase 1 — Introduce EvidenceRefs (Search Returns Pointers, Not Content) (2–5 days)
- Add an internal “EvidenceRef” shape returned by `search_knowledge`:
  - ID, kind, document_id, label, score, “why matched”, and a tight “coverage hint” (no long previews).
- Add server-side “evidence planner” logic:
  - Choose a small set of best refs.
  - Deduplicate by canonical anchor (e.g., `table_id+row_index`, `page_anchor+block_anchor`).
  - Route by intent (table-first vs text-first) but **only output refs**.
- Keep backwards compatibility temporarily behind a feature flag if needed; otherwise hard-cut since you’re pre-production.

Deliverable: `search_knowledge` output is smaller/cleaner, and stable across repeated calls.

---

### Phase 2 — Implement `read_knowledge` ("Reading Knowledge") (3–7 days)
- Implement `read_knowledge(refs=[{ref, cursor?}], max_chars=..., mode=...)`.
- Implement canonical reads per ref type:
  - Text: excerpt around anchor (bounded).
  - Tables: structured rows/columns (lossless), plus an optional compact rendering only when it is guaranteed complete (no knowledge loss).
- Port the existing continuation logic (`next_cursor`) to apply to refs:
  - Cursor continues within the same ref deterministically (e.g., “next rows” / “next blocks”).
- Ensure budgets are enforced at read-time (chars/tokens), not at search-time.
- Add a “bulk read attempt” behavior for “list everything”:
  - Try to materialize the full list in one read *only* when it fits within safe prompt/output limits (never bloat the prompt).
  - If it cannot fit, return a bounded partial plus `next_cursor` and metadata (row count, table count) so the assistant can ask the user to narrow. Pagination is only used when the user explicitly requests it.

Deliverable: the assistant can read exactly the needed slice and continue with `next_cursor` when needed.

---

### Phase 3 — Update Orchestrator Behavior (Query → Read → Answer) (2–5 days)
- Update prompting/tool guidance so the LLM’s default loop becomes:
  1) `search_knowledge` (refs only)
  2) `read_knowledge` (materialize)
  3) answer / act (CRM/email/etc.)
- Add a “high confidence fast path” (optional optimization):
  - Backend prefetches the top ref’s read payload into cache immediately after search (so the read is near-instant).
- Ensure the assistant asks clarifying questions when the evidence ref indicates multiple possible meanings (e.g., “which card type?”).
- Add the “list everything” display policy (to prevent silent knowledge trimming):
  - If the full list fits within the configured caps, print it in the LLM response.
  - If it exceeds caps, do not pretend it’s complete; ask the user to narrow by filters (and paginate only on explicit request).

Deliverable: fewer wasted tool calls, less irrelevant evidence, and consistent behavior across intents.

---

### Phase 4 — Remove / Sunset Legacy Paths That Create Noise (2–7 days)
- Deprecate “content-heavy `search_knowledge` snippets” in agentic mode.
- Ensure the normal agentic workflow uses `read_knowledge` only.
- Ensure tables are represented canonically via `read_knowledge` (structured rows/columns), not large table-markdown chunks.

Deliverable: a single clear way to retrieve evidence, with minimal duplication and stable budgets.

---

### Phase 5 — Scale + Quality Hardening (ongoing, but start immediately after Phase 2)
- Add retrieval quality tests (“golden queries”) for PDFs + tables + spreadsheets:
  - Verify the right ref is chosen, correct read slice, and low noise.
- Add observability:
  - Evidence planner decision logs (ref counts, dedup stats, selected anchors).
  - Read continuation stats (`next_cursor` usage), average reads per turn, and token/char spend.
- Performance tuning:
  - Cache ref resolution and read materialization per tenant/doc version.
  - Batch reads; cap ref counts; enforce deterministic ordering.

Deliverable: measurable improvements in speed, cost, and reliability as tenants/docs grow.

---

## Business POV (What Changes for Users)

### Scenario 1 — “Give me issuance & renewal fees”
Today: the AI may see many table chunks (interest rates, withdrawal limits, FX markup) and spend time picking.
After: it finds one pointer (“Issuance & Renewal Fees”), reads only those rows, and answers directly.
- Success = fewer irrelevant steps, faster answer, lower token spend, fewer wrong answers.

### Scenario 2 — “Draft an email to a customer about fees”
Today: the AI might read extra tables and risk mixing fees.
After: it reads the exact policy/table rows needed, then drafts the email with the right numbers.
- Success = fewer corrections, higher trust, fewer “wrong fee” incidents.

### Scenario 3 — “List all card issuance fees (full list)”
Today: the AI might partially read and miss some rows, or mix unrelated tables.
After: it does a single bulk read attempt and prints the full list if it fits; if too big, it asks the user to narrow or explicitly paginate (using `next_cursor`).
- Success = no fake completeness, fewer retries, predictable time/cost.

### Scenario 4 — “Ambiguous query” (e.g., “card fees”)
Today: the AI might dump mixed evidence and guess.
After: it reads the best matching ref and then asks a single clarifying question (“issuance, replacement, or annual fee?”) when needed.
- Success = fewer wrong assumptions, fewer retries.

### Scenario 5 — Operator debugging (internal)
Today: hard to tell why the AI picked certain snippets.
After: internal logs/inspector show: chosen refs, why they matched, what was read, and where continuation happened.
- Success = faster debugging and iteration during onboarding.

---

## Final Decisions (Locked)
1) “List everything” is printed only when it fits within safe prompt/output limits; otherwise the assistant asks the user to filter (no prompt bloat).
2) Over-cap default is: ask to narrow first; paginate only when the user explicitly requests continuation.
3) `read_knowledge` may return compact table text only when completeness is guaranteed; otherwise it returns lossless rows/columns for the LLM to render.
