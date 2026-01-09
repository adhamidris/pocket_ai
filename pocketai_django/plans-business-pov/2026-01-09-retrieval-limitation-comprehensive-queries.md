# RAG Retrieval Limitation for Comprehensive Queries

**Date**: 2026-01-09
**Issue**: User asks "list all credit cards" but only receives partial results
**Conversation**: bb9a021c-f535-4b6b-ab7c-8704a32ab2e4
**Business**: 70c8914c-a476-4455-8442-31035698858b

---

## Executive Summary

When users ask comprehensive questions like "list all credit cards and their issuance fees", the RAG system returns only a subset of available credit cards from the PDF document, despite the document containing 18+ card types across 4-5 distinct tables. This creates a poor user experience and erodes trust, as evidenced by the user's final message: "so there are other cards and you were just lying to me".

---

## Root Cause Analysis

### Primary Root Causes

#### 1. **Comprehensive Intent Detection Disabled by Row Label Matching** (CRITICAL)

**Location**: `apps/rag/ai_orchestrator.py:4007`

```python
comprehensive_intent = has_comprehensive_keyword and not matched_row_labels
```

**Problem**: For query "list me all credit cards and their issuance fees":
- `has_comprehensive_keyword = True` (contains "list" and "all")
- `matched_row_labels` contains "credit", "cards", "fees" (common row labels in the PDF)
- **Result**: `comprehensive_intent = False`

The logic assumes that matching ANY row label means the user wants a specific row, but for "list all X" queries, this is incorrect. The user wants ALL rows, not a specific one.

#### 2. **Snippet Limit Too Low for Multi-Table Documents** (HIGH)

**Configuration**: `MCP_PROMPT_MAX_SNIPPETS=4`

**Problem**: The PDF has 4-5 distinct tables with 18+ card types. With only 4 snippets per search, users can only see ~4 cards per query. The system cannot show all cards even across multiple searches because:
- First search: 4 snippets (4 cards)
- Second search: All 4 previous snippets filtered out by seen-filter
- No mechanism to retrieve remaining tables

#### 3. **Table Diversification Not Working for Comprehensive Queries**

**Location**: `apps/rag/ai_orchestrator.py:4745-4760`

The round-robin table diversification code exists but only runs when `comprehensive_intent=True`. Since comprehensive_intent is incorrectly set to False (see Root Cause #1), this diversification never happens.

#### 4. **Chunk-Level vs Table-Level Retrieval Mismatch**

**Observation**: `chunk_candidates=23` consistently in logs

The PDF content is chunked at a granular level (23 chunks), but for comprehensive queries, users need table-level retrieval. The current system:
- Retrieves chunks based on semantic similarity
- May return 4 chunks from the same table (high similarity)
- Misses other tables with lower but still relevant similarity

---

## Evidence from Logs

```
conversation="bb9a021c-f535-4b6b-ab7c-8704a32ab2e4"

User Query: "list me all credit cards and their issuance fees"
Search Result: snippet_count=8, chunk_candidates=23
Tool Output: snippet_count=8 → clipped to MCP_PROMPT_MAX_SNIPPETS

User Follow-up: "any more?"
Search Result: snippet_count=8 BUT all 8 were already_seen
Tool Output: read_state_breakdown={} (empty - all filtered)

User: "so there are other cards and you were just lying to me"
```

---

## Proposed Solution

### Phase 1: Fix Comprehensive Intent Detection (Immediate)

**Change**: Modify the comprehensive_intent logic to not be disabled by row label matches when enumeration keywords like "all", "every", "list" are present.

```python
# Current (broken):
comprehensive_intent = has_comprehensive_keyword and not matched_row_labels

# Proposed fix:
enumeration_keywords = {"all", "every", "everything", "list", "complete", "entire", "whole"}
has_enumeration_keyword = bool(tokens & enumeration_keywords)
# Only disable comprehensive for "show Gold card" type queries (show + specific label, no enumeration)
comprehensive_intent = has_comprehensive_keyword and (has_enumeration_keyword or not matched_row_labels)
```

### Phase 2: Increase Snippet Limit for Comprehensive Queries (Short-term)

**Change**: Dynamically increase `MCP_PROMPT_MAX_SNIPPETS` when `comprehensive_intent=True`.

Add configuration:
```
MCP_PROMPT_MAX_SNIPPETS_COMPREHENSIVE=12
```

When comprehensive_intent is detected, use the higher limit to ensure multi-table documents are fully represented.

### Phase 3: Table-Level Diversification for Comprehensive Queries (Medium-term)

**Change**: When `comprehensive_intent=True`, ensure snippets are diversified across ALL tables in the document, not just the top-scoring chunks.

Algorithm:
1. Identify all distinct tables in the upload
2. Retrieve at least 1-2 representative rows per table
3. Apply round-robin selection across tables before applying snippet limit

### Phase 4: Document-Level "Show All" Mode (Long-term)

**Change**: For single-document queries with enumeration intent, provide a special mode that returns a structured summary of ALL tables in the document.

```json
{
  "mode": "document_overview",
  "tables": [
    {"title": "Credit Cards - Standard", "cards": ["E-Commerce", "White", "Classic", "Gold", "Cash Back"]},
    {"title": "Credit Cards - Premium", "cards": ["Mileseverywhere Standard", "Titanium", "Platinum"]},
    ...
  ]
}
```

---

## Business POV

### Scenarios and Expected User Experience

#### Scenario 1: Customer Comparing Credit Cards
**Before Fix**: Customer asks "list all credit cards". Receives 4-5 cards. Asks "any more?" and is told "no". Later discovers 10+ other cards exist. **Trust broken**.

**After Fix**: Customer asks "list all credit cards". Receives all 18+ cards grouped by category. Can immediately compare all options. **High satisfaction**.

#### Scenario 2: Sales Team Using Portal
**Before Fix**: Sales agent asks about products for a customer. Gets partial list. Recommends wrong product because better option was not shown. **Lost revenue**.

**After Fix**: Sales agent sees complete product catalog. Recommends optimal product. **Increased conversion**.

#### Scenario 3: Multi-Tenant Scalability
**Before Fix**: Each tenant with multi-table PDFs has this problem. Support tickets increase. Platform reputation suffers.

**After Fix**: Comprehensive queries work reliably across all tenants. Reduced support burden. **Platform credibility**.

### Success Metrics
- **Recall for comprehensive queries**: Target 95%+ of relevant content returned
- **User follow-up rate**: Reduce "any more?" type queries by 80%
- **User trust signals**: Reduce complaints like "you were lying" to near-zero
- **Table coverage**: For multi-table documents, ensure all tables represented in comprehensive query results

### Risk Assessment
- **Phase 1**: Low risk (logic fix, well-tested path)
- **Phase 2**: Medium risk (config change, may increase token usage)
- **Phase 3**: Medium risk (algorithm change, needs testing)
- **Phase 4**: Higher risk (new feature, needs design review)

---

## Implementation Priority

1. **Phase 1** - Fix comprehensive_intent logic (blocks everything else)
2. **Phase 2** - Increase snippet limit for comprehensive queries
3. **Phase 3** - Table diversification
4. **Phase 4** - Document overview mode (future consideration)

---

## Technical Details

### Files to Modify

1. `apps/rag/ai_orchestrator.py`
   - Line ~4007: Fix comprehensive_intent detection
   - Lines ~4745-4760: Ensure diversification runs when intent is correct

2. `apps/mcp/tools.py`
   - Line ~2948: Support dynamic snippet limit based on comprehensive_intent

3. `.env.organized` (or equivalent)
   - Add `MCP_PROMPT_MAX_SNIPPETS_COMPREHENSIVE=12`

### Testing Requirements

1. Unit test: comprehensive_intent detection with various query types
2. Integration test: "list all X" queries return all X from multi-table documents
3. Regression test: specific queries like "Gold card fees" still work correctly
4. Load test: ensure increased snippet limit doesn't break latency SLOs
