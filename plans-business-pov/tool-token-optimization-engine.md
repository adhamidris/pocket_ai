# Plan: Tool Contract & Token Optimization Engine

## Goal
Reduce per-turn token usage and improve tool-call reliability by advertising only the minimum relevant tool schemas to the LLM (especially external MCP tools), without adding extra LLM calls/latency.

## Key design decisions
- **Deterministic tool routing (server-side):** select a top-K subset of remote MCP tools per turn based on lexical relevance to the user message.
- **No extra model hops:** avoid 2-step “tool selection” LLM calls; routing happens in-process.
- **Schema compaction:** remote tool schemas/descriptions are trimmed to reduce prompt bloat while preserving required fields/types.
- **Continuity bias:** recent remote connection usage nudges selection to keep follow-up turns stable.

## Implementation steps
1. Add a small, testable remote-tool router that selects top-K tools per turn.
2. Refactor remote tool schema generation to build from normalized descriptors (single source of truth).
3. Gate remote tool advertising in agentic mode behind the router (configurable max tools; “allow all” escape hatch).
4. Add regression tests covering:
   - minimal internal tool contract in agentic mode
   - top-K remote tool selection when user intent clearly targets a remote integration
5. Add debug visibility for token usage breakdown to diagnose “why this turn is expensive”.

## Rollout & safety
- Default: apply remote-tool routing only when `rag_agentic_mode` is enabled.
- Config knobs:
  - `MCP_AGENTIC_REMOTE_TOOL_MAX_TOOLS`
  - `MCP_AGENTIC_ALWAYS_ADVERTISE_REMOTE_TOOLS`
- Deterministic ordering to avoid “refresh changes tool list” inconsistencies.

---

# Business POV

## Why this matters
Token waste shows up as higher infra cost, lower throughput, and a worse UX (slow responses, earlier context exhaustion). It also makes tool-calling less reliable when the model is overwhelmed with irrelevant tools.

## Scenarios
1. **Greeting / smalltalk**
   - User: “Hi”
   - Expected UX: instant response, no tool catalog loaded, near-zero prompt overhead.
   - Success: prompt tokens drop sharply vs. previous behavior.

2. **GitHub request (read-only)**
   - User: “List one of my GitHub repos”
   - Expected UX: only a small GitHub-relevant subset of tools is available; agent calls the right tools quickly.
   - Success: fewer prompt tokens per call; tool accuracy stays high.

3. **Follow-up on same integration**
   - User: “Now open an issue in that repo”
   - Expected UX: router continues to expose the same integration tools (continuity) so the agent doesn’t “forget” what it can do.
   - Success: reduced retries/clarifications; stable tool availability.

4. **Non-MCP knowledge question**
   - User: “What fees do you charge for X?”
   - Expected UX: only knowledge tools are advertised; remote MCP tools are excluded.
   - Success: lower prompt size and fewer irrelevant tool calls.

## Risks / trade-offs
- If top-K selection is too strict, the model might not see a niche tool it needs. Mitigation: configurable K, continuity bias, and an “advertise all” escape hatch.

## Metrics
- Median prompt tokens per turn (overall + by route: greeting / knowledge / remote MCP).
- Tool-call success rate (fewer retries, fewer wrong-tool attempts).
- P95 latency per turn.
