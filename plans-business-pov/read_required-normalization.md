# Normalize `read_required` to mean “context is insufficient”

## Plan

### Goal
Make `read_required` consistently mean: **the current snippet context is likely insufficient to answer accurately without reading more** (via `read_document`, `get_document_structure`, or table tooling). Today it is often used as a proxy for `intent == "identifier"` and/or “preview exists”, which drives unnecessary reads and inconsistent enforcement.

### Design principles
- **Sufficiency-first**: prefer a “can we answer from what we already have?” signal over query-type heuristics.
- **Deterministic and observable**: backend decisions should be explainable (reasons + telemetry), not opaque.
- **Low-cost by default**: avoid extra LLM calls in the hot path unless explicitly enabled.
- **Guardrails stay strict**: identifier gating remains the privacy boundary; `read_required` is not a permission.

### Step 1 — Define `read_required` semantics + triggers
- New semantics: `read_required=True` indicates “answer may be incomplete/unsafe without reading more”.
- `read_required` should be computed primarily from **snippet properties** and **query requirements**, not only query type.
- Add a small set of **reasons** (e.g., `table_truncated`, `preview_too_short`, `needs_exact_terms`, `multi_item_enumeration`, `identifier_low_coverage`) to make behavior debuggable.

### Step 2 — Implement a sufficiency-based heuristic (backend)
Implement a `compute_read_required(...)` routine used by `search_knowledge` that:
- Starts with `read_required=False`.
- Flips to `True` when:
  - `read_state in {"summary","preview"}` AND any “incomplete evidence” signals are present (e.g., truncated tables, very short snippet text, missing key fields that the question asks for).
  - The query implies exhaustive coverage (enumeration) and we only have partial table previews (prefer `get_document_structure`/`full_page`).
- Uses `intent == "identifier"` only as a **prior** (lower thresholds), not as a rule.
- Emits `read_required_reasons=[...]` for each snippet and an aggregated summary for the tool response.

### Step 3 — Align orchestrator enforcement with the new meaning
Delete the orchestrator “forced read” behavior entirely so the system remains LLM-driven (not a deterministic chatbot gate).
- Remove the final-answer override that blocks responses when `read_required` is present (and/or tables are present).
- Replace hard enforcement with observability:
  - Log/metric when `read_required=true` but no read tool was invoked in the turn.
  - Track “answer-after-read” deltas (if available) to tune heuristics safely.

### Step 4 — Update prompts + docs to remove contradictions
Prompts should match the backend contract (with no backend forcing):
- Replace “ignore `read_required` when snippet is sufficient” with “trust `read_required` because it means insufficient context”.
- Keep an explicit “answer directly when `read_required=false`” rule.
- Update internal docs explaining the new contract.

### Step 5 — Tests + observability
- Unit tests for `compute_read_required` (cover identifier lookup, table truncation, enumeration intent, already-full snippets).
- Regression tests ensuring orchestrator never injects/returns a “forced read” placeholder response.
- Add dashboards/metrics:
  - `read_document_call_rate` by intent
  - `unfulfilled_read_required_rate` (`read_required=true` but no reads)
  - latency P50/P90
  - “answer corrected after read” proxy (if available)

### Step 6 — Rollout plan
- Behind a feature flag / business config (for the *heuristic* only; forced-read gate is removed unconditionally):
  - Phase 1: enable logging + reasons only (no behavior changes).
  - Phase 2: enable new computation for a small tenant set.
  - Phase 3: remove any legacy prompt language that encourages redundant reads.

## Business POV

### What improves for customers
- Faster answers for simple ID/code lookups when the preview already contains the answer.
- Fewer “please wait while I read…” moments when not needed.
- More reliable answers on policies/fees/eligibility because the system reads when evidence is incomplete.

### What improves for the business
- Lower tool spend (fewer unnecessary reads) and lower latency.
- Higher trust: fewer hallucinations caused by answering from incomplete previews.
- Better debuggability: “why did we read?” is visible via reasons and metrics.

### Realistic scenarios + expected experience
1) **SKU/product-code lookup**
   - Visitor: “What product is code 20666?”
   - Expected: `search_knowledge` returns a snippet containing the mapping → `read_required=false` → instant answer (1 tool call).

2) **Fees / legal fine print**
   - Visitor: “What are the fees and charges for Card X?”
   - Expected: preview snippet lacks the full fee table → `read_required=true` (`table_truncated` / `preview_too_short`) → system reads the page and answers with exact values.

3) **“List all” / exhaustive request**
   - Visitor: “List all cards and their annual fees.”
   - Expected: system uses structure/table tools to confirm completeness; if only partial previews are retrieved, `read_required=true` until full coverage is obtained.

4) **Sensitive document gated by identifiers**
   - Visitor: “Show my statement for December.”
   - Expected: identifier guardrail blocks retrieval without required identifiers; agent asks for the required identifier(s) rather than reading arbitrary docs.

5) **Table-heavy question with partial evidence**
   - Visitor: “Compare limits across these 3 plans.”
   - Expected: `read_required=true` if table columns/rows aren’t fully present in snippet; reads once (batched pages) then answers.

### Success measures
- Reduce `read_document` calls on code/ID lookups without reducing accuracy.
- Ensure zero backend “forced read” gating and zero placeholder overrides.
- Maintain or improve answer quality on policy/fee questions (fewer corrections/escalations).
- Improve latency (especially P90) on common short queries.

### What might regress (and how we’ll detect it)
- If we under-trigger reads, we risk incomplete answers → monitor “follow-up clarification rate” and “answer corrected after read” signals.
- If we over-trigger reads, we increase latency/cost → monitor `read_document_call_rate` and P90 latency.
