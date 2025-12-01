• Trace Walkthrough

  - Overall turn portal.orchestrator.turn ran ~45.2 s. Most of that was sequential DeepSeek calls; Django + orchestrator glue stayed sub‑100 ms per span.
  - Initial pass llm.deepseek.tools (span 90b29a…) took ~5.9 s to classify intent and propose the first tool call.
  - Tool iteration #0 (search): MCP called search_knowledge once (a80beb…), which triggered a short RAG run (knowledge.search ~0.33 s with alias short-circuit). DeepSeek then spent ~10.5 s
  (b77f71…) processing those snippets before asking for the next tool. Total time for iteration #0 ≈ 10.9 s.
  - Tool iteration #1 (table aggregate): Backend ran a single batched table_aggregate (73114e…) which finished in 2.8 s. DeepSeek then reasoned for ~18 s (a17351…) on the returned rows before
  emitting the final answer request. Total time for iteration #1 ≈ 20.8 s.
  - Final answer: Prompt build negligible; the answer LLM call (962221…) streamed in ~7.5 s. Sanitizer added <1 ms.
  - Planner/finalize: Planner prompt triggered another DeepSeek call (54813c…) lasting ~10.6 s, bringing finalization to ~10.7 s. Planner still proposed zero actions/extractions (validation
  showed 0 valid entries despite 4 attempts flagged “unknown_type”).

  LLM Call Count

  1. Initial pass (reasoning before tools) – 5.9 s.
  2. After search_knowledge tool – 10.5 s.
  3. After table_aggregate tool – 18 s.
  4. Final answer drafting – 7.5 s.
  5. Planner/finalizer – 10.6 s.

  Total: 5 DeepSeek calls, same structure as the prior trace.

  Bottlenecks

  - LLM latency dominates: 5 calls sum to ~52 s of LLM time, which is larger than the 45 s wall clock because some overlap occurs with backend work.
  - table_aggregate backend work is inexpensive (2.8 s), but the reasoning before/after it is long.
  - Planner still consumes ~10 s even when producing no actions. Planner validation flagged 4 “unknown_type” extractions, meaning the planner tried to output something outside the schema;
  prompts are enforcing “no legacy actions,” but there’s room to remind the planner to skip non-mapped extraction types entirely.

  Observations vs prior run

  - Same 2-tool pattern (search → aggregate). No redundant list_tables or read_document.
  - Knowledge search faster this time (0.33 s vs ~0.93 s) due to alias short-circuit.
  - Table call still batched all six products + three stores correctly; no iteration cap issues.

  Follow-ups

  - If we want to shave latency further, planner call (~10 s) is now proportionally large; consider shorter planner prompt or async later.
  - The biggest single block remains the LLM reasoning after table_aggregate (18 s). Fast-path batching/guard (once unstashed) will reduce iterations but not reasoning time; to shrink that,
  we’d need model-side adjustments (temperature, shorter context, or even caching partial responses).
