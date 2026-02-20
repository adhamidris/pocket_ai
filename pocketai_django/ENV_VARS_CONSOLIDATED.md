# Consolidated Environment Variables Guide

## Status Note
This guide reflects current usage:
- **MCP** uses `MCP_PROVIDER`.
- **Non‑MCP flows (voice/post‑call)** may use `LLM_PROVIDER`.
- `LLM_TEMPERATURE` applies across providers.

---

## Required Environment Variables

### Provider Selection (MCP)
```bash
# Which LLM provider to use for MCP orchestration
# Options: "openai" or "deepseek"
MCP_PROVIDER=openai
```

### Provider Selection (Non‑MCP / Voice)
```bash
# Optional: legacy/voice provider selection
# Options: "openai" or "deepseek"
LLM_PROVIDER=openai
```

### OpenAI Configuration
```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com  # Optional, defaults to official API
```

### DeepSeek Configuration
```bash
DEEPSEEK_API_KEY=sk-...
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com  # Optional
```

---

## LLM Behavior Controls

### Temperature (Applies to ALL providers)
```bash
# Controls randomness: 0.0 = deterministic, 1.0 = very creative
# Recommended: 0.3 for customer service
LLM_TEMPERATURE=0.3
```

### Agentic Read v2 (MCP)
```bash
# Enable refs-first read contract (read_knowledge)
MCP_AGENTIC_READ_V2_ENABLED=true

# Master contract gate (defaults to true in settings)
MCP_NEW_CONTRACT_ENABLED=true
```

### Scope Clarification UI Mode (MCP)
```bash
# Enable MCQ-style clarification metadata for broad-scope ambiguity.
# Default false keeps text-only clarification (backward-compatible payloads).
MCP_SCOPE_CLARIFICATION_MCQ_ENABLED=false
```

### Search Controls (Recommended: LLM-Driven)
```bash
# How many times the LLM can call search_knowledge per user message
# Set to 1 to encourage a single batched search per turn (use `queries=[...]`)
# Increase to allow follow-up/refinement searches when needed
MCP_MAX_SEARCHES_PER_TURN=1

# How many query variants to try PER search call (internal expansion)
# 3 is a good default for batched sub-queries; set to 1 to disable fanout
MCP_SEARCH_MAX_QUERY_VARIANTS=3
```

**Total queries per turn** = `MCP_MAX_SEARCHES_PER_TURN × MCP_SEARCH_MAX_QUERY_VARIANTS` = **3**

---

## Optional Search Tuning

```bash
# Time budget (ms) for parallel query fanout (0 = sequential)
# Only matters if QUERY_VARIANTS > 1
MCP_SEARCH_FANOUT_BUDGET_MS=0

# RRF K parameter for combining multi-query results
MCP_SEARCH_FANOUT_RRF_K=60

# Rate limit: max search_knowledge calls per minute (business-wide)
MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE=120

# Latency warning threshold (ms)
MCP_SLO_SEARCH_WARN_MS=1200
```

---

## Removed Variables (No Longer Needed)

❌ `OPENAI_TEMPERATURE` - Replaced by `LLM_TEMPERATURE`
❌ `DEEPSEEK_TEMPERATURE` - Replaced by `LLM_TEMPERATURE`

---

## Full Example .env

```bash
# === PROVIDER ===
MCP_PROVIDER=openai

# === OPENAI ===
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini

# === LLM BEHAVIOR ===
LLM_TEMPERATURE=0.3

# === SEARCH (LLM-DRIVEN) ===
MCP_MAX_SEARCHES_PER_TURN=1
MCP_SEARCH_MAX_QUERY_VARIANTS=3
MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE=120
```

---

## Migration Guide

If you have these old vars in your `.env`:

### Before:
```bash
OPENAI_TEMPERATURE=0.3
DEEPSEEK_TEMPERATURE=0.3
```

### After:
```bash
MCP_PROVIDER=openai
LLM_TEMPERATURE=0.3
# (Remove OPENAI_TEMPERATURE and DEEPSEEK_TEMPERATURE)
```

---

## Search Strategy Comparison

### Option A: Agentic Batched Search (Recommended)
```bash
MCP_MAX_SEARCHES_PER_TURN=1
MCP_SEARCH_MAX_QUERY_VARIANTS=3
```
- ✅ Single tool call per turn (lower tool-loop overhead)
- ✅ Handles multi-part questions via `queries=[...]` (fused + deduped results)
- ✅ Keeps prompt evidence bounded (snippets still capped)

### Option B: Strict Single Query (Lowest Latency)
```bash
MCP_MAX_SEARCHES_PER_TURN=1
MCP_SEARCH_MAX_QUERY_VARIANTS=1
```
- ✅ Most predictable/fastest search
- ❌ More likely to miss relevant docs when wording varies

If you want an escape hatch for refinement, set `MCP_MAX_SEARCHES_PER_TURN=2` (one batched search + one fallback search).
