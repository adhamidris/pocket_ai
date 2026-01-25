# Plan: Reduce redundant RAG tool loops

## Plan
1. Inspect recent `rag.log` runs to quantify search/read repetition and batching behavior.
2. Trace prompt-windowing and compaction paths to confirm whether earlier tool evidence is being dropped mid-loop.
3. Verify env-var wiring for snippet caps, query fanout, tool-output truncation, and stage history limits.
4. Align prompts/guardrails so the model trusts retrieval results, batches queries/reads early, and safely stops when evidence is missing.
5. Validate improvements by re-running a representative “promotional email” request and comparing tool counts and latency.

## Business POV (expected UX)
### Scenario 1: “Send a promotional email about World Credit Card”
- Before: The assistant repeatedly searches for “benefits / perks / rewards”, reads the same document/pages multiple times, then risks either delay or invented details.
- After: The assistant does 1 batched search, 1 batched read, writes the email using only found evidence, and clearly states when “benefits brochure / detailed perks” isn’t present in the KB.
- Success metrics: fewer tool calls, faster response, no hallucinated benefits/fees.

### Scenario 2: Multi-part question (“fees + limits + eligibility”)
- After: The assistant uses `queries=[...]` once, reads the relevant docs once, and answers each sub-part with citations grounded in retrieved content (without internal tool talk).
- Success metrics: one-pass completeness, minimal back-and-forth, consistent answers.

### Scenario 3: Missing information (KB genuinely doesn’t contain it)
- After: The assistant stops after a small bounded retrieval attempt and tells the user what isn’t available, optionally requesting the missing brochure/policy doc.
- Success metrics: high trust, fewer escalations, no forced guessing.

### Scenario 4: Tight budgets (lower snippet caps / tool-output limits)
- After: The assistant still behaves “politely”: uses batching to stay within budgets, summarizes what it has, and explicitly marks unknowns instead of filling gaps.
- Success metrics: stable behavior under constraints, predictable latency/cost.

