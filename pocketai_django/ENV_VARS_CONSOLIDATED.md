# Consolidated Environment Variables Guide

## Changes Made
1. **Removed `LLM_PROVIDER`** - Use only `MCP_PROVIDER`
2. **Removed `OPENAI_TEMPERATURE` and `DEEPSEEK_TEMPERATURE`** - Use only `LLM_TEMPERATURE`
3. **Clarified search controls** - Use LLM-driven approach

---

## Required Environment Variables

### Provider Selection
```bash
# Which LLM provider to use for MCP orchestration
# Options: "openai" or "deepseek"
MCP_PROVIDER=openai
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

### Search Controls (Recommended: LLM-Driven)
```bash
# How many times the LLM can call search_knowledge per user message
# Set to 3 for comprehensive queries ("list all X")
MCP_MAX_SEARCHES_PER_TURN=3

# How many query variants to try PER search call (internal expansion)
# Keep at 1 for predictable latency
MCP_SEARCH_MAX_QUERY_VARIANTS=1
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

❌ `LLM_PROVIDER` - Replaced by `MCP_PROVIDER`
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
MCP_MAX_SEARCHES_PER_TURN=3
MCP_SEARCH_MAX_QUERY_VARIANTS=1
MCP_SEARCH_KNOWLEDGE_CALLS_PER_MINUTE=120
```

---

## Migration Guide

If you have these old vars in your `.env`:

### Before:
```bash
LLM_PROVIDER=openai
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

### Option A: LLM-Driven (Recommended)
```bash
MCP_MAX_SEARCHES_PER_TURN=3
MCP_SEARCH_MAX_QUERY_VARIANTS=1
```
- ✅ LLM decides when to search again
- ✅ Adapts to query complexity
- ✅ Predictable latency per search

### Option B: System-Driven
```bash
MCP_MAX_SEARCHES_PER_TURN=1
MCP_SEARCH_MAX_QUERY_VARIANTS=3
```
- ✅ Automatic query expansion
- ❌ LLM can't adapt to partial results
- ❌ Higher latency per search

**We recommend Option A (LLM-driven)** for better control and adaptability.
