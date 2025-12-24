# RAG Efficiency Optimization - Search Snippet First Strategy

## Overview
Optimized the MCP orchestrator to minimize unnecessary tool calls by instructing the LLM to answer directly from search snippets when they contain sufficient information.

## Problem
Previous behavior:
1. `search_knowledge` returns snippet with **complete answer**
2. LLM **still calls** `read_knowledge` to fetch structured table data
3. Result: **2 tool calls, higher latency, increased costs**

Example from logs (product code 20666):
- Search snippet already contained: product name, code, distribution data
- System still made a second `table.aggregate` call
- Total time: ~15 seconds for a simple lookup

## Solution
Added explicit optimization rules in two locations:

### 1. Guardrails Section (line 98)
```python
"Start answering as soon as the evidence is enough. If search snippets already 
contain complete information to fully answer the visitor's sole question, respond 
immediately—do not make additional tool calls unless the visitor asks for more 
details or the snippet is ambiguous."
```

### 2. Tool Playbook - search_knowledge (line 131)
```python
"EFFICIENCY RULE: If search snippets already contain complete information to fully 
answer the visitor's question, respond directly without calling `read_knowledge`. 
Only proceed with `read_knowledge` if: 
  (a) the snippets are incomplete or ambiguous, OR 
  (b) the visitor explicitly requests additional details not present in the snippets, OR 
  (c) you need precise structured table data for complex queries that snippets cannot satisfy."
```

## When to Use read_knowledge

The LLM should now **only** call `read_knowledge` when:

| Condition | Example |
|-----------|---------|
| **Snippets are incomplete** | Snippet shows product exists but visitor asks for detailed specs not in snippet |
| **Visitor asks for more details** | "What product holds code 20666?" → snippet answers. "Show me ALL the distribution data" → needs `read_knowledge` |
| **Ambiguous snippets** | Multiple conflicting snippets, need full document context |
| **Complex table queries** | Aggregations, sorting, filtering that require structured data |
| **Follow-up on same source** | Already have `document_id`, need different columns/filters |

## When to Answer from Snippet

The LLM should **answer directly** from search snippet when:

✅ Snippet contains the exact information requested  
✅ User asks a simple lookup question ("what product...", "who is...", "when did...")  
✅ No ambiguity in the snippet  
✅ Visitor hasn't requested additional details  

## Expected Benefits

### Before Optimization:
```
User: "what product holds code of 20666?"
→ search_knowledge (200ms)
→ read_knowledge/table.aggregate (6000ms)  
→ LLM response (2000ms)
Total: ~8.2 seconds, 2 tool calls
```

### After Optimization:
```
User: "what product holds code of 20666?"
→ search_knowledge (200ms)
→ LLM response (2000ms)
Total: ~2.2 seconds, 1 tool call
```

**Improvements:**
- ⚡ **73% faster** (~6 seconds saved)
- 💰 **50% fewer tool calls** (cost reduction)
- 🎯 **Better UX** (quicker responses)

## Testing

To verify the optimization is working:

1. **Restart the Django server** (required for prompt changes)
2. Ask a simple question: `"what product holds code of 20666?"`
3. Check `rag.log` for `search_knowledge.snippets`
4. Verify **only 1 tool call** (search_knowledge), no `read_knowledge` call
5. Response should come directly from snippet

### Test Queries

**Should answer from snippet (1 tool call):**
- "what product holds code of 20666?"
- "who is the customer with email xyz@example.com?"
- "what is the price of product X?"

**Should use read_knowledge (2 tool calls):**
- "show me ALL distribution data for product 20666"
- "give me a breakdown of sales by location for code 20666"
- "compare product 20666 with product 20667"

## Files Modified

- `/apps/mcp/prompts.py`: Lines 98 and 131

## Rollback

If needed, revert the changes in `prompts.py` to restore previous behavior.

## Notes

- This optimization is **conservative** - it still allows `read_knowledge` when genuinely needed
- The LLM decides based on snippet content sufficiency, not just snippet presence
- Complex queries naturally trigger `read_knowledge` for structured data
- Simple lookups get instant answers from search
