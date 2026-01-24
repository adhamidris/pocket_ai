# MCP Tool Output Truncation Fix (Prompt Compaction)

## Plan
1. Reproduce the failure where `read_document` tool outputs exceed `MCP_PROMPT_TOOL_OUTPUT_MAX_CHARS` and collapse into metadata-only JSON (`truncated=true`, `prompt_compact=true`) with no usable content.
2. Ensure knowledge tools (`search_knowledge`, `read_document`, table/dataset tools) are compacted into a stable, bounded prompt view that preserves IDs + a content preview.
3. Improve the tool-message truncation fallback to shrink large JSON while keeping a minimal content preview instead of dropping the payload entirely.
4. Add regression coverage for “oversized tool message still includes content preview”.
5. Validate via targeted MCP tests and a real conversation replay/log inspection.

## Business POV
- Scenario: A customer asks “Send me an email about your high-tier credit cards.” Expected UX: the assistant reads the relevant pricing/features page once and drafts the email immediately, without repeating “read” attempts. Success: fewer tool-loop iterations and no “content missing” reasoning.
- Scenario: A customer asks for a specific fee in a large PDF. Expected UX: the assistant receives a compact but usable excerpt (with correct IDs/pages) and answers or asks a precise follow-up. Success: no generic “tool returned nothing” behavior; follow-ups are specific (“Which card type?” / “Which page?”).
- Scenario: A tenant uploads a very large document. Expected UX: the assistant remains responsive and does not blow prompt budgets; it sees bounded previews and can progressively drill down. Success: stable latency and no regressions in “tool budget exceeded” errors.
- Scenario: Operators/support review tool traces. Expected UX: the trace still reflects meaningful evidence (even when truncated), making debugging straightforward. Success: support can see “what the model saw” without pulling raw artifacts.
