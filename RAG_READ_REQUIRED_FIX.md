# RAG Efficiency Fix - Addressing read_required Override Issue

## Problem Identified

The LLM was still calling `read_knowledge` even after receiving sufficient snippets because:

1. **Backend sets `read_required=True`** automatically for `intent="identifier"` queries with ≤2 snippets (line 2305-2309 in tools.py)
2. **Previous prompt instruction was too weak**: "read_required is a hint, not a command"
3. **LLM followed the hint** despite having complete information in snippets

## Root Cause Code

```python
# apps/mcp/tools.py, lines 2305-2309
if intent == "identifier":
    if len(snippet_payloads) <= 2:
        read_required = True  # ← Automatically set!
```

For the query "what product holds code of 20666?":
- Intent detected: `identifier`  
- Snippets returned: 1
- System sets:** `read_required=True`
- LLM sees the hint and calls `read_knowledge` unnecessarily

## Solution - Strengthened Prompt Instructions

Added **4 explicit override instructions** to make the LLM ignore `read_required` when snippets are sufficient:

### 1. Guardrails Section (line 98)
**Before:**
```
"If snippets already cover the question, stop calling tools."
```

**After:**
```
"If search snippets already contain complete information to fully answer the 
visitor's sole question, respond immediately—do not make additional tool calls 
unless the visitor asks for more details or the snippet is ambiguous."
```

### 2. Evidence Rules (line 113)  
**Before:**
```
"`read_required` is a hint, not a command. Table aggregates already count as full evidence."
```

**After:**
```
"`read_required` is a suggestion, NOT a mandate. Even when `read_required=true`, 
if the search snippet already contains ALL the information needed to fully answer 
the visitor's question, respond directly without calling `read_knowledge`. Only 
call `read_knowledge` when genuinely necessary (incomplete data, ambiguous snippets, 
or visitor requests additional details)."
```

### 3. search_knowledge Tool (line 131)
```
"EFFICIENCY RULE: If search snippets already contain complete information to fully 
answer the visitor's question, respond directly without calling `read_knowledge`. 
Only proceed with `read_knowledge` if: (a) snippets are incomplete or ambiguous, 
OR (b) visitor explicitly requests additional details not in snippets, OR (c) you 
need precise structured table data for complex queries snippets cannot satisfy."
```

### 4. read_knowledge Tool (line 134)
```
**Efficiency check first**: Before calling this tool, verify that search snippets 
don't already contain sufficient information to answer the visitor's question. 
Only proceed if you need more data."
```

## Testing Instructions

### 1. Restart Django Server
```bash
pkill -f "python manage.py runserver 127.0.0.1:3000"
cd pocketai_django
source .venv/bin/activate  
python manage.py runserver 127.0.0.1:3000
```

### 2. Test Query
Ask: **"what product holds code of 20666?"**

### 3. Check Logs
```bash
tail -100 var/logs/rag.log | grep -E "(search_knowledge|read_knowledge)"
```

**Expected behavior:**
```
✅ search_knowledge.snippets (returns complete answer)
✅ [NO read_knowledge call]
✅ LLM responds directly
```

**Current (buggy) behavior:**
```
❌ search_knowledge.snippets (returns complete answer)  
❌ read_knowledge/table.aggregate (unnecessary call)
❌ LLM responds
```

### 4. Verify Success
Look for **ONLY ONE** tool call in the latest conversation logs. Should see:
- `search_knowledge.snippets` at timestamp X
- `turn.summary` shortly after (no read_knowledge in between)

## Decision Tree for LLM

```
User asks: "what product holds code of 20666?"
    ↓
search_knowledge returns snippet with:
  - Product Code: 20666
  - Product Name: ازموراب 40 مجم 14 كبسولة  
  - Distribution data
  - read_required: true ← IGNORE THIS
    ↓
Question: Does snippet contain COMPLETE answer?
  YES → Answer directly (1 tool call) ✅
  NO  → Call read_knowledge for more data
```

## Alternative Solutions Considered

### Option A: Remove backend read_required logic (REJECTED)
- Would require changing tools.py line 2305-2309
- Might break other use cases that genuinely need the hint
- Prompt-level fix is safer

### Option B: Add snippet content analysis (COMPLEX)
- Would require LLM to parse and validate snippet content
- Already doing this implicitly with stronger instructions

### Option C: Current solution (CHOSEN) ✅
- Strengthen prompt instructions in multiple places
- Override backend hints when snippets are sufficient  
- No backend code changes needed
- Conservative and safe

## Files Modified

- `/apps/mcp/prompts.py`:
  - Line 98: Enhanced guardrail
  - Line 113: Strengthened read_required override
  - Line 131: Added EFFICIENCY RULE  
  - Line 134: Added efficiency check reminder

## Rollback

If issues arise, revert changes in `prompts.py` lines 98, 113, 131, and 134.

## Success Metrics

Before:
- Average: 2 tool calls per identifier lookup
- Latency: ~8-15 seconds
- read_knowledge call rate: ~95%

Target:
- Average: 1 tool call per identifier lookup
- Latency: ~2-4 seconds  
- read_knowledge call rate: <20% (only when genuinely needed)
