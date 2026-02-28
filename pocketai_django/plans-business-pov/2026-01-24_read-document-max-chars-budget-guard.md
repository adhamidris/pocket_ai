# Agentic `read_knowledge` Max Chars Budget Guard

## Plan
1. Compute a prompt-safe `max_chars_allowed` for agentic `read_knowledge(refs=...)` using: per-turn remaining character budget, `RAG_MAX_INLINE_KNOWLEDGE_CHARS`, and `MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS` (minus a safety margin for JSON escaping/overhead).
2. When the LLM requests `max_chars` above `max_chars_allowed`, return a `constraint_error` with `error_code=max_chars_exceeded`, `max_chars_allowed`, and a clear retry hint.
3. Ensure prompt compaction for agentic `read_knowledge` does not silently shrink content below what the tool returned (bounded by the prompt-safe limit).
4. Add regression tests to prevent reintroducing “guess and retry” behavior.

## Business POV
- Scenario: Visitor asks for a detailed product comparison from a large PDF. Expected UX: assistant reads a bounded excerpt and answers; if more detail is needed, it asks a tighter follow-up or reads another chunk. Success: no repeated “increase max_chars” loops; answers remain grounded.
- Scenario: Visitor requests “read the whole document”. Expected UX: assistant responds with a clear limitation (“I can only read up to X chars at a time”) and proposes a structured approach (read specific pages/sections). Success: fewer timeouts; better user trust due to explicit constraints.
- Scenario: Support debugging a “missing details” complaint. Expected UX: tool traces clearly show `max_chars_allowed` and the model’s retry behavior. Success: faster root-cause isolation and fewer ambiguous “tool returned nothing” tickets.
